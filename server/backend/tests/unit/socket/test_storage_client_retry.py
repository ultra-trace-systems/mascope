"""
The socket storage's Redis client heals a short blip by itself.

An idle pooled connection that Redis or a proxy closed used to fail the first
command sent over it: the client had no retries. These check that the client
is built with them, that a dropped connection is replaced, and that the
retries still bound how long a Redis that never answers can stall a call.
"""

import asyncio
import time
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


class _FakeRedis:
    """
    A local listener standing in for Redis; counts the connections it takes.

    ``answer`` replies ``+OK`` to every command; otherwise it reads and never
    replies. ``close_first`` drops the first connection once it has answered a GET,
    as Redis or a proxy drops one that sat idle.
    """

    def __init__(self, *, answer: bool, close_first: bool = False):
        self.answer = answer
        self.close_first = close_first
        self.connections = 0
        self._server: asyncio.Server | None = None

    async def _handle(self, reader, writer):
        self.connections += 1
        number = self.connections
        try:
            while data := await reader.read(65536):
                if not self.answer:
                    continue
                # One reply per command; the connect handshake pipelines some.
                commands = data.count(b"\r\n*") + data.startswith(b"*")
                writer.write(b"+OK\r\n" * commands)
                await writer.drain()
                if self.close_first and number == 1 and b"GET" in data:
                    break
        finally:
            writer.close()

    async def __aenter__(self) -> str:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"redis://127.0.0.1:{port}"

    async def __aexit__(self, *exc):
        self._server.close()


@pytest.mark.asyncio
async def test_a_connection_redis_dropped_is_replaced(connect_kwargs):
    kwargs = await connect_kwargs()
    kwargs.pop("url")
    fake = _FakeRedis(answer=True, close_first=True)

    async with fake as url:
        client = real_from_url(url, **kwargs)
        assert await client.get("a") == "OK"
        await asyncio.sleep(0.05)  # the server has dropped that connection
        assert await client.get("b") == "OK"
        await client.aclose()

    assert fake.connections == 2


@pytest.mark.asyncio
async def test_a_redis_that_never_answers_costs_one_attempt_per_retry(
    connect_kwargs, monkeypatch
):
    """
    redis-py runs the client's Retry inside each command attempt as well, around
    a health-check PING when ``health_check_interval`` is set, so the attempts
    multiplied: 9 connections for a GET with 2 retries. The stall bound in
    ``RedisStorageClient.connect`` holds only while they add up.
    """
    monkeypatch.setattr(storage_client, "SOCKET_TIMEOUT", 0.2)
    kwargs = await connect_kwargs()
    kwargs.pop("url")
    fake = _FakeRedis(answer=False)

    async with fake as url:
        client = real_from_url(url, **kwargs)
        started = time.monotonic()
        with pytest.raises(RedisTimeoutError):
            await client.get("a")
        elapsed = time.monotonic() - started
        await client.aclose()

    attempts = storage_client.COMMAND_RETRIES + 1
    assert fake.connections == attempts
    # Each attempt waits out the timeout once, plus a backoff under 0.2 s.
    assert elapsed < attempts * 0.2 + 0.2 + 0.3


@pytest.mark.asyncio
async def test_the_timeouts_still_bound_a_hung_redis(connect_kwargs):
    kwargs = await connect_kwargs()

    assert kwargs["socket_timeout"] == 5
    assert kwargs["socket_connect_timeout"] == 5
