"""
Shared test utilities for the Mascope test suite.

Provides:
- ID generation (`gen_test_id`)
- Test database connection parameters (`TEST_DB_HOST` / `_PORT` / `_USER`)
  and password resolution (`get_test_password`)
- Test database naming (`TEST_ENV`, `scoped_db_name`)

The connection and naming helpers are used by both the root conftest (async
engine factory) and the migrations test conftest (sync engines). Centralised
here so test infrastructure shares a single source of truth for which server
it talks to and what it may call a database on it.
"""

import hashlib
import os
import re
from pathlib import Path

from mascope_backend.db.id import gen_id


# --- Test database connection parameters ---

# Resolved at import time from TEST_DB_* env vars (CI override) with dev
# container defaults as fallback. Intentionally independent of
# runtime.config.database so test infrastructure stays hermetic — tests
# must not be affected by whichever Mascope env happens to be active.
TEST_DB_HOST: str = os.environ.get("TEST_DB_HOST", "localhost")
TEST_DB_PORT: str = os.environ.get("TEST_DB_PORT", "5432")
TEST_DB_USER: str = os.environ.get("TEST_DB_USER", "mascope_user")

# --- Credential resolution ---


def get_test_password() -> str:
    """Resolve PostgreSQL password for test connections.

    Resolution order:
    - `POSTGRES_TEST_PASSWORD` env var (CI and explicit local override)
    - `${MASCOPE_PATH}/.runtime/secrets/postgres_password.txt` (local dev)

    :return: PostgreSQL password string
    :rtype: str
    :raises RuntimeError: If neither source is available
    """
    password = os.environ.get("POSTGRES_TEST_PASSWORD")
    if password:
        return password

    mascope_path = os.environ.get("MASCOPE_PATH")
    if not mascope_path:
        raise RuntimeError(
            "Cannot resolve test DB password: "
            "set POSTGRES_TEST_PASSWORD or MASCOPE_PATH env var"
        )
    secret_path = Path(mascope_path) / ".runtime" / "secrets" / "postgres_password.txt"
    if not secret_path.exists():
        raise RuntimeError(
            f"Cannot resolve test DB password: secret file not found at {secret_path}"
        )
    return secret_path.read_text().strip()


# --- Test database naming ---

# Test databases are named `mascope_test_{env}_{category}`. The env segment
# scopes the name to *this* checkout, so two backend suites running at once
# against the shared Postgres - two worktrees on one machine, or two agent
# sessions - cannot destroy each other's databases. The factories drop the
# database they are about to create, so without the env segment the second run
# to start deletes the first one's schema out from under it, mid-test: the
# failures that surface are unrelated-looking asyncpg errors ("cannot perform
# operation: another operation is in progress"), not anything pointing at the
# real cause.
#
# The rest of the runtime already namespaces per instance - `mascope dev run
# --instance` binds a worktree to a slot with its own `mascope_<env>` database,
# filestore and ports - so this brings the test databases in line with it.
#
# Resolution order:
#   - `MASCOPE_TEST_ENV`: explicit override, used as the whole segment. Still
#     sanitised for the identifier - `Feature/ABC-123` becomes
#     `feature_abc_123`, so two spellings of one slug do land on one database -
#     but it is the one tier carrying no checkout digest, which is what lets it
#     give a run a private namespace or make two runs deliberately share one.
#   - `MASCOPE_ENV`, else `wt-<checkout directory name>` - the label - with a
#     digest of the checkout's absolute path appended. `MASCOPE_ENV` is the
#     variable `runtime.state.env` consults first, so a shell holding an
#     instance (`eval "$(mascope instance show --export)"`) labels its test
#     databases after that instance and they sit beside its `mascope_<env>`.
#
# The label is for the reader; the digest is what isolates. Neither candidate
# label identifies a checkout on its own: `MASCOPE_ENV` follows the *shell*, so
# an export left over from one worktree would otherwise re-merge the next one,
# and a directory name is not unique either (`~/work/mascope` and
# `/tmp/mascope`). `mascope instance` resolves the same clash the same way -
# `_env_name_for` falls back to a path digest - but only when the base name is
# already taken, because it has a registry to consult; there is none here, so
# the digest is unconditional. Note the label is sanitised for a database
# identifier, so it reads `wt_foo` where `mascope instance list` shows `wt-foo`.
#
# The checkout is read off `__file__` rather than the runtime: it needs no git
# call, no `MASCOPE_PATH` and no `state.json`, so the helpers here stay hermetic
# and a checkout that has never allocated an instance is still isolated. The
# persisted `state.json` env is deliberately NOT consulted - it lives under the
# shared `MASCOPE_PATH` and therefore reads the same from every worktree, which
# is precisely the collision this scoping exists to prevent.
#
# Two runs from the *same* checkout still collide; that is what the
# `MASCOPE_TEST_ENV` override is for.

# tests/test_utils.py -> tests -> backend -> server -> checkout root
_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]

_DB_NAME_PREFIX = "mascope_test_"

# PostgreSQL identifiers are truncated to NAMEDATALEN-1 bytes, silently. Two
# long names that differ only past the cut would collapse onto one database -
# for the migrations category that would merge the stairway, drift and seeded
# databases - so names are shortened here instead, deliberately.
_MAX_DB_NAME_LEN = 63

#: Characters of hex kept from a SHA-1, wherever a name needs disambiguating.
_DIGEST_LEN = 6


def _sanitize(name: str) -> str:
    """Reduce `name` to a lowercase PostgreSQL-safe identifier fragment.

    :param name: Arbitrary env or category name
    :type name: str
    :return: Lowercase `[a-z0-9_]` fragment, never empty
    :rtype: str
    """
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "env"


def _digest(text: str) -> str:
    """Return the short hex digest used to disambiguate names.

    Not a security boundary - it only has to be stable across processes, which
    rules out `hash()`.

    :param text: Value to digest
    :type text: str
    :return: `_DIGEST_LEN` lowercase hex characters
    :rtype: str
    """
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:_DIGEST_LEN]


def _resolve_test_env(checkout_root: Path | None = None) -> str:
    """Resolve the env segment of the test database names.

    See the note above for the resolution order, why the label alone cannot be
    trusted to identify a checkout, and why the persisted `state.json` env is
    left out of it.

    :param checkout_root: Checkout to scope to; defaults to this file's own
    :type checkout_root: Path, optional
    :return: Sanitised env name
    :rtype: str
    """
    override = os.environ.get("MASCOPE_TEST_ENV")
    if override:
        return _sanitize(override)

    root = checkout_root if checkout_root is not None else _CHECKOUT_ROOT
    label = os.environ.get("MASCOPE_ENV") or f"wt-{root.name}"
    return f"{_sanitize(label)}_{_digest(str(root))}"


#: Env segment shared by every test database this run creates.
TEST_ENV: str = _resolve_test_env()


def _fit(env: str, budget: int) -> str:
    """Shorten `env` to `budget` characters without making it ambiguous.

    Truncation alone would map two long env names onto one database; appending
    a digest of the full name keeps them apart. `budget` has to leave room for
    that digest - `scoped_db_name` is what guarantees it.

    :param env: Sanitised env name
    :type env: str
    :param budget: Characters available for the env segment, `> _DIGEST_LEN`
    :type budget: int
    :return: `env` unchanged, or a truncated form ending in a short digest
    :rtype: str
    """
    if len(env) <= budget:
        return env
    return f"{env[: budget - _DIGEST_LEN - 1]}_{_digest(env)}"


def scoped_db_name(category: str) -> str:
    """Build the database name for a test category in this run's env.

    The category is kept whole and the env segment absorbs any shortening, so
    categories stay distinguishable however long the env name is.

    Not named `test_db_name`: this module matches `python_files = test_*.py`,
    so pytest would collect any `test_*` callable in it as a test case.

    :param category: Test category identifier (e.g. `unit_tests`, `migrations`)
    :type category: str
    :return: Database name like `mascope_test_wt_myworktree_a1b2c3_unit_tests`
    :rtype: str
    :raises ValueError: If `category` is too long to leave the env segment room
    """
    suffix = f"_{_sanitize(category)}"
    budget = _MAX_DB_NAME_LEN - len(_DB_NAME_PREFIX) - len(suffix)
    if budget <= _DIGEST_LEN:
        raise ValueError(
            f"Test category {category!r} leaves no room for the env segment "
            f"inside Postgres' {_MAX_DB_NAME_LEN}-character identifier limit. "
            "Shorten it - truncating here would merge two categories onto one "
            "database, silently."
        )
    return f"{_DB_NAME_PREFIX}{_fit(TEST_ENV, budget)}{suffix}"


# --- ID generation ---


def gen_test_id(size: int = 16) -> str:
    """Generate a random ID of exactly `size` characters.

    Delegates to `mascope_backend.db.id.gen_id` — same alphabet and
    generation logic used by application models (alphanumeric only, no
    `-` or `_`). Centralised here so test files have a single import
    point and any test-specific ID generation changes stay in one place.

    The default size of 16 matches the `VARCHAR(16)` constraint on
    primary key columns — pass `size` explicitly when a column has a
    different constraint.

    :param size: Character length of the generated ID
    :type size: int
    :return: Random alphanumeric nanoid string
    :rtype: str
    """
    return gen_id(size)
