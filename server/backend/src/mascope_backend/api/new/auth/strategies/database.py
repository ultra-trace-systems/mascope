import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends
from fastapi_users import exceptions
from fastapi_users.authentication.strategy.db import (
    AccessTokenDatabase,
    DatabaseStrategy,
)
from fastapi_users.manager import BaseUserManager
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.db import (
    AccessToken,
    User,
    async_session,
    get_async_session,
    hash_access_token,
)


class HashedAccessTokenDatabase(SQLAlchemyAccessTokenDatabase[AccessToken]):
    """
    The access-token adapter, over a table that keeps only token digests.

    fastapi-users looks a token up by the value its holder presents; this
    looks it up by that value's digest, so the rest of the strategy runs
    unchanged on a table with no token in it.
    """

    async def get_by_token(
        self, token: str, max_age: Optional[datetime] = None
    ) -> Optional[AccessToken]:
        """
        The row the presented token was minted as, or None.

        :param token: The token as its holder presents it.
        :param max_age: Refuse a row minted before this.
        :return: The row, or None where no live row has the token's digest.
        """
        digest = hash_access_token(token)
        statement = select(AccessToken).where(AccessToken.token_hash == digest)
        if max_age is not None:
            statement = statement.where(AccessToken.created_at >= max_age)
        # The lookup compares digests, not the token: how much of a presented
        # token matches a stored one says nothing about the digest it hashes to.
        return (await self.session.execute(statement)).scalar_one_or_none()


class HashedDatabaseStrategy(DatabaseStrategy[User, int, AccessToken]):
    """
    The bearer-token strategy: mints a token, stores its digest, hands the
    token back once.

    Holds each token to its own service's lifetime
    (``AccessTokenConfig.lifetime_for``): the default for most, the file
    converter's shorter one for its tokens. A token past it is refused as
    fastapi-users refuses any expired token.
    """

    def __init__(self, database: AccessTokenDatabase[AccessToken]):
        # No strategy-wide lifetime: read_token holds each token to its
        # service's, so the base class's lifetime_seconds is left unset.
        super().__init__(database, lifetime_seconds=None)

    async def read_token(
        self, token: Optional[str], user_manager: BaseUserManager[User, int]
    ) -> Optional[User]:
        if token is None:
            return None

        access_token = await self.database.get_by_token(token)
        if access_token is None:
            return None
        lifetime = auth_settings.access_token.lifetime_for(access_token.service_name)
        if access_token.created_at < datetime.now(timezone.utc) - timedelta(
            seconds=lifetime
        ):
            return None

        try:
            parsed_id = user_manager.parse_id(access_token.user_id)
            return await user_manager.get(parsed_id)
        except (exceptions.UserNotExists, exceptions.InvalidID):
            return None

    async def write_token(self, user: User) -> str:
        """
        Mint a token for the user and store its digest.

        :return: The token itself, which nothing stores: the caller shows or
            hands it on once.
        """
        token = secrets.token_urlsafe()
        await self.database.create(
            {"token_hash": hash_access_token(token), "user_id": user.id}
        )
        return token


async def get_access_token_db(session: AsyncSession = Depends(get_async_session)):
    """
    Provides a database adapter for access tokens, allowing retrieval, creation, and deletion
    of access tokens associated with users.

    This adapter is used by the authentication strategy for managing API key-based authentication.

    :param session: SQLAlchemy async session, injected via dependency.
    :return: Access token database adapter for interacting with AccessToken table.
    """
    yield HashedAccessTokenDatabase(session, AccessToken)


# Database strategy for access token authentication (access token stored in DB)
def get_database_strategy(
    access_token_db: AccessTokenDatabase[AccessToken] = Depends(get_access_token_db),
) -> HashedDatabaseStrategy:
    """
    Returns a DatabaseStrategy for access token authentication.

    This strategy validates access token stored in the database, associating each key with a user ID.
    Tokens expire after their own service's lifetime (``read_token``).
    """
    return HashedDatabaseStrategy(access_token_db)


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
    :rtype: HashedAccessTokenDatabase
    """
    if session is not None:
        yield HashedAccessTokenDatabase(session, AccessToken)
        return
    async with async_session() as own_session:
        yield HashedAccessTokenDatabase(own_session, AccessToken)


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
    :rtype: HashedDatabaseStrategy
    """
    async with get_access_token_db_context(session) as access_token_db:
        yield HashedDatabaseStrategy(access_token_db)
