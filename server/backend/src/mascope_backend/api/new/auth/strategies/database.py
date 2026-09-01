from contextlib import asynccontextmanager

from fastapi import Depends
from fastapi_users.authentication.strategy.db import (
    AccessTokenDatabase,
    DatabaseStrategy,
)
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.db import AccessToken, async_session, get_async_session


async def get_access_token_db(session: AsyncSession = Depends(get_async_session)):
    """
    Provides a database adapter for access tokens, allowing retrieval, creation, and deletion
    of access tokens associated with users.

    This adapter is used by the authentication strategy for managing API key-based authentication.

    :param session: SQLAlchemy async session, injected via dependency.
    :return: Access token database adapter for interacting with AccessToken table.
    """
    yield SQLAlchemyAccessTokenDatabase(session, AccessToken)


# Database strategy for access token authentication (access token stored in DB)
def get_database_strategy(
    access_token_db: AccessTokenDatabase[AccessToken] = Depends(get_access_token_db),
) -> DatabaseStrategy:
    """
    Returns a DatabaseStrategy for access token authentication.

    This strategy validates access token stored in the database, associating each key with a user ID.
    Tokens expire after the defined lifetime.
    """
    return DatabaseStrategy(
        access_token_db,
        lifetime_seconds=auth_settings.access_token.ACCESS_TOKEN_EXPIRATION_SECONDS,
    )


@asynccontextmanager
async def get_access_token_db_context(session: AsyncSession | None = None):
    """
    Context manager for access token database operations outside of HTTP requests.

    Used in scenarios like Socket.IO authentication where FastAPI's
    dependency injection is not available.

    Pass ``session`` to reuse a session the caller already holds. A caller that
    also builds a user manager needs both adapters at once, and opening one
    session each means two connections held for the length of the call - on a
    path that takes no admission-control permit (see ``mascope_backend.db``),
    so nothing bounds how many requests do that concurrently.

    Sharing has a price the caller owns: everything on one session is one
    transaction, so a commit from any of them would commit whatever the others
    have pending. The one caller that shares today puts three readers on it -
    this adapter, the user manager's, and the token-context lookup - and none
    of them writes.

    :param session: Existing session to bind the adapter to; when None the
        context manages its own.
    :type session: AsyncSession | None
    :yield: Database adapter for access token operations
    :rtype: SQLAlchemyAccessTokenDatabase
    """
    if session is not None:
        yield SQLAlchemyAccessTokenDatabase(session, AccessToken)
        return
    async with async_session() as own_session:
        yield SQLAlchemyAccessTokenDatabase(own_session, AccessToken)


@asynccontextmanager
async def get_database_strategy_context(session: AsyncSession | None = None):
    """
    Context manager for database strategy outside of HTTP requests.

    Provides access token validation capabilities in non-HTTP contexts
    like Socket.IO authentication.

    :param session: Existing session to bind the strategy's adapter to; see
        :func:`get_access_token_db_context`.
    :type session: AsyncSession | None
    :yield: Database strategy instance
    :rtype: DatabaseStrategy
    """
    async with get_access_token_db_context(session) as access_token_db:
        strategy = DatabaseStrategy(
            access_token_db,
            lifetime_seconds=auth_settings.access_token.ACCESS_TOKEN_EXPIRATION_SECONDS,
        )
        yield strategy
