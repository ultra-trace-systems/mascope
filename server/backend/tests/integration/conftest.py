"""
Test fixtures specific to integration tests.

This module establishes the test database and session infrastructure specifically
for integration tests. It creates an isolated PostgreSQL database just for
integration tests so that they don't interfere with unit or other test categories.

Key components:
- Integration test-specific database engine and session fixtures
- Database patching to redirect application code to the test database
- Session factory for integration test database access

Integration testing approach:
- Tests how components work together
- Tests more realistic flows through the application
- Verifies subsystem interactions without mocking all dependencies
- Maintains test isolation between categories (unit vs. integration)
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

import mascope_backend.db as db_module


@pytest_asyncio.fixture(scope="session")
async def async_engine(async_engine_factory):
    """Create an async PostgreSQL engine specifically for integration tests.

    Must be async and await the factory — see `async_engine_factory` in root
    `conftest.py` for why `asyncio.run()` cannot be used here.

    :param async_engine_factory: Async factory to create database engines
    :return: SQLAlchemy async engine instance
    :rtype: AsyncEngine
    """
    return await async_engine_factory("integration_tests")


@pytest.fixture(scope="session")
def async_session_factory(async_engine):
    """Create an async session factory for integration tests.

    Session-scoped: one factory instance shared across all integration tests.
    Used directly by session-scoped fixtures (persistent test data such as
    roles and users) and indirectly via function-scoped session fixtures.

    :param async_engine: The SQLAlchemy async engine for integration tests
    :type async_engine: AsyncEngine
    :return: Session factory for creating database sessions
    :rtype: async_sessionmaker
    """
    return async_sessionmaker(
        async_engine,
        expire_on_commit=False,  # Keeps objects usable after commit without requerying
        autoflush=False,  # Prevents automatic DB sync for more control in tests
    )


@pytest.fixture(scope="session", autouse=True)
def patch_db(async_session_factory):
    """Redirect all application database access to the integration test database.

    Patches `db_module.ASYNC_SESSION_MAKER` for the duration of the test
    session. This affects both `async_session()` (used in controllers and
    services) and `get_async_session()` (FastAPI dependency injection).

    autouse=True: runs automatically for every integration test without
    explicit inclusion in each test function.

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
    """Point application database access at the integration database for each test.

    `patch_db` installs the factory once, when the first integration test runs,
    and the unit conftest installs its own factory into the same global in the
    same way. In a selection that interleaves the categories -
    `pytest tests/integration/a tests/unit/b tests/integration/c` - the later
    integration tests would otherwise run their application code against the
    unit database while their fixtures write to the integration one. Rebinding
    per test makes the category a test belongs to decide, whatever ran before it.

    :param async_session_factory: The test session factory
    :type async_session_factory: async_sessionmaker
    :param monkeypatch: Restores the previous binding after the test
    :type monkeypatch: pytest.MonkeyPatch
    """
    monkeypatch.setattr(db_module, "ASYNC_SESSION_MAKER", async_session_factory)
