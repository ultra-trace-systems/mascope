"""
Fixtures for the Alembic migrations test category.

Provides two independent test databases (`mascope_test_migrations` for
the stairway test, `mascope_test_migrations_drift` for the drift test)
and the matching Alembic `Config` / engine fixtures. Self-contained sync
infrastructure — does not use the async machinery from the root conftest.

See `server/backend/tests/README.md` (Migration tests) for the rationale,
lifecycle, and how this category interacts with `alembic/env.py`.

NOTE: For adding more pytest-alembic tests later:
    The drift DB is session-scoped. With only one pytest-alembic test
    (``test_model_definitions_match_ddl``) this is fine — the test is
    idempotent. If more pytest-alembic tests are added that depend on
    specific DB state, either reset the DB between them or move the
    fixture to function scope.
"""

from pathlib import Path
from typing import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from test_utils import (
    TEST_DB_HOST,
    TEST_DB_PORT,
    TEST_DB_USER,
    get_test_password,
)


# --- Constants ---

# Resolved from this file, so the suite always tests the migrations of the
# checkout it lives in. Deriving it from MASCOPE_PATH (the shared runtime home,
# normally the main checkout) would test *those* migrations against *this*
# tree's models - a convincing but bogus drift failure from a worktree.
BACKEND_PATH = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_PATH / "alembic.ini"

STAIRWAY_DB_NAME = "mascope_test_migrations"
DRIFT_DB_NAME = "mascope_test_migrations_drift"
SEEDED_DB_NAME = "mascope_test_migrations_seeded"


# --- URL builders (sync, psycopg2 — matches DatabaseConfig.get_postgres_url_sync) ---


def _sync_url(db_name: str) -> str:
    """Build sync psycopg2 URL for `db_name`.

    Matches the format produced by `DatabaseConfig.get_postgres_url_sync`,
    so the test exercises the same driver as dev/prod migrations.
    """
    return (
        f"postgresql+psycopg2://{TEST_DB_USER}:{get_test_password()}"
        f"@{TEST_DB_HOST}:{TEST_DB_PORT}/{db_name}"
    )


def _admin_url() -> str:
    """Build sync admin URL pointing at the `postgres` maintenance DB."""
    return _sync_url("postgres")


# --- DB lifecycle helpers ---


def _drop_database(admin_engine: Engine, db_name: str) -> None:
    """Terminate connections and drop `db_name` if it exists.

    Uses the same `pg_terminate_backend` pattern as the async factory in
    the root conftest — necessary because a leftover connection (from a
    previous failed run) blocks `DROP DATABASE`.
    """
    with admin_engine.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": db_name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))


def _ephemeral_db(db_name: str) -> Iterator[str]:
    """Drop+create `db_name` and yield its sync URL; drop again on teardown.

    Shared lifecycle used by both the stairway and drift databases.
    The DB is created empty — no schema, no `alembic_version` table.
    """
    admin_engine = create_engine(
        _admin_url(), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        _drop_database(admin_engine, db_name)
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))

        yield _sync_url(db_name)

    finally:
        _drop_database(admin_engine, db_name)
        admin_engine.dispose()


# --- Stairway fixtures ---


@pytest.fixture(scope="session")
def stairway_db_url() -> Iterator[str]:
    """Session-scoped: drop+create `mascope_test_migrations`, drop on teardown.

    Used by the stairway test exclusively. State accumulates across the
    parametrize chain — after revision N is applied, the DB is at N and
    the next step starts there.
    """
    yield from _ephemeral_db(STAIRWAY_DB_NAME)


@pytest.fixture(scope="session")
def stairway_alembic_config(stairway_db_url: str) -> Config:
    """Programmatic Alembic Config pointed at the stairway test database.

    Loads `alembic.ini` for `script_location` and post_write_hooks, but
    overrides `sqlalchemy.url`. The patched `env.py._resolve_url()` honors
    this override and skips the runtime-derived URL — that's what isolates
    the test from whichever Mascope env is active.

    Named `stairway_alembic_config` (not `alembic_config`) to avoid
    colliding with the pytest-alembic fixture of the same name used by
    the drift test.
    """
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", stairway_db_url)
    return cfg


# --- Seeded data-migration fixtures ---
#
# Stairway and drift both walk the chain against a database created empty, so
# neither one ever executes a data migration's row-handling code. A migration
# that rewrites customer data needs rows to act on: these fixtures give a test
# its own database it can stop partway down the chain, seed, and then finish
# upgrading. Module-scoped so one such test file pays for one walk of the
# chain no matter how many assertions it makes; a second file gets a fresh
# database because the fixtures are torn down between modules.


@pytest.fixture(scope="module")
def seeded_db_url() -> Iterator[str]:
    """Module-scoped: drop+create `mascope_test_migrations_seeded`, drop after.

    Separate from both the stairway and the drift databases so a seeded test
    can leave the chain part-applied without disturbing either.
    """
    yield from _ephemeral_db(SEEDED_DB_NAME)


@pytest.fixture(scope="module")
def seeded_alembic_config(seeded_db_url: str) -> Config:
    """Programmatic Alembic Config pointed at the seeded test database.

    Same override mechanism as `stairway_alembic_config`: `alembic.ini` for
    `script_location`, `sqlalchemy.url` replaced so the patched
    `env.py._resolve_url()` targets this database instead of the active env's.
    """
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", seeded_db_url)
    return cfg


@pytest.fixture(scope="module")
def seeded_engine(seeded_db_url: str) -> Iterator[Engine]:
    """Sync engine on the seeded database, for inserting and reading rows."""
    engine = create_engine(seeded_db_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


# --- Drift test fixtures (pytest-alembic) ---


@pytest.fixture(scope="session")
def drift_db_url() -> Iterator[str]:
    """Session-scoped: drop+create `mascope_test_migrations_drift`, drop on teardown.

    Used by the pytest-alembic drift test. Separate from the stairway DB
    so the two test files don't share state — `test_model_definitions_match_ddl`
    expects to drive its own ``upgrade head`` against a known starting
    point.
    """
    yield from _ephemeral_db(DRIFT_DB_NAME)


@pytest.fixture
def alembic_config(drift_db_url: str) -> dict:
    """Override pytest-alembic's `alembic_config` to point at the drift DB.

    Returns a dict consumed by pytest-alembic to build its internal
    `alembic.config.Config`. The `file` key tells pytest-alembic to load
    our `alembic.ini` (for `script_location`, post_write_hooks, etc.);
    `sqlalchemy.url` overrides the URL so the patched `env.py._resolve_url()`
    targets the drift test DB.

    See pytest-alembic docs for the dict-based config form:
    https://pytest-alembic.readthedocs.io/en/latest/api.html
    """
    return {
        "file": str(ALEMBIC_INI),
        "sqlalchemy.url": drift_db_url,
    }


@pytest.fixture
def alembic_engine(drift_db_url: str) -> Iterator[Engine]:
    """Override pytest-alembic's `alembic_engine` with a sync engine on the drift DB.

    pytest-alembic uses this engine for schema introspection during
    `compare_metadata`. The URL must match the one used by `alembic_config`
    above — otherwise migration commands and introspection would target
    different databases.
    """
    engine = create_engine(drift_db_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
