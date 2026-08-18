"""
Service presence tracking with Redis storage.

Tracks which backend services (e.g., file-converter) are connected via
Socket.IO. Each service writes a Redis key on connect and deletes it on
disconnect, allowing any worker to check service availability.

The presence key carries a TTL and is refreshed by a renewal task that lives
exactly as long as the connection (started on connect, cancelled on disconnect).
A graceful disconnect deletes the key immediately; the TTL is the backstop for
the case a disconnect handler never runs - an unclean backend death (power loss,
OOM, `docker kill`) - where the renewal task dies with the process and the key
lapses on its own rather than reporting a service that is no longer there.
"""

import asyncio
import os

from mascope_backend.runtime import runtime
from mascope_backend.socket.storage.client import redis_storage_client
from mascope_backend.socket.storage.config import storage_config


async def _write_presence_key(service_name: str, sid: str) -> bool:
    """Write (or refresh) a service's presence key with its TTL.

    Shared by the initial registration and every renewal. The write is
    unconditional, so a renewal from any live connection also restores a key
    that a co-located connection's disconnect deleted, which keeps availability
    true for as long as some connection of the service is renewing it.
    """
    key = storage_config.service_key(service_name)
    try:
        await redis_storage_client.client.set(key, sid, ex=storage_config.service_ttl)
        return True
    except Exception as e:
        runtime.logger.exception(
            f"Failed to write presence for '{service_name}': {e} [Worker {os.getpid()}]"
        )
        return False


async def register_service(service_name: str, sid: str) -> bool:
    """
    Register a service as connected in Redis.

    Called from the Socket.IO connect handler when a service successfully
    authenticates on its namespace. The caller should treat a failed
    registration as grounds to refuse the connection: an accepted-but-
    unregistered service would read as absent to every availability check for
    as long as its socket lives, whereas a refused client reconnects and
    registers afresh.

    The key is written with a TTL; call :func:`start_presence_renewal` after a
    successful registration so it is refreshed for as long as the service stays
    connected.

    :param service_name: Service identifier (e.g., "file-converter")
    :type service_name: str
    :param sid: Socket.IO session ID of the connected service
    :type sid: str
    :return: True when the presence key was written
    :rtype: bool
    """
    worker_pid = os.getpid()
    if await _write_presence_key(service_name, sid):
        runtime.logger.debug(
            f"Service '{service_name}' registered (sid={sid}) [Worker {worker_pid}]"
        )
        return True
    return False


# Renewal tasks keyed by (service_name, sid), one per live connection on this
# worker. Kept only so the disconnect handler can cancel the matching task.
_renewal_tasks: dict[tuple[str, str], asyncio.Task] = {}


async def _renew_presence(service_name: str, sid: str) -> None:
    """Refresh a service's presence TTL until the task is cancelled."""
    while True:
        await asyncio.sleep(storage_config.service_renewal_interval)
        # A transient Redis failure is logged inside and retried on the next
        # tick; the key may lapse until then, which reads as a (recoverable)
        # unavailability, never as a stale positive.
        await _write_presence_key(service_name, sid)


def start_presence_renewal(service_name: str, sid: str) -> None:
    """Begin refreshing a service's presence TTL for the life of its connection.

    Call once after a successful :func:`register_service`. Idempotent per
    (service_name, sid): a lingering task for the same key is cancelled first.
    """
    _cancel_renewal_task(service_name, sid)
    _renewal_tasks[(service_name, sid)] = asyncio.create_task(
        _renew_presence(service_name, sid)
    )


def _cancel_renewal_task(service_name: str, sid: str) -> asyncio.Task | None:
    task = _renewal_tasks.pop((service_name, sid), None)
    if task is not None:
        task.cancel()
    return task


async def stop_presence_renewal(service_name: str, sid: str) -> None:
    """Stop a service's presence renewal.

    Call from the disconnect handler *before* :func:`unregister_service`, so a
    renewal in flight cannot rewrite the key the unregister is about to delete.
    """
    task = _cancel_renewal_task(service_name, sid)
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass


# Lua script for check-and-delete operation on disconnect.
# Deletes the key only when its value matches the given sid,
# preventing a stale disconnect if the service reconnected with a new sid
# before the disconnect handler ran.
_UNREGISTER_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


async def unregister_service(service_name: str, sid: str) -> None:
    """
    Called from the Socket.IO disconnect handler when a service disconnects.

    Removes a service's presence key from Redis only if the stored sid
    matches the disconnecting sid.

    :param service_name: Service identifier (e.g., "file-converter")
    :type service_name: str
    :param sid: Socket.IO session ID of the disconnecting service
    :type sid: str
    """
    worker_pid = os.getpid()
    key = storage_config.service_key(service_name)

    try:
        deleted = await redis_storage_client.client.eval(
            _UNREGISTER_SCRIPT, 1, key, sid
        )
        if deleted:
            runtime.logger.debug(
                f"Service '{service_name}' unregistered (sid={sid}) [Worker {worker_pid}]"
            )
        else:
            runtime.logger.debug(
                f"Service '{service_name}' disconnect skipped — sid mismatch "
                f"(disconnecting={sid}) [Worker {worker_pid}]"
            )
    except Exception as e:
        runtime.logger.exception(
            f"Failed to unregister service '{service_name}': {e} [Worker {worker_pid}]"
        )


async def is_service_connected(service_name: str) -> bool:
    """
    Check if a service is currently connected (cross-worker).

    Reads a Redis key that is set on connect and deleted on disconnect.
    Works from any worker because Redis is shared state.

    :param service_name: Service identifier (e.g., "file-converter")
    :type service_name: str
    :return: True if the service has an active connection
    :rtype: bool
    """
    key = storage_config.service_key(service_name)

    try:
        return bool(await redis_storage_client.client.exists(key))
    except Exception as e:
        runtime.logger.exception(
            f"Failed to check service '{service_name}' presence: {e}"
        )
        return False
