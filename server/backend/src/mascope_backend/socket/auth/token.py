"""Socket-specific JWT token validation and extraction."""

import re

from mascope_backend.api.new.auth.backend import auth_backend_jwt
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.runtime import runtime
from mascope_backend.socket.auth.exceptions import SocketUnauthenticatedError


# JWT token pattern (header.payload.signature)
JWT_PATTERN = re.compile(r"^[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+$")


async def get_jwt_from_cookies(cookies: str) -> str:
    """
    Extract authentication token from cookie string.

    :param cookies: Raw cookie string from HTTP headers
    :type cookies: str
    :raises HTTPException: If auth cookie is not found
    :return: JWT token string
    :rtype: str
    """
    try:
        cookie_dict = {
            c.split("=")[0].strip(): c.split("=")[1].strip() for c in cookies.split(";")
        }
    except Exception as e:
        raise SocketUnauthenticatedError("Invalid cookie format") from e

    jwt_token = cookie_dict.get(auth_settings.COOKIE_NAME)
    if not jwt_token:
        raise SocketUnauthenticatedError("Authentication cookie not found")

    return jwt_token


async def validate_jwt_token(jwt_token: str):
    """
    Validate JWT token and return associated user and the token's issue time.

    Verifies the provided JWT token using the configured authentication strategy
    and retrieves the associated user from the database. A token whose session
    has been revoked is refused here as it is on the REST API.

    :param jwt_token: JWT token string from client cookie
    :type jwt_token: str
    :return: ``(user, iat)``; ``iat`` is ``None`` for a token minted before
        tokens carried one
    :rtype: tuple[User, Optional[int]]
    """
    try:
        # Step 1. Basic token format validation
        if not isinstance(jwt_token, str):
            raise SocketUnauthenticatedError("Invalid token format: not a string")
        if not JWT_PATTERN.match(jwt_token):
            raise SocketUnauthenticatedError("Invalid token format: not a JWT token")

        # Step 2. Token validation using cookie strategy
        # TODO Late import to avoid circular dependencies
        from mascope_backend.api.new.users.user_manager.util import (
            get_user_manager_context,
        )

        jwt_strategy = auth_backend_jwt.get_strategy()

        async with get_user_manager_context() as user_manager:
            session = await jwt_strategy.read_session(jwt_token, user_manager)
            if not session:
                raise SocketUnauthenticatedError(
                    "Token validation failed: user not found or session revoked"
                )
            return session
    except SocketUnauthenticatedError:
        raise
    except Exception as e:
        # logger.exception: the raised SocketUnauthenticatedError is handled
        # as a routine rejection upstream, so this is the only record of the
        # unexpected underlying failure
        runtime.logger.exception("Token validation failed")
        raise SocketUnauthenticatedError("Token validation failed") from e
