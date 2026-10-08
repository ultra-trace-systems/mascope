import json
from datetime import datetime as dt
from datetime import timezone

from sqlalchemy import select, update

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.new.auth.access_token.validation import (
    validate_service_access_token,
)
from mascope_backend.api.new.auth.backend import auth_backend_access_token
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.api.new.auth.strategies.database import (
    get_database_strategy_context,
)
from mascope_backend.db import AccessToken, User, async_session
from mascope_backend.runtime import runtime


#: The service whose token the converter fetches to write back an upload's
#: results. Kept, renewed and minted by ``ensure_access_token``, which a
#: sign-in asks and an upload asks through ``get_access_token``: a machine
#: account never signs in, so the upload is where its token comes from, and
#: a person's token serves whoever uploads on it between their sign-ins.
_FILE_CONVERTER_SERVICE = "file-converter"


async def _newest_access_token(user: User, service_name: str) -> AccessToken | None:
    """The user's newest token for the service, or None.

    Multiple tokens can exist for the pair (device pairing adds tokens
    without revoking, and a renewal leaves the old one beside the new), so
    the newest is taken rather than scalar_one_or_none, which would raise on
    duplicates. Newest first is what hands out a renewed token over the one
    it supersedes.
    """
    async with async_session() as session:
        token_query = await session.execute(
            select(AccessToken)
            .where(AccessToken.user_id == user.id)
            .where(AccessToken.service_name == service_name)
            .order_by(AccessToken.created_at.desc())
        )
        return token_query.scalars().first()


async def _valid_access_token(user: User, service_name: str) -> tuple[str | None, bool]:
    """The user's newest token for the service if it still validates.

    Not a controller: the callers decide what a missing token means, and the
    controller wrapper would hand them a bare ``ApiException`` instead of the
    ``InvalidTokenException`` the validation raises.

    :return: The token string, or None where the user holds none that
        validates; and whether a token was found at all, which decides between
        "expired" and "no access" for a caller that refuses
    :rtype: tuple[str | None, bool]
    """
    token = await _newest_access_token(user, service_name)
    if token is None:
        return None, False
    try:
        await validate_service_access_token(token.token, service_name)
    except InvalidTokenException:
        return None, True  # expired/invalid
    return token.token, True


def _age_seconds(token: AccessToken) -> float:
    """How long ago the token was minted."""
    created_at = token.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (dt.now(timezone.utc) - created_at).total_seconds()


@api_controller()
async def get_access_token(user: User, service_name: str) -> str:
    """The user's access token for the service.

    For the file-converter service, what ``ensure_access_token`` answers: the
    token the user holds while it is valid, a new one beside it near its end,
    one minted where they hold none - for an editor or above, as the sign-in
    mints. The request asking is authenticated as the user already, so an
    editor's upload is never refused for the want of a token - a machine
    account's first upload (a machine account sits at editor), a token that
    lapsed, a script or an agent uploading on a person's token whose owner
    has not signed in since it neared its end. A sign-in does the same, as a
    convenience. A guest is refused, as the want of a token refused them
    before: authenticated is not allowed to upload, and the refusal here is
    what keeps a guest's upload out of the converter, since the upload
    routes pass anyone for an instrument the server has no workspace for.

    For any other service the token is handed back only while it validates,
    and the user is refused where it is missing or no longer does.

    :param user: The authenticated user
    :type user: User
    :param service_name: Name of the service (e.g., "file-converter")
    :type service_name: str
    :return: The access token string
    :rtype: str
    :raises: InvalidTokenException for a guest, and where another service's
        token is invalid or missing
    """
    if service_name == _FILE_CONVERTER_SERVICE:
        editor = auth_settings.ROLE_ACCESS_LEVELS["editor"]
        if user.role_id is None or user.role_id < editor:
            raise InvalidTokenException(
                "You don't have access to this service. Uploading needs the "
                "editor role or above."
            )
        return await ensure_access_token(user=user, service_name=service_name)

    token, had_token = await _valid_access_token(user, service_name)
    if token is not None:
        return token

    # Which of the two is decided by whether a token was found at all, not by
    # `token`, which is None for an expired one too - reading it here would
    # report every expired token as "no access" and leave the expiry message
    # unreachable.
    if had_token:
        raise InvalidTokenException(
            "Your access to this service has expired. Please log in to Mascope again to refresh your access."
        )
    raise InvalidTokenException(
        "You don't have access to this service. Please log in to Mascope again to refresh your access."
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
async def remove_access_tokens(
    user, service_name: str, *, older_than_seconds: float | None = None
):
    """
    Removes access tokens for the specified service associated with the current authenticated user.

    This function retrieves access tokens linked to the user and then logs out
    each token using the access token authentication backend.

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


@api_controller()
async def ensure_access_token(user, service_name: str) -> str:
    """The user's token for the service: kept while it is valid, renewed
    beside itself towards the end of its life, minted where there is none.

    A sign-in asks this for the file-converter token, and so does an upload,
    through ``get_access_token``, so the token does not depend on its owner
    signing in: a script or an agent uploading on a person's token, and a
    machine account, which never signs in, renew it by uploading. A valid
    token is kept, because every upload of the user's that the converter has
    not reached yet carries the token it was uploaded with, and a sign-in
    that replaced it - another tab, another device, a session that expired -
    failed each of them when the converter handed its result back. Within
    ``FILE_CONVERTER_TOKEN_RENEWAL_SECONDS`` of its end a new token is minted
    beside it and the old one left to lapse on its own: new uploads take the
    newest, the queued ones keep validating on the one they carry. Where the
    user holds none that is valid - a first sign-in, a token past its
    lifetime, tokens an administrator removed - the rows past their lifetime
    are removed and a new one minted. Only those: two requests that both
    found none mint in turn, and the second's removal must not take the
    token the first was handed a moment ago, on its way to the converter
    with a file.

    Decided from the row's age alone, not through the validator: the
    validator reports a read it could not make as "invalid", and for a
    sign-in "could not check" has to mean "leave it alone". A read that
    fails here raises: the sign-in hook logs it and keeps the token, and an
    upload is refused with the failure rather than handed a token it cannot
    be sure of.

    :param user: The authenticated user
    :param service_name: Name of the service (e.g., "file-converter")
    :return: The token string the user now holds for the service
    :rtype: str
    """
    settings = auth_settings.access_token
    lifetime = settings.ACCESS_TOKEN_EXPIRATION_SECONDS
    token = await _newest_access_token(user, service_name)
    if token is None or _age_seconds(token) >= lifetime:
        await remove_access_tokens(
            user=user, service_name=service_name, older_than_seconds=lifetime
        )
        return await create_access_token(user=user, service_name=service_name)
    if lifetime - _age_seconds(token) <= settings.FILE_CONVERTER_TOKEN_RENEWAL_SECONDS:
        return await create_access_token(user=user, service_name=service_name)
    return token.token
