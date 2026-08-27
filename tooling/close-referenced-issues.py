#!/usr/bin/env python3
#
# Print the issue numbers a pull request body says it closes.
#
# Why this exists: GitHub closes a linked issue only when the pull request
# merges into the repository's DEFAULT branch. Mascope's default branch is
# `master`, the release branch, while every pull request merges into
# `develop` - so `Closes #N` has never once fired here. A backlog sweep found
# 19 issues declared closed by a merged pull request, 8 of them still open,
# and every "Closes #N" written since the repository was created has been
# decorative. This script is the parsing half of the workflow that makes the
# declaration mean something; `.github/workflows/close-referenced-issues.yaml`
# does the closing.
#
# Deliberately narrow about what counts as a closing reference:
#
#   - Only GitHub's own keywords (close/fixes/resolved/...). `Refs #N` means
#     "related to" and MUST NOT close anything - this repository's commit
#     messages use it precisely to reference an issue the change does not
#     finish, and reading it as a close would shut live work.
#   - Only bare `#N`, never `owner/repo#N`. Closing an issue in another
#     repository from here would be a surprise, and the token would not carry
#     the rights for it anyway.
#   - Nothing inside a fenced code block. Pull request bodies in this
#     repository quote log output and diffs freely, and a fence is the one
#     place a `#N` is text rather than a reference.
#
# No dependencies and no network: it reads text and prints numbers, so the
# workflow can pipe a body through it and the test suite can exercise the
# parsing without a GitHub API in the loop.
#
# Usage:
#   python3 tooling/close-referenced-issues.py < body.md
#   python3 tooling/close-referenced-issues.py body.md

import re
import sys


# GitHub's documented closing keywords, all of the spellings it honours.
_KEYWORDS = (
    "close",
    "closes",
    "closed",
    "fix",
    "fixes",
    "fixed",
    "resolve",
    "resolves",
    "resolved",
)

# `Closes #12`, `fixes: #12`, `RESOLVED  #12`. The negative lookbehind on the
# keyword start keeps `refs` and any other word ending in a keyword from
# matching, and the one on `#` rejects `owner/repo#12`.
_CLOSING = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(_KEYWORDS) + r")\s*:?\s+(?<![\w/])#(\d+)\b",
    re.IGNORECASE,
)

# Fenced code blocks, ``` or ~~~, including an unterminated final fence.
_FENCE = re.compile(r"^(?P<fence>```|~~~).*?(?:^(?P=fence).*?$|\Z)", re.M | re.S)


def strip_code_fences(text: str) -> str:
    """
    Remove fenced code blocks, so quoted output cannot close an issue.

    :param text: Markdown text, typically a pull request body.
    :type text: str
    :return: The same text with every fenced block removed.
    :rtype: str
    """
    return _FENCE.sub("", text)


def closing_references(text: str) -> list[int]:
    """
    Issue numbers the text declares it closes, in first-seen order.

    :param text: Markdown text, typically a pull request body.
    :type text: str
    :return: De-duplicated issue numbers, order preserved.
    :rtype: list[int]
    """
    seen: dict[int, None] = {}
    for match in _CLOSING.finditer(strip_code_fences(text)):
        seen.setdefault(int(match.group(1)), None)
    return list(seen)


def main(argv: list[str]) -> int:
    """
    Print one issue number per line.

    :param argv: Command-line arguments; an optional path to read instead of
                 standard input.
    :type argv: list[str]
    :return: Process exit status.
    :rtype: int
    """
    if len(argv) > 2:
        print(f"usage: {argv[0]} [FILE]", file=sys.stderr)
        return 2
    if len(argv) == 2:
        with open(argv[1], encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = sys.stdin.read()
    for number in closing_references(text):
        print(number)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
