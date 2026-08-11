"""Socket authentication decorators."""

from functools import wraps
from typing import Callable, Optional

from mascope_backend.api.new.auth.access_token.validation import (
    validate_service_access_token,
)
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.runtime import runtime
from mascope_backend.socket.auth.exceptions import (
    SocketAuthConfigError,
    SocketAuthError,
    SocketForbiddenError,
    SocketUnauthenticatedError,
)
from mascope_backend.socket.storage import SocketSessionError, get_session_user


def socket_auth(minimum_role: str, service_name: Optional[str] = None):
    """
    Decorator for Socket.IO event handlers that enforces role-based access control.

    :param minimum_role: Minimum role required to access this event
    :type minimum_role: str
    :param service_name: Name of the service requesting authentication (if applicable)
    :type service_name: Optional[str]
    """

    def decorator(handler: Callable):
        @wraps(handler)
        async def wrapper(sid: str, *args, **kwargs):
            # Build namespace (defaults to "/" for frontend users)
            namespace = f"/{service_name}" if service_name else "/"

            try:
                # Get user session from Redis
                session = await get_session_user(sid, namespace=namespace)

                # Validate role permissions
                required_role_id = auth_settings.ROLE_ACCESS_LEVELS.get(minimum_role)
                if required_role_id is None:
                    runtime.logger.error(f"Invalid role configuration: {minimum_role}")
                    raise SocketAuthConfigError()

                if session["role_id"] < required_role_id:
                    raise SocketForbiddenError()

                return await handler(sid, *args, **kwargs)

            except (SocketAuthError, SocketSessionError) as e:
                # Expected rejection (expired session, insufficient role).
                # SocketSessionError belongs here too: `connect` accepts
                # unauthenticated sockets, so every event a client emits
                # without a stored session lands on it. The genuine session
                # faults (corrupt data, Redis down) are already reported at
                # ERROR where they happen, in get_session_user.
                runtime.logger.info(f"Socket auth error for {sid}: {str(e)}")

            except Exception:
                # Not an auth rejection: this broad catch also swallows
                # exceptions from the handler itself, so record the traceback
                # or the bug would be invisible
                runtime.logger.exception(
                    f"Unexpected socket error in '{handler.__name__}' for {sid}"
                )

        return wrapper

    return decorator


def file_converter_socket_auth(minimum_role: str):
    """
    Decorator for file-converter events that validates access token and permissions.

    :param minimum_role: Minimum role required
    :type minimum_role: str
    """

    def decorator(handler: Callable):
        @wraps(handler)
        async def wrapper(sid: str, data: dict, *args, **kwargs):
            try:
                # Get access token from event data
                access_token = data.pop("access_token", None)
                if not access_token:
                    raise SocketUnauthenticatedError("Missing access token")

                # Validate token and get user
                user = await validate_service_access_token(
                    access_token=access_token, service_name="file-converter"
                )

                # Check role permissions
                required_role_id = auth_settings.ROLE_ACCESS_LEVELS.get(minimum_role)
                if required_role_id is None:
                    runtime.logger.error(f"Invalid role configuration: {minimum_role}")
                    raise SocketAuthConfigError()

                if user.role_id < required_role_id:
                    raise SocketForbiddenError()

                runtime.logger.debug(
                    f"Authenticated file-converter event from user '{user.username}' "
                    f"with role_id {user.role_id} for file '{data.get('filename', 'unknown')}'"
                )

                return await handler(sid, data, *args, **kwargs)
            except InvalidTokenException as e:
                # Expected 401-class rejection; the raise carries it upstream
                runtime.logger.info(f"File converter auth error: {str(e)}")
                raise SocketUnauthenticatedError(str(e)) from e
            except SocketAuthError as e:
                runtime.logger.info(f"File converter auth error: {str(e)}")
                raise
            except Exception:
                # Not an auth rejection: this broad catch also swallows
                # exceptions from the handler itself, so record the traceback
                # or the bug would be invisible
                runtime.logger.exception(
                    f"Unexpected file-converter socket error in '{handler.__name__}'"
                )

        return wrapper

    return decorator
