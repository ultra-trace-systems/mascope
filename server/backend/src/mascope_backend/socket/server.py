"""Socket.IO server initialization and configuration."""

import json as _json
import math

import socketio

from mascope_backend.origins import is_allowed_dev_origin
from mascope_backend.runtime import runtime
from mascope_backend.socket.logging import get_socket_logger


class _NanSafeJson:
    """JSON codec that renders non-finite floats (NaN, Infinity) as ``null``.

    Python's default JSON encoder emits the bare literals ``NaN`` / ``Infinity``,
    which are invalid JSON. The browser's socket.io parser rejects such a packet
    with a "parse error" and disconnects, so any emitted payload carrying a
    non-finite float - e.g. an unmatched isotope's ``match_mz_error`` in a
    composition-search notification - would silently break the client socket.
    Sanitising on ``dumps`` keeps every server->client packet valid JSON;
    ``loads`` (client->server) stays standard.
    """

    @staticmethod
    def _sanitize(obj):
        if isinstance(obj, float):
            return obj if math.isfinite(obj) else None
        if isinstance(obj, dict):
            return {key: _NanSafeJson._sanitize(value) for key, value in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_NanSafeJson._sanitize(value) for value in obj]
        return obj

    @staticmethod
    def dumps(obj, **kwargs):
        return _json.dumps(_NanSafeJson._sanitize(obj), **kwargs)

    @staticmethod
    def loads(*args, **kwargs):
        return _json.loads(*args, **kwargs)


def _allowed_origins():
    """
    Origin policy for the Socket.IO handshake.

    Engine.IO refuses a handshake whose ``Origin`` is not allowed, which is the
    only server-side cross-site check the realtime channel has: the auth cookie
    is ``SameSite=lax``, so without this the browser's default is the sole thing
    standing between a hostile page and a credentialed connection.

    In production the app is same-origin, so ``None`` is exactly right - Engine.IO
    then allows only the deployment's own origin, reconstructed per request from
    ``X-Forwarded-Proto`` + ``X-Forwarded-Host`` (nginx sets both; without the
    latter it would compare against the upstream name and refuse every browser).
    Deriving it per request keeps the check host-agnostic, so a deployment on any
    hostname works with no configuration. The accepted set is that forwarded
    origin *plus* the unforwarded one the request arrived with, which behind
    nginx is the internal upstream name - reachable only from inside the Docker
    network, since the backend publishes no host port.

    Development serves the app from Vite on another port, so those origins are
    named explicitly. A predicate rather than a list because the tailnet case is
    a pattern; Engine.IO calls it with the origin (and the WSGI environ, which we
    do not need).

    Note that ``[]`` would be wrong for "deny all": Engine.IO skips validation
    entirely when the setting is an empty list.

    :return: A predicate in dev, or ``None`` to enforce same-origin in prod.
    """
    if runtime.mode == "dev":
        return lambda origin, *_: is_allowed_dev_origin(origin)
    return None


def create_socket_server() -> socketio.AsyncServer:
    """
    Create Socket.IO server with Redis for multi-worker coordination.

    Redis is required for all deployment modes (dev/prod) as it handles:
    - Cross-worker Socket.IO event routing (pub/sub) - socketio.AsyncRedisManager redis client
    - Session storage and RBAC validation - RedisStorageClient (see socket/storage/client.py)

    :return: Configured Socket.IO server instance
    :rtype: socketio.AsyncServer
    :raises RuntimeError: If Redis is not configured properly
    """
    if not runtime.config.redis or runtime.config.redis.get_redis_url() is None:
        raise RuntimeError(
            "Redis configuration is required for Socket.IO server. "
            "Check that Redis is configured in your mascope.toml file."
        )
    redis_url = runtime.config.redis.get_redis_url()

    try:
        client_manager = socketio.AsyncRedisManager(redis_url)
        runtime.logger.info(f"Socket server: Connected to Redis at {redis_url}")
    except Exception as e:
        raise RuntimeError(f"Failed to connect to Redis at {redis_url}: {e}.") from e

    return socketio.AsyncServer(
        async_mode="asgi",  # run in ASGI mode
        cors_allowed_origins=_allowed_origins(),
        json=_NanSafeJson,  # emit valid JSON (NaN/Infinity -> null); see class docstring
        client_manager=client_manager,  # Redis manager for multi-worker
        namespaces=[
            "/",  # default namespace for general communication user socket client -> mascope server
            "/file-converter",  # namespace for file converter service
            "/tof-agent",  # namespace for TOF-instrument agent
        ],
        ping_timeout=300,  # 5 minutes for ping response
        logger=get_socket_logger(),
        engineio_logger=False,
    )


# Main Socket.IO server instance
sio = create_socket_server()
