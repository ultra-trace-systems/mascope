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

The signed-in room the room-less record-sync broadcasts go to
(:mod:`mascope_backend.socket.signed_in`) is one of the rooms an evicted socket
leaves; a socket eviction keeps is put back in it, which is how the tab of an
account released from the password screen hears those broadcasts again.

Only the sockets subscribe would now refuse are evicted - the same comparison,
:func:`socket_hold_reason` - so a session the cutoff's tolerance still keeps,
the one that just changed its own password, carries on.

Signing out of one tab ends that tab's session alone and moves no cutoff, so
eviction, which works by account and keeps every socket that may still read,
cannot reach it. :func:`sign_out_socket` does it for the one socket the
logout request names: the tab is no longer signed in, so it leaves every room,
its own ``user-{id}`` included, and its session goes.
"""

import os
from typing import Optional

from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.signed_in import SIGNED_IN_ROOM, admit_signed_in
from mascope_backend.socket.storage import (
    SocketSessionError,
    clear_user_session,
    forget_user_socket,
    get_session_user,
    get_user_socket_sids,
    room_tracker,
)


def socket_hold_reason(user, issued_at: Optional[int]) -> Optional[str]:
    """
    Why a socket of this account may hear nothing but its own room.

    :param user: The account the socket is signed in as, or ``None`` when the
        account no longer exists.
    :param issued_at: The ``iat`` the socket's session recorded.
    :return: The reason, worded to follow "user <id>", or ``None`` when the
        socket may join the rooms its access allows.
    """
    # Imported lazily: the auth package imports socket.auth, which imports the
    # socket storage this module sits beside.
    from mascope_backend.api.new.auth.sessions import session_revoked

    if user is None:
        return "has been deleted"
    if user.must_change_password:
        return "owes a password change"
    if not user.is_active or session_revoked(user, issued_at):
        return "has had its session ended"
    return None


async def evict_ended_sessions(user_id: int, user=None) -> int:
    """
    Make every socket of an account that may no longer read leave its rooms.

    Called by everything that moves the account's session cutoff or holds it at
    the password screen - the forced-password-change sweep (the owner's route
    and the maintenance script), a password change, an account disable,
    sign-out-everywhere, an account deletion - after the change is committed.
    An account the database no longer has may read nothing, so every one of
    its sockets is evicted.
    Never raises: the change it follows already stands, and a failure here
    leaves the sockets no worse off than before eviction existed.

    :param user_id: The account.
    :param user: The account as just committed, if the caller has it; read
        from the database otherwise. A deleted account's caller passes none.
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
                # A socket that may read hears the room-less broadcasts again:
                # the held tab of an account that has just changed its
                # password left the room, or never entered it, at sign-in.
                if SIGNED_IN_ROOM not in rooms:
                    await admit_signed_in(sid, user, session.get("issued_at"))
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


async def sign_out_socket(sid: str, user_id: int) -> bool:
    """
    End the socket session of a tab that has just signed out.

    The logout route names the socket from the request's ``X-SID``, which the
    client supplies, so the socket is touched only when its stored session is
    the account signing out: a header naming another account's socket changes
    nothing. The session is cleared first, so a subscribe arriving meanwhile is
    refused; then the socket leaves every room it is on record in, the
    signed-in room and ``user-{id}`` with the others. The user's membership of
    a room goes only where no other socket of theirs holds it, as on a
    disconnect: signing out of one tab leaves the others as they are.

    The connection itself stays open: the tab carries on as one that has not
    signed in, as the SPA's socket is before its login form.
    Never raises: the session the cookie carried has ended either way.

    :param sid: The socket the signing-out request names, on the default
        namespace.
    :param user_id: The account signing out.
    :return: Whether the socket was signed out.
    """
    worker_pid = os.getpid()
    try:
        try:
            session = await get_session_user(sid)
        except SocketSessionError:
            # Never authenticated, or already gone: nothing is signed in.
            return False
        if session.get("user_id") != user_id:
            runtime.logger.warning(
                f"Sign-out of user {user_id} named socket {sid} of another "
                f"account; left it as it is [Worker {worker_pid}]"
            )
            return False

        await clear_user_session(sid)
        # Out of the user's socket index (cleared above) before the others are
        # read, so a room only this socket held goes and one another tab holds
        # stays the user's.
        await room_tracker.release_socket(user_id, sid)
        rooms = await room_tracker.socket_rooms(sid)
        for room in rooms:
            await sio.leave_room(sid, room)
        await room_tracker.forget_socket(sid)

        runtime.logger.info(
            f"Signed socket {sid} of user {user_id} out of {len(rooms)} room(s) "
            f"[Worker {worker_pid}]"
        )
        return True
    except Exception:
        runtime.logger.exception(
            f"Failed to sign socket {sid} of user {user_id} out [Worker {worker_pid}]"
        )
        return False
