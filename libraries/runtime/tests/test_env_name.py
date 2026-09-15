"""Tests: what characters a runtime env name may contain.

The rule lives here rather than in each consumer because an env name travels
into a filesystem path, an SSH command line, a Postgres database name and the
dev auth cookie's name. Widening it in one of those places alone is what would
let them disagree, so the alphabet is pinned in one test rather than four.
"""

import pytest

from mascope_runtime import ENV_NAME_PATTERN, is_valid_env_name


@pytest.mark.parametrize(
    "name",
    ["default", "prod", "demo", "wt-my-feature", "tof1", "a_b-C9", "x"],
)
def test_the_names_the_cli_generates_are_valid(name):
    # `mascope instance` slugs a worktree directory into exactly this shape.
    assert is_valid_env_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "",
        None,
        "my env",  # whitespace: breaks SSH command lines
        "feature/branch",  # path separator: escapes .runtime/env/
        "wt;rm -rf",  # shell metacharacter
        "wt=x",  # cookie-name separator (RFC 6265)
        "ä-env",  # non-ASCII
        "wt.x",  # currently excluded; pinned so widening is a deliberate edit
    ],
)
def test_names_outside_the_alphabet_are_refused(name):
    assert not is_valid_env_name(name)


def test_the_pattern_must_match_the_whole_name():
    # A partial match would accept "ok/../../etc" on the strength of its prefix.
    assert ENV_NAME_PATTERN.match("ok/nope")
    assert not is_valid_env_name("ok/nope")
