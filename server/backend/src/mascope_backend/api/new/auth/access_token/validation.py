"""Access token validation."""

from datetime import datetime as dt
from datetime import timedelta, timezone

from sqlalchemy import or_, update

from mascope_backend.api.new.auth.access_token.util import (
    get_token_device_id,
    get_token_service,
)
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
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
    :raises InvalidTokenException: The deployment requires paired devices and
        this agent token has none (issued before the device registry, or its
        device was removed).
    """
    if service_name not in pairing_settings.ALLOWED_SERVICES:
        return
    if device_id is None and runtime.config.require_device_tokens:
        raise InvalidTokenException(
            "This deployment accepts only paired agent credentials. "
            "Re-pair this machine from the agent's setup wizard to get a new token."
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

        # Step 2. Token validation using access token strategy
        from mascope_backend.api.new.users.user_manager.util import (
            get_user_manager_context,
        )

        async with get_database_strategy_context() as database_strategy:
            async with get_user_manager_context() as user_manager:
                user = await database_strategy.read_token(access_token, user_manager)
                if not user:
                    raise InvalidTokenException(
                        "Token validation failed, no associated user found"
                    )

                # Verify service name
                token_service = await get_token_service(access_token)
                if token_service != service_name:
                    raise InvalidTokenException(
                        f"The provided token is not authorized for {service_name}. Please try to refresh the token."
                    )

                # Device policy for agent tokens (no-op for other services)
                ensure_device_bound(
                    service_name, await get_token_device_id(access_token)
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
