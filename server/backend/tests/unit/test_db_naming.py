"""Tests: test-database naming (``test_utils.scoped_db_name``).

Every backend suite drops the databases it is about to create, so the names
are the only thing keeping two concurrent runs on the shared Postgres - two
worktrees on one machine, or two agent sessions - from destroying each other's
schema mid-test. These assertions pin the two properties that has to rest on:
the name is scoped to the checkout - the *path*, not just its last segment -
and distinct inputs never collapse onto one database.
"""

import re

import pytest
import test_utils
from test_utils import scoped_db_name


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start from a bare environment so a developer's own exports cannot leak in."""
    monkeypatch.delenv("MASCOPE_TEST_ENV", raising=False)
    monkeypatch.delenv("MASCOPE_ENV", raising=False)


# --- Name shape ---


def test_name_carries_both_the_env_and_the_category():
    name = scoped_db_name("unit_tests")

    assert name.startswith("mascope_test_")
    assert name.endswith("_unit_tests")
    assert test_utils.TEST_ENV[:8] in name


def test_name_is_a_legal_unquoted_identifier():
    """The name is interpolated into DDL, so sanitisation is load-bearing."""
    assert re.fullmatch(r"[a-z][a-z0-9_]*", scoped_db_name("unit_tests"))


def test_categories_stay_distinct_within_one_env():
    """The three migration databases must not merge - each holds a different chain."""
    names = {
        scoped_db_name(category)
        for category in ("migrations", "migrations_drift", "migrations_seeded")
    }

    assert len(names) == 3


def test_name_fits_postgres_identifier_limit():
    """Postgres truncates past 63 bytes silently, which would merge categories."""
    for category in ("unit_tests", "integration_tests", "migrations_seeded"):
        assert len(scoped_db_name(category)) <= 63


def test_a_long_env_is_shortened_without_losing_the_category(monkeypatch):
    monkeypatch.setattr(test_utils, "TEST_ENV", "wt_" + "a" * 80)

    name = scoped_db_name("integration_tests")

    assert len(name) <= 63
    assert name.endswith("_integration_tests")


def test_two_long_envs_do_not_collapse_onto_one_database(monkeypatch):
    """Plain truncation would give both of these the same name."""
    shared = "wt_" + "a" * 60

    monkeypatch.setattr(test_utils, "TEST_ENV", shared + "_one")
    first = scoped_db_name("unit_tests")
    monkeypatch.setattr(test_utils, "TEST_ENV", shared + "_two")
    second = scoped_db_name("unit_tests")

    assert first != second


def test_a_category_too_long_to_scope_is_refused():
    """Silently overshooting the limit is what merges two categories onto one DB."""
    with pytest.raises(ValueError, match="identifier limit"):
        scoped_db_name("x" * 50)


# --- Env resolution ---


def test_env_defaults_to_the_checkout_directory():
    """No exports set: the worktree's own name isolates it from every other one."""
    resolved = test_utils._resolve_test_env()

    assert resolved.startswith("wt_")
    assert test_utils._sanitize(test_utils._CHECKOUT_ROOT.name) in resolved


def test_two_checkouts_sharing_a_directory_name_stay_apart(tmp_path):
    """`~/work/mascope` and `/tmp/mascope` are two checkouts, not one.

    The reported collision, in its remaining form: a label derived from the
    directory name alone is not unique, so the path digest is what actually
    separates them.
    """
    first = test_utils._resolve_test_env(tmp_path / "a" / "mascope")
    second = test_utils._resolve_test_env(tmp_path / "b" / "mascope")

    assert first.startswith("wt_mascope_")
    assert second.startswith("wt_mascope_")
    assert first != second


def test_one_checkout_always_resolves_the_same_way(tmp_path):
    """Must be stable across runs, or teardown could not drop what it created."""
    root = tmp_path / "mascope"

    assert test_utils._resolve_test_env(root) == test_utils._resolve_test_env(root)


def test_mascope_env_is_honoured(monkeypatch, tmp_path):
    """A shell holding an instance (`mascope instance show --export`) reuses its env."""
    monkeypatch.setenv("MASCOPE_ENV", "wt-some-worktree")

    assert test_utils._resolve_test_env(tmp_path).startswith("wt_some_worktree_")


def test_mascope_env_does_not_merge_two_checkouts(monkeypatch, tmp_path):
    """It follows the shell, so an export left over from another worktree is stale."""
    monkeypatch.setenv("MASCOPE_ENV", "wt-alpha")

    first = test_utils._resolve_test_env(tmp_path / "alpha")
    second = test_utils._resolve_test_env(tmp_path / "beta")

    assert first != second


def test_explicit_override_wins_over_the_instance_env(monkeypatch):
    monkeypatch.setenv("MASCOPE_ENV", "wt-some-worktree")
    monkeypatch.setenv("MASCOPE_TEST_ENV", "shared")

    assert test_utils._resolve_test_env() == "shared"


def test_explicit_override_can_deliberately_share_one_namespace(monkeypatch, tmp_path):
    """The documented escape hatch: no digest, so two checkouts can be merged."""
    monkeypatch.setenv("MASCOPE_TEST_ENV", "shared")

    assert test_utils._resolve_test_env(tmp_path / "alpha") == "shared"
    assert test_utils._resolve_test_env(tmp_path / "beta") == "shared"


def test_env_names_are_sanitised_for_postgres(monkeypatch):
    monkeypatch.setenv("MASCOPE_TEST_ENV", "Feature/ABC-123 xyz")

    assert test_utils._resolve_test_env() == "feature_abc_123_xyz"
