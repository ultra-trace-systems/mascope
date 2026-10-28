"""
Socket.IO session management with Redis storage.

Stores authentication sessions in Redis for cross-worker access.
Each session contains user_id, username, and role_id for RBAC checks, and for
a web session the issued_at and expires_at of the token it was authenticated
with.

Sessions persist for a configurable TTL (default 24 hours) as a safety net.
Primary cleanup occurs on socket disconnect. TTL is refreshed on each
access to keep active users authenticated indefinitely, and for a web socket
also by a renewal task that lives exactly as long as the connection (started
on connect, cancelled on disconnect), so a tab that only listens keeps its
session and records too. The renewal is also what signs a web socket out when
its token expires: it wakes at the expiry and hands the socket to
:func:`mascope_backend.socket.eviction.sign_out_expired_socket`.

A web (default-namespace) session is also indexed by user, so every socket an
account has open can be found from any worker - which is what ending its
sessions needs (see :mod:`mascope_backend.socket.eviction`). Room membership
under the Redis manager is local to the worker holding the connection, so
without the index a socket on another worker could not be reached.
"""

import asyncio
import json
import os
import time
from typing import Any, Optional

from mascope_backend.db import User
from mascope_backend.runtime import runtime
from mascope_backend.socket.storage.client import redis_storage_client
from mascope_backend.socket.storage.config import storage_config
from mascope_backend.socket.storage.exceptions import SocketSessionError
from mascope_backend.socket.storage.workers import renew_socket_claim


#: The namespace of the web application's sockets, the only ones indexed by
#: user: service sockets join no rooms through ``subscribe``.
WEB_NAMESPACE = "/"


async def save_user_session(
    sid: str,
    user: User,
    namespace: str = "/",
    issued_at: Optional[int] = None,
    expires_at: Optional[int] = None,
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
    :param expires_at: The ``exp`` of that token: the session is signed out
        when it passes. ``None`` for a service token.
    :type expires_at: Optional[int]
    :raises SocketSessionError: If session cannot be saved to Redis
    """
    try:
        worker_pid = os.getpid()
        session_data = {
            "user_id": user.id,
            "username": user.username,
            "role_id": user.role_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }

        # Store in Redis with configured TTL
        await redis_storage_client.client.setex(
            storage_config.session_key(sid, namespace),
            storage_config.session_ttl,
            json.dumps(session_data),
        )
        if namespace == WEB_NAMESPACE:
            user_sockets_key = storage_config.user_sockets_key(user.id)
            await redis_storage_client.client.sadd(user_sockets_key, sid)
            await redis_storage_client.client.expire(
                user_sockets_key, storage_config.session_ttl
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
    disconnect or logout to immediately invalidate the session. A web session
    is also removed from its user's socket index.

    :param sid: Socket.IO session ID
    :type sid: str
    :param namespace: Socket.IO namespace
    :type namespace: str
    """
    worker_pid = os.getpid()
    try:
        session_key = storage_config.session_key(sid, namespace)
        if namespace == WEB_NAMESPACE:
            await _unindex_session(session_key, sid)
        deleted = await redis_storage_client.client.delete(session_key)

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


async def _unindex_session(session_key: str, sid: str) -> None:
    """
    Remove a web session's socket from its user's index, before it is deleted.

    Best effort: a session that cannot be read cannot name its user, and the
    index tolerates a stale entry (see :func:`get_user_socket_sids`), so a
    failure here must not stop the session itself from being cleared.

    :param session_key: The session's Redis key.
    :param sid: Socket.IO session ID
    """
    try:
        if data := await redis_storage_client.client.get(session_key):
            await forget_user_socket(json.loads(data)["user_id"], sid)
    except Exception as e:
        runtime.logger.warning(
            f"Redis session storage client: could not unindex session {sid}: {e}"
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
        if namespace == WEB_NAMESPACE:
            # And what eviction finds the socket by, so the index, the room
            # record and the worker record live as long as the session (see
            # start_session_renewal for a socket that never emits).
            await redis_storage_client.client.expire(
                storage_config.user_sockets_key(session["user_id"]),
                storage_config.session_ttl,
            )
            await redis_storage_client.client.expire(
                storage_config.socket_rooms_key(sid), storage_config.room_ttl
            )
            await redis_storage_client.client.expire(
                storage_config.socket_worker_key(sid), storage_config.session_ttl
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


async def peek_session_user(sid: str, namespace: str = "/") -> Optional[dict[str, Any]]:
    """
    A socket's session as stored, without refreshing it as a read does.

    For a caller looking over every socket of an account for the ones to sign
    out: a socket its dead worker left must not have its records kept alive by
    being looked at.

    :param sid: Socket.IO session ID
    :type sid: str
    :param namespace: Socket.IO namespace
    :type namespace: str
    :return: The session, or ``None`` when there is none
    :rtype: Optional[dict[str, Any]]
    """
    data = await redis_storage_client.client.get(
        storage_config.session_key(sid, namespace)
    )
    return json.loads(data) if data else None


async def has_session(sid: str, namespace: str = "/") -> bool:
    """
    Whether a socket has a session, without refreshing it as a read does.

    :param sid: Socket.IO session ID
    :type sid: str
    :param namespace: Socket.IO namespace
    :type namespace: str
    :return: True if the session key exists
    :rtype: bool
    """
    return bool(
        await redis_storage_client.client.exists(
            storage_config.session_key(sid, namespace)
        )
    )


async def get_user_socket_sids(user_id: int) -> set[str]:
    """
    The session IDs of every web socket a user has open, on any worker.

    May include a socket that went away without a disconnect (a worker that
    died); its session is gone too, so a caller finds no session for it and
    should :func:`forget_user_socket` it.

    :param user_id: User ID
    :type user_id: int
    :return: Socket.IO session IDs
    :rtype: set[str]
    """
    return set(
        await redis_storage_client.client.smembers(
            storage_config.user_sockets_key(user_id)
        )
    )


async def forget_user_socket(user_id: int, sid: str) -> None:
    """
    Remove one socket from a user's socket index.

    :param user_id: User ID
    :type user_id: int
    :param sid: Socket.IO session ID
    :type sid: str
    """
    await redis_storage_client.client.srem(
        storage_config.user_sockets_key(user_id), sid
    )


# Renewal tasks keyed by sid, one per authenticated web socket on this worker.
# Kept only so the disconnect handler can cancel the matching task.
_renewal_tasks: dict[str, asyncio.Task] = {}


def _first_delay(expires_at: Optional[float]) -> float:
    """The renewal's first sleep: an interval, or less if the token expires sooner."""
    delay = storage_config.session_renewal_interval
    if expires_at is None:
        return delay
    return min(delay, expires_at - time.time())


async def _renew_session(sid: str, expires_at: Optional[float] = None) -> None:
    """
    Keep a web socket's records alive until cancelled.

    Each tick re-writes its worker record, session or not, and refreshes its
    session, index entry and room record. A session whose token has expired is
    signed out instead, and the tick before it is brought forward to the
    expiry, so the socket goes when its token does, not up to an interval
    later. ``expires_at``, the expiry of a token the socket authenticated
    with at connect, brings the first tick forward the same way.
    """
    delay = _first_delay(expires_at)
    while True:
        await asyncio.sleep(delay)
        delay = storage_config.session_renewal_interval
        # Written, not refreshed: a socket signed out for longer than the
        # record's TTL has lost it, and must not read as a ghost once it signs
        # in. Only this worker, which holds the connection, may write it.
        await renew_socket_claim(sid)
        try:
            # Reading the session refreshes the other three.
            session = await get_session_user(sid)
        except SocketSessionError:
            # Logged inside; retried on the next tick. A session cleared under
            # a live socket has nothing to refresh until the socket is gone.
            continue
        if (expires_at := session.get("expires_at")) is None:
            # A service socket, or a session saved before sessions kept the
            # token's expiry.
            continue
        remaining = expires_at - time.time()
        if remaining <= 0:
            # Imported lazily: eviction imports this package.
            from mascope_backend.socket.eviction import sign_out_expired_socket

            await sign_out_expired_socket(sid, session["user_id"])
        else:
            delay = min(delay, remaining)


def start_session_renewal(sid: str, expires_at: Optional[float] = None) -> None:
    """
    Keep a web socket's session and records alive for the life of its connection.

    Call from the default namespace's connect handler for every connection,
    authenticated there or not: a socket may be authenticated later, at
    sign-in, by a request another worker serves. Idempotent per sid: a
    lingering task is cancelled first, so the connect handler calls it again
    with the expiry of the token it authenticated the socket with.

    :param sid: Socket.IO session ID
    :type sid: str
    :param expires_at: The ``exp`` of the socket's token, if known: the first
        tick is brought forward to it, so a socket that connects in its
        token's last interval is still signed out when the token expires
    :type expires_at: Optional[float]
    """
    _cancel_renewal_task(sid)
    _renewal_tasks[sid] = asyncio.create_task(_renew_session(sid, expires_at))


def _cancel_renewal_task(sid: str) -> asyncio.Task | None:
    task = _renewal_tasks.pop(sid, None)
    if task is not None:
        task.cancel()
    return task


async def stop_session_renewal(sid: str) -> None:
    """
    Stop a web socket's session renewal.

    Call from the disconnect handler *before* the session and its records are
    cleared, so a renewal in flight cannot refresh a key about to be deleted.

    :param sid: Socket.IO session ID
    :type sid: str
    """
    task = _cancel_renewal_task(sid)
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            # Expected: the task was cancelled just above.
            pass
