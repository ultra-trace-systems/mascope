from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.db import AccessToken, async_session


async def _read_token_row(token: str, session: AsyncSession):
    """Read the one row a token lookup needs, in a single query.

    Selects the token column alongside the service name so a missing row stays
    distinguishable from a row whose service name is NULL - the two raise
    different exceptions, and callers are told to do different things about
    them.

    :param token: The access token string.
    :type token: str
    :param session: Session to run the query on.
    :type session: AsyncSession
    :return: The (token, service_name) row, or None when no such token exists.
    """
    result = await session.execute(
        select(AccessToken.token, AccessToken.service_name).where(
            AccessToken.token == token
        )
    )
    return result.one_or_none()


async def get_token_service(token: str, session: AsyncSession | None = None) -> str:
    """
    Validate the existence of a token and retrieve its associated service name.

    Pass ``session`` to run on a session the caller already holds. Opening one
    here while the caller holds another means a nested checkout: the caller can
    then block waiting for a connection only it could release, which is how a
    worker's pool deadlocks under a bulk upload. See the note in
    :mod:`mascope_backend.db`.

    This used to run two queries against the same row - an existence check and
    a service-name query - each opening its own session.

    :param token: The access token string.
    :type token: str
    :param session: Existing session to query on; when None one is opened here.
    :type session: AsyncSession | None
    :return: Service name if the token is valid and has an associated service.
    :rtype: str
    :raises InvalidTokenException: If the token is invalid or no service name exists.
    """
    if session is not None:
        row = await _read_token_row(token, session)
    else:
        async with async_session() as own_session:
            row = await _read_token_row(token, own_session)

    if row is None:
        raise InvalidTokenException("Invalid access token.")

    service_name = row.service_name
    if not service_name:
        raise InvalidTokenException("No service name for the token.")

    return service_name
