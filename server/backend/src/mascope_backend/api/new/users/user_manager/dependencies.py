"""
FastAPI dependency injection module for user management.

This module provides dependency functions specifically designed for FastAPI's
dependency injection system. These dependencies are used in route declarations
to provide user management capabilities through FastAPI Users library.
"""

from fastapi import Depends
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.db import User, get_async_session


async def get_user_db(
    session: AsyncSession = Depends(get_async_session, scope="function"),
) -> SQLAlchemyUserDatabase:
    """
    Dependency function to provide a `SQLAlchemyUserDatabase` instance for FastAPI Users.

    This function is used to inject a database adapter for user management within the FastAPI Users library.
    It works by utilizing the injected SQLAlchemy session (retrieved via `get_async_session`) and the `User` model,
    returning a `SQLAlchemyUserDatabase` object. This adapter is then used by FastAPI Users to handle operations
    such as user creation, querying, and updating.

    The session is function-scoped: it closes, and gives back its admission
    permit, when the endpoint returns, not after the request's background
    tasks have run. A launch route hands its work to a background task that
    can run for minutes; a request-scoped session would hold the permit all
    that time (see ``get_async_session``). This dependency returns rather than
    yields for the same reason: FastAPI refuses a request-scoped ``yield``
    dependency that depends on a function-scoped one, and a ``yield``
    dependency is request-scoped unless it says otherwise.

    :param session: An active SQLAlchemy async session, injected via dependency, defaults to Depends(get_async_session)
    :type session: AsyncSession, optional
    :return: A SQLAlchemyUserDatabase instance for interacting with the User model.
    :rtype: SQLAlchemyUserDatabase
    """
    return SQLAlchemyUserDatabase(session, User)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> UserManager:
    """
    Dependency to retrieve the UserManager.
    Injects the user database session.

    Returns rather than yields, as ``get_user_db`` does and for its reason.

    :param user_db: SQLAlchemyUserDatabase instance for handling user database operations,
                    defaults to Depends(get_user_db).
    :return: An instance of `UserManager` for managing user operations.
    :rtype: UserManager
    """
    return UserManager(user_db)
