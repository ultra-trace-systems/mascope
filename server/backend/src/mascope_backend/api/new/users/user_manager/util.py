"""
Context managers for user management outside of FastAPI request context.

This module provides async context managers for accessing user management
functionality in non-HTTP contexts (e.g., Socket.IO authentication,
CLI commands). Unlike dependencies.py which is used for HTTP routes,
these utilities manage their own database sessions.
"""

from contextlib import asynccontextmanager

from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.db import User, async_session


@asynccontextmanager
async def get_user_db_context(session: AsyncSession | None = None):
    """
    Context manager for user database access outside of HTTP requests.

    Used in scenarios like Socket.IO authentication
    where FastAPI's dependency injection is not available.

    Pass ``session`` to reuse a session the caller already holds, rather than
    holding a second connection alongside it for the length of the call.

    :param session: Existing session to bind the adapter to; when None the
        context manages its own.
    :type session: AsyncSession | None
    :yield: Database adapter for user operations
    :rtype: SQLAlchemyUserDatabase
    """
    if session is not None:
        yield SQLAlchemyUserDatabase(session, User)
        return
    async with async_session() as own_session:
        yield SQLAlchemyUserDatabase(own_session, User)


@asynccontextmanager
async def get_user_manager_context(session: AsyncSession | None = None):
    """
    Context manager for user management outside of HTTP requests.

    Provides user management capabilities in non-HTTP contexts like
    Socket.IO authentication or scheduled tasks.

    :param session: Existing session to bind the manager's adapter to; see
        :func:`get_user_db_context`.
    :type session: AsyncSession | None
    :yield: User manager instance
    :rtype: UserManager
    """
    async with get_user_db_context(session) as user_db:
        user_manager = UserManager(user_db)
        yield user_manager
