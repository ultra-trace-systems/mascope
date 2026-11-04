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

Signing out ends one web session and moves no cutoff, so eviction, which works
by account and keeps every socket that may still read, cannot reach it.
:func:`sign_out_socket` does it for the one socket the logout request names:
the tab is no longer signed in, so it leaves every room, its own ``user-{id}``
included, and its session goes. The session cookie is the browser's, though,
so the browser's other tabs are signed out with it, and their sockets were
authenticated with the same token: :func:`sign_out_session` signs out every
socket of the account whose session recorded that token's ``iat``. The issue
time is the session's only mark, to the second, so a sign-in of the same
account in another browser within the same second goes too; that errs towards
signing out, and its tab signs in again.

A web session's token also expires, after ``JWT_EXPIRATION_SECONDS``, and from
then on the tab is signed out over HTTP. Its socket's session keeps the
token's ``exp`` beside its ``iat``: :func:`socket_hold_reason` holds a session
past it, and the renewal that keeps a connected socket's records alive
(:mod:`mascope_backend.socket.storage.sessions`) wakes at the expiry and signs
the socket out with :func:`sign_out_expired_socket`, as a logout would. Without
that, a tab left open past the token's lifetime kept its rooms, and a logout
in a tab that had signed in again since, with a token of another ``iat``,
could not reach it.

Each socket signed out, by a logout or at its token's expiry, hears
:data:`SIGNED_OUT_EVENT` on its own connection. A tab that signed out knows
already, but the browser's other tabs do not: once any tab signs in again the
cookie works for them all, so they would never meet the 401 that shows the
sign-in screen, and would show data with a socket that hears nothing. On the
event the SPA opens a new connection and reads the profile again, which shows
the sign-in screen, or signs the socket in again when the browser holds a
session once more.
"""

import os
import time
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
    has_session,
    peek_session_user,
    room_tracker,
    socket_is_held,
)


#: The event a socket hears when its session has been signed out.
SIGNED_OUT_EVENT = "signed_out"


def socket_hold_reason(
    user, issued_at: Optional[int], expires_at: Optional[int]
) -> Optional[str]:
    """
    Why a socket of this account may hear nothing but its own room.

    :param user: The account the socket is signed in as, or ``None`` when the
        account no longer exists.
    :param issued_at: The ``iat`` the socket's session recorded.
    :param expires_at: The ``exp`` the socket's session recorded; ``None`` for
        a session saved before sessions kept it, which nothing expires.
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
    if expires_at is not None and expires_at <= time.time():
        return "has had its session expire"
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
            # A socket its dead worker left may keep its session for up to a
            # day, but no room: it is evicted as one without a session. While
            # the session lasts it stays indexed, as a worker that only
            # stalled past its heartbeat still holds it and the next eviction
            # must find it. Asked first, so reading the session does not
            # refresh a ghost's records.
            session = None
            try:
                if await socket_is_held(sid):
                    session = await get_session_user(sid)
                elif not await has_session(sid):
                    raise SocketSessionError("No session found")
            except SocketSessionError:
                # Gone without a disconnect (a worker died), or alive with its
                # session lost: either way nothing says it may keep its rooms.
                # A leave_room for a socket nobody holds is a harmless publish.
                session = None
                await forget_user_socket(user_id, sid)
            if (
                session is not None
                and socket_hold_reason(
                    user, session.get("issued_at"), session.get("expires_at")
                )
                is None
            ):
                kept |= rooms
                # A socket that may read hears the room-less broadcasts again:
                # the held tab of an account that has just changed its
                # password left the room, or never entered it, at sign-in.
                if SIGNED_IN_ROOM not in rooms:
                    await admit_signed_in(
                        sid, user, session.get("issued_at"), session.get("expires_at")
                    )
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

        await _sign_out(sid, user_id)
        return True
    except Exception:
        runtime.logger.exception(
            f"Failed to sign socket {sid} of user {user_id} out [Worker {worker_pid}]"
        )
        return False


async def sign_out_session(
    user_id: int, issued_at: Optional[int], sid: Optional[str] = None
) -> int:
    """
    Sign out every socket a web session that has just ended authenticated.

    The logout request's own socket, named by ``sid``, goes as
    :func:`sign_out_socket` signs it out. Then every other socket of the
    account whose session recorded ``issued_at`` goes the same way: the
    browser's other tabs, whose cookie the logout has just cleared. A socket
    of the account authenticated with another token, in another browser, keeps
    its session and rooms.

    Never raises: the session the cookie carried has ended either way.

    :param user_id: The account signing out.
    :param issued_at: The ``iat`` of the ending session's token; ``None`` for a
        token minted before tokens carried one, which matches the sockets
        authenticated with such a token.
    :param sid: The socket the signing-out request names, if any.
    :return: How many sockets were signed out.
    """
    signed_out = 0
    if sid and await sign_out_socket(sid, user_id):
        signed_out += 1
    try:
        others = await get_user_socket_sids(user_id)
    except Exception:
        runtime.logger.exception(
            f"Failed to list the sockets of user {user_id} to sign out "
            f"[Worker {os.getpid()}]"
        )
        return signed_out
    for other in others:
        try:
            # Looked at without a refresh, so a socket its dead worker left
            # keeps no longer for having been read.
            session = await peek_session_user(other)
            if (
                session is None
                or session.get("user_id") != user_id
                or session.get("issued_at") != issued_at
            ):
                continue
            await _sign_out(other, user_id)
            signed_out += 1
        except Exception:
            runtime.logger.exception(
                f"Failed to sign socket {other} of user {user_id} out "
                f"[Worker {os.getpid()}]"
            )
    return signed_out


async def sign_out_expired_socket(sid: str, user_id: int) -> bool:
    """
    Sign out a socket whose session token has expired.

    Called by the renewal of the worker holding the connection when the
    session's ``exp`` passes. The socket goes as a logout signs it out: the tab
    is no longer signed in over HTTP either. Its session is read again first,
    so a socket that signed in again meanwhile, with a newer token, stays.
    Never raises: the renewal that calls it must carry on.

    :param sid: The socket, on the default namespace.
    :param user_id: The account its expired session was signed in as.
    :return: Whether the socket was signed out.
    """
    try:
        session = await peek_session_user(sid)
        if (
            session is None
            or session.get("user_id") != user_id
            or (expires_at := session.get("expires_at")) is None
            or expires_at > time.time()
        ):
            return False
        await _sign_out(sid, user_id)
        return True
    except Exception:
        runtime.logger.exception(
            f"Failed to sign socket {sid} of user {user_id} out at its session's "
            f"expiry [Worker {os.getpid()}]"
        )
        return False


async def _sign_out(sid: str, user_id: int) -> None:
    """
    Clear a socket's session, take it out of every room it is on record in,
    and tell it so.

    :param sid: The socket, on the default namespace, signed in as ``user_id``.
    :param user_id: The account it is signed in as.
    """
    await clear_user_session(sid)
    # Out of the user's socket index (cleared above) before the others are
    # read, so a room only this socket held goes and one another tab holds
    # stays the user's.
    await room_tracker.release_socket(user_id, sid)
    rooms = await room_tracker.socket_rooms(sid)
    for room in rooms:
        await sio.leave_room(sid, room)
    await room_tracker.forget_socket(sid)
    # Last, so a tab acting on it finds the socket already signed out. The
    # socket's own sid is a room every connection is in, rooms left or not.
    await sio.emit(SIGNED_OUT_EVENT, {}, room=sid, namespace="/")

    runtime.logger.info(
        f"Signed socket {sid} of user {user_id} out of {len(rooms)} room(s) "
        f"[Worker {os.getpid()}]"
    )
