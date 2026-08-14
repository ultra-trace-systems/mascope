#!/usr/bin/env python3
"""
Regenerate the vendored breached-password blocklists.

The password policy rejects anything shorter than ``MIN_PASSWORD_LENGTH``, so
most of a top-N frequency list is unreachable and only the long tail of it is
worth carrying. Filtering the upstream list to entries the policy could
otherwise accept turns 8.5 MB into roughly 675 KB.

Two files are written, and they are not the same size on purpose:

* the backend gets every filtered entry, because a ``frozenset`` in a server
  process does not care;
* the frontend gets the head of the list in upstream rank order plus the
  generated patterns, because the full set is ~320 KB gzipped and the browser
  copy exists only to give instant feedback. It is a strict subset, so anything
  it reports is genuinely common - it just does not catch everything, and the
  backend re-validates on submit either way.

Usage:
    uv run tooling/vendor-common-passwords.py
    uv run tooling/vendor-common-passwords.py --source /path/to/downloaded.txt
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the policy's minimum length is defined. Parsed rather than imported so
#: this script does not need the backend package installed, and so a rename is
#: a loud failure here instead of a silently mis-filtered list.
POLICY_SOURCE = (
    REPO_ROOT
    / "server/backend/src/mascope_backend/api/new/users/user_manager/service.py"
)

UPSTREAM_REPO = "https://github.com/danielmiessler/SecLists"
UPSTREAM_PATH = "Passwords/Common-Credentials/xato-net-10-million-passwords-1000000.txt"
#: Pinned so a regeneration is reproducible and a diff is reviewable.
UPSTREAM_COMMIT = "c205c36a445bff37f8e58a9ec829105cd4975c58"
UPSTREAM_URL = (
    f"https://raw.githubusercontent.com/danielmiessler/SecLists/{UPSTREAM_COMMIT}/"
    f"{UPSTREAM_PATH}"
)
UPSTREAM_LICENCE = "MIT"

BACKEND_OUT = (
    REPO_ROOT
    / "server/backend/src/mascope_backend/api/new/users/user_manager/common_passwords.txt"
)
FRONTEND_OUT = REPO_ROOT / "server/frontend/src/lib/common-passwords.txt"

#: How many upstream entries, in rank order, reach the browser. The upstream
#: list is only genuinely frequency-ranked near its head - further down it is
#: grouped alphabetically - so a larger slice would buy arbitrary entries rather
#: than more likely ones.
FRONTEND_RANK_LIMIT = 1000

#: Ceilings the drift test also asserts, so a regeneration cannot quietly make
#: the browser payload heavy.
FRONTEND_MAX_BYTES = 40_000
BACKEND_MAX_BYTES = 2_000_000


def read_min_password_length() -> int:
    """
    Read MIN_PASSWORD_LENGTH out of the backend policy module.

    :raises SystemExit: If the constant cannot be found.
    :return: The configured minimum password length.
    """
    match = re.search(
        r"^\s*MIN_PASSWORD_LENGTH\s*=\s*(\d+)\s*$",
        POLICY_SOURCE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        sys.exit(
            f"Could not find MIN_PASSWORD_LENGTH in {POLICY_SOURCE}. "
            "If it was renamed, update this script - the filter depends on it."
        )
    return int(match.group(1))


def generated_patterns(min_length: int) -> list[str]:
    """
    Passwords that are long, obvious, and too structural to rely on a leak list.

    A frequency list only contains what has actually been breached and
    published. These are the shapes a user reaches for when told their password
    must be longer, so they are worth rejecting whether or not anyone has leaked
    them yet.

    :param min_length: The policy's minimum password length.
    :return: Lowercased candidate passwords.
    """
    patterns: list[str] = []
    lengths = range(min_length, min_length + 9)

    # Single character repeated ("aaaaaaaaaaaa", "111111111111").
    for char in "abcdefghijklmnopqrstuvwxyz0123456789":
        for length in lengths:
            patterns.append(char * length)

    # Digit runs, ascending and descending, wrapping at 10 digits.
    ascending = "0123456789" * 4
    descending = "9876543210" * 4
    for length in lengths:
        patterns.append(ascending[:length])
        patterns.append(descending[:length])
        patterns.append(ascending[1 : length + 1])

    # Keyboard walks along each row and down the columns.
    rows = ["qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890"]
    walks = [
        "".join(rows[0] + rows[1] + rows[2]),
        "".join(rows[3] + rows[0]),
        "qazwsxedcrfvtgbyhnujmik",
        "1qaz2wsx3edc4rfv5tgb",
        "zaq12wsxcde34rfv",
    ]
    for walk in walks:
        for length in lengths:
            if len(walk) >= length:
                patterns.append(walk[:length])

    # Repeated words, including the product's own name - the single most likely
    # thing for a user of this application to reach for.
    for word in ("password", "mascope", "qwerty", "letmein", "welcome", "admin"):
        patterns.append(word * 2)
        patterns.append((word * 3)[: min_length + 4])
        for suffix in ("123456", "1234", "12345678", "2024", "2025", "2026", "!"):
            candidate = f"{word}{suffix}"
            if len(candidate) >= min_length:
                patterns.append(candidate)

    return [p for p in patterns if len(p) >= min_length]


def load_source(source: Path | None) -> list[str]:
    """
    Read the upstream list, downloading the pinned revision when not supplied.

    :param source: Optional path to an already-downloaded copy.
    :return: Upstream entries in their original rank order.
    """
    if source is not None:
        raw = source.read_bytes()
    else:
        print(f"Downloading {UPSTREAM_URL}", file=sys.stderr)
        with urllib.request.urlopen(UPSTREAM_URL, timeout=300) as response:
            raw = response.read()
    return raw.decode("utf-8", errors="replace").splitlines()


def write_list(path: Path, entries: list[str], header: list[str], limit: int) -> None:
    """
    Write a blocklist file, header first, entries sorted for a readable diff.

    :param path: Destination file.
    :param entries: Entries to write.
    :param header: Comment lines, written without the leading "# ".
    :param limit: Maximum size in bytes; exceeding it is an error.
    :raises SystemExit: If the rendered file exceeds ``limit``.
    """
    body = "\n".join(f"# {line}".rstrip() for line in header)
    body += "\n" + "\n".join(sorted(entries)) + "\n"
    encoded = body.encode("utf-8")
    if len(encoded) > limit:
        sys.exit(
            f"{path.name} would be {len(encoded)} bytes, over the {limit} byte "
            "ceiling. Lower FRONTEND_RANK_LIMIT or raise the ceiling deliberately."
        )
    # Newline pinned so the file is byte-identical on Windows and Linux, which
    # the drift test depends on.
    path.write_bytes(encoded)
    print(
        f"Wrote {path.relative_to(REPO_ROOT)}: {len(entries)} entries, {len(encoded)} bytes"
    )


def main() -> None:
    """Regenerate both blocklists."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to an already-downloaded copy of the upstream list.",
    )
    args = parser.parse_args()

    min_length = read_min_password_length()
    upstream = load_source(args.source)

    # Deduplicate while preserving upstream rank, which is what makes the
    # frontend slice the *likeliest* entries rather than an arbitrary cut.
    seen: set[str] = set()
    ranked: list[str] = []
    for line in upstream:
        entry = line.strip().lower()
        if len(entry) < min_length or entry in seen:
            continue
        # Both loaders treat a leading "#" as a comment, so an entry starting
        # with one could never be matched. Dropping it here keeps the file and
        # the loaders in agreement, and keeps the entry count exact. The
        # upstream list contains a single such entry.
        if entry.startswith("#"):
            continue
        seen.add(entry)
        ranked.append(entry)

    patterns = generated_patterns(min_length)
    backend_entries = sorted(set(ranked) | set(patterns))
    frontend_entries = sorted(set(ranked[:FRONTEND_RANK_LIMIT]) | set(patterns))

    provenance = [
        "Breached-password blocklist. GENERATED FILE - do not edit by hand.",
        "Regenerate with: uv run tooling/vendor-common-passwords.py",
        "",
        f"Source:  {UPSTREAM_REPO}",
        f"Path:    {UPSTREAM_PATH}",
        f"Commit:  {UPSTREAM_COMMIT}",
        f"Licence: {UPSTREAM_LICENCE} (SPDX-License-Identifier: MIT)",
        "         Copyright (c) 2025 Daniel Miessler",
        "",
        f"Filter:  lowercased, deduplicated, entries of at least {min_length}",
        "         characters - the policy rejects anything shorter, so shorter",
        "         entries could never be reached. Entries starting with '#' are",
        "         dropped: that is this file's comment marker.",
        "Added:   generated repeats, digit runs, keyboard walks and product",
        "         strings that a frequency list would only contain by accident.",
    ]

    write_list(
        BACKEND_OUT,
        backend_entries,
        provenance
        + [
            "",
            "Scope:   every filtered entry. The server holds this in a frozenset,",
            "         where the size does not matter.",
        ],
        BACKEND_MAX_BYTES,
    )
    write_list(
        FRONTEND_OUT,
        frontend_entries,
        provenance
        + [
            "",
            f"Scope:   the first {FRONTEND_RANK_LIMIT} entries in upstream rank order,",
            "         plus the generated patterns - a strict subset of the backend",
            "         copy. The full set is ~320 KB gzipped, which is not worth",
            "         shipping for feedback the server repeats on submit. Anything",
            "         flagged here is genuinely common; the rest is caught server",
            "         side. The subset relation is asserted by the drift test.",
        ],
        FRONTEND_MAX_BYTES,
    )


if __name__ == "__main__":
    main()
