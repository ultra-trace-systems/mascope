from sqlalchemy import select

from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.db import AccessToken, async_session


async def validate_token_exists(token: str) -> bool:
    """
    Validate if the access token exists in the database.

    :param token: The access token string.
    :type token: str
    :return: True if the token exists, False otherwise.
    :rtype: bool
    """
    async with async_session() as session:
        result = await session.execute(
            select(AccessToken).where(AccessToken.token == token)
        )
        return result.scalar_one_or_none() is not None


async def get_service_name_for_token(token: str) -> str:
    """
    Retrieve the service name associated with an access token.

    :param token: The access token string.
    :type token: str
    :return: Service name if found.
    :rtype: str
    :raises InvalidTokenException: If the token is invalid or has no associated service name.
    """
    async with async_session() as session:
        result = await session.execute(
            select(AccessToken.service_name).where(AccessToken.token == token)
        )
        service_name = result.scalar_one_or_none()

        if not service_name:
            raise InvalidTokenException("No service name for the token.")

        return service_name


async def get_token_service(token: str) -> str:
    """
    Validate the existence of a token and retrieve its associated service name.

    :param token: The access token string.
    :type token: str
    :return: Service name if the token is valid and has an associated service.
    :rtype: str
    :raises InvalidTokenException: If the token is invalid or no service name exists.
    """
    if not await validate_token_exists(token):
        raise InvalidTokenException("Invalid access token.")

    return await get_service_name_for_token(token)


async def get_token_service_and_device(token: str) -> tuple[str, int | None]:
    """
    Retrieve a token's service name and device binding in one lookup.

    Used on the per-request bearer path, where the device binding is needed
    right next to the service name and a second query per request would be
    waste.

    :param token: The access token string.
    :type token: str
    :return: The service name and the bound device id (None when unbound).
    :rtype: tuple[str, int | None]
    :raises InvalidTokenException: If the token is invalid or has no service name.
    """
    async with async_session() as session:
        result = await session.execute(
            select(AccessToken.service_name, AccessToken.device_id).where(
                AccessToken.token == token
            )
        )
        row = result.one_or_none()

    if row is None:
        raise InvalidTokenException("Invalid access token.")
    service_name, device_id = row
    if not service_name:
        raise InvalidTokenException("No service name for the token.")

    return service_name, device_id


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
