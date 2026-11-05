"""Redis client for cross-worker session storage.

Manages Redis connection for socket-related state that must persist across
multiple uvicorn workers. This client is separate from the AsyncRedisManager
used by Socket.IO for pub/sub event coordination.

Usage:
- Authentication sessions (user_id, username, role_id)
- Room membership tracking

Redis key namespaces:
- mascope:session:*  - Authentication sessions
- mascope:rooms:*    - Room membership
"""

from redis.asyncio import Redis, from_url
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialWithJitterBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from mascope_backend.runtime import runtime
from mascope_backend.socket.storage.config import storage_config


#: Retries of a command that failed with a connection error or a timeout. One
#: is enough to replace a pooled connection Redis or a proxy closed while it
#: sat idle. Every command the socket storage sends is safe to send again
#: (SET, EXPIRE, DEL, SADD, SREM, a check-and-delete script).
COMMAND_RETRIES = 1

#: Seconds one attempt waits to connect, or for a reply, before it fails.
SOCKET_TIMEOUT = 5


class RedisStorageClient:
    """
    Redis client manager for Socket.IO session storage.

    Provides a single Redis connection shared by multiple storage concerns
    (sessions, rooms, etc.). Each concern uses its own key namespace prefix
    for isolation while sharing the connection pool.

    Connection lifecycle managed by FastAPI lifespan (app/fast.py).
    """

    def __init__(self):
        """Initialize Redis client (not connected)."""
        self._client: Redis | None = None

    async def connect(self) -> None:
        """
        Connect to Redis server.

        :raises RuntimeError: If Redis is not configured
        :raises ConnectionError: If Redis connection fails
        """
        if not runtime.config.redis or runtime.config.redis.get_redis_url() is None:
            raise RuntimeError(
                "Redis configuration is required for session storage. "
                "Check that Redis is configured in your mascope.toml file."
            )

        redis_url = runtime.config.redis.get_redis_url()

        try:
            # This client is consulted inline by API requests (e.g. the
            # converter-availability gate before uploads, the login rate
            # limiter). No blocking Redis commands are used on it, so the
            # timeouts bound how long a hung Redis can stall a request: at
            # most (COMMAND_RETRIES + 1) attempts of SOCKET_TIMEOUT, 10 s,
            # plus a backoff under 0.2 s. The retry replaces a connection
            # Redis dropped while it sat idle, so that call does not fail.
            # No health_check_interval: redis-py runs the same Retry around
            # its health-check PING inside each command attempt, so a Redis
            # that accepts and never answers cost (retries + 1) ** 2 attempts.
            self._client = from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=SOCKET_TIMEOUT,
                socket_connect_timeout=SOCKET_TIMEOUT,
                retry=Retry(
                    ExponentialWithJitterBackoff(cap=1.0, base=0.1),
                    COMMAND_RETRIES,
                ),
                retry_on_error=[RedisConnectionError, RedisTimeoutError],
            )
            # Test connection
            await self._client.ping()

            ttl_hours = storage_config.session_ttl / 3600
            runtime.logger.info(
                f"Redis session client connected at {redis_url} "
                f"(session TTL: {ttl_hours:.1f} hours)"
            )
        except Exception as e:
            # No log here: the raised ConnectionError is logged by the caller
            # (e.g. the FastAPI lifespan startup handler)
            raise ConnectionError(f"Redis connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Close Redis connection gracefully."""
        if self._client:
            await self._client.aclose()
            runtime.logger.info("Redis session client disconnected")

    @property
    def client(self) -> Redis:
        """
        Get Redis client instance.

        :return: Connected Redis client
        :rtype: Redis
        :raises RuntimeError: If client is not connected
        """
        if not self._client:
            raise RuntimeError("Redis client not connected. Call connect() first.")
        return self._client


# Global Redis session client instance
redis_storage_client = RedisStorageClient()
