"""
Service presence tracking with Redis storage.

Tracks which backend services (e.g., file-converter) are connected via
Socket.IO. Each service writes a Redis key on connect and deletes it on
disconnect, allowing any worker to check service availability.

Lifecycle is managed by Socket.IO connect/disconnect handlers.
Socket.IO ping_timeout guarantees disconnect fires even
on ungraceful shutdown.
"""

import os

from mascope_backend.runtime import runtime
from mascope_backend.socket.storage.client import redis_storage_client
from mascope_backend.socket.storage.config import storage_config


async def register_service(service_name: str, sid: str) -> None:
    """
    Register a service as connected in Redis.

    Called from the Socket.IO connect handler when a service successfully
    authenticates on its namespace.

    :param service_name: Service identifier (e.g., "file-converter")
    :type service_name: str
    :param sid: Socket.IO session ID of the connected service
    :type sid: str
    """
    worker_pid = os.getpid()
    key = storage_config.service_key(service_name)

    try:
        await redis_storage_client.client.set(key, sid)
        runtime.logger.debug(
            f"Service '{service_name}' registered (sid={sid}) [Worker {worker_pid}]"
        )
    except Exception as e:
        runtime.logger.exception(
            f"Failed to register service '{service_name}': {e} [Worker {worker_pid}]"
        )


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


async def get_service_sid(service_name: str) -> str | None:
    """
    Return the Socket.IO sid of a connected service, or ``None`` if absent.

    Reads the same Redis presence key that :func:`register_service` writes, so
    an emitter can target the service's own connection with ``to=`` instead of
    broadcasting a payload to every socket on the namespace. Works from any
    worker because Redis is shared state.

    :param service_name: Service identifier (e.g., "file-converter")
    :type service_name: str
    :return: The registered sid, or ``None`` when the service is not connected
    :rtype: str | None
    """
    key = storage_config.service_key(service_name)

    try:
        return await redis_storage_client.client.get(key)
    except Exception as e:
        runtime.logger.exception(f"Failed to read service '{service_name}' sid: {e}")
        return None


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
