"""
Service presence tracking (``socket.storage.services``).

The presence key backs the converter-availability gate that lets upload and
peak-detection routes return 503 instead of accepting work no converter would
receive. It is written with a TTL and refreshed by a renewal task that lives as
long as the connection, so the key cannot outlive the process that wrote it: a
graceful disconnect clears it at once, and an unclean death (power loss, OOM,
``docker kill``) - where no disconnect handler runs - leaves the renewal task
dead and the key lapsing on its own rather than reporting a converter that is
gone.

These drive the functions directly with a fake Redis client; no real Redis,
Socket.IO, or event loop coordination beyond the renewal task itself.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mascope_backend.socket.storage import services
from mascope_backend.socket.storage.config import storage_config


_SERVICE = "file-converter"
_KEY = storage_config.service_key(_SERVICE)


def _fake_redis(monkeypatch) -> AsyncMock:
    """Point the services module at a fake Redis client and return it."""
    client = AsyncMock()
    monkeypatch.setattr(
        services, "redis_storage_client", SimpleNamespace(client=client)
    )
    return client


@pytest.mark.asyncio
async def test_register_writes_the_key_with_a_ttl(monkeypatch):
    """A presence key is always written with an expiry, never as a plain SET."""
    client = _fake_redis(monkeypatch)

    assert await services.register_service(_SERVICE, "sid-1") is True
    client.set.assert_awaited_once_with(_KEY, "sid-1", ex=storage_config.service_ttl)


@pytest.mark.asyncio
async def test_register_returns_false_on_redis_error(monkeypatch):
    """A failed write returns False so the connect handler can refuse the socket."""
    client = _fake_redis(monkeypatch)
    client.set.side_effect = RuntimeError("redis down")

    assert await services.register_service(_SERVICE, "sid-1") is False


@pytest.mark.asyncio
async def test_is_service_connected_reflects_key_existence(monkeypatch):
    client = _fake_redis(monkeypatch)

    client.exists = AsyncMock(return_value=1)
    assert await services.is_service_connected(_SERVICE) is True

    client.exists = AsyncMock(return_value=0)
    assert await services.is_service_connected(_SERVICE) is False


@pytest.mark.asyncio
async def test_is_service_connected_is_false_on_redis_error(monkeypatch):
    """A Redis failure reads as unavailable, never as a stale positive."""
    client = _fake_redis(monkeypatch)
    client.exists = AsyncMock(side_effect=RuntimeError("redis down"))

    assert await services.is_service_connected(_SERVICE) is False


@pytest.mark.asyncio
async def test_renewal_refreshes_the_ttl_until_stopped(monkeypatch):
    """While connected the key is re-written (refreshing its TTL); stopping ends it."""
    client = _fake_redis(monkeypatch)
    monkeypatch.setattr(storage_config, "service_renewal_interval", 0.01)

    services.start_presence_renewal(_SERVICE, "sid-1")
    assert (_SERVICE, "sid-1") in services._renewal_tasks

    await asyncio.sleep(0.05)
    await services.stop_presence_renewal(_SERVICE, "sid-1")

    refreshes = client.set.await_count
    assert refreshes >= 1, "renewal should have refreshed the key at least once"
    # Every refresh carries the TTL.
    for call in client.set.await_args_list:
        assert call.kwargs["ex"] == storage_config.service_ttl

    # After stopping, the loop is gone: no further refreshes, no leaked handle.
    await asyncio.sleep(0.03)
    assert client.set.await_count == refreshes
    assert (_SERVICE, "sid-1") not in services._renewal_tasks


@pytest.mark.asyncio
async def test_starting_renewal_twice_replaces_the_task(monkeypatch):
    """A second start for the same key cancels the first, leaving one live task."""
    _fake_redis(monkeypatch)
    monkeypatch.setattr(storage_config, "service_renewal_interval", 0.01)

    services.start_presence_renewal(_SERVICE, "sid-1")
    first = services._renewal_tasks[(_SERVICE, "sid-1")]
    services.start_presence_renewal(_SERVICE, "sid-1")
    second = services._renewal_tasks[(_SERVICE, "sid-1")]

    assert first is not second

    # The replaced task is cancelled; let it settle, then confirm it is finished.
    await asyncio.sleep(0.02)
    assert first.done()

    await services.stop_presence_renewal(_SERVICE, "sid-1")
    assert second.done()
    assert (_SERVICE, "sid-1") not in services._renewal_tasks


@pytest.mark.asyncio
async def test_stop_renewal_is_safe_when_none_is_running():
    """Stopping a renewal that was never started is a no-op, not an error."""
    await services.stop_presence_renewal(_SERVICE, "never-started")
