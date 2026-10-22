#!/usr/bin/env python3
#
# Design notes: every note under docs/dev opens with a header saying what it
# is, how far it is built and where its open work is tracked, and
# docs/dev/README.md lists them all in a table made from those headers.
#
# Why this exists: the headers and the table were kept by hand, and nothing
# failed a pull request that added a note without one, let a status go stale,
# or let the table drift from the headers, and the 2026-10-21 audit found
# every note in that state.
#
# A note is `docs/dev/<name>.md`, except the README and the developer guide.
# It opens with an optional banner line, its `# Title`, and then the header,
# one Markdown bullet per field in this order (a long value runs on in lines
# indented two spaces):
#
#   - **Status:** design | building | built | retired
#   - **As of:** YYYY-MM-DD, the day the header was last checked
#   - **Tracked in:** a Linear project or issue link, a GitHub issue link,
#     `closed` or `none`, each with any words after it
#   - **Supersedes:** / - **Superseded by:** (optional) the note(s) it names
#   - **What:** one sentence, the README table's "What it is"
#
# Commands - stdlib only, as tooling/changelog.py, whose Problem and report
# this shares:
#
#   check   Every note has a well-formed header, and the README's table lists
#           each note once with the header's Status, Tracked in and What.
#           A design or building note whose As of is older than STALE_DAYS
#           is a warning, which fails nothing. CI runs it on every pull
#           request.
#   index   Rewrite the README's table from the headers.
#
# Usage:
#   uv run python tooling/notes.py check
#   uv run python tooling/notes.py index
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path
from typing import NamedTuple

from changelog import Problem, _plural, _write, report


ROOT = Path(__file__).resolve().parent.parent
NOTES = "docs/dev"
README = "README.md"
NOT_NOTES = {README, "developer_guide.md"}

STATUSES = ("design", "building", "built", "retired")
# A note with open work: it must name where that work is tracked, and its
# header goes stale.
OPEN = {"design", "building"}
STALE_DAYS = 90
# Days As of may run ahead of the checking machine's date: CI's runners are on
# UTC, and an author east of it can be on tomorrow already.
AHEAD_DAYS = 1

# The header's fields in the order a note writes them, and those it must have.
FIELDS = ("Status", "As of", "Tracked in", "Supersedes", "Superseded by", "What")
REQUIRED = ("Status", "As of", "Tracked in", "What")
SUPERSEDING = ("Supersedes", "Superseded by")
# The fields the README's table repeats, after the note's name.
TABLE_FIELDS = ("Status", "Tracked in", "What")

FIELD = re.compile(r"^- \*\*(?P<name>[^*]+?):\*\*(?P<value>.*)$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
LINEAR = re.compile(r"https://linear\.app/[\w-]+/(?:project|issue)/[\w-]+")
GITHUB_ISSUE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/issues/\d+")
UNTRACKED = re.compile(r"^(?P<word>closed|none)\b")
# A file a Supersedes line names: in backticks or as a link's target.
NAMED_FILE = re.compile(r"`(?P<code>[^`\s]+\.md)`|\]\((?P<link>[^)\s]+\.md)\)")

TABLE_HEAD = "| Note | Status | Tracked in | What it is |"
TABLE_RULE = "|---|---|---|---|"
RETIRED_HEADING = "## Retired"
NOTE_CELL = re.compile(r"^\[`?(?P<text>[^`\]]+)`?\]\((?P<target>[^)]+)\)$")
RETIRED_CELL = re.compile(r"^`(?P<name>[^`]+\.md)`$")
CELL_SPLIT = re.compile(r"(?<!\\)\|")

RUN = "uv run python tooling/notes.py"
HOW_TO_HEAD = (
    "after its title, a note opens with the header: - **Status:**, - **As of:**, "
    "- **Tracked in:**, optionally - **Supersedes:** or - **Superseded by:**, and "
    "- **What:**, in that order (docs/dev/README.md)"
)


class Field(NamedTuple):
    line: int  # 1-based, of the bullet
    value: str  # continuation lines folded into one


class Note(NamedTuple):
    name: str  # the file name, `x.md`
    rel: str  # its path from the root
    fields: dict[str, Field]
    end: int  # the 1-based line after the header, for what is missing from it


class Row(NamedTuple):
    line: int
    name: str
    cells: dict[str, str]  # "Status", "Tracked in", "What"


class Table(NamedTuple):
    start: int  # 0-based index of TABLE_HEAD
    end: int  # 0-based index of the first line after the last row
    rows: list[Row]


class NotesError(Exception):
    """A command that cannot go ahead; the message says why."""


# ============= Reading =============


def _fold(text: str) -> str:
    return " ".join(text.split())


def note_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for path in (root / NOTES).glob("*.md")
        if path.is_file() and path.name not in NOT_NOTES
    )


def read_note(path: Path, root: Path) -> tuple[Note | None, list[Problem]]:
    """A note's header fields, or None and why when it has no header at all."""
    rel = path.relative_to(root).as_posix()
    lines = path.read_text(encoding="utf-8").split("\n")

    def skip_blank(index: int) -> int:
        while index < len(lines) and not lines[index].strip():
            index += 1
        return index

    index = skip_blank(0)
    if index < len(lines) and not lines[index].startswith("# "):
        index = skip_blank(index + 1)  # the banner line
    if index >= len(lines) or not lines[index].startswith("# "):
        return None, [
            Problem(rel, 1, "no `# Title` line, after at most one banner line")
        ]
    index = skip_blank(index + 1)

    fields: dict[str, Field] = {}
    order: list[tuple[str, int]] = []
    problems = []
    while index < len(lines) and (match := FIELD.match(lines[index])):
        line = index + 1
        name = match["name"].strip()
        parts = [match["value"]]
        index += 1
        while (
            index < len(lines)
            and lines[index].startswith("  ")
            and lines[index].strip()
        ):
            parts.append(lines[index])
            index += 1
        if name not in FIELDS:
            problems.append(Problem(rel, line, f"`{name}` is not a header field"))
        elif name in fields:
            problems.append(Problem(rel, line, f"`{name}` is in the header twice"))
        else:
            fields[name] = Field(line, _fold(" ".join(parts)))
            order.append((name, line))
    end = index + 1

    if not fields:
        return None, [Problem(rel, end, f"no header: {HOW_TO_HEAD}")]
    for (before, _), (after, line) in zip(order, order[1:]):
        if FIELDS.index(after) < FIELDS.index(before):
            problems.append(
                Problem(rel, line, f"`{after}` comes after `{before}`: {HOW_TO_HEAD}")
            )
    for name in REQUIRED:
        if name not in fields:
            problems.append(
                Problem(rel, end, f"the header has no `{name}`: {HOW_TO_HEAD}")
            )
    return Note(path.name, rel, fields, end), problems


def _cells(line: str) -> list[str]:
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [cell.strip().replace("\\|", "|") for cell in CELL_SPLIT.split(inner)]


def read_table(lines: list[str]) -> Table | None:
    """The README's table of notes, or None when it has none."""
    try:
        start = lines.index(TABLE_HEAD)
    except ValueError:
        return None
    rows = []
    index = start + 1
    if index < len(lines) and lines[index].startswith("|-"):
        index += 1
    while index < len(lines) and lines[index].startswith("|"):
        cells = _cells(lines[index])
        match = NOTE_CELL.match(cells[0])
        name = match["target"] if match else cells[0].strip("`")
        rest = cells[1:] + [""] * (len(TABLE_FIELDS) + 1 - len(cells))
        rows.append(Row(index + 1, name, dict(zip(TABLE_FIELDS, rest))))
        index += 1
    return Table(start, index, rows)


def retired_names(lines: list[str]) -> set[str]:
    """The notes the README lists as retired: deleted, so named nowhere else."""
    names = set()
    inside = False
    for line in lines:
        if line.startswith("## "):
            inside = line.startswith(RETIRED_HEADING)
        elif inside and line.startswith("|"):
            match = RETIRED_CELL.match(_cells(line)[0])
            if match:
                names.add(match["name"])
    return names


def _read_readme(root: Path) -> list[str]:
    path = root / NOTES / README
    if not path.is_file():
        raise NotesError(f"{NOTES}/{README} is missing")
    return path.read_text(encoding="utf-8").split("\n")


# ============= check =============


def _field_problems(
    note: Note, root: Path, retired: set[str], today: datetime.date
) -> list[Problem]:
    problems = []
    fields = note.fields

    def problem(name: str, message: str, level: str = "error") -> None:
        problems.append(Problem(note.rel, fields[name].line, message, level))

    status = fields["Status"].value if "Status" in fields else None
    if status is not None and status not in STATUSES:
        problem("Status", f"Status is `{status}`, not one of {', '.join(STATUSES)}")

    if "As of" in fields:
        value = fields["As of"].value
        try:
            if not DATE.fullmatch(value):
                raise ValueError
            as_of = datetime.date.fromisoformat(value)
        except ValueError:
            problem("As of", f"As of is `{value}`, not a date written YYYY-MM-DD")
        else:
            age = (today - as_of).days
            if age < -AHEAD_DAYS:
                problem("As of", f"As of is {value}, a day still to come")
            elif status in OPEN and age > STALE_DAYS:
                problem(
                    "As of",
                    f"a {status} note last checked {age} days ago: check the header "
                    "against the tree and its tracker, and set As of to today",
                    level="warning",
                )

    if "Tracked in" in fields:
        value = fields["Tracked in"].value
        untracked = UNTRACKED.match(value)
        if untracked:
            if status in OPEN:
                problem(
                    "Tracked in",
                    f"a {status} note has open work, so it cannot be tracked in "
                    f"`{untracked['word']}`: file the work as a Linear issue or a "
                    "GitHub issue and link it",
                )
        elif not (LINEAR.search(value) or GITHUB_ISSUE.search(value)):
            problem(
                "Tracked in",
                "Tracked in links no Linear project or issue and no GitHub issue, "
                "and is not `closed` or `none`",
            )

    for name in SUPERSEDING:
        if name not in fields:
            continue
        named = [
            match["code"] or match["link"]
            for match in NAMED_FILE.finditer(fields[name].value)
        ]
        if not named:
            problem(name, f"{name} names no note: write its file name in backticks")
        for target in named:
            exists = (root / NOTES / target).is_file()
            if not exists and Path(target).name not in retired:
                problem(
                    name,
                    f"{name} names `{target}`, which is neither in {NOTES} nor in "
                    f"the README's retired notes",
                )

    if "What" in fields and not fields["What"].value:
        problem("What", "What is empty: one sentence on what the note is")
    return problems


def _table_problems(
    notes: dict[str, Note | None], table: Table | None, readme: str
) -> list[Problem]:
    fix = f"run {RUN} index"
    if table is None:
        return [Problem(readme, None, f"no table of notes headed `{TABLE_HEAD}`")]
    problems = []
    seen: set[str] = set()
    for row in table.rows:
        if row.name in seen:
            problems.append(
                Problem(readme, row.line, f"`{row.name}` is in the table twice; {fix}")
            )
            continue
        seen.add(row.name)
        if row.name not in notes:
            problems.append(
                Problem(
                    readme,
                    row.line,
                    f"the table lists `{row.name}`, which is not a note in {NOTES}; "
                    f"{fix}",
                )
            )
            continue
        note = notes[row.name]
        if note is None:
            continue
        for name, cell in row.cells.items():
            if name in note.fields and _fold(cell) != note.fields[name].value:
                problems.append(
                    Problem(
                        readme,
                        row.line,
                        f"the table's {name} for `{row.name}` is not its header's "
                        f"(line {note.fields[name].line}); {fix}",
                    )
                )
    for name in notes:
        if name not in seen:
            problems.append(
                Problem(readme, table.start + 1, f"`{name}` is not in the table; {fix}")
            )
    return problems


def read_notes(root: Path) -> tuple[dict[str, Note | None], list[Problem]]:
    notes: dict[str, Note | None] = {}
    problems = []
    for path in note_paths(root):
        note, note_problems = read_note(path, root)
        notes[path.name] = note
        problems.extend(note_problems)
    return notes, problems


def check(root: Path = ROOT, today: datetime.date | None = None) -> list[Problem]:
    """Every header problem and every way the README's table disagrees with them."""
    today = today or datetime.date.today()
    lines = _read_readme(root)
    notes, problems = read_notes(root)
    retired = retired_names(lines)
    for note in notes.values():
        if note is not None:
            problems.extend(_field_problems(note, root, retired, today))
    problems.extend(_table_problems(notes, read_table(lines), f"{NOTES}/{README}"))
    return problems


# ============= index =============


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


def render_rows(notes: dict[str, Note]) -> list[str]:
    """The table's rows, in the order of `notes` (read_notes reads them by name)."""
    rows = []
    for name, note in notes.items():
        fields = note.fields
        cells = [
            f"[`{name}`]({name})",
            *(_cell(fields[field].value) for field in TABLE_FIELDS),
        ]
        rows.append(f"| {' | '.join(cells)} |")
    return rows


def index(root: Path = ROOT) -> str:
    """Rewrite the README's table of notes from their headers."""
    lines = _read_readme(root)
    table = read_table(lines)
    if table is None:
        raise NotesError(f"{NOTES}/{README} has no table headed `{TABLE_HEAD}`")
    notes, problems = read_notes(root)
    missing = [
        problem
        for problem in problems
        if notes[Path(problem.path).name] is None
        or any(
            name not in notes[Path(problem.path).name].fields for name in TABLE_FIELDS
        )
    ]
    if missing:
        report(missing)
        count = len({problem.path for problem in missing})
        raise NotesError(
            f"{_plural(count, 'note lacks', 'notes lack')} a field the table shows; "
            "nothing written"
        )
    rows = render_rows({name: note for name, note in notes.items() if note})
    updated = [
        *lines[: table.start],
        TABLE_HEAD,
        TABLE_RULE,
        *rows,
        *lines[table.end :],
    ]
    path = root / NOTES / README
    if updated == lines:
        return (
            f"{NOTES}/{README}: the table is up to date ({_plural(len(rows), 'note')})"
        )
    _write(path, "\n".join(updated))
    return f"{NOTES}/{README}: wrote the table ({_plural(len(rows), 'note')})"


# ============= main =============


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description="Design notes under docs/dev.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="what CI checks")
    commands.add_parser("index", help="rewrite the README's table from the headers")
    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            problems = check(root)
            report(problems)
            errors = [problem for problem in problems if problem.level == "error"]
            warnings = len(problems) - len(errors)
            if errors:
                print(f"notes: {_plural(len(errors), 'problem')}", file=sys.stderr)
                return 1
            print(
                "notes: ok" + (f" ({_plural(warnings, 'warning')})" if warnings else "")
            )
        elif args.command == "index":
            print(index(root))
    except NotesError as error:
        print(f"notes: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
