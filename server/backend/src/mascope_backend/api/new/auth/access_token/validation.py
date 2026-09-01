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

    This runs on every Socket.IO service-token path - the connection handshake
    and each file-converter event, so once per progress report during a
    conversion - and on the liveness probe in ``access_token.service`` that
    every upload takes before it stores anything. None of them holds an
    admission-control permit: the semaphore in :mod:`mascope_backend.db` guards
    the injected-session path only, so nothing bounds how many of these run at
    once. Its per-call connection cost is what decided whether a bulk upload
    saturated a worker's pool and stalled it for ``pool_timeout``.

    The HTTP bearer path does not come through here. It is authenticated by
    ``get_enabled_backends``, which calls :func:`get_token_service` directly -
    also without a permit, and also once per request, so a resumable upload
    pays it once per chunk. That is why the query collapse in that function
    matters on its own.

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
