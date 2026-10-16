#!/usr/bin/env python3
#
# Changelog fragments: each pull request describes itself in a file of its own
# under changelog.d/, and cutting a release compiles them into CHANGELOG.md.
#
# Why this exists: every pull request used to append its entry to the one
# [Unreleased] section of CHANGELOG.md. `.gitattributes` marked the file
# merge=union, so local git never conflicted there, but GitHub merges without
# a repository's merge drivers: as soon as one pull request with an entry
# merged, every other open one with an entry showed as conflicting and needed
# a rebase. A file of its own touches no line another pull request touches, so
# two of them never conflict, and CHANGELOG.md changes only when a release is
# cut.
#
# A fragment is `changelog.d/<name>.<section>.md`. <name> is the branch, or
# anything else no other pull request will pick; <section> is one of SECTIONS.
# The text is the entry exactly as CHANGELOG.md is to show it: a bullet, "- ",
# whose continuation lines are indented two spaces. A pull request with two
# entries for one section puts both in the file, a blank line between them.
#
# Commands - stdlib only, so CI runs it with a bare python3:
#
#   check            [Unreleased] in CHANGELOG.md holds no entries, every
#                    fragment is well-formed, and no entry is written twice.
#                    CI runs it on every pull request.
#   check --release  The same, and no fragment is left over: a pull request
#                    into master releases what it carries, so its entries
#                    must already be in the release's section. CI runs it on
#                    pull requests into master.
#   preview          Print the [Unreleased] section the fragments make.
#   compile X.Y.Z    Write the fragments into CHANGELOG.md as the section
#                    `## [X.Y.Z] - <date>`, delete them, and set CITATION.cff's
#                    version and date-released. Run again for the same
#                    version, it adds what merged since to that section.
#   extract          Move entries written under [Unreleased] in CHANGELOG.md
#                    into fragments named after the current branch: the way
#                    out for a branch that still edits CHANGELOG.md.
#
# Usage:
#   python3 tooling/changelog.py check
#   python3 tooling/changelog.py compile 1.13.0 [--date 2026-10-20]
from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = "CHANGELOG.md"
FRAGMENTS = "changelog.d"
CITATION = "CITATION.cff"

# A section's file-name suffix and its heading, in the order a release lists
# them: Keep a Changelog's six, plus the Performance this changelog has used.
SECTIONS = {
    "added": "Added",
    "changed": "Changed",
    "deprecated": "Deprecated",
    "removed": "Removed",
    "performance": "Performance",
    "fixed": "Fixed",
    "security": "Security",
}

UNRELEASED = "## [Unreleased]"
# What stands under [Unreleased] between releases. compile and extract write
# it; check accepts it or nothing.
UNRELEASED_NOTE = (
    "Changes merged since the newest release below are described in\n"
    "[`changelog.d/`](changelog.d/), one file per pull request, until the next\n"
    "release gathers them here."
)

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
FRAGMENT_NAME = re.compile(rf"(?P<name>{NAME.pattern})\.(?P<section>[a-z]+)\.md")
VERSION = re.compile(r"\d+\.\d+\.\d+")
VERSION_HEADING = re.compile(
    r"^## \[(?P<version>\d+\.\d+\.\d+)\](?: - (?P<date>\d{4}\.\d{2}\.\d{2}))?"
)

HOW_TO_ADD = (
    "A changelog entry goes in a file of its own, changelog.d/<branch>.<section>.md "
    f"(<section> one of {', '.join(SECTIONS)}), not in CHANGELOG.md, so that no two "
    "pull requests edit the same lines. See changelog.d/README.md."
)


class Problem(NamedTuple):
    path: str
    line: int | None
    message: str


class Fragment(NamedTuple):
    path: Path
    name: str
    section: str
    text: str  # the entries, without leading or trailing blank lines


class ChangelogError(Exception):
    """A command that cannot go ahead; the message says why."""


# ============= Reading =============


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _plural(count: int, word: str, words: str | None = None) -> str:
    return f"{count} {word if count == 1 else words or word + 's'}"


def split_entries(lines: list[str], first: int = 0) -> list[tuple[int, str]]:
    """The bullet entries in `lines`, each as (index of its first line, text).

    An entry runs from a "- " at the start of a line to the next one, a
    heading, or a paragraph that starts at the margin after a blank line,
    which Markdown no longer reads as part of the list item. A line at the
    margin straight after the entry's text is a lazy continuation and stays.
    """
    entries: list[tuple[int, str]] = []
    current: list[str] | None = None
    start = 0
    previous_blank = False

    def close() -> None:
        if current is not None:
            entries.append((start, "\n".join(current).rstrip()))

    for index, line in enumerate(lines, start=first):
        if line.startswith("- "):
            close()
            current, start = [line], index
        elif line.startswith("#") or (line[:1].strip() and previous_blank):
            close()
            current = None
        elif current is not None:
            current.append(line)
        previous_blank = not line.strip()
    close()
    return entries


def _normal(text: str) -> str:
    """An entry with its whitespace folded, to compare two copies of it."""
    return " ".join(text.split())


def _section_of(title: str) -> str | None:
    """The SECTIONS key a `###` heading names, if any."""
    return next(
        (key for key, name in SECTIONS.items() if name.lower() == title.lower()), None
    )


def _read_changelog(root: Path) -> list[str]:
    return (root / CHANGELOG).read_text(encoding="utf-8").split("\n")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _unreleased_span(lines: list[str]) -> tuple[int, int]:
    """Indices of the [Unreleased] heading and of the heading after its section."""
    for start, line in enumerate(lines):
        if line.rstrip() == UNRELEASED:
            break
    else:
        raise ChangelogError(f"{CHANGELOG} has no `{UNRELEASED}` heading")
    for end in range(start + 1, len(lines)):
        if lines[end].startswith("## "):
            return start, end
    return start, len(lines)


def _fragment_problems(rel: str, text: str) -> list[Problem]:
    """Why `text` would not compile into the entries it is meant to be."""
    lines = text.split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return [Problem(rel, None, "the fragment is empty")]
    problems = []
    if not lines[first].startswith("- "):
        problems.append(
            Problem(
                rel, first + 1, 'an entry starts with "- ", as it reads in CHANGELOG.md'
            )
        )
    previous_blank = False
    for index in range(first, len(lines)):
        line = lines[index]
        if line.startswith("#"):
            problems.append(
                Problem(
                    rel,
                    index + 1,
                    "a fragment holds entries only: its section comes from the file name",
                )
            )
        elif line[:1].strip() and not line.startswith("- ") and previous_blank:
            problems.append(
                Problem(
                    rel,
                    index + 1,
                    "this paragraph is not part of an entry: indent a continuation "
                    "paragraph by two spaces",
                )
            )
        previous_blank = not line.strip()
    return problems


def read_fragments(root: Path) -> tuple[list[Fragment], list[Problem]]:
    """The fragments under changelog.d/, and what is wrong with any of them."""
    fragments: list[Fragment] = []
    problems: list[Problem] = []
    directory = root / FRAGMENTS
    if not directory.is_dir():
        return fragments, problems
    for path in sorted(directory.iterdir()):
        rel = _relative(path, root)
        if path.name == "README.md" or path.name.startswith("."):
            continue
        match = FRAGMENT_NAME.fullmatch(path.name)
        if not path.is_file() or not match:
            problems.append(
                Problem(
                    rel,
                    None,
                    "not a fragment: name it <branch>.<section>.md, "
                    f"<section> one of {', '.join(SECTIONS)}",
                )
            )
            continue
        if match["section"] not in SECTIONS:
            problems.append(
                Problem(
                    rel,
                    None,
                    f"no section is called {match['section']!r}: use one of "
                    f"{', '.join(SECTIONS)}",
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        found = _fragment_problems(rel, text)
        problems.extend(found)
        if not found:
            fragments.append(
                Fragment(path, match["name"], match["section"], text.strip("\n"))
            )
    return fragments, problems


def _fragments_or_fail(root: Path) -> list[Fragment]:
    fragments, problems = read_fragments(root)
    if problems:
        report(problems)
        raise ChangelogError("fix the problems above first")
    return fragments


# ============= check =============


def check(root: Path = ROOT, release: bool = False) -> list[Problem]:
    """Everything that stops the changelog from compiling as it should."""
    lines = _read_changelog(root)
    start, end = _unreleased_span(lines)
    problems = []

    body = "\n".join(lines[start + 1 : end]).strip()
    if body and body != UNRELEASED_NOTE:
        problems.append(
            Problem(
                CHANGELOG,
                start + 1,
                f"the [Unreleased] section has been edited. {HOW_TO_ADD} "
                "To move what is there into a fragment: python3 tooling/changelog.py extract",
            )
        )

    fragments, fragment_problems = read_fragments(root)
    problems.extend(fragment_problems)

    # An entry written twice is one that came back: a rebase that kept both
    # sides of CHANGELOG.md, say, or a fragment copied rather than moved.
    seen: dict[str, str] = {}
    for index, text in split_entries(lines):
        seen.setdefault(_normal(text), f"{CHANGELOG}, line {index + 1}")
    for fragment in fragments:
        rel = _relative(fragment.path, root)
        for index, text in split_entries(fragment.text.split("\n")):
            key = _normal(text)
            if key in seen:
                problems.append(
                    Problem(rel, index + 1, f"this entry is already in {seen[key]}")
                )
            else:
                seen[key] = rel

    if release:
        for fragment in fragments:
            problems.append(
                Problem(
                    _relative(fragment.path, root),
                    None,
                    "not compiled. A pull request into master releases what it "
                    "carries, so its entries belong in the release's section: "
                    "python3 tooling/changelog.py compile X.Y.Z (developer guide, "
                    '"Cutting a citable release")',
                )
            )
    return problems


def report(problems: list[Problem]) -> None:
    """Print problems, as annotations on the pull request when run in Actions."""
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for problem in problems:
        if in_actions:
            where = f"file={problem.path}"
            if problem.line:
                where += f",line={problem.line}"
            message = problem.message.replace("%", "%25")
            print(f"::error {where}::{message}")
        else:
            where = problem.path + (f":{problem.line}" if problem.line else "")
            print(f"{where}: {problem.message}", file=sys.stderr)


# ============= compile =============


def _added_order(root: Path) -> dict[str, int]:
    """For each path under changelog.d/, the commit that last added it, oldest first.

    On develop that is the order the pull requests merged in - the order
    their entries used to be appended in. Empty outside a git checkout.
    """
    try:
        log = subprocess.run(
            [
                "git",
                "log",
                "--reverse",
                # Parents before children whatever the timestamps say: two
                # commits rebase-merged in the same second keep their order.
                "--topo-order",
                "--no-renames",
                "--diff-filter=A",
                "--format=%x00",
                "--name-only",
                "--",
                FRAGMENTS,
            ],
            cwd=root,
            capture_output=True,
            check=True,
            encoding="utf-8",
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return {}
    order: dict[str, int] = {}
    for index, commit in enumerate(log.split("\0")):
        for path in commit.split():
            order[path] = index
    return order


def gather(root: Path, fragments: list[Fragment]) -> dict[str, list[str]]:
    """Each section's entries, oldest fragment first; sections in release order.

    A fragment no commit has added yet comes last; ties go by file name.
    """
    order = _added_order(root)
    ordered = sorted(
        fragments,
        key=lambda f: (order.get(_relative(f.path, root), sys.maxsize), f.path.name),
    )
    return {
        section: [f.text for f in ordered if f.section == section]
        for section in SECTIONS
        if any(f.section == section for f in ordered)
    }


def _subsection(title: str, blocks: list[str]) -> list[str]:
    return [f"### {title}", "", *"\n\n".join(blocks).split("\n"), ""]


def _merge_into(existing: list[str], entries: dict[str, list[str]]) -> list[str]:
    """`existing`, the body of a released section, with `entries` added to it.

    New entries follow those of their subsection; a subsection it lacks goes
    in before the first one that comes later in SECTIONS. What is there
    already is kept as written.
    """
    preamble: list[str] = []
    parts: list[tuple[str, list[str]]] = []
    for line in existing:
        if line.startswith("### "):
            parts.append((line[4:].strip(), []))
        elif parts:
            parts[-1][1].append(line)
        else:
            preamble.append(line)
    blocks = {title: ["\n".join(body).strip("\n")] for title, body in parts}
    titles = [title for title, _ in parts]
    rank = {title: index for index, title in enumerate(SECTIONS.values())}
    for section, new in entries.items():
        title = SECTIONS[section]
        if title in blocks:
            blocks[title].extend(new)
            continue
        blocks[title] = list(new)
        later = [i for i, t in enumerate(titles) if rank.get(t, -1) > rank[title]]
        titles.insert(later[0] if later else len(titles), title)
    intro = "\n".join(preamble).strip("\n")
    lines = [*intro.split("\n"), ""] if intro else []
    for title in titles:
        lines.extend(_subsection(title, [b for b in blocks[title] if b]))
    return lines


def _count(entries: dict[str, list[str]]) -> str:
    counts = [
        (len(split_entries("\n".join(blocks).split("\n"))), SECTIONS[section])
        for section, blocks in entries.items()
    ]
    detail = ", ".join(f"{n} {title}" for n, title in counts)
    return f"{_plural(sum(n for n, _ in counts), 'entry', 'entries')} ({detail})"


def _set_citation(root: Path, version: str, date: datetime.date) -> None:
    path = root / CITATION
    text = path.read_text(encoding="utf-8")
    for key, value in (
        ("version", version),
        ("date-released", f'"{date.isoformat()}"'),
    ):
        text, found = re.subn(rf"(?m)^{key}: .*$", f"{key}: {value}", text, count=1)
        if not found:
            raise ChangelogError(f"{CITATION} has no top-level `{key}:` to set")
    _write(path, text)


def _parse_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def compile_release(root: Path, version: str, date: datetime.date | None = None) -> str:
    """Write the fragments into CHANGELOG.md as `version`; say what was done.

    `date` defaults to today for a new section, and to the section's own date
    when `version` is the newest section already.
    """
    if not VERSION.fullmatch(version):
        raise ChangelogError(
            f"{version!r} is not X.Y.Z. A pre-release compiles nothing: its "
            "entries stay in changelog.d/ until the release itself is cut."
        )
    problems = check(root)
    if problems:
        report(problems)
        raise ChangelogError("fix the problems above first")
    fragments, _ = read_fragments(root)  # the check has vouched for them
    if not fragments:
        raise ChangelogError(f"{FRAGMENTS}/ holds no fragments to compile")
    entries = gather(root, fragments)

    lines = _read_changelog(root)
    start, end = _unreleased_span(lines)
    newest = VERSION_HEADING.match(lines[end]) if end < len(lines) else None
    if newest and newest["version"] == version:
        # The release's section exists already: something merged after it was
        # closed, and ships in it.
        after = next(
            (i for i in range(end + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        if date is None and newest["date"]:
            date = datetime.datetime.strptime(newest["date"], "%Y.%m.%d").date()
        date = date or datetime.date.today()
        body = _merge_into(lines[end + 1 : after], entries)
        verb = "gains"
    else:
        if newest and _parse_version(newest["version"]) > _parse_version(version):
            raise ChangelogError(
                f"{version} is older than {newest['version']}, the newest section"
            )
        if any(
            (m := VERSION_HEADING.match(line)) and m["version"] == version
            for line in lines
        ):
            raise ChangelogError(f"{CHANGELOG} has a [{version}] section already")
        after = end
        date = date or datetime.date.today()
        body = []
        for section, blocks in entries.items():
            body.extend(_subsection(SECTIONS[section], blocks))
        verb = "takes"
    heading = f"## [{version}] - {date.strftime('%Y.%m.%d')}"

    out = [*lines[: start + 1], "", UNRELEASED_NOTE, "", heading, "", *body]
    while not out[-1].strip():
        out.pop()
    out = [*out, "", *lines[after:]]
    _write(root / CHANGELOG, "\n".join(out))
    for fragment in fragments:
        fragment.path.unlink()
    _set_citation(root, version, date)
    return (
        f"{CHANGELOG}: {heading[3:]} {verb} {_count(entries)} from "
        f"{_plural(len(fragments), 'fragment')}, now deleted.\n"
        f"{CITATION}: version {version}, date-released {date.isoformat()}."
    )


def preview(root: Path = ROOT) -> str:
    """The [Unreleased] section the fragments would make, as Markdown."""
    fragments = _fragments_or_fail(root)
    out = [UNRELEASED, ""]
    for section, blocks in gather(root, fragments).items():
        out.extend(_subsection(SECTIONS[section], blocks))
    return "\n".join(out).rstrip() + "\n"


# ============= extract =============


def branch_name(root: Path) -> str:
    """The current branch as a fragment name: `mas-12/x` reads `mas-12-x`."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            check=True,
            encoding="utf-8",
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        branch = ""
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", branch).strip("-_")
    if not name or branch == "HEAD":
        raise ChangelogError("not on a branch: name the fragments with --name")
    return name


def extract(root: Path, name: str) -> str:
    """Move the entries under [Unreleased] into fragments called `name`."""
    if not NAME.fullmatch(name):
        raise ChangelogError(
            f"{name!r} cannot name a fragment: use letters, digits, - and _"
        )
    lines = _read_changelog(root)
    start, end = _unreleased_span(lines)

    # Which section each line of [Unreleased] is under, and which lines are
    # accounted for: anything else would be lost when the section is emptied.
    section: str | None = None
    under: dict[int, str | None] = {}
    accounted = {i for i in range(start + 1, end) if not lines[i].strip()}
    for index in range(start + 1, end):
        line = lines[index]
        if line.startswith("### "):
            section = _section_of(line[4:].strip())
            if section is None:
                raise ChangelogError(
                    f"{CHANGELOG}, line {index + 1}: fragments have no `{line}` section; "
                    f"use one of {', '.join(SECTIONS.values())}, then run extract again"
                )
            accounted.add(index)
        under[index] = section
    entries = split_entries(lines[start + 1 : end], first=start + 1)
    for index, text in entries:
        accounted.update(range(index, index + text.count("\n") + 1))
        if under[index] is None:
            raise ChangelogError(
                f"{CHANGELOG}, line {index + 1}: an entry under no `###` heading, so its "
                "section is unknown. Put it under one, then run extract again."
            )
    note = "\n".join(lines[start + 1 : end]).find(UNRELEASED_NOTE)
    if note >= 0:
        first = start + 1 + "\n".join(lines[start + 1 : end])[:note].count("\n")
        accounted.update(range(first, first + UNRELEASED_NOTE.count("\n") + 1))
    stray = sorted(set(range(start + 1, end)) - accounted)
    if stray:
        raise ChangelogError(
            f"{CHANGELOG}, line {stray[0] + 1}: text under [Unreleased] that is not an "
            "entry. Move or delete it by hand, then run extract again."
        )
    if not entries:
        raise ChangelogError("[Unreleased] holds no entries to extract")

    known = {_normal(text) for _, text in split_entries(lines[:start] + lines[end:])}
    for fragment in _fragments_or_fail(root):
        known.update(
            _normal(text) for _, text in split_entries(fragment.text.split("\n"))
        )

    moved: dict[str, list[str]] = {}
    skipped = 0
    for index, text in entries:
        if _normal(text) in known:
            skipped += 1
            continue
        known.add(_normal(text))
        moved.setdefault(under[index], []).append(text)

    directory = root / FRAGMENTS
    directory.mkdir(exist_ok=True)
    summary = [f"{CHANGELOG}: [Unreleased] emptied."]
    for key, texts in moved.items():
        path = directory / f"{name}.{key}.md"
        blocks = [path.read_text(encoding="utf-8").strip("\n")] if path.exists() else []
        _write(path, "\n\n".join(blocks + texts) + "\n")
        summary.append(
            f"{_relative(path, root)}: {_plural(len(texts), 'entry', 'entries')}"
        )
    if skipped:
        summary.append(
            f"Left out {_plural(skipped, 'entry', 'entries')} already in {CHANGELOG} "
            "or another fragment."
        )

    _write(
        root / CHANGELOG,
        "\n".join([*lines[: start + 1], "", UNRELEASED_NOTE, "", *lines[end:]]),
    )
    return "\n".join(summary)


# ============= main =============


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(
        description="Changelog fragments under changelog.d/."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="what CI checks")
    check_parser.add_argument(
        "--release", action="store_true", help="also require that no fragment is left"
    )
    commands.add_parser(
        "preview", help="print the [Unreleased] section the fragments make"
    )
    compile_parser = commands.add_parser(
        "compile", help="write the fragments into a release"
    )
    compile_parser.add_argument("version", help="the release, X.Y.Z")
    compile_parser.add_argument(
        "--date",
        type=datetime.date.fromisoformat,
        help="the release date, YYYY-MM-DD (default: today, or the section's own date "
        "when it exists already)",
    )
    extract_parser = commands.add_parser(
        "extract", help="move entries under [Unreleased] into fragments"
    )
    extract_parser.add_argument(
        "--name", help="the fragments' name (default: the branch)"
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            problems = check(root, release=args.release)
            report(problems)
            if problems:
                print(
                    f"changelog: {_plural(len(problems), 'problem')}", file=sys.stderr
                )
                return 1
            print("changelog: ok")
        elif args.command == "preview":
            # Bytes, so Windows writes neither CRLF nor its console encoding
            sys.stdout.buffer.write(preview(root).encode("utf-8"))
        elif args.command == "compile":
            print(compile_release(root, args.version, args.date))
        elif args.command == "extract":
            print(extract(root, args.name or branch_name(root)))
    except ChangelogError as error:
        print(f"changelog: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
