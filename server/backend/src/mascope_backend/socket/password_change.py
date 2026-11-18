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
An eviction that finds Redis out of reach once it is connected is kept, to be
tried again when Redis answers, as in a worker
(:mod:`mascope_backend.socket.eviction`); but the script ends when it is done,
so it waits a few retry intervals for those before it disconnects, and says how
many went through on a retry, those that did while it was still notifying
included, and how many it had to drop.
"""

from collections.abc import Sequence

from mascope_backend.runtime import runtime
from mascope_backend.socket.eviction import (
    evict_ended_sessions,
    settle_sign_out_retries,
    sign_out_retries_ever_kept,
    sign_out_retries_kept,
)
from mascope_backend.socket.records.service import (
    emit_record_reload,
    emit_record_updated,
)
from mascope_backend.socket.storage import redis_storage_client
from mascope_backend.socket.storage.config import storage_config


#: How many retry intervals a script waits for its kept evictions to go through.
SCRIPT_RETRY_ROUNDS = 3


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
    stands, and the open sessions are no worse off than before. An eviction
    that found Redis out of reach after it connected is waited for, up to
    :data:`SCRIPT_RETRY_ROUNDS` retry intervals, and reported when dropped.

    :param user_ids: The accounts whose requirement changed.
    :param required: Whether they now owe a password change.
    :return: Whether the open sessions were notified: False when Redis could
        not be reached, or an eviction was dropped.
    """
    try:
        await redis_storage_client.connect()
    except Exception:
        runtime.logger.exception(
            "Could not reach Redis: open sockets keep their rooms until they "
            "reconnect, and open tabs are not notified"
        )
        return False
    kept_before = sign_out_retries_ever_kept()
    try:
        evicted = await notify_password_change(user_ids, required=required)
    finally:
        # While Redis is still connected, and on a failure too: the loop
        # ends with the script, and would cancel the retries without a word.
        kept, dropped = await _settle_kept_evictions(kept_before)
        await redis_storage_client.disconnect()
    if required:
        runtime.logger.info(f"Open sockets evicted from their rooms: {evicted}")
    if kept:
        runtime.logger.info(
            f"Evictions that went through on a retry: {kept - dropped} of {kept}"
        )
    runtime.logger.info(f"Accounts notified on their own room: {len(user_ids)}")
    return not dropped


async def _settle_kept_evictions(kept_before: int) -> tuple[int, int]:
    """
    Wait for the evictions kept to be tried again, then drop what is left.

    One kept early in the run can go through while the script still notifies
    the other accounts, as soon as Redis answers: it is counted as kept and as
    gone through, though nothing is left of it to wait for.

    :param kept_before: :func:`sign_out_retries_ever_kept` before the run.
    :return: How many were kept during the run, and how many of them were
        dropped.
    """
    kept = sign_out_retries_ever_kept() - kept_before
    waiting = sign_out_retries_kept()
    if not waiting:
        return kept, 0
    window = SCRIPT_RETRY_ROUNDS * storage_config.sign_out_retry_interval
    runtime.logger.warning(
        f"{waiting} eviction(s) found Redis out of reach: waiting up to {window} s "
        "for Redis to answer, to try them again"
    )
    dropped = await settle_sign_out_retries(window)
    if dropped:
        runtime.logger.error(
            f"Dropped {dropped} eviction(s) that found Redis out of reach: those "
            "accounts' open sockets keep their rooms until they reconnect or "
            "their token expires"
        )
    return kept, dropped
