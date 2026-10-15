"""
Socket.IO session management with Redis storage.

Stores authentication sessions in Redis for cross-worker access.
Each session contains user_id, username, and role_id for RBAC checks, and for
a web session the issued_at of the token it was authenticated with.

Sessions persist for a configurable TTL (default 24 hours) as a safety net.
Primary cleanup occurs on socket disconnect. TTL is refreshed on each
access to keep active users authenticated indefinitely.
"""

import json
import os
from typing import Any, Optional

from mascope_backend.db import User
from mascope_backend.runtime import runtime
from mascope_backend.socket.storage.client import redis_storage_client
from mascope_backend.socket.storage.config import storage_config
from mascope_backend.socket.storage.exceptions import SocketSessionError


async def save_user_session(
    sid: str, user: User, namespace: str = "/", issued_at: Optional[int] = None
) -> None:
    """
    Save authenticated user session to Redis.

    Stores user authentication data (user_id, username, role_id) in Redis
    with automatic expiration. Sessions are accessible from any worker for
    RBAC checks. TTL acts as a safety net for orphaned sessions; primary
    cleanup occurs on socket disconnect.

    :param sid: Socket.IO session ID
    :type sid: str
    :param user: Authenticated user instance
    :type user: User
    :param namespace: Socket.IO namespace
    :type namespace: str
    :param issued_at: The ``iat`` of the web session token the socket was
        authenticated with, compared with the account's session cutoff on
        subscribe. ``None`` for a service token or a web token minted before
        tokens carried one.
    :type issued_at: Optional[int]
    :raises SocketSessionError: If session cannot be saved to Redis
    """
    try:
        worker_pid = os.getpid()
        session_data = {
            "user_id": user.id,
            "username": user.username,
            "role_id": user.role_id,
            "issued_at": issued_at,
        }

        # Store in Redis with configured TTL
        await redis_storage_client.client.setex(
            storage_config.session_key(sid, namespace),
            storage_config.session_ttl,
            json.dumps(session_data),
        )

        runtime.logger.debug(
            f"Redis session storage client: saved session for {sid}: user={user.username}, "
            f"role_id={user.role_id} [Worker {worker_pid}]"
        )

    except Exception as e:
        runtime.logger.exception(
            f"Redis session storage client: failed to save session for SID '{sid}': {str(e)} "
            f"[Worker {worker_pid}]"
        )
        raise SocketSessionError("Failed to save session") from e


async def clear_user_session(sid: str, namespace: str = "/") -> None:
    """
    Clear user session from Redis.

    Removes authentication data for the given session ID. Called on
    disconnect or logout to immediately invalidate the session.

    :param sid: Socket.IO session ID
    :type sid: str
    :param namespace: Socket.IO namespace
    :type namespace: str
    """
    worker_pid = os.getpid()
    try:
        deleted = await redis_storage_client.client.delete(
            storage_config.session_key(sid, namespace),
        )

        if deleted:
            runtime.logger.debug(
                f"Redis session storage client: cleared session for {sid} [Worker {worker_pid}]"
            )
        else:
            runtime.logger.trace(
                f"Redis session storage client: no session found to clear for {sid} [Worker {worker_pid}]"
            )

    except Exception as e:
        runtime.logger.exception(
            f"Redis session storage client: failed to clear session for {sid}: {e} [Worker {worker_pid}]"
        )


async def get_session_user(sid: str, namespace: str = "/") -> dict[str, Any]:
    """
    Get user authentication data from Redis session.

    Retrieves stored user_id, username, and role_id for RBAC checks.
    Works across all workers since data is in Redis. Automatically
    refreshes the session TTL on each access to keep active users
    authenticated indefinitely.

    :param sid: Socket.IO session ID
    :type sid: str
    :param namespace: Socket.IO namespace
    :type namespace: str
    :return: User session data containing user_id, username, role_id
    :rtype: dict[str, Any]
    :raises SocketSessionError: If session not found or invalid
    """
    worker_pid = os.getpid()
    try:
        session_key = storage_config.session_key(sid, namespace)

        # Get session data
        if not (data := await redis_storage_client.client.get(session_key)):
            runtime.logger.debug(
                f"Redis session storage client: no session found for {sid} [Worker {worker_pid}]"
            )
            raise SocketSessionError("No session found")

        # Parse session
        session = json.loads(data)

        # Validate required fields
        if not isinstance(session.get("role_id"), int):
            raise SocketSessionError("Invalid session: missing or invalid role_id")

        # Refresh TTL on access (keeps active users sessions authenticated)
        await redis_storage_client.client.expire(
            session_key, storage_config.session_ttl
        )

        runtime.logger.trace(
            f"Redis session storage client: retrieved session for {sid}, TTL refreshed [Worker {worker_pid}]"
        )

        return session

    except json.JSONDecodeError as e:
        runtime.logger.exception(
            f"Redis session storage client: failed to decode session data for {sid}: {e} "
            f"[Worker {worker_pid}]"
        )
        raise SocketSessionError("Corrupted session data") from e
    except SocketSessionError:
        raise
    except Exception as e:
        runtime.logger.exception(
            f"Redis session storage client: session error for {sid}: {e} [Worker {worker_pid}]"
        )
        raise SocketSessionError(f"Session error: {e}") from e
