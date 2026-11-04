"""
Which worker holds a web socket, and whether that worker is still alive.

A web socket's session and room record carry a day-long TTL, renewed hourly by
the worker holding the connection. A worker that dies uncleanly (OOM kill,
``docker kill``, power loss) runs no disconnect handler, so what its sockets
left in Redis outlives them by up to a day. Whatever asks whether another of a
user's sockets is still there - a tab closing, eviction - has to tell such a
ghost from a live socket, or the ghost's rooms stay the user's for that day.

Each worker therefore keeps a short-lived heartbeat key, written when the first
web socket connects to it and refreshed while it runs, and each web socket
records the worker that holds it. A socket counts as held while its worker's
heartbeat is there; the heartbeat lapses within ``worker_ttl`` of a death, where
the session would take a day.

The heartbeat is a liveness signal for the worker, not for any one socket: a
socket that dropped without a disconnect on a worker that still runs is cleaned
up by that worker's own disconnect handler. A worker whose event loop stalls
past ``worker_ttl`` reads as dead until it beats again; its sockets then hold
no room for others, which only routes notifications to the user's own room, as
before rooms were counted per socket.

The heartbeat also calls Redis steadily on every worker a web socket reached,
so it is what reports a Redis that stays down. Every storage call logs a failed Redis
connection as a warning kept out of monitoring, since a blip is ridden out; the
heartbeat counts its own failures in a row, and once ``redis_outage_beats`` of
them have failed it logs one monitored warning for the outage, and one more when
a beat succeeds again. Each worker reports its own, under one monitoring issue.

Redis keys:
    mascope:workers:{worker_id} - present while that worker runs
    mascope:sockets:worker:{sid} - the worker holding one web socket
"""

import asyncio
import os
import time
import uuid
from typing import Callable

from mascope_backend.runtime import runtime
from mascope_backend.socket.storage.client import (
    is_transient_redis_error,
    log_storage_failure,
    redis_storage_client,
)
from mascope_backend.socket.storage.config import storage_config
from mascope_runtime.logging import SENTRY_FINGERPRINT


# (pid, id): the id is made again in a forked child, which must not answer for
# its parent's sockets.
_worker: tuple[int, str] | None = None

# This worker's heartbeat task, started by the first web socket to connect.
_heartbeat: asyncio.Task | None = None


#: Monitoring groups of the outage and of its end: one issue each across
#: workers and releases, whichever line logs them.
OUTAGE_FINGERPRINT = "redis-outage:worker-heartbeat"
RECOVERY_FINGERPRINT = "redis-outage-over:worker-heartbeat"


class RedisOutage:
    """
    This worker's run of heartbeats that found Redis out of reach.

    Only a transient failure (:func:`is_transient_redis_error`) counts: any
    other is a fault already reported on its own, and says nothing of whether
    Redis answers.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._clear()

    def _clear(self) -> None:
        self.failed_beats = 0
        self.since: float | None = None
        self.reported = False

    def beat_failed(self) -> None:
        """Count a failed beat; report the outage on the threshold's beat."""
        if self.since is None:
            self.since = self._clock()
        self.failed_beats += 1
        if self.reported or self.failed_beats < storage_config.redis_outage_beats:
            return
        self.reported = True
        runtime.logger.bind(**{SENTRY_FINGERPRINT: [OUTAGE_FINGERPRINT]}).warning(
            f"Redis has been out of reach for {self._lasted()}: "
            f"{self.failed_beats} worker heartbeats in a row failed "
            f"[Worker {os.getpid()}]"
        )

    def beat_succeeded(self) -> None:
        """End the run; report the outage over if it was reported."""
        if self.reported:
            runtime.logger.bind(**{SENTRY_FINGERPRINT: [RECOVERY_FINGERPRINT]}).warning(
                f"Redis is back after {self._lasted()} out of reach: "
                f"{self.failed_beats} worker heartbeats failed "
                f"[Worker {os.getpid()}]"
            )
        self._clear()

    def _lasted(self) -> str:
        seconds = round(self._clock() - self.since)
        if seconds < 120:
            return f"{seconds} s"
        return f"{round(seconds / 60)} min"


_redis_outage = RedisOutage()


def worker_id() -> str:
    """
    This worker process's identity, unique across hosts and restarts.

    :return: An opaque identifier
    :rtype: str
    """
    global _worker
    pid = os.getpid()
    if _worker is None or _worker[0] != pid:
        _worker = (pid, uuid.uuid4().hex)
    return _worker[1]


async def _beat() -> None:
    """Write (or refresh) this worker's heartbeat key."""
    try:
        await redis_storage_client.client.setex(
            storage_config.worker_key(worker_id()), storage_config.worker_ttl, "1"
        )
    except Exception as e:
        # Retried on the next tick. A lapse meanwhile makes this worker's
        # sockets read as gone, which leaves rooms rather than keeping them.
        log_storage_failure(
            f"Worker heartbeat: failed to write it: {e} [Worker {os.getpid()}]",
            e,
        )
        if is_transient_redis_error(e):
            _redis_outage.beat_failed()
    else:
        _redis_outage.beat_succeeded()


async def _keep_beating() -> None:
    """Refresh this worker's heartbeat until cancelled."""
    while True:
        await asyncio.sleep(storage_config.worker_renewal_interval)
        await _beat()


def _heartbeat_running() -> bool:
    return (
        _heartbeat is not None
        and not _heartbeat.done()
        and _heartbeat.get_loop() is asyncio.get_running_loop()
    )


async def claim_socket(sid: str) -> None:
    """
    Record that this worker holds a web socket, and keep its heartbeat going.

    Call from the default namespace's connect handler, before the socket can
    join a room. The heartbeat is written first, so the socket never names a
    worker whose key is missing.

    :param sid: Socket.IO session ID
    :type sid: str
    """
    global _heartbeat
    await _beat()
    if not _heartbeat_running():
        _heartbeat = asyncio.create_task(_keep_beating())
    await renew_socket_claim(sid)


async def renew_socket_claim(sid: str) -> None:
    """
    Write (again) that this worker holds a web socket.

    Call only on the worker holding the connection: from :func:`claim_socket`,
    and from the socket's session renewal on every tick, whether or not it has
    a session. A socket that sat signed out for longer than ``session_ttl``
    would otherwise have lost its record by the time it signs in, and read as
    a ghost for the rest of its connection; an EXPIRE cannot bring back a key
    that has lapsed.

    :param sid: Socket.IO session ID
    :type sid: str
    """
    try:
        await redis_storage_client.client.setex(
            storage_config.socket_worker_key(sid),
            storage_config.session_ttl,
            worker_id(),
        )
    except Exception as e:
        # The socket then reads as gone to the user's other tabs: they leave
        # rooms it is in rather than keep rooms it is not.
        log_storage_failure(
            f"Worker heartbeat: failed to record the worker of socket {sid}: {e} "
            f"[Worker {os.getpid()}]",
            e,
        )


async def socket_is_held(sid: str) -> bool:
    """
    Whether a running worker holds a web socket.

    False for a socket whose worker died without its disconnect handler, and
    for one with no record of its worker.

    :param sid: Socket.IO session ID
    :type sid: str
    :return: True while the socket's worker is alive
    :rtype: bool
    """
    client = redis_storage_client.client
    holder = await client.get(storage_config.socket_worker_key(sid))
    if not holder:
        return False
    return bool(await client.exists(storage_config.worker_key(holder)))


async def release_socket_claim(sid: str) -> None:
    """
    Drop a web socket's worker record (called on disconnect).

    :param sid: Socket.IO session ID
    :type sid: str
    """
    try:
        await redis_storage_client.client.delete(storage_config.socket_worker_key(sid))
    except Exception as e:
        log_storage_failure(
            f"Worker heartbeat: failed to drop the worker of socket {sid}: {e}",
            e,
        )


async def stop_worker_heartbeat() -> None:
    """
    Stop this worker's heartbeat and remove its key (called on shutdown).

    A socket of this worker whose disconnect did not run reads as gone at once,
    rather than after ``worker_ttl``.
    """
    global _heartbeat
    task, _heartbeat = _heartbeat, None
    if task is None:
        # No web socket connected here: no key was written.
        return
    if task.get_loop() is asyncio.get_running_loop():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected: the task was cancelled just above.
            pass
    try:
        await redis_storage_client.client.delete(storage_config.worker_key(worker_id()))
    except Exception as e:
        log_storage_failure(
            f"Worker heartbeat: failed to remove it: {e} [Worker {os.getpid()}]",
            e,
        )
