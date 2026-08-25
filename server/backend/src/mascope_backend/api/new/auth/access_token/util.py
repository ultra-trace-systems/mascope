from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.db import AccessToken, async_session


def _token_auth_context_query(token: str):
    return select(
        AccessToken.service_name,
        AccessToken.device_id,
        AccessToken.created_at,
    ).where(AccessToken.token == token)


async def get_token_auth_context(token: str, session: AsyncSession | None = None):
    """
    Retrieve a token's service name, device binding, and age in one lookup.

    Used on the per-request bearer path, where the service name, the device
    binding (strict-mode gate) and the creation time (device-token freshness)
    are all needed together and a query per property would be waste.

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


async def get_token_device_id(token: str) -> int | None:
    """
    Retrieve the device binding of an access token.

    :param token: The access token string.
    :type token: str
    :return: The bound device id, or None when the token is unbound or unknown.
    :rtype: int | None
    """
    async with async_session() as session:
        result = await session.execute(
            select(AccessToken.device_id).where(AccessToken.token == token)
        )
        return result.scalar_one_or_none()
