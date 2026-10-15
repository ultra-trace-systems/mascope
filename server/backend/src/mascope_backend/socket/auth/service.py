"""Core Socket.IO authentication functionality."""

from typing import Optional

from mascope_backend.api.new.auth.access_token.validation import (
    validate_service_access_token,
)
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.runtime import runtime
from mascope_backend.socket.auth.exceptions import (
    SocketAuthConfigError,
    SocketForbiddenError,
    SocketUnauthenticatedError,
)
from mascope_backend.socket.auth.token import validate_jwt_token
from mascope_backend.socket.storage import clear_user_session, save_user_session


async def verify_role_permission(user, minimum_role: str) -> None:
    """
    Verify if user has sufficient role permissions.

    :param user: User instance to verify
    :param minimum_role: Minimum role required
    :raises SocketAuthConfigError: If role configuration is invalid
    :raises SocketForbiddenError: If user's role is insufficient
    """
    required_role_id = auth_settings.ROLE_ACCESS_LEVELS.get(minimum_role)
    if required_role_id is None:
        runtime.logger.error(f"Invalid role configuration: {minimum_role}")
        raise SocketAuthConfigError()

    if user.role_id < required_role_id:
        raise SocketForbiddenError()


async def authenticate_socket_connection(
    sid: str,
    token: str,
    minimum_role: str,
    service_name: Optional[str] = None,
) -> None:
    """
    Authenticate Socket.IO connection with Redis session storage.

    Validates the client's connection by:
    - Clearing any existing session to prevent stale data
    - Validating the token (JWT for frontend, access token for services)
    - Retrieving and verifying the associated user
    - Checking role permissions
    - Storing session in Redis for cross-worker access

    :param sid: Socket.IO session ID
    :type sid: str
    :param token: Authentication token (JWT for frontend, access token for services)
    :type token: str
    :param minimum_role: Minimum role required for this connection
    :type minimum_role: str
    :param service_name: Name of the service requesting authentication (if applicable)
    :type service_name: Optional[str]
    :raises SocketUnauthenticatedError: If authentication fails
    :raises SocketForbiddenError: If user's role is insufficient
    :raises SocketAuthConfigError: If role configuration is invalid
    """
    namespace = f"/{service_name}" if service_name else "/"
    try:
        # --- Clear any existing session first to prevent stale data ---
        await clear_user_session(sid=sid, namespace=namespace)

        # --- Validate provided authentication token ---
        issued_at = None
        if service_name:
            # Service authentication (e.g., file-agent)
            user = await validate_service_access_token(token, service_name)
        else:
            # Frontend user authentication. The token's issue time is kept so
            # subscribe can tell when the account's session cutoff passes it.
            user, issued_at = await validate_jwt_token(token)

        # --- Verify role permissions ---
        await verify_role_permission(user, minimum_role)

        # --- Save user session to Redis for cross-worker access ---
        await save_user_session(sid, user, namespace=namespace, issued_at=issued_at)
        runtime.logger.debug(
            f"{service_name.title() if service_name else 'User'} socket session {sid} "
            f"authenticated by user '{user.username}'"
        )
    except InvalidTokenException as e:
        raise SocketUnauthenticatedError(str(e)) from e
    except Exception as e:
        # Handle authentication error and clean up session."""
        await clear_user_session(sid=sid, namespace=namespace)

        if isinstance(e, (SocketForbiddenError, SocketAuthConfigError)):
            raise SocketUnauthenticatedError(str(e)) from e
        if isinstance(e, SocketUnauthenticatedError):
            raise

        # logger.exception: the raised SocketUnauthenticatedError is handled
        # as a routine rejection upstream, so this is the only record of the
        # unexpected underlying failure
        runtime.logger.exception("Socket authentication failed")
        raise SocketUnauthenticatedError("Authentication failed") from e
