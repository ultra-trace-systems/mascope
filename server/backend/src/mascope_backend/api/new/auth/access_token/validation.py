"""Access token validation."""

from mascope_backend.api.new.auth.access_token.util import get_token_service
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.api.new.auth.strategies.database import (
    get_database_strategy_context,
)
from mascope_backend.db import async_session
from mascope_backend.runtime import runtime


async def validate_service_access_token(access_token: str, service_name: str):
    """
    Validate service access token and return associated user.

    Every request from an agent, the file converter or the SDK runs this first,
    and it takes no admission-control permit - the semaphore in
    :mod:`mascope_backend.db` guards the injected-session path only. Its
    per-call connection cost is therefore multiplied by however many bearer
    requests are in flight, with nothing to cap it, which is what let a bulk
    upload saturate a worker's pool and stall it for ``pool_timeout``.

    So it holds exactly one session. The strategy and the user manager are used
    sequentially, never concurrently, so a session each bought nothing but a
    second held connection; and the service-name lookup runs on that same
    session rather than checking out a third from inside the other two - a
    caller that holds a connection and then needs another can block on one only
    it could release.

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

                    # Verify service name
                    token_service = await get_token_service(access_token, session)
                    if token_service != service_name:
                        raise InvalidTokenException(
                            f"The provided token is not authorized for {service_name}. Please try to refresh the token."
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
