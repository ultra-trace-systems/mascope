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

# A licence string has to look like an SPDX expression before we read it as one.
_EXPRESSION = re.compile(r"^[A-Za-z0-9.+()\s-]+$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9.+-]+$")
_SPLIT_TERMS = re.compile(r"\s+(?:AND|OR)\s+")
_SPLIT_WITH = re.compile(r"\s+WITH\s+")

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


def parse_expression(text: str) -> list[tuple[str, str | None]] | None:
    """Split an SPDX expression into ``(licence, exception)`` pairs.

    Returns ``None`` when ``text`` is not a well-formed expression of SPDX
    identifiers. That is how free text - "SEE LICENSE IN LICENSE.md",
    "UNLICENSED", "Custom: https://..." - gets caught: unparseable is treated
    as suspicious, never as unknown-and-therefore-fine.
    """
    if not _EXPRESSION.match(text):
        return None
    flat = text.replace("(", " ").replace(")", " ")
    terms = [term.strip() for term in _SPLIT_TERMS.split(flat) if term.strip()]
    if not terms:
        return None
    parsed: list[tuple[str, str | None]] = []
    for term in terms:
        parts = _SPLIT_WITH.split(term)
        if len(parts) > 2:
            return None
        name = parts[0]
        exception = parts[1] if len(parts) == 2 else None
        if not _IDENTIFIER.match(name):
            return None
        if exception is not None and not _IDENTIFIER.match(exception):
            return None
        parsed.append((name, exception))
    return parsed


def judge(ident: str, declared: str) -> list[tuple[str, str, str]]:
    """Return findings for one package's declared licence string."""
    terms = parse_expression(declared)
    if terms is None:
        return [(ident, declared, NOT_SPDX)]
    disallowed = sorted({name for name, _ in terms if name not in ALLOWED})
    if disallowed:
        return [(ident, declared, "not on the allowlist: " + ", ".join(disallowed))]
    unread = sorted({exc for _, exc in terms if exc and exc not in ALLOWED_EXCEPTIONS})
    if unread:
        return [(ident, declared, "unreviewed exception: " + ", ".join(unread))]
    return []


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
