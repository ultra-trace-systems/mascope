"""
Global pytest fixtures and factory functions for the entire test suite.

This module provides core testing infrastructure shared across all test
categories (unit, integration, etc.). The factory creates isolated
PostgreSQL databases per test category, dropped and recreated each
session for a clean slate.

Local dev: requires `mascope dev up` (postgres at localhost:5432).
CI: PostgreSQL service container, credentials via `POSTGRES_TEST_PASSWORD`.

Connection settings (host/port/user/password) are resolved by helpers in
`test_utils.py` — intentionally independent of `runtime.config.database`
to keep test infrastructure hermetic. Tests must not be affected by
whichever Mascope env happens to be active.

Async fixture design:
    `async_engine_factory` is a session-scoped async fixture that yields
    an async callable. Callers must use `@pytest_asyncio.fixture(scope="session")`
    and must `await` the factory call so that all engine setup runs inside
    pytest-asyncio's managed session event loop.

Design principles:
- Ephemeral: databases created fresh each session, dropped on teardown
- Isolated: separate database per category (`mascope_test_unit_tests`, etc.)
- Fixture dependency chain: Always from narrower scope to wider scope
  (function → class → module → session), never the reverse
- Explicit organization: Test fixtures are organized by their scope and purpose
"""

import sys

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from test_utils import (
    TEST_DB_HOST,
    TEST_DB_PORT,
    TEST_DB_USER,
    get_test_password,
)

from mascope_backend.db import Base


# --- Process-global application state ---

#: Module-level caches and throttles that outlive a test. They are per worker
#: by design in production; in a test session "per worker" is the whole run, so
#: without this every test inherits whatever the previous one left behind. The
#: auth token cache is keyed on the token string, and the suite reuses fixed
#: token literals, so a row deleted and recreated between two tests can still
#: be served from the previous test's entry. The last_seen throttle is worse:
#: a test that fakes the monotonic clock leaves a stamp from that fake clock
#: behind, and a later real call comparing against it silently skips its write.
#:
#: Reached through ``sys.modules`` rather than imported. Importing anything
#: under ``mascope_backend.api.new.auth`` executes that package's ``__init__``,
#: which pulls in the whole auth stack - fastapi-users, the database strategy,
#: and a read of the JWT secret file at import. That would become a
#: collection-time requirement for every backend run, including the migration
#: tests and the library tests, which need none of it.
_PROCESS_GLOBAL_STATE = (
    ("mascope_backend.api.new.auth.access_token.cache", "_entries"),
    ("mascope_backend.api.new.auth.access_token.validation", "_last_seen_written_at"),
)


def _clear_process_global_state() -> None:
    """Empty every process-global container listed in `_PROCESS_GLOBAL_STATE`.

    Resolved per call: the container is looked up on the module each time, so a
    test that monkeypatched one gets the real object cleared after monkeypatch
    has restored it.

    :return: None
    """
    for module_name, attribute in _PROCESS_GLOBAL_STATE:
        module = sys.modules.get(module_name)
        if module is not None:
            getattr(module, attribute).clear()


@pytest.fixture(autouse=True)
def reset_process_global_state():
    """Give every test an empty auth cache and last_seen throttle.

    Autouse at the rootdir conftest, so it covers unit, integration, system and
    migration tests alike, and is set up before every function-scoped fixture a
    test module defines. Higher-scoped fixtures still wrap it, so a
    session-scoped fixture that warmed either container during setup would have
    its work cleared by the first test; nothing does that today.

    The per-module clearing fixtures in tests/unit/api/auth/ are kept
    deliberately rather than folded into this one. Double clearing is free, and
    they state the precondition where it is read.

    :yield: None
    """
    _clear_process_global_state()
    yield
    _clear_process_global_state()


# --- Connection URLs ---


def _get_test_db_url(db_name: str) -> str:
    """Build asyncpg URL for a named test database.

    :param db_name: Target database name
    :type db_name: str
    :return: SQLAlchemy async connection URL
    :rtype: str
    """
    return (
        f"postgresql+asyncpg://{TEST_DB_USER}:{get_test_password()}"
        f"@{TEST_DB_HOST}:{TEST_DB_PORT}/{db_name}"
    )


def _get_admin_url() -> str:
    """Build asyncpg URL for admin operations against the `postgres` maintenance DB.

    :return: SQLAlchemy async connection URL
    :rtype: str
    """
    return (
        f"postgresql+asyncpg://{TEST_DB_USER}:{get_test_password()}"
        f"@{TEST_DB_HOST}:{TEST_DB_PORT}/postgres"
    )


# --- Startup check ---


def _check_postgres_available() -> None:
    """Fail fast with a clear message if the PostgreSQL server is not reachable.

    Runs at collection time so tests don't spend time collecting only to
    fail on the first fixture setup. Skipped when neither `POSTGRES_TEST_PASSWORD`
    nor `MASCOPE_PATH` is set — this covers import-only scenarios where a
    later, more specific failure is preferable.
    """
    import psycopg2

    try:
        password = get_test_password()
    except RuntimeError:
        return  # can't resolve credentials, let the fixture fail with its own error

    try:
        conn = psycopg2.connect(
            host=TEST_DB_HOST,
            port=int(TEST_DB_PORT),
            user=TEST_DB_USER,
            password=password,
            database="postgres",
            connect_timeout=3,
        )
        conn.close()
    except psycopg2.OperationalError:
        raise RuntimeError(
            f"\n\nCannot connect to PostgreSQL at {TEST_DB_HOST}:{TEST_DB_PORT}.\n"
            "Run 'mascope dev up' before running tests locally.\n"
        )


_check_postgres_available()


# --- Engine factory fixture ---


@pytest_asyncio.fixture(scope="session")
async def async_engine_factory():
    """Async factory fixture that creates isolated PostgreSQL engines per test category.

    Yields an async callable. Each call creates a `mascope_test_{category}` database
    from scratch (drop if exists, create, run `Base.metadata.create_all`) inside the
    pytest-asyncio session event loop. All engines and databases are tracked and cleaned
    up after the full test session ends.

    Must be called as an async session-scoped fixture in category-specific
    conftest.py files:
        @pytest_asyncio.fixture(scope="session")
        async def async_engine(async_engine_factory):
            return await async_engine_factory("unit_tests")

    :return: Async callable producing per-category AsyncEngine instances
    :rtype: callable
    """
    created: list[tuple[AsyncEngine, str]] = []

    async def _create_engine(category_name: str) -> AsyncEngine:
        """Create and initialise a PostgreSQL engine for `category_name`.

        Steps:
        - Terminate stale connections and drop any existing test database
        - Create fresh isolated test database
        - Build engine and create schema via SQLAlchemy metadata

        :param category_name: Test category identifier (e.g. `unit_tests`)
        :type category_name: str
        :return: Configured AsyncEngine connected to the test database
        :rtype: AsyncEngine
        """
        db_name = f"mascope_test_{category_name}"

        # --- Terminate stale connections and drop any existing test database ---
        admin_engine = create_async_engine(
            _get_admin_url(),
            isolation_level="AUTOCOMMIT",
        )
        async with admin_engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": db_name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))

            # --- Create fresh isolated test database ---
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await admin_engine.dispose()

        # --- Build engine and create schema via SQLAlchemy metadata ---
        engine = create_async_engine(_get_test_db_url(db_name), echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        created.append((engine, db_name))
        return engine

    yield _create_engine

    # Teardown: drop all test databases created during this session
    admin_engine = create_async_engine(
        _get_admin_url(),
        isolation_level="AUTOCOMMIT",
    )
    for engine, db_name in created:
        await engine.dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": db_name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    await admin_engine.dispose()
