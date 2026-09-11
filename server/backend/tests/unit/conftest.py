"""
Test fixtures specific to unit tests.

This module establishes the test database and session infrastructure specifically
for unit tests. It creates an isolated PostgreSQL database just for unit tests
so that they don't interfere with integration or other test categories.

Key components:
- Unit test-specific database engine and session fixtures
- Database patching to redirect application code to the test database
- Session factory for unit test database access

Unit testing approach:
- Tests individual components in isolation from the rest of the system
- Mocks external dependencies
- Fast, focused tests for specific functionality
- Independent tests with minimal shared state
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

import mascope_backend.db as db_module


@pytest_asyncio.fixture(scope="session")
async def async_engine(async_engine_factory):
    """Create an async PostgreSQL engine specifically for unit tests.

    Must be async and await the factory — see `async_engine_factory` in root
    `conftest.py` for why `asyncio.run()` cannot be used here.

    :param async_engine_factory: Async factory to create database engines
    :return: SQLAlchemy async engine instance
    :rtype: AsyncEngine
    """
    return await async_engine_factory("unit_tests")


@pytest.fixture(scope="session")
def async_session_factory(async_engine):
    """Create an async session factory for unit tests.

    Session-scoped: one factory instance shared across all unit tests.
    Used directly by session-scoped fixtures (persistent test data) and
    indirectly via the `session` fixture for function-scoped test isolation.

    :param async_engine: The SQLAlchemy async engine for unit tests
    :type async_engine: AsyncEngine
    :return: Session factory for creating database sessions
    :rtype: async_sessionmaker
    """
    return async_sessionmaker(
        async_engine,
        expire_on_commit=False,  # Keeps objects usable after commit without requerying
        autoflush=False,  # Prevents automatic DB synch for more control in tests
    )


@pytest.fixture(scope="session", autouse=True)
def patch_db(async_session_factory):
    """Redirect all application database access to the unit test database.

    Patches `db_module.ASYNC_SESSION_MAKER` for the duration of the test
    session. This affects both `async_session()` (used in controllers and
    services) and `get_async_session()` (FastAPI dependency injection).

    autouse=True: runs automatically for every unit test without explicit
    inclusion in each test function.

    :param async_session_factory: The test session factory
    :type async_session_factory: async_sessionmaker
    """
    # Store the original session maker to restore it later
    original_session_maker = db_module.ASYNC_SESSION_MAKER

    # Replace with our test session factory
    db_module.ASYNC_SESSION_MAKER = async_session_factory

    yield

    # Restore the original session maker
    db_module.ASYNC_SESSION_MAKER = original_session_maker


@pytest.fixture(autouse=True)
def bind_db(async_session_factory, monkeypatch):
    """Point application database access at the unit test database for each test.

    `patch_db` installs the factory once, when the first unit test runs, and the
    integration conftest installs its own factory into the same global in the
    same way. In a selection that interleaves the categories -
    `pytest tests/unit/a tests/integration/b tests/unit/c` - the later unit
    tests would otherwise run their application code against the integration
    database while their fixtures write to the unit one. Rebinding per test
    makes the category a test belongs to decide, whatever ran before it.

    :param async_session_factory: The test session factory
    :type async_session_factory: async_sessionmaker
    :param monkeypatch: Restores the previous binding after the test
    :type monkeypatch: pytest.MonkeyPatch
    """
    monkeypatch.setattr(db_module, "ASYNC_SESSION_MAKER", async_session_factory)
