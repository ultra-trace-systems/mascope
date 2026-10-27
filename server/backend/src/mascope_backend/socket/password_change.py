"""
Telling open tabs that a forced password change was armed or withdrawn.

Arming and withdrawing the requirement are bulk updates, which fire no ORM
events, so nothing reaches a browser until its next request unless whoever
made the change says so. Three callers do: the owner's route, and the
``require_password_change`` and ``clear_password_change_requirement``
maintenance scripts. They share this loop so that they cannot drift apart.

For each account, its sockets go through :func:`evict_ended_sessions` first.
Once armed, that evicts them from every room but ``user-{id}``, so nothing more
reaches them. Once withdrawn, nothing is evicted, and a socket signed in at
the password screen is let back into the signed-in room, so it hears the
room-less broadcasts again; its resource rooms come back as the tab subscribes.
A socket open since before the arming stays where it is: arming ended its
session, so its tab is refused when it re-fetches and signs in again.
Then the account's tab hears on its own room that its record changed, and the
auth store re-fetches the current user, which moves the tab to the password
screen or off it. Last, open user-management tables are told to reload once.

A maintenance script serves no sockets, but the Socket.IO server's Redis
manager publishes each ``leave_room`` and emit to the workers that hold them.
:func:`notify_password_change_from_script` connects that Redis first.
"""

from collections.abc import Sequence

from mascope_backend.runtime import runtime
from mascope_backend.socket.eviction import evict_ended_sessions
from mascope_backend.socket.records.service import (
    emit_record_reload,
    emit_record_updated,
)
from mascope_backend.socket.storage import redis_storage_client


async def notify_password_change(user_ids: Sequence[int], *, required: bool) -> int:
    """
    Evict and notify the open sockets of the accounts just armed or released.

    :param user_ids: The accounts whose requirement changed.
    :param required: Whether they now owe a password change.
    :return: How many sockets were evicted.
    """
    evicted = 0
    for user_id in user_ids:
        evicted += await evict_ended_sessions(user_id)
        # The auth store re-fetches the current user on this event and ignores
        # the payload, so a minimal record is enough.
        await emit_record_updated(
            record_type="user_me",
            record_id=str(user_id),
            record={"id": user_id, "must_change_password": required},
            room=f"user-{user_id}",
        )
    # Open user-management tables would otherwise show stale flags.
    await emit_record_reload(record_type="user")
    return evicted


async def notify_password_change_from_script(
    user_ids: Sequence[int], *, required: bool
) -> bool:
    """
    :func:`notify_password_change` from a process that serves no sockets.

    A Redis that cannot be reached is reported, not raised: the change already
    stands, and the open sessions are no worse off than before.

    :param user_ids: The accounts whose requirement changed.
    :param required: Whether they now owe a password change.
    :return: Whether the open sessions were notified.
    """
    try:
        await redis_storage_client.connect()
    except Exception:
        runtime.logger.exception(
            "Could not reach Redis: open sockets keep their rooms until they "
            "reconnect, and open tabs are not notified"
        )
        return False
    try:
        evicted = await notify_password_change(user_ids, required=required)
    finally:
        await redis_storage_client.disconnect()
    if required:
        runtime.logger.info(f"Open sockets evicted from their rooms: {evicted}")
    runtime.logger.info(f"Accounts notified on their own room: {len(user_ids)}")
    return True
