"""Tests: a worker's shutdown drops the sign-outs it kept for a retry, and says so.

An eviction or a logout's sign-out that found Redis out of reach is kept by
the worker that tried it (``mascope_backend.socket.eviction``). The worker's
lifespan stops that on shutdown, before the Redis client closes. The start's
other tasks are stubbed, as in ``test_weakened_start_log.py``.
"""

import sys

import pytest

from mascope_backend.api.controllers.sample.files.process import service as process
from mascope_backend.app.fast import fast


_FAST_MODULE = sys.modules["mascope_backend.app.fast"]


@pytest.mark.asyncio
async def test_the_lifespan_stops_the_sign_out_retries_before_redis_closes(
    monkeypatch,
):
    order: list[str] = []

    async def _nothing(*_args, **_kwargs) -> None:
        return None

    async def _stop_retries() -> None:
        order.append("retries")

    async def _disconnect() -> None:
        order.append("redis")

    monkeypatch.setattr(_FAST_MODULE, "init_db", _nothing)
    monkeypatch.setattr(_FAST_MODULE.redis_storage_client, "connect", _nothing)
    monkeypatch.setattr(_FAST_MODULE.redis_storage_client, "disconnect", _disconnect)
    monkeypatch.setattr(_FAST_MODULE, "stop_worker_heartbeat", _nothing)
    monkeypatch.setattr(_FAST_MODULE, "stop_sign_out_retries", _stop_retries)
    monkeypatch.setattr(process, "drain_auto_process_tasks", _nothing)

    async with _FAST_MODULE.lifespan(fast):
        assert order == []

    assert order == ["retries", "redis"]
