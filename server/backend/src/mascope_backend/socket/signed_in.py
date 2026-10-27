"""
The room a record-sync event goes to when its emitter names no room.

Such an event - a workspace created, a target collection changed, a user
updated - used to go to every socket on the default namespace. The handshake
accepts a socket that has not signed in (the SPA connects before its login
form), and one whose account is held at the password screen, so both heard
when records changed on the server. Now it goes to :data:`SIGNED_IN_ROOM`,
which a web socket is in only while its session may read:

- it enters when it authenticates and :func:`socket_hold_reason` finds nothing
  holding it: at connect from its cookie, or at sign-in from the login route;
- eviction makes it leave with its other rooms when the account may no longer
  read, and enters it again when eviction finds the socket may read - the tab
  of a held account that has just changed its password;
- a client cannot ask for it: the ``user-`` prefix is the private channels',
  and subscribe refuses every one but the socket's own.

The room is recorded beside the socket's others (``room_tracker.join_socket``),
which is how eviction, on whichever worker, knows to take it away.
"""

from typing import Optional

from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.storage import room_tracker


#: Under the ``user-`` prefix, which subscribe grants only as ``user-{id}``.
SIGNED_IN_ROOM = "user-*"


async def admit_signed_in(sid: str, user, issued_at: Optional[int]) -> bool:
    """
    Put a web socket in the signed-in room if its session may read, else out.

    :param sid: The socket, on the default namespace.
    :param user: The account it has just authenticated as.
    :param issued_at: The ``iat`` of the token it authenticated with.
    :return: Whether the socket is now in the room.
    """
    # Imported lazily: eviction imports this module to readmit a socket.
    from mascope_backend.socket.eviction import socket_hold_reason

    if (reason := socket_hold_reason(user, issued_at)) is not None:
        runtime.logger.debug(
            f"Socket {sid} left out of the signed-in room: its user {reason}"
        )
        await hold_from_broadcasts(sid)
        return False
    await sio.enter_room(sid, SIGNED_IN_ROOM)
    await room_tracker.join_socket(sid, SIGNED_IN_ROOM)
    return True


async def hold_from_broadcasts(sid: str) -> None:
    """
    Take a web socket out of the signed-in room; harmless if it is not in it.

    :param sid: The socket, on the default namespace.
    """
    await sio.leave_room(sid, SIGNED_IN_ROOM)
    await room_tracker.leave_socket(sid, SIGNED_IN_ROOM)
