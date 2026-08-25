"""Access token validation."""

from datetime import datetime as dt
from datetime import timedelta, timezone

from sqlalchemy import or_, update

from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token.util import get_token_auth_context
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import (
    AgentCredentialRefusedException,
    InvalidTokenException,
)
from mascope_backend.api.new.auth.pairing.config import pairing_settings
from mascope_backend.api.new.auth.strategies.database import (
    get_database_strategy_context,
)
from mascope_backend.db import AgentDevice, async_session
from mascope_backend.runtime import runtime


# Seconds between last_seen_at writes for one device: "last seen" needs
# minute precision, so a busy agent (a resumable upload is one request per
# chunk) costs one write per window instead of one per request.
DEVICE_LAST_SEEN_THROTTLE_S = 60


def ensure_device_bound(service_name: str, device_id: int | None) -> None:
    """
    Refuse an unbound agent token when the deployment requires paired devices.

    Applies only to the pairable agent services; personal (mascope_sdk) and
    internal (file-converter) tokens have no device concept and pass through.

    :param service_name: The token's service scope.
    :type service_name: str
    :param device_id: The token's device binding, None when unbound.
    :type device_id: int | None
    :raises AgentCredentialRefusedException: The deployment requires paired
        devices and this agent token has none (issued before the device
        registry, or its device was removed).
    """
    if service_name not in pairing_settings.ALLOWED_SERVICES:
        return
    if device_id is None and runtime.config.require_device_tokens:
        raise AgentCredentialRefusedException(
            "This deployment accepts only paired agent credentials. "
            "Re-pair this machine from the agent's setup wizard to get a new token."
        )


def ensure_device_token_fresh(
    service_name: str, device_id: int | None, created_at: dt | None
) -> None:
    """
    Refuse a device-bound agent token that is older than its short lifetime.

    Device tokens live in plaintext on shared instrument PCs, so they expire
    far sooner than the 360-day database-strategy cap and the agent renews them
    automatically. Enforced only for device-bound tokens; unbound personal or
    internal tokens keep the default lifetime. A token past the limit is
    refused, not deleted - renewal (a fresh token) or re-pairing is the way
    back.

    :param service_name: The token's service scope.
    :param device_id: The token's device binding; None means not a device token.
    :param created_at: When the token was issued.
    :raises AgentCredentialRefusedException: The device token has outlived its
        lifetime.
    """
    if device_id is None or created_at is None:
        return
    lifetime = timedelta(
        seconds=auth_settings.access_token.DEVICE_TOKEN_LIFETIME_SECONDS
    )
    if dt.now(timezone.utc) - created_at > lifetime:
        raise AgentCredentialRefusedException(
            "This agent credential has expired. The agent renews its token "
            "automatically; if it has been offline past the token's lifetime, "
            "re-pair the machine from the agent's setup wizard."
        )


async def touch_device_last_seen(device_id: int) -> None:
    """
    Record that a device authenticated, at most once per throttle window.

    The throttle lives in the WHERE clause, so the common case is a no-op
    UPDATE matching zero rows.

    :param device_id: The authenticated device.
    :type device_id: int
    """
    now = dt.now(timezone.utc)
    cutoff = now - timedelta(seconds=DEVICE_LAST_SEEN_THROTTLE_S)
    async with async_session() as session:
        await session.execute(
            update(AgentDevice)
            .where(AgentDevice.device_id == device_id)
            .where(
                or_(
                    AgentDevice.last_seen_at.is_(None),
                    AgentDevice.last_seen_at < cutoff,
                )
            )
            .values(last_seen_at=now)
        )
        await session.commit()


async def validate_service_access_token(access_token: str, service_name: str):
    """
    Validate service access token and return associated user.

    :param access_token: Access token string for service
    :type access_token: str
    :param service_name: Expected service name
    :type service_name: str
    :return: User instance if token is valid
    :raises InvalidTokenException: If token is invalid or service mismatch
    """
    try:
        # Step 1. Basic token validation
        if not isinstance(access_token, str):
            raise InvalidTokenException("Invalid token format: not a string")

        # Step 1b. A validation this recent is reused rather than repeated.
        # An upload revalidates the same token once per chunk; see
        # access_token.cache for what that costs and what caching it trades
        # away (revocation becomes effective within the window, not at once).
        cached_user = token_cache.get(
            access_token, service_name, auth_settings.access_token
        )
        if cached_user is not None:
            return cached_user

        # Step 2. Token validation using access token strategy
        from mascope_backend.api.new.users.user_manager.util import (
            get_user_manager_context,
        )

        # One session for both adapters. They are used sequentially, never
        # concurrently, so a session each bought nothing and cost a second
        # connection held for the whole call. This path takes no admission
        # permit (see mascope_backend.db), so its concurrency is bounded only
        # by how many bearer requests are in flight - which is what exhausted
        # the pool under a bulk upload: every waiter then blocked for
        # pool_timeout and the worker stopped serving anything at all.
        async with async_session() as session:
            async with get_database_strategy_context(session) as database_strategy:
                async with get_user_manager_context(session) as user_manager:
                    user = await database_strategy.read_token(
                        access_token, user_manager
                    )
                    if not user:
                        raise InvalidTokenException(
                            "Token validation failed, no associated user found"
                        )

                    # Service scope, device binding and token age in one
                    # lookup, run on the session already open above. This used
                    # to call get_token_service as well, which re-read the same
                    # row twice more for values this query already returns; and
                    # it used to open its own session, which meant holding one
                    # connection while needing a second - the pattern
                    # mascope_backend.db documents as the worker deadlock, on a
                    # path that takes no permit. Sharing the session means this
                    # function never needs a connection it does not already
                    # hold. The exceptions raised are unchanged.
                    token_service, device_id, created_at = await get_token_auth_context(
                        access_token, session
                    )
                    if token_service != service_name:
                        raise InvalidTokenException(
                            f"The provided token is not authorized for {service_name}. Please try to refresh the token."
                        )

                    # Device policy for agent tokens (no-op for other services):
                    # the deployment's paired-device requirement and the short
                    # device-token lifetime.
                    ensure_device_bound(service_name, device_id)
                    ensure_device_token_fresh(service_name, device_id, created_at)

                    # Only successes are cached: tokens are minted on demand,
                    # so a cached rejection would refuse one that had just
                    # been created.
                    token_cache.put(
                        access_token, service_name, user, auth_settings.access_token
                    )
                    return user

    except InvalidTokenException as e:
        # Routine 401-class condition (expired/mismatched service token, cured
        # by a token refresh); the raised exception reports it upstream
        runtime.logger.info(f"User's service token validation failed: {str(e)}")
        raise
    except Exception as e:
        runtime.logger.error(f"Service token validation failed: {str(e)}")
        raise InvalidTokenException("Token validation failed") from e
