"""
Holding a live socket to its own room once its session may no longer read.

Subscribe refuses every room but ``user-{id}`` to a socket whose account owes a
password change, is disabled, or whose web session has been ended (the
account's session cutoff has moved past the token the socket authenticated
with). That covers every *new* subscription. A socket that subscribed before
the account changed keeps its rooms, though, and record-sync events with full
record data reach it until it disconnects - so whatever changes the account
also evicts its sockets, here.

Eviction is a ``leave_room`` of every room but ``user-{id}``, not a disconnect:
the tab keeps its channel, hears on its own room that its session is over, and
the client's reconnect logic is left as it is. Under the Redis manager a
socket's rooms live in the worker holding the connection; ``leave_room`` for a
socket held elsewhere is published to the workers and applied by the one that
holds it, and the sockets and their rooms are found through the Redis records
kept beside the session (:mod:`mascope_backend.socket.storage`).

Only the sockets subscribe would now refuse are evicted - the same comparison,
:func:`socket_hold_reason` - so a session the cutoff's tolerance still keeps,
the one that just changed its own password, carries on.
"""

import os
from typing import Optional

from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.storage import (
    SocketSessionError,
    forget_user_socket,
    get_session_user,
    get_user_socket_sids,
    room_tracker,
)


def socket_hold_reason(user, issued_at: Optional[int]) -> Optional[str]:
    """
    Why a socket of this account may hear nothing but its own room.

    :param user: The account the socket is signed in as.
    :param issued_at: The ``iat`` the socket's session recorded.
    :return: The reason, worded to follow "user <id>", or ``None`` when the
        socket may join the rooms its access allows.
    """
    # Imported lazily: the auth package imports socket.auth, which imports the
    # socket storage this module sits beside.
    from mascope_backend.api.new.auth.sessions import session_revoked

    if user.must_change_password:
        return "owes a password change"
    if not user.is_active or session_revoked(user, issued_at):
        return "has had its session ended"
    return None


async def evict_ended_sessions(user_id: int, user=None) -> int:
    """
    Make every socket of an account that may no longer read leave its rooms.

    Called by everything that moves the account's session cutoff or holds it at
    the password screen - the forced-password-change sweep, a password change,
    an account disable, sign-out-everywhere - after the change is committed.
    Never raises: the change it follows already stands, and a failure here
    leaves the sockets no worse off than before eviction existed.

    :param user_id: The account.
    :param user: The account as just committed, if the caller has it; read
        from the database otherwise.
    :return: How many sockets were evicted.
    """
    worker_pid = os.getpid()
    try:
        sids = await get_user_socket_sids(user_id)
        if not sids:
            return 0
        if user is None:
            from mascope_backend.db import User, async_session

            async with async_session() as db:
                user = await db.get(User, user_id)
            if user is None:
                return 0

        own_room = f"user-{user_id}"
        left: set[str] = set()
        kept: set[str] = set()
        evicted = 0
        for sid in sids:
            rooms = await room_tracker.socket_rooms(sid)
            try:
                session = await get_session_user(sid)
            except SocketSessionError:
                # Gone without a disconnect (a worker died), or alive with its
                # session lost: either way nothing says it may keep its rooms.
                # A leave_room for a socket nobody holds is a harmless publish.
                session = None
                await forget_user_socket(user_id, sid)
            if (
                session is not None
                and socket_hold_reason(user, session.get("issued_at")) is None
            ):
                kept |= rooms
                continue
            for room in rooms - {own_room}:
                await sio.leave_room(sid, room)
                await room_tracker.leave_socket(sid, room)
                left.add(room)
            if session is not None or rooms - {own_room}:
                evicted += 1

        # The per-user membership answers "is this user in the room" for
        # notification routing; a room another of the user's sockets still
        # holds stays.
        for room in left - kept:
            await room_tracker.leave(user_id, room)

        if evicted:
            runtime.logger.info(
                f"Evicted {evicted} socket(s) of user {user_id} from "
                f"{len(left)} room(s) [Worker {worker_pid}]"
            )
        return evicted
    except Exception:
        runtime.logger.exception(
            f"Failed to evict the sockets of user {user_id} [Worker {worker_pid}]"
        )
        return 0
