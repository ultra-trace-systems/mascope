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
# licence per package. Runnable locally exactly the way CI runs it:
#   python3 tooling/check-licenses.py
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


LOCKFILES = ("server/frontend/package-lock.json",)

# Permissive licences cleared for use in Mascope. Every entry is one somebody
# looked at; adding one is a deliberate act, not a convenience.
ALLOWED = {
    "MIT",
    "ISC",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BlueOak-1.0.0",
    "OFL-1.1",
    # File-level (weak) copyleft, and the only MPL entry is lightningcss plus
    # its platform binaries, pulled in transitively by vite. We consume it
    # unmodified and never redistribute it: the frontend image is a multi-stage
    # build that copies only `dist/`, so node_modules never reaches a published
    # artefact, and a CSS transformer's output is our CSS, not its own. MPL's
    # obligations are per-file and do not reach Apache-2.0 code they are
    # combined with. Modifying it, or shipping node_modules, would each need a
    # fresh look.
    "MPL-2.0",
}

# SPDX exceptions - the right-hand side of `X WITH Y`. Empty on purpose: nothing
# uses one today, so any WITH clause that appears is something nobody has read.
ALLOWED_EXCEPTIONS: set[str] = set()

# Packages whose registry metadata carries no licence field, verified by hand.
# Pinned to an exact version so a bump comes back for a fresh look instead of
# inheriting the exception forever.
REVIEWED = {
    "combine-errors@3.0.3": 'MIT - "## License\\n\\nMIT" in the published Readme.md',
    "xmlhttprequest-ssl@2.1.2": (
        "MIT - verbatim MIT text in its LICENSE file; declared through the "
        "deprecated `licenses` array, which npm does not copy into the lockfile"
    ),
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
Each finding above is one of three things:

  * a permissive licence nobody has approved yet - add the SPDX identifier to
    ALLOWED in tooling/check-licenses.py, in its own commit, with a comment
    saying who decided and on what basis;

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


def _unmet(node: tuple) -> list[tuple[str, str]]:
    """Return what stops ``node`` being allowed, as ``(kind, detail)`` pairs.

    An empty list means allowed. AND needs every operand, so its operands'
    reasons accumulate. OR only needs one option, so it reports nothing as
    soon as any option is allowed, and names the whole choice when none is -
    which points at the failing subexpression rather than at identifiers the
    reader might otherwise take for requirements.
    """
    if node[0] == "licence":
        _, name, exception = node
        reasons = []
        if name not in ALLOWED:
            reasons.append(("licence", name))
        if exception is not None and exception not in ALLOWED_EXCEPTIONS:
            reasons.append(("exception", exception))
        return reasons
    if node[0] == "and":
        return [reason for operand in node[1] for reason in _unmet(operand)]
    per_option = [_unmet(option) for option in node[1]]
    if any(not reasons for reasons in per_option):
        return []
    return [("choice", _render(node))]


def _describe(reasons: list[tuple[str, str]]) -> str:
    """Turn ``_unmet`` output into one line, grouped by kind."""
    parts = []
    for kind, label in (
        ("licence", "not on the allowlist"),
        ("exception", "unreviewed exception"),
        ("choice", "no allowed option in"),
    ):
        details = sorted({detail for found, detail in reasons if found == kind})
        if details:
            parts.append(f"{label}: {', '.join(details)}")
    return "; ".join(parts)


def judge(ident: str, declared: str) -> list[tuple[str, str, str]]:
    """Return findings for one package's declared licence string."""
    node = parse_expression(declared)
    if node is None:
        return [(ident, declared, NOT_SPDX)]
    reasons = _unmet(node)
    if not reasons:
        return []
    return [(ident, declared, _describe(reasons))]


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

    problems: list[tuple[str, str, str]] = []
    seen_reviewed: set[str] = set()
    checked = 0

    for path, package in packages.items():
        if path == "" or package.get("link"):
            continue  # the root project itself, and workspace symlinks
        checked += 1
        name = path.rsplit("node_modules/", 1)[-1]
        ident = f"{name}@{package.get('version', '?')}"

        if ident in REVIEWED:
            seen_reviewed.add(ident)
            continue

        declared = package.get("license", package.get("licenses"))
        if declared is None:
            problems.append((ident, "<none>", "declares no licence"))
        elif not isinstance(declared, str):
            problems.append((ident, json.dumps(declared), "non-string licence field"))
        else:
            problems.extend(judge(ident, declared))

    for stale in sorted(set(REVIEWED) - seen_reviewed):
        print(f"note: {stale} is no longer installed - drop it from REVIEWED")

    if not problems:
        print(f"OK  {lock_path}: {checked} packages, every licence allowed")
    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    findings: list[tuple[Path, list[tuple[str, str, str]]]] = []

    for name in LOCKFILES:
        lock_path = root / name
        if not lock_path.is_file():
            sys.exit(f"{name}: not found (run this from anywhere in the repo)")
        problems = check(lock_path)
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
