"""Tests: a worker of a weakened demo stack says so in its log at start.

The security suite's weakened target turns the rate limiter and the
write-origin guard off on a demo stack (``mascope_backend.weakened_controls``).
A stack running like that must not look healthy in its own log, so each
worker's lifespan announces the switches before it serves. The start's other
tasks (database, Redis, the shutdown drain) are stubbed: what this pins is
that the lifespan makes the announcement, not what it says, which
``tests/unit/test_weakened_controls.py`` covers.
"""

import sys

import pytest
from test_utils import captured_logs

from mascope_backend import weakened_controls as wc
from mascope_backend.api.controllers.sample.files.process import service as process
from mascope_backend.app.fast import fast


#: The module, not the application the package re-exports under its name.
_FAST_MODULE = sys.modules["mascope_backend.app.fast"]


async def _nothing(*_args, **_kwargs) -> None:
    return None


@pytest.fixture
def quiet_start(monkeypatch):
    monkeypatch.setattr(_FAST_MODULE, "init_db", _nothing)
    monkeypatch.setattr(_FAST_MODULE.redis_storage_client, "connect", _nothing)
    monkeypatch.setattr(_FAST_MODULE.redis_storage_client, "disconnect", _nothing)
    monkeypatch.setattr(_FAST_MODULE, "stop_worker_heartbeat", _nothing)
    monkeypatch.setattr(process, "drain_auto_process_tasks", _nothing)


async def _started_with(monkeypatch, **env) -> list[tuple[str, str]]:
    for name in (wc.DEMO_STACK_MARKER, *wc.SWITCHES):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with captured_logs(level="DEBUG") as records:
        async with _FAST_MODULE.lifespan(fast):
            pass
    return [
        (r["level"].name, r["message"]) for r in records if r["function"] == "announce"
    ]


@pytest.mark.asyncio
async def test_a_weakened_worker_announces_it_at_start(monkeypatch, quiet_start):
    announced = await _started_with(
        monkeypatch, **{wc.DEMO_STACK_MARKER: "1", wc.ORIGIN_GUARD_OFF: "1"}
    )
    assert len(announced) == 1
    level, message = announced[0]
    assert level == "WARNING" and message.startswith("WEAKENED STACK: ")


@pytest.mark.asyncio
async def test_a_healthy_worker_announces_nothing(monkeypatch, quiet_start):
    assert await _started_with(monkeypatch, **{wc.DEMO_STACK_MARKER: "1"}) == []
