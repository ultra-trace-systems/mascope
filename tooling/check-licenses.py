#!/usr/bin/env python3
#
# Fail the build if any package in the frontend lockfile declares a licence that
# is not on the allowlist below - or declares nothing at all.
#
# Why this exists: primeicons 8.0.0 relicensed from MIT to the commercial
# PrimeUI licence, and it arrived as a routine ten-line Dependabot diff whose
# only interesting hunk was `-"license": "MIT"` / `+"license": "SEE LICENSE IN
# LICENSE.md"`. It merged. The evidence was sitting in the pull request and
# nobody had reason to look at it. Mascope ships Apache-2.0 from a public repo,
# so a dependency that quietly turns non-permissive is a licensing problem for
# every image we publish - this reads the field a reviewer would otherwise have
# to spot by eye.
#
# It checks the whole lockfile rather than the pull request's diff. A diff check
# answers "did this PR add a violation"; this answers "is the tree clean", which
# is the property that stays true and has no base ref to get wrong.
#
# No network, no node_modules, no dependencies - the lockfile already records a
# licence per package. CI runs it as `python3 tooling/check-licenses.py`; the
# portable spelling, which also works on Windows where there is no `python3`,
# is:
#   uv run --no-project python tooling/check-licenses.py
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


LOCKFILES = ("server/frontend/package-lock.json",)
PYTHON_LOCKFILE = "uv.lock"

# Permissive licences cleared for anything that declares them. Every entry is
# one somebody looked at; adding one is a deliberate act, not a convenience.
ALLOWED = {
    "MIT",
    "ISC",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BlueOak-1.0.0",
    "OFL-1.1",
    # Permissive variants and public-domain dedications, all reached through
    # the Python tree: MIT-CMU is pillow's MIT variant, 0BSD/Zlib/CC0-1.0 are
    # bundled inside numpy, Unlicense is email-validator, and PSF-2.0 is the
    # licence CPython itself ships under (typing-extensions, matplotlib,
    # defusedxml, greenlet). None imposes a condition on combined work.
    "0BSD",
    "CC0-1.0",
    "MIT-CMU",
    "PSF-2.0",
    "Unlicense",
    "Zlib",
    # Not a software licence: CC-BY-4.0 covers colorcet's colormap *data*, and
    # its one condition is attribution when that material is redistributed. It
    # imposes nothing on the code that reads it, but it is not a no-op either,
    # which is why it is called out here rather than filed with the rest.
    "CC-BY-4.0",
    # File-level (weak) copyleft, reached from both trees - lightningcss via
    # vite, and certifi, pathspec, bidict, fqdn and tqdm on the Python side,
    # which unlike lightningcss are installed into the backend image. All are
    # consumed unmodified as published packages, and MPL's obligation is to
    # make the source of *its own* files available, which an unmodified public
    # package already satisfies. Its terms are per-file and do not reach the
    # Apache-2.0 code they sit alongside. Vendoring or patching one would
    # change that, and would need a fresh look.
    "MPL-2.0",
}

# Licences cleared only for the packages named here, because the argument for
# them is about those packages rather than about the licence. Values are
# package-name prefixes. This is REVIEWED's instinct applied to a licence
# rather than a version: a grant reasoned out for one dependency should not be
# inherited in silence by the next thing that happens to share its licence.
#
# Empty at the moment. MPL-2.0 lived here while the only MPL dependency was
# lightningcss and the argument for it rested on node_modules never shipping.
# The Python tree then turned up five more that do ship, and the general
# argument - unmodified, per-file obligations - covers all of them, so it moved
# to ALLOWED. The mechanism stays for the next licence whose justification
# really is about one package rather than about the licence.
SCOPED: dict[str, tuple[str, ...]] = {}

# SPDX exceptions - the right-hand side of `X WITH Y`. An exception only ever
# widens what its licence permits, but it is still text somebody has to read,
# so they go in one at a time rather than being waved through as a class.
ALLOWED_EXCEPTIONS = {
    # llvmlite: "BSD-2-Clause AND Apache-2.0 WITH LLVM-exception". The LLVM
    # exception lifts Apache-2.0's patent and attribution conditions for code
    # compiled by or linked into the toolchain - strictly more permissive than
    # the Apache-2.0 already on the allowlist.
    "LLVM-exception",
}

# SPDX identifiers are case-insensitive, so match on a folded key while keeping
# the declared spelling for the message. Without this, "apache-2.0" reads as
# "not on the allowlist" - naming a licence that is in fact approved, and
# inviting a second ALLOWED entry for the same thing.
_ALLOWED_FOLDED = {identifier.casefold() for identifier in ALLOWED}
_SCOPED_FOLDED = {
    identifier.casefold(): prefixes for identifier, prefixes in SCOPED.items()
}
_EXCEPTIONS_FOLDED = {exception.casefold() for exception in ALLOWED_EXCEPTIONS}

# Packages whose registry metadata carries no licence field, verified by hand.
# Pinned to an exact version so a bump comes back for a fresh look instead of
# inheriting the exception forever. One map per ecosystem: a name@version is
# only meaningful within its own registry, and a shared map would report every
# npm entry as stale whenever the Python side ran, and vice versa.
NPM_REVIEWED = {
    "combine-errors@3.0.3": 'MIT - "## License\\n\\nMIT" in the published Readme.md',
    "xmlhttprequest-ssl@2.1.2": (
        "MIT - verbatim MIT text in its LICENSE file; declared through the "
        "deprecated `licenses` array, which npm does not copy into the lockfile"
    ),
}

# The same, for Python distributions, keyed by the PEP 503 normalised name.
# Slightly wider than the npm map: a Python package can also be unreadable
# because it is locked for another platform and so never installed here.
PYTHON_REVIEWED = {
    # LGPL, and the only copyleft licence in either tree that ships. psycopg2
    # is imported as a library and neither modified nor statically linked,
    # which is what the LGPL's conditions turn on, and LGPL-3.0 and Apache-2.0
    # are compatible. Pinned so a major bump comes back for a fresh look.
    "psycopg2-binary@2.9.12": (
        "LGPL with exceptions - verbatim in the LICENSE file shipped in the "
        "wheel; used as an unmodified imported library"
    ),
    # Declare nothing in their published metadata; each verified by hand.
    "clr-loader@0.3.1": "MIT - verbatim MIT text in the LICENSE file in its wheel",
    "matplotlib-inline@0.2.1": (
        "BSD-3-Clause - verbatim BSD 3-Clause text in the LICENSE file in its wheel"
    ),
    "tuspyserver@4.2.0": (
        "MIT - the published package declares nothing and names no project URL, "
        "but the upstream repository (github.com/edihasaj/tuspyserver, author "
        "matches) carries MIT License text. No tag exists for this version, so "
        "the evidence is the LICENSE on the default branch"
    ),
    # Locked for platforms this check does not run on, so their metadata is
    # never installed here to be read. Verified against their PyPI metadata.
    "appnope@0.1.4": "BSD-2-Clause - License-Expression in the project's PyPI metadata",
    "pexpect@4.9.0": "ISC - `License: ISC license` and the ISC classifier on PyPI",
    "ptyprocess@0.7.0": "ISC - ISC classifier on PyPI",
}

# A licence string has to parse as an SPDX expression before we read it as one.
# The grammar, from the SPDX specification, with AND binding tighter than OR:
#
#   expression := term (OR term)*
#   term       := factor (AND factor)*
#   factor     := identifier [WITH exception] | "(" expression ")"
#
# The two operators mean opposite things and the difference decides verdicts:
# `A AND B` imposes both licences, so both have to be allowed, while `A OR B`
# lets the licensee pick, so one allowed option is enough. Reading `(MIT OR
# CC0-1.0)` as a conjunction fails a dependency that is perfectly usable under
# MIT - and the only remedy this file offers would be to allowlist CC0-1.0
# outright, which then silently passes a package licensed under CC0-1.0 alone.
_TOKEN = re.compile(r"[A-Za-z0-9.+-]+|[()]")
_OPERATORS = frozenset({"AND", "OR", "WITH"})
_RESERVED = _OPERATORS | frozenset({"(", ")"})

NOT_SPDX = "not an SPDX expression - read the actual licence text"

HOWTO = """
Each finding above is one of these:

  * a permissive licence nobody has approved yet - add the SPDX identifier to
    ALLOWED in tooling/check-licenses.py, in its own commit, with a comment
    saying who decided and on what basis;

  * a licence cleared only for named packages ("cleared only for other
    packages") - the argument recorded in SCOPED was made about a different
    dependency, so it does not carry over. Read this one, and either widen
    that entry's prefixes with the reasoning, or treat it as below;

  * a package with no licence in its registry metadata - verify it by hand
    (its LICENSE file, or its README) and add name@version to REVIEWED with
    the evidence;

  * a dependency that is genuinely not usable here - pin it back to its last
    acceptable version, replace it, or drop it.

Widening the allowlist to make the build green is the one response that
defeats the point of the check.
"""


class _Malformed(Exception):
    """The tokens are not a well-formed SPDX expression."""


def _tokenize(text: str) -> list[str] | None:
    """Split ``text`` into SPDX tokens, or ``None`` if it holds anything else."""
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = _TOKEN.match(text, pos)
        if match is None:
            return None  # a character no SPDX expression can contain, e.g. ":"
        tokens.append(match.group())
        pos = match.end()
    return tokens


def _identifier(tokens: list[str], pos: int) -> str:
    """The token at ``pos``, which has to be an identifier rather than syntax."""
    if pos >= len(tokens) or tokens[pos] in _RESERVED:
        raise _Malformed
    return tokens[pos]


def _parse_factor(tokens: list[str], pos: int) -> tuple[tuple, int]:
    if pos < len(tokens) and tokens[pos] == "(":
        node, pos = _parse_expression(tokens, pos + 1)
        if pos >= len(tokens) or tokens[pos] != ")":
            raise _Malformed  # unbalanced: "(MIT" never closes
        return node, pos + 1
    name = _identifier(tokens, pos)
    pos += 1
    if pos < len(tokens) and tokens[pos] == "WITH":
        return ("licence", name, _identifier(tokens, pos + 1)), pos + 2
    return ("licence", name, None), pos


def _parse_term(tokens: list[str], pos: int) -> tuple[tuple, int]:
    node, pos = _parse_factor(tokens, pos)
    operands = [node]
    while pos < len(tokens) and tokens[pos] == "AND":
        node, pos = _parse_factor(tokens, pos + 1)
        operands.append(node)
    return (operands[0] if len(operands) == 1 else ("and", tuple(operands))), pos


def _parse_expression(tokens: list[str], pos: int) -> tuple[tuple, int]:
    node, pos = _parse_term(tokens, pos)
    options = [node]
    while pos < len(tokens) and tokens[pos] == "OR":
        node, pos = _parse_term(tokens, pos + 1)
        options.append(node)
    return (options[0] if len(options) == 1 else ("or", tuple(options))), pos


def parse_expression(text: str) -> tuple | None:
    """Parse an SPDX expression into a tree, or ``None`` if it is not one.

    Nodes are plain tuples: ``("licence", name, exception_or_None)``,
    ``("and", operands)`` and ``("or", options)``.

    Returns ``None`` when ``text`` is not a well-formed expression of SPDX
    identifiers. That is how free text - "SEE LICENSE IN LICENSE.md",
    "UNLICENSED", "Custom: https://..." - gets caught, along with malformed
    expressions such as "MIT (": unparseable is treated as suspicious, never
    as unknown-and-therefore-fine.
    """
    tokens = _tokenize(text)
    if not tokens:
        return None
    try:
        node, pos = _parse_expression(tokens, 0)
    except _Malformed:
        return None
    if pos != len(tokens):
        return None  # trailing junk, e.g. "MIT )" or "MIT MIT"
    return node


def _render(node: tuple) -> str:
    """Render a parsed expression back to SPDX text, for failure messages."""
    if node[0] == "licence":
        _, name, exception = node
        return f"{name} WITH {exception}" if exception else name
    joiner = " AND " if node[0] == "and" else " OR "
    return "(" + joiner.join(_render(operand) for operand in node[1]) + ")"


def _clearance(name: str, package: str) -> str | None:
    """Why ``name`` is not cleared for ``package``, or ``None`` if it is."""
    folded = name.casefold()
    if folded in _ALLOWED_FOLDED:
        return None
    prefixes = _SCOPED_FOLDED.get(folded)
    if prefixes is None:
        return "licence"
    return None if package.startswith(prefixes) else "scoped"


def _unmet(node: tuple, package: str) -> list[tuple[str, str]]:
    """Return what stops ``node`` being allowed, as ``(kind, detail)`` pairs.

    An empty list means allowed. AND needs every operand, so its operands'
    reasons accumulate. OR only needs one option, so it reports nothing as
    soon as any option is allowed, and names the whole choice when none is -
    which points at the failing subexpression rather than at identifiers the
    reader might otherwise take for requirements.

    ``package`` is the npm package name, which decides the SCOPED grants.
    """
    if node[0] == "licence":
        _, name, exception = node
        reasons = []
        kind = _clearance(name, package)
        if kind is not None:
            reasons.append((kind, name))
        if exception is not None and exception.casefold() not in _EXCEPTIONS_FOLDED:
            reasons.append(("exception", exception))
        return reasons
    if node[0] == "and":
        return [reason for operand in node[1] for reason in _unmet(operand, package)]
    per_option = [_unmet(option, package) for option in node[1]]
    if any(not reasons for reasons in per_option):
        return []
    return [("choice", _render(node))]


def _describe(reasons: list[tuple[str, str]]) -> str:
    """Turn ``_unmet`` output into one line, grouped by kind."""
    parts = []
    for kind, label in (
        ("licence", "not on the allowlist"),
        ("scoped", "cleared only for other packages"),
        ("exception", "unreviewed exception"),
        ("choice", "no allowed option in"),
    ):
        details = sorted({detail for found, detail in reasons if found == kind})
        if details:
            parts.append(f"{label}: {', '.join(details)}")
    return "; ".join(parts)


def judge(ident: str, declared: str) -> list[tuple[str, str, str]]:
    """Return findings for one package's declared licence string.

    ``ident`` is ``name@version`` as built by ``check``; the package name is
    recovered from it because SCOPED grants are decided by which package
    declares the licence, not just by the licence.
    """
    package = ident.rsplit("@", 1)[0]
    node = parse_expression(declared)
    if node is None:
        return [(ident, declared, NOT_SPDX)]
    reasons = _unmet(node, package)
    if not reasons:
        return []
    return [(ident, declared, _describe(reasons))]


def _note(message: str) -> None:
    """Surface a housekeeping note somewhere it will actually be read.

    These fire on an otherwise green run, and nobody opens the log of a job
    that passed - so under Actions the note goes out as a workflow annotation,
    which shows on the run summary and against the file in the diff. Stale
    entries would otherwise accumulate unseen, which is the exact rot the
    version pins exist to prevent.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning file=tooling/check-licenses.py::{message}")
    else:
        print(f"note: {message}")


def check(lock_path: Path) -> list[tuple[str, str, str]]:
    """Return ``(package, declared licence, reason)`` for every failing package."""
    data = json.loads(lock_path.read_text(encoding="utf-8"))

    # Guard against passing on a file we cannot actually read. lockfileVersion 1
    # carries no per-package licence data, and an empty `packages` map would
    # otherwise sail through as "nothing wrong here".
    lock_version = data.get("lockfileVersion", 0)
    if lock_version < 2:
        sys.exit(f"{lock_path}: lockfileVersion {lock_version} carries no licence data")
    packages = data.get("packages") or {}
    if len(packages) < 2:
        sys.exit(f"{lock_path}: no packages found - refusing to pass on nothing")

    problems: set[tuple[str, str, str]] = set()
    seen_reviewed: set[str] = set()
    seen: set[str] = set()

    for path, package in packages.items():
        if path == "" or package.get("link"):
            continue  # the root project itself, and workspace symlinks
        name = path.rsplit("node_modules/", 1)[-1]
        ident = f"{name}@{package.get('version', '?')}"
        # npm hoists one package to several paths; `problems` is a set so a
        # duplicate reports once instead of padding the count with copies.
        seen.add(ident)

        if ident in NPM_REVIEWED:
            seen_reviewed.add(ident)
            continue

        declared = package.get("license")
        if declared is None:
            problems.add((ident, "<none>", "declares no licence"))
        elif not isinstance(declared, str):
            # npm normalises `license` to a string, and does not copy the
            # deprecated `licenses` array into a v2+ lockfile at all. Guarded
            # anyway: a shape we cannot read must not pass as "nothing wrong".
            problems.add((ident, json.dumps(declared), "non-string licence field"))
        else:
            problems.update(judge(ident, declared))

    for stale in sorted(set(NPM_REVIEWED) - seen_reviewed):
        _note(f"{stale} is no longer installed - drop it from REVIEWED")

    if not problems:
        print(f"OK  {lock_path}: {len(seen)} packages, every licence allowed")
    return sorted(problems)


# The Python side records no licence in uv.lock - unlike npm, uv stores no
# metadata beyond name, version and hashes. The data does exist, but only in the
# installed distributions, so this half reads the environment.
#
# Reading an environment is exactly what disqualified `npm query` for the
# JavaScript side: in a checkout with nothing installed it finds no packages and
# reports success, a false green indistinguishable from a real one. The lockfile
# is therefore still the authority on *what should be there*: every third-party
# package it names has to be present, or the check refuses to report at all.
#
# Metadata comes in three shapes, best first:
#   License-Expression   PEP 639, already SPDX - parsed like the npm side
#   License :: ...       a trove classifier, coarser than SPDX (see CLASSIFIERS)
#   License:             free text, parsed only when it happens to be SPDX
CLASSIFIERS = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    # Coarser than SPDX, deliberately. The classifier does not say which BSD or
    # which Apache; these map to the form the ecosystem overwhelmingly means.
    # The reported reason says the verdict came from a classifier, so a package
    # where the distinction would matter can be pinned in REVIEWED instead.
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
}

# Free-text `License:` values that are an SPDX identifier in all but spelling.
# Exact strings only, and only where there is nothing to interpret; anything
# else stays unparseable and comes back as a finding.
FREE_TEXT = {
    "Apache 2.0": "Apache-2.0",
    "Apache Software License": "Apache-2.0",
    "BSD": "BSD-3-Clause",
}


def _normalize(name: str) -> str:
    """PEP 503 name normalisation, so uv.lock and the environment agree."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked(lock_path: Path) -> tuple[dict[str, str], set[str]]:
    """Return ``({name: version}, first_party)`` from ``uv.lock``.

    First-party packages carry a non-registry source (editable or virtual).
    They are this repository's own code, already covered by its LICENSE, and
    several declare nothing at all - checking them would only ever produce
    findings about ourselves.
    """
    import tomllib  # stdlib on 3.11+; imported here so the npm half needs no

    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    third_party: dict[str, str] = {}
    first_party: set[str] = set()
    for package in data.get("package", ()):
        name = _normalize(package["name"])
        if "registry" in (package.get("source") or {}):
            third_party[name] = package.get("version", "?")
        else:
            first_party.add(name)
    return third_party, first_party


def _declared(metadata) -> tuple[str | None, str]:
    """Return ``(licence string, where it came from)`` for one distribution."""
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression, "License-Expression"

    classifiers = [
        c for c in metadata.get_all("Classifier") or () if c.startswith("License ::")
    ]
    unmapped = sorted(c for c in classifiers if c not in CLASSIFIERS)
    if unmapped:
        # One unreadable classifier is reported even when others alongside it
        # map cleanly. Letting the mapped ones answer for the package would
        # pass something declaring both MIT and AGPL-3.0 as plain MIT.
        return unmapped[0], "unmapped classifier"
    if classifiers:
        # Several classifiers mean every one of them applies, as AND does.
        return " AND ".join(sorted({CLASSIFIERS[c] for c in classifiers})), "classifier"

    free = (metadata.get("License") or "").strip()
    if free:
        # Often literally "MIT" or "Apache-2.0"; judged as SPDX when it parses,
        # and reported as unreadable when it does not. FREE_TEXT covers the
        # near-misses that leave nothing to interpret.
        first = free.splitlines()[0].strip()
        return FREE_TEXT.get(first, first), "License free text"
    return None, "nothing"


def check_python(lock_path: Path) -> list[tuple[str, str, str]]:
    """Return ``(package, declared licence, reason)`` for every failing package."""
    import importlib.metadata

    locked, first_party = _locked(lock_path)
    if not locked:
        sys.exit(f"{lock_path}: no third-party packages found - refusing to pass")

    installed = {}
    for dist in importlib.metadata.distributions():
        metadata = dist.metadata
        name = _normalize((metadata.get("Name") or "").strip())
        if name and name not in installed:
            installed[name] = metadata

    # The lockfile decides what has to be examined. A package it names that is
    # not installed is not a licence we may assume is fine - it is one nobody
    # read, so it becomes a finding like any other and REVIEWED is the way to
    # settle it. A handful are genuinely platform-conditional (macOS-only, say)
    # and will never install here; those are exactly what REVIEWED is for.
    missing = set(locked) - set(installed) - first_party
    if len(missing) > len(locked) // 2:
        # Not an out-of-date entry or two: this ran somewhere nothing was
        # installed. Say that plainly instead of listing hundreds of packages.
        sys.exit(
            f"{lock_path}: {len(missing)} of {len(locked)} locked packages are "
            f"not installed, so almost nothing could be read - run "
            f"`uv sync --all-groups` first"
        )

    problems: set[tuple[str, str, str]] = set()
    seen_reviewed: set[str] = set()

    for name, version in sorted(locked.items()):
        ident = f"{name}@{version}"
        if ident in PYTHON_REVIEWED:
            seen_reviewed.add(ident)
            continue

        if name in missing:
            problems.add((ident, "<not installed>", "locked but not installed here"))
            continue

        declared, source = _declared(installed[name])
        if declared is None:
            problems.add((ident, "<none>", "declares no licence"))
            continue

        findings = judge(ident, declared)
        if findings and source != "License-Expression":
            # Say where a verdict came from: a classifier or a free-text field
            # is weaker evidence than a declared SPDX expression, and that
            # changes what the reader should go and check.
            findings = [(i, d, f"{r} (from the {source})") for i, d, r in findings]
        problems.update(findings)

    for stale in sorted(set(PYTHON_REVIEWED) - seen_reviewed):
        _note(f"{stale} is no longer locked - drop it from PYTHON_REVIEWED")

    if not problems:
        print(f"OK  {lock_path}: {len(locked)} packages, every licence allowed")
    return sorted(problems)


USAGE = """usage: check-licenses.py [npm|python|all]

  npm     server/frontend/package-lock.json  (no install, no network)
  python  uv.lock, read through the installed distributions
  all     both (the default)
"""


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    target = argv[0] if argv else "all"
    if target not in ("npm", "python", "all") or len(argv) > 1:
        sys.exit(USAGE)

    root = Path(__file__).resolve().parent.parent
    findings: list[tuple[Path, list[tuple[str, str, str]]]] = []

    # (lockfile, checker) in the order they are reported.
    wanted: list[tuple[str, object]] = []
    if target in ("npm", "all"):
        wanted += [(name, check) for name in LOCKFILES]
    if target in ("python", "all"):
        wanted.append((PYTHON_LOCKFILE, check_python))

    for name, checker in wanted:
        lock_path = root / name
        if not lock_path.is_file():
            sys.exit(f"{name}: not found - update LOCKFILES if the lockfile moved")
        problems = checker(lock_path)
        if problems:
            findings.append((lock_path, problems))

    if not findings:
        return 0

    width = max(len(ident) for _, probs in findings for ident, _, _ in probs)
    for lock_path, problems in findings:
        print(f"\nFAIL  {lock_path}: {len(problems)} package(s) need a decision\n")
        for ident, declared, reason in sorted(problems):
            print(f"  {ident.ljust(width)}  {declared}")
            print(f"  {' ' * width}  -> {reason}")
    print(HOWTO)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
