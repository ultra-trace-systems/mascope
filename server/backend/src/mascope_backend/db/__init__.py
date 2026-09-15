"""
Database initialization and configuration module.

This module handles PostgreSQL database connection setup, session management,
and initialization procedures.

Exports:
- Database connection functions (configure_database_engine, async_session, etc.)
- All ORM models from models.py
- All view mappings from views.py
"""

import asyncio
import os
from typing import AsyncGenerator, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mascope_backend.db import models
from mascope_backend.db.models import *  # noqa: F403, F401 - re-export models
from mascope_backend.db.secrets import postgres_password
from mascope_backend.db.views import Sample
from mascope_backend.runtime import runtime
from mascope_runtime.config import BackendConfig


# Initialize global variables at module load
ASYNC_SESSION_MAKER: async_sessionmaker[AsyncSession] | None = None
db_cfg = cast(BackendConfig, runtime.config).database

# Admission control for the dependency-injected session path (get_async_session
# only - nothing else may take a permit; see _DB_SEMAPHORE_PERMITS).
#
# A permit holder needs more than one connection. The auth SELECT opens a
# transaction on the injected session that is not committed, so that connection is
# held until the request ends; dependencies and controllers that run while it is
# still open (require_workspace_role -> _get_workspace_membership, and the ~290
# other async_session() call sites) then check out a SECOND connection, ungated.
# Free capacity beyond the admitted holders is what makes that second checkout
# possible.
#
# So the size is NOT a free choice, and NOT simply pool_size. Two conditions have
# to hold at once for N admitted holders:
#
#   N <= pool_size     - their own connections fit in the base pool
#   N <= max_overflow  - the overflow can serve all N nested checkouts at once
#
# hence min(). Sizing at the pool ceiling (pool_size + max_overflow) deadlocks the
# worker: every holder takes its auth connection, the pool is empty, and all of
# them then block on their nested checkout until pool_timeout (120 s) expires - on
# prod defaults, ten requests stalling a worker for two minutes. Sizing at
# pool_size alone is only safe while max_overflow >= pool_size, which prod (3/7)
# and dev (5/10) satisfy but the shipped base defaults (3/2) do not - so the
# condition is computed here rather than restated in a comment and left to drift.
#
# max(1, ...) keeps a max_overflow of 0 from producing a semaphore that admits
# nobody. See #1845.
_DB_SEMAPHORE_PERMITS = max(1, min(db_cfg.pool_size, db_cfg.max_overflow))
db_semaphore = asyncio.Semaphore(_DB_SEMAPHORE_PERMITS)


# Database configuration and session management
async def configure_database_engine() -> None:
    """
    Configure the PostgreSQL async engine and global session maker
    using SQLAlchemy's async_sessionmaker.
    This function is called during initialization (once per worker during startup)
    to establish a connection with the database.

    :return: None
    """
    database_url = db_cfg.get_postgres_url(
        password=postgres_password, env_name=runtime.env.name
    )
    db_name = db_cfg.get_postgres_database_name(env_name=runtime.env.name)
    runtime.logger.info(f"Using PostgreSQL at {db_cfg.host}:{db_cfg.port}/{db_name}")

    trace_mode: bool = runtime.config.log_level == "trace"

    engine = create_async_engine(
        database_url,
        pool_pre_ping=db_cfg.pool_pre_ping,
        echo=trace_mode,
        pool_size=db_cfg.pool_size,
        max_overflow=db_cfg.max_overflow,
        pool_timeout=db_cfg.pool_timeout,
    )

    # Define the global session maker using async_sessionmaker
    global ASYNC_SESSION_MAKER
    ASYNC_SESSION_MAKER = async_sessionmaker(
        engine, expire_on_commit=db_cfg.expire_on_commit
    )


async def dispose_engine() -> None:
    """
    Dispose the async engine and close all connection pool connections.
    Resets ASYNC_SESSION_MAKER to None so any accidental post-dispose
    call to async_session() fails immediately.
    No-op if the engine has not been configured.

    :return: None
    """
    global ASYNC_SESSION_MAKER
    if ASYNC_SESSION_MAKER is None:
        return
    engine = ASYNC_SESSION_MAKER.kw["bind"]
    await engine.dispose()
    ASYNC_SESSION_MAKER = None


def async_session() -> AsyncSession:
    """
    Session getter for manual session management.

    This function returns a new SQLAlchemy session that needs to be manually handled.
    It is useful for scenarios where we need fine-grained control over the session's
    lifecycle, such as flushing manually or performing tasks outside the session block.

    Key points:
    - Requires manual management (you must use `async with`).
    - Useful for batch processing where manual flushes or commits are required.

    Example usage:
        async with async_session() as session:
            # Perform operations within the session block
            session.flush()  # Optionally flush without committing

    :return: A new SQLAlchemy async session.
    :rtype: AsyncSession
    """
    if ASYNC_SESSION_MAKER is None:
        raise RuntimeError(
            "Database engine is not configured. Call configure_database_engine() first."
        )
    return ASYNC_SESSION_MAKER()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency-injected session for FastAPI route handlers.

    This function yields a session that is automatically managed by FastAPI's
    dependency injection system.
    It ensures that the session is opened at the start of the request and closed
    when the request finishes.

    Key points:
    - Automatically manages session lifecycle (opened and closed at the correct time).
    - Integrates with FastAPI `Depends()` to inject the session into routes.
    - Useful for request-response workflows where session lifecycle should be automated.

    Example usage in a FastAPI route:
        from fastapi import Depends

        @app.get("/items")
        async def get_items(session: AsyncSession = Depends(get_async_session)):
            # Perform operations within the session

    :yield: Yields an active SQLAlchemy session for database interactions.
    :rtype: AsyncGenerator[AsyncSession, None]
    """
    if ASYNC_SESSION_MAKER is None:
        raise RuntimeError(
            "Database engine is not configured. Call configure_database_engine() first."
        )
    async with db_semaphore:
        async with ASYNC_SESSION_MAKER() as session:
            yield session


# Initialization and main interface functions
async def init_db() -> None:
    """
    Initialize database connection for a worker process.

    This function is called by each worker during startup.
    It configures the database engine for this worker's connection pool.

    :raises Exception: If engine configuration or connection test fails
    :return: None
    """
    try:
        await configure_database_engine()
        await _test_database_connection()
        _log_pool_configuration()
    except Exception:
        runtime.logger.exception("Database initialization error")
        raise


async def _test_database_connection() -> None:
    """
    Test connection and log PostgreSQL version.

    :raises Exception: If the connection cannot be established
    :return: None
    """
    try:
        async with async_session() as session:
            result = await session.execute(text("SELECT version()"))
            pg_version = result.scalar()
            runtime.logger.info(
                f"PostgreSQL connection established successfully. Version: {pg_version}"
            )

            result = await session.execute(text("SELECT datname FROM pg_database"))
            databases = [row[0] for row in result.fetchall()]
            runtime.logger.debug(f"Available databases: {databases}")
    except Exception:
        # No log here: init_db logs the failure once, with the traceback
        raise


def _log_pool_configuration() -> None:
    """
    Log connection pool configuration for this worker.

    :return: None
    """
    if ASYNC_SESSION_MAKER is None:
        return
    try:
        engine = ASYNC_SESSION_MAKER.kw["bind"]
        worker_pid = os.getpid()

        runtime.logger.debug(
            f"Worker {worker_pid} pool config: "
            f"size={engine.pool.size()}, "
            f"max_overflow={engine.pool._max_overflow}, "
            f"timeout={engine.pool._timeout}s, "
            f"session_admissions={_DB_SEMAPHORE_PERMITS}"
        )
        # Say so when the overflow is what caps admissions, rather than letting
        # the throttle look like the pool size it no longer follows. Raising
        # pool_size is the obvious response to pool exhaustion, and on its own
        # it does not raise this: the nested checkout every admitted request
        # makes is served from max_overflow.
        if db_cfg.max_overflow < db_cfg.pool_size:
            runtime.logger.warning(
                f"max_overflow ({db_cfg.max_overflow}) is below pool_size "
                f"({db_cfg.pool_size}), so concurrent injected sessions are "
                f"capped at {_DB_SEMAPHORE_PERMITS} instead of "
                f"{db_cfg.pool_size}. Raise max_overflow to at least pool_size "
                "to use the whole base pool."
            )
    except Exception as e:
        runtime.logger.debug(f"Could not log pool configuration: {e}")


__all__ = [
    # Connection management
    "configure_database_engine",
    "dispose_engine",
    "async_session",
    "get_async_session",
    "init_db",
    # Views
    "Sample",
    # Models (dynamically included from models.__all__)
    *models.__all__,
]
