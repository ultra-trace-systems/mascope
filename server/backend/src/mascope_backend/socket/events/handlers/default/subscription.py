"""Room subscription management with Redis tracking.

Handles subscribe/unsubscribe events for Socket.IO rooms with cross-worker
membership tracking. Each subscription:
1. Adds SID to Socket.IO room (for AsyncRedisManager pub/sub routing)
2. Records membership in Redis (for cross-worker presence queries, and per
   socket so eviction can find the rooms of a socket on another worker)
"""

from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.auth.decorators import socket_auth
from mascope_backend.socket.eviction import socket_hold_reason
from mascope_backend.socket.storage import get_session_user, room_tracker


@sio.event(namespace="/")
@socket_auth(minimum_role="guest")
async def subscribe(sid, room):
    """
    Subscribe authenticated user to a room, if the workspace ACL permits it.

    Rooms carry record-sync events with full record data, so a subscription is
    gated by the same read-access check as the REST API
    (``user_can_subscribe_to_room``). A user that lacks access to the room's
    resource is silently not joined - never receiving another tenant's data.

    An account owing a forced password change is held to its own ``user-{id}``
    room, for the same reason the REST dependencies refuse it: without this it
    would keep reading over the socket exactly what the API has just started
    refusing. The personal room is the single exemption because it is how the
    gated tab hears that it has been gated, and that it has been released - the
    owner sweep and the credentials route both emit there. The handshake stays
    open for the same reason: refusing it would leave the SPA with no channel
    at all, and nothing to receive the release on.

    A socket whose web session has been ended since it authenticated - the
    account's session cutoff has moved past the token's issue time, the same
    comparison the cookie strategy makes - is held to the same room, and so is
    a disabled account. The tab hears there that its session is over, and its
    next REST request is refused. A socket already subscribed when any of this
    happens is evicted from its rooms by whatever changed the account
    (:mod:`mascope_backend.socket.eviction`).

    Performs dual registration on success:
    - Socket.IO room (for event routing via AsyncRedisManager)
    - Redis tracking (for cross-worker membership queries)

    :param sid: Socket session ID
    :type sid: str
    :param room: Room identifier (e.g., a sample_batch_id or "user-123")
    :type room: str
    """
    # Imported lazily: the workspace ACL pulls in the auth/user-manager stack,
    # which imports socket.auth - a module-level import here would be circular.
    from mascope_backend.api.new.workspaces.dependencies import (
        user_can_subscribe_to_room,
    )
    from mascope_backend.db import User, async_session

    # Get user_id from session
    session = await get_session_user(sid)
    user_id = session["user_id"]

    # Authorize the subscription against the workspace ACL. The user object is
    # needed for the superuser / global-role bypass, which the session omits.
    async with async_session() as db:
        user = await db.get(User, user_id)
    if user is None or not await user_can_subscribe_to_room(room, user):
        runtime.logger.warning(
            f"Denied room subscription: user {user_id} -> room '{room}'"
        )
        return

    # Checked after the ACL so the log says which boundary refused, and using
    # the user already loaded above rather than a second query. Eviction makes
    # the same check of a socket that subscribed before the account changed.
    if room != f"user-{user_id}" and (
        reason := socket_hold_reason(user, session.get("issued_at"))
    ):
        runtime.logger.warning(
            f"Denied room subscription: user {user_id} {reason} -> room '{room}'"
        )
        return

    # Socket.IO room (AsyncRedisManager handles pub/sub routing)
    await sio.enter_room(sid, room)

    # Redis tracking (cross-worker membership queries, and the socket's own
    # rooms for eviction)
    await room_tracker.join(user_id, room)
    await room_tracker.join_socket(sid, room)


@sio.event(namespace="/")
@socket_auth(minimum_role="guest")
async def unsubscribe(sid, room):
    """
    Unsubscribe authenticated user from a room.

    Removes from both Socket.IO room and Redis tracking.

    :param sid: Socket session ID
    :type sid: str
    :param room: Room identifier
    :type room: str
    """
    # Get user_id from session
    session = await get_session_user(sid)
    user_id = session["user_id"]

    # Remove from Socket.IO room
    await sio.leave_room(sid, room)

    # Remove from Redis tracking
    await room_tracker.leave(user_id, room)
    await room_tracker.leave_socket(sid, room)
