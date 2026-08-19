import json

from sqlalchemy import select, update

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.new.auth.access_token.validation import (
    validate_service_access_token,
)
from mascope_backend.api.new.auth.backend import auth_backend_access_token
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.api.new.auth.strategies.database import (
    get_database_strategy_context,
)
from mascope_backend.db import AccessToken, User, async_session
from mascope_backend.runtime import runtime


#: The service whose token the converter fetches to write back an upload's
#: results. Minted for a person at login; a machine account never logs in, so
#: it is minted at pairing and re-minted on demand here when missing/expired.
_FILE_CONVERTER_SERVICE = "file-converter"


@api_controller()
async def get_access_token(user: User, service_name: str) -> str:
    """
    Gets an access token for the specified user and service.
    Raises InvalidTokenException if token is missing or invalid.

    :param user: The authenticated user
    :type user: User
    :param service_name: Name of the service (e.g., "file-converter")
    :type service_name: str
    :return: The access token string if valid
    :rtype: str
    :raises: InvalidTokenException if token invalid/missing
    """
    async with async_session() as session:
        # Query existing token for the user and service. Multiple tokens can
        # exist for the pair (device pairing adds tokens without revoking);
        # use the newest instead of scalar_one_or_none, which would raise on
        # duplicates.
        token_query = await session.execute(
            select(AccessToken)
            .where(AccessToken.user_id == user.id)
            .where(AccessToken.service_name == service_name)
            .order_by(AccessToken.created_at.desc())
        )
        token = token_query.scalars().first()

    if token is not None:
        try:
            await validate_service_access_token(token.token, service_name)
            return token.token
        except InvalidTokenException:
            token = None  # expired/invalid; fall through to mint-or-raise

    # A machine account (instrument agent) never logs in, so its
    # file-converter token is never minted or refreshed by a sign-in. Mint it
    # on demand here so an upload is never refused for the want of one - at
    # first upload, and again if the token has lapsed.
    if (
        service_name == _FILE_CONVERTER_SERVICE
        and getattr(user, "account_type", None) == ACCOUNT_TYPE_MACHINE
    ):
        return await create_access_token(user=user, service_name=service_name)

    if token is None:
        raise InvalidTokenException(
            "You don't have access to this service. Please log in to Mascope again to refresh your access."
        )
    raise InvalidTokenException(
        "Your access to this service has expired. Please log in to Mascope again to refresh your access."
    )


@api_controller()
async def generate_access_token(user, service_name: str):
    """
    Generates an access token for the current authenticated user.

    This function uses the access token authentication backend to log the user in
    and return an access token, which is stored in the database.
    """
    async with get_database_strategy_context() as database_strategy:
        response = await auth_backend_access_token.login(database_strategy, user)
    # Decode the response body and extract the token
    data = json.loads(response.body.decode())
    token = data["access_token"]
    # Update token type
    async with async_session() as session:
        await session.execute(
            update(AccessToken)
            .where(AccessToken.token == token)
            .values(service_name=service_name)
        )
        await session.commit()

    runtime.logger.debug(
        f"{user.username} access token for {service_name} is generated"
    )
    return response


@api_controller()
async def create_access_token(
    user,
    service_name: str,
    description: str | None = None,
    device_id: int | None = None,
) -> str:
    """
    Creates a new access token WITHOUT removing the user's existing tokens
    for the service, so several agent machines can hold their own token
    (used by device pairing; the manual regenerate flow still replaces).

    :param user: The user the token belongs to
    :type user: User
    :param service_name: Name of the service (e.g., "file-agent")
    :type service_name: str
    :param description: Optional label stamped on the token (e.g. the
        paired machine's hostname)
    :type description: str, optional
    :param device_id: The paired machine holding this token, when the token
        is bound to a registered device
    :type device_id: int, optional
    :return: The raw token string
    :rtype: str
    """
    async with get_database_strategy_context() as database_strategy:
        token = await database_strategy.write_token(user)
    async with async_session() as session:
        await session.execute(
            update(AccessToken)
            .where(AccessToken.token == token)
            .values(
                service_name=service_name,
                description=description,
                device_id=device_id,
            )
        )
        await session.commit()
    runtime.logger.debug(
        f"{user.username} access token for {service_name} is created"
        + (f" ({description})" if description else "")
    )
    return token


@api_controller()
async def remove_access_tokens(user, service_name: str):
    """
    Removes access tokens for the specified service associated with the current authenticated user.

    This function retrieves access tokens linked to the user and then logs out
    each token using the access token authentication backend.
    """
    async with async_session() as session:
        # Query all access tokens associated with the user
        tokens_query = await session.execute(
            select(AccessToken)
            .where(AccessToken.user_id == user.id)
            .where(AccessToken.service_name == service_name)
        )
        tokens = tokens_query.scalars().all()

        if not tokens:
            return {"message": f"No access tokens found for user `{user.username}`."}

        # Use the backend logout to destroy each token
        async with get_database_strategy_context() as database_strategy:
            for token in tokens:
                await auth_backend_access_token.logout(
                    database_strategy, user, token.token
                )

    return {
        "message": f"All {service_name} access tokens for user {user.username} have been removed."
    }


@api_controller()
async def regenerate_access_token(user, service_name: str):
    """Remove existing tokens and generate new one."""
    await remove_access_tokens(user=user, service_name=service_name)
    return await generate_access_token(user=user, service_name=service_name)
