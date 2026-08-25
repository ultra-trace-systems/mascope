from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token.config import AccessTokenConfig
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.db import AccessToken, async_session


def _token_auth_context_query(token: str):
    return select(
        AccessToken.service_name,
        AccessToken.device_id,
        AccessToken.created_at,
    ).where(AccessToken.token == token)


async def get_token_auth_context(
    token: str, session: AsyncSession | None = None
) -> tuple[str, int | None, datetime]:
    """
    Retrieve a token's service name, device binding, and age in one lookup.

    The uncached read. Callers on a request path want
    :func:`resolve_token_context` instead; this is what that falls back to.

    Pass ``session`` when the caller already holds one. Opening a second
    session while the caller's first is still checked out is the
    hold-one-need-another pattern that ``mascope_backend.db`` documents as the
    worker-deadlock mechanism: this path takes no admission-control permit, so
    enough concurrent callers can each hold a connection and then block
    forever waiting for one that only they could release. Running the query on
    the caller's session means no caller of this function ever needs a second
    connection.

    :param token: The access token string.
    :type token: str
    :param session: Existing session to run the lookup on; when None this
        opens (and releases) its own.
    :type session: AsyncSession | None
    :return: ``(service_name, device_id, created_at)``; device_id is None when
        the token is unbound.
    :rtype: tuple[str, int | None, datetime]
    :raises InvalidTokenException: If the token is invalid or has no service name.
    """
    if session is not None:
        result = await session.execute(_token_auth_context_query(token))
        row = result.one_or_none()
    else:
        # Own the session: read, then release it before raising, so a
        # rejection does not hold a connection while the exception unwinds.
        async with async_session() as own_session:
            result = await own_session.execute(_token_auth_context_query(token))
            row = result.one_or_none()

    if row is None:
        raise InvalidTokenException("Invalid access token.")
    service_name, device_id, created_at = row
    if not service_name:
        raise InvalidTokenException("No service name for the token.")

    return service_name, device_id, created_at


async def resolve_token_context(
    token: str,
    config: AccessTokenConfig,
    session: AsyncSession | None = None,
) -> tuple[str, int | None, datetime]:
    """
    A token's ``(service_name, device_id, created_at)``, read at most once
    per cache window.

    The one place this lookup and its caching policy live. Both authenticated
    paths need the same three columns - ``get_enabled_backends`` for HTTP
    bearer requests and ``validate_service_access_token`` for Socket.IO events
    - and each had grown its own copy of "look it up, cache it, use the
    tuple". Two implementations of one policy is how the caching rule, the
    namespace and the fallback drift apart, and how a future invalidation hook
    ends up wired into one of them.

    Caching this is safe in a way caching a validated *user* is not: the three
    columns are written once, when the token is minted, and no request path
    updates them afterwards. The only writers are the labelling UPDATEs in
    ``access_token.service``, which run on a token that has not been handed out
    yet. So an entry cannot go stale - it can only outlive the row, and every
    caller re-checks what the row's existence actually buys (see
    :func:`mascope_backend.api.new.auth.access_token.cache.get_auth_context`).

    The one writer outside a request is the demo seeder, which re-inserts its
    fixed token strings with a new ``created_at``. A cached entry there holds
    the older timestamp, which is the strict direction - it can only refuse a
    token sooner, never keep a dead one alive - and demo tokens are unbound, so
    the freshness rule does not look at it at all.

    Only successes are cached, which is what keeps the mint-then-label window
    harmless: a row read between the INSERT and the UPDATE has no service name,
    is rejected, and the rejection is not remembered.

    ``session`` must not carry uncommitted writes to ``access_token``: the
    value read on it is stored in a process-global cache, so a transaction that
    then rolled back would leave this worker holding a context for a row that
    never existed.

    :param token: The access token string.
    :type token: str
    :param config: Access-token settings carrying the cache TTL.
    :type config: AccessTokenConfig
    :param session: Existing session to run the lookup on when the cache
        misses; when None the lookup opens (and releases) its own.
    :type session: AsyncSession | None
    :return: ``(service_name, device_id, created_at)``; device_id is None when
        the token is unbound.
    :rtype: tuple[str, int | None, datetime]
    :raises InvalidTokenException: If the token is invalid or has no service name.
    """
    context = token_cache.get_auth_context(token, config)
    if context is not None:
        return context
    # Stored only on a miss. Refreshing on a hit would re-stamp the expiry on
    # every request, so a token in continuous use would never fall out of the
    # cache and the window would stop being a bound on anything.
    context = await get_token_auth_context(token, session)
    token_cache.put_auth_context(token, context, config)
    return context
