"""
The socket storage's Redis client heals a short blip by itself.

An idle pooled connection that Redis or a proxy closed used to fail the first
command sent over it, and a command that met a Redis restart failed at once:
the client had no health check and no retries. These check that the client is
built with both, and that the options reach the connections it makes.
"""

from unittest.mock import AsyncMock

import pytest
from redis.asyncio import from_url as real_from_url
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from mascope_backend.socket.storage import client as storage_client


@pytest.fixture
def connect_kwargs(monkeypatch):
    """Connect a fresh storage client and return what it built Redis with."""
    seen: dict = {}

    def _from_url(url, **kwargs):
        seen.update(kwargs, url=url)
        return AsyncMock()

    monkeypatch.setattr(storage_client, "from_url", _from_url)

    async def _connect() -> dict:
        await storage_client.RedisStorageClient().connect()
        return seen

    return _connect


@pytest.mark.asyncio
async def test_the_client_retries_connection_errors_and_timeouts(connect_kwargs):
    kwargs = await connect_kwargs()
    url = kwargs.pop("url")

    connection = real_from_url(url, **kwargs).connection_pool.make_connection()

    assert connection.retry.get_retries() == storage_client.COMMAND_RETRIES > 0
    for error in (RedisConnectionError, RedisTimeoutError):
        assert error in connection.retry._supported_errors
        assert error in kwargs["retry_on_error"]


@pytest.mark.asyncio
async def test_the_client_checks_an_idle_connection_before_using_it(connect_kwargs):
    kwargs = await connect_kwargs()
    url = kwargs.pop("url")

    connection = real_from_url(url, **kwargs).connection_pool.make_connection()

    assert connection.health_check_interval == storage_client.HEALTH_CHECK_INTERVAL > 0


@pytest.mark.asyncio
async def test_the_timeouts_still_bound_a_hung_redis(connect_kwargs):
    kwargs = await connect_kwargs()

    assert kwargs["socket_timeout"] == 5
    assert kwargs["socket_connect_timeout"] == 5
