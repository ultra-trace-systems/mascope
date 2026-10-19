"""
Frontend user connection lifecycle for Mascope web application.

Handles socket connections on the default namespace used by the main web application.
"""

import os

from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.auth import authenticate_socket_connection
from mascope_backend.socket.auth.exceptions import (
    SocketUnauthenticatedError,
)
from mascope_backend.socket.auth.token import get_jwt_from_cookies
from mascope_backend.socket.storage import (
    SocketSessionError,
    clear_user_session,
    get_session_user,
    room_tracker,
    start_session_renewal,
    stop_session_renewal,
)


@sio.event(namespace="/")
async def connect(sid: str, environ: dict) -> bool:
    """
    Handle new frontend user connections on default namespace.

    Always accepts the connection but attempts authentication if JWT token in cookie is present.
    If authentication succeeds, the socket session is associated with the user.
    If authentication fails or no credentials are present, the connection remains
    unauthenticated but still active.

    NOTE: Authentication on connection looks like current Socket.IO best practice.
    The unauthenticated connections may be rejected for security reasons.
    This approach provides early authentication validation, clear connection lifecycle.
    Socket.IO will automatically disconnect the client if an exception raised
    during the connect or if event returned False.

    :param sid: Socket session ID
    :type sid: str
    :param environ: WSGI environment containing request data
    :type environ: dict
    :return: Connection acceptance status
    :rtype: bool
    """
    worker_pid = os.getpid()
    try:
        # --- Get cookies from environment ---
        cookies = environ.get("HTTP_COOKIE")
        if not cookies:
            raise SocketUnauthenticatedError("No cookies in request")

        # --- Extract JWT token from cookies ---
        jwt_token = await get_jwt_from_cookies(cookies)

        # --- Authenticate socket connection and associate session with user ---
        await authenticate_socket_connection(
            sid=sid, token=jwt_token, minimum_role="guest"
        )
        # Keeps the session, and what eviction finds the socket by, alive while
        # the tab only listens (see storage_config.session_renewal_interval).
        start_session_renewal(sid)
        runtime.logger.debug(
            f"Socket server: user's socket client {sid} connected [Worker {worker_pid}]"
        )

        return True

    except SocketUnauthenticatedError as e:
        # Routine: every visitor without a valid login cookie lands here (and
        # every open tab after a session expires), so this is not a fault.
        runtime.logger.info(
            f"User socket session authentication failed: {str(e)} [Worker {worker_pid}]"
        )
        return True
    except Exception as e:
        runtime.logger.error(
            f"Unexpected error during user socket connection: {str(e)} [Worker {worker_pid}]"
        )
        return True


@sio.event
async def disconnect(sid: str) -> None:
    """
    Handle frontend user socket disconnections.
    - Remove user from all room memberships (room_tracker)
    - Clear authentication session (session manager)

    :param sid: Socket session ID
    :type sid: str
    """
    worker_pid = os.getpid()
    runtime.logger.debug(
        f"Socket server: user's socket client {sid} disconnected [Worker {worker_pid}]"
    )

    try:
        # Clean up room memberships BEFORE clearing session (need user_id)
        session = await get_session_user(sid)
        user_id = session["user_id"]
        await room_tracker.leave_all(user_id)

        runtime.logger.debug(
            f"User session disconnected: cleaned up rooms for user {user_id} (sid={sid}) "
            f"[Worker {worker_pid}]"
        )

    except SocketSessionError:
        # No session found - user was never authenticated or already cleaned up
        runtime.logger.trace(
            f"Socket disconnect: no session found for {sid} [Worker {worker_pid}]"
        )

    finally:
        # Clear session from Redis storage, and the socket's own room record,
        # once nothing can refresh them any more
        await stop_session_renewal(sid)
        await room_tracker.forget_socket(sid)
        await clear_user_session(sid)
