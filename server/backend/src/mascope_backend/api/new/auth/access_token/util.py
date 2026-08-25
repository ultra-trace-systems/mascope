from sqlalchemy import select

from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.db import AccessToken, async_session


async def get_token_auth_context(token: str):
    """
    Retrieve a token's service name, device binding, and age in one lookup.

    Used on the per-request bearer path, where the service name, the device
    binding (strict-mode gate) and the creation time (device-token freshness)
    are all needed together and a query per property would be waste.

    :param token: The access token string.
    :type token: str
    :return: ``(service_name, device_id, created_at)``; device_id is None when
        the token is unbound.
    :rtype: tuple[str, int | None, datetime]
    :raises InvalidTokenException: If the token is invalid or has no service name.
    """
    async with async_session() as session:
        result = await session.execute(
            select(
                AccessToken.service_name,
                AccessToken.device_id,
                AccessToken.created_at,
            ).where(AccessToken.token == token)
        )
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
