import json
from datetime import datetime as dt
from datetime import timezone

from sqlalchemy import select, update

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.new.auth.backend import auth_backend_access_token
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.api.new.auth.strategies.database import (
    get_database_strategy_context,
)
from mascope_backend.db import AccessToken, User, async_session, hash_access_token
from mascope_backend.runtime import runtime


#: The service whose token the converter writes an upload's results back
#: with. Minted for each request that hands the converter work, by
#: ``get_access_token``: the server keeps only a digest of a token, so it has
#: none it could hand out again.
_FILE_CONVERTER_SERVICE = "file-converter"


def _age_seconds(token: AccessToken) -> float:
    """How long ago the token was minted."""
    created_at = token.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (dt.now(timezone.utc) - created_at).total_seconds()


def may_hold_converter_token(user: User) -> bool:
    """Whether the file converter's token is this account's to be given.

    An account whose own role is editor or above, whatever role it holds in
    a workspace: the two layers are independent (``docs/authorization.md``).
    The rule :func:`get_access_token` refuses the token by, for a caller
    that has to know before it asks. The refusal itself is no way to find
    out: :func:`get_access_token` is a controller, so what it raises reaches
    its caller as the API's own exception, like any fault inside it.

    :param user: The account.
    :type user: User
    :return: True where the account's role is editor or above.
    :rtype: bool
    """
    editor = auth_settings.ROLE_ACCESS_LEVELS["editor"]
    return user.role_id is not None and user.role_id >= editor


@api_controller()
async def get_access_token(user: User, service_name: str) -> str:
    """A file-converter token for the work this request hands the converter.

    Minted here, for this request: the server keeps only a digest of each
    token, so the one a user was given before cannot be handed out again.
    Each upload or peak request takes one (a bulk upload shares its one
    across its files), the converter carries it with the work and writes the
    results back on it, and it lapses after
    ``FILE_CONVERTER_TOKEN_LIFETIME_SECONDS``, which outlasts any queue. The
    user's converter tokens already past that are reaped by the mint, so the
    rows stay bounded. The request asking is authenticated as the user
    already, so an editor's upload is never refused for the want of a token.
    A guest is refused: authenticated is not allowed to upload, and the
    refusal here is what keeps a guest's upload out of the converter, since
    the upload routes pass anyone for an instrument the server has no
    workspace for.

    No other service's token is handed out by the server: those are shown
    once, to the person or agent they are minted for.

    :param user: The authenticated user
    :type user: User
    :param service_name: Name of the service; only "file-converter"
    :type service_name: str
    :return: The access token string
    :rtype: str
    :raises: InvalidTokenException for a guest, and for any other service
    """
    if service_name != _FILE_CONVERTER_SERVICE:
        raise InvalidTokenException(
            f"The server keeps no {service_name} token it could hand out. "
            "Regenerate the token from your profile."
        )
    if not may_hold_converter_token(user):
        raise InvalidTokenException(
            "You don't have access to this service. Uploading needs the "
            "editor role or above."
        )
    lifetime = auth_settings.access_token.lifetime_for(service_name)
    await remove_access_tokens(
        user=user, service_name=service_name, older_than_seconds=lifetime
    )
    return await create_access_token(user=user, service_name=service_name)


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
            .where(AccessToken.token_hash == hash_access_token(token))
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
            .where(AccessToken.token_hash == hash_access_token(token))
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
async def remove_access_tokens(
    user, service_name: str, *, older_than_seconds: float | None = None
):
    """
    Removes access tokens for the specified service associated with the current authenticated user.

    The rows are deleted directly: only their digests are stored, so there is
    no token to log out through the auth backend, and logging out a database
    token does nothing but delete its row.

    :param older_than_seconds: Remove only the tokens minted at least this
        long ago - the rows past their lifetime, for a mint that must not
        take a token another request was handed a moment ago; None removes
        every token of the service
    :type older_than_seconds: float, optional
    """
    async with async_session() as session:
        # Query all access tokens associated with the user
        tokens_query = await session.execute(
            select(AccessToken)
            .where(AccessToken.user_id == user.id)
            .where(AccessToken.service_name == service_name)
        )
        tokens = tokens_query.scalars().all()
        if older_than_seconds is not None:
            tokens = [t for t in tokens if _age_seconds(t) >= older_than_seconds]

        if not tokens:
            return {"message": f"No access tokens found for user `{user.username}`."}

        for token in tokens:
            await session.delete(token)
        await session.commit()

    return {
        "message": f"All {service_name} access tokens for user {user.username} have been removed."
    }


@api_controller()
async def regenerate_access_token(user, service_name: str):
    """Remove existing tokens and generate new one."""
    await remove_access_tokens(user=user, service_name=service_name)
    return await generate_access_token(user=user, service_name=service_name)
