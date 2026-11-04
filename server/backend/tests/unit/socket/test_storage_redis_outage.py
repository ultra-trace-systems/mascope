"""
How the worker heartbeat reports a Redis that stays down.

Every socket storage call logs a failed Redis connection as a warning kept out
of monitoring (``test_storage_redis_failures.py``), so a blip files nothing.
The heartbeat is what reports one that lasts. What must hold:

- ``redis_outage_beats`` failed beats in a row file one monitored warning,
  under its own fingerprint, naming how long Redis has been out of reach;
  fewer file nothing;
- a long outage files that warning once, not once per beat;
- the first beat that succeeds after it files one more, under another
  fingerprint, and the beats after it nothing;
- a blip that ends before the threshold starts the count again;
- a failure that is not Redis out of reach does not count;
- a web socket's connect beats too, but its failure does not count, and its
  success ends the run.

The heartbeat is driven against a Redis stub that fails until told otherwise,
on a fake clock; the records go through the real monitoring sink with the
SDK's two capture calls recorded.
"""

from types import SimpleNamespace
from unittest import mock

import pytest
import sentry_sdk
from redis.exceptions import ConnectionError as RedisConnectionError
from test_utils import captured_logs

from mascope_backend.runtime import runtime
from mascope_backend.socket.storage import workers
from mascope_backend.socket.storage.config import storage_config
from mascope_runtime import logging as runtime_logging


_THRESHOLD = storage_config.redis_outage_beats


class _Redis:
    """A Redis client that raises ``error`` on every command while it is set."""

    def __init__(self):
        self.error: Exception | None = RedisConnectionError("Connection refused.")

    async def setex(self, *args, **kwargs):
        if self.error is not None:
            raise self.error
        return True


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def redis(monkeypatch, clock):
    stub = _Redis()
    monkeypatch.setattr(workers, "redis_storage_client", SimpleNamespace(client=stub))
    monkeypatch.setattr(workers, "_redis_outage", workers.RedisOutage(clock=clock))
    return stub


@pytest.fixture
def monitoring(monkeypatch):
    """
    The real monitoring sink, with what it would send recorded.

    :return: ``(kind, fingerprint)`` per event the sink would have sent.
    """
    sent: list[tuple] = []

    def _record(kind):
        return lambda *args, **kwargs: sent.append(
            (kind, sentry_sdk.get_current_scope()._fingerprint)
        )

    monkeypatch.setattr(sentry_sdk, "capture_exception", _record("exc"))
    monkeypatch.setattr(sentry_sdk, "capture_message", _record("msg"))
    sink_id = runtime.logger.add(
        runtime_logging._sentry_sink, level="WARNING", enqueue=False, catch=False
    )
    try:
        yield sent
    finally:
        runtime.logger.remove(sink_id)


_OUTAGE = ("msg", [workers.OUTAGE_FINGERPRINT])
_RECOVERY = ("msg", [workers.RECOVERY_FINGERPRINT])


class _Stop(Exception):
    pass


async def _beats(count: int, clock: _Clock) -> None:
    """Run the heartbeat's own loop for ``count`` beats on the fake clock."""
    slept = 0

    async def _sleep(seconds: float) -> None:
        nonlocal slept
        if slept == count:
            raise _Stop
        slept += 1
        clock.now += seconds

    with mock.patch.object(workers, "asyncio", SimpleNamespace(sleep=_sleep)):
        with pytest.raises(_Stop):
            await workers._keep_beating()


@pytest.fixture
def connect(monkeypatch, clock):
    """
    A web socket connecting to this worker, its heartbeat already running.

    :return: An async function connecting one socket, 0.3 s after the last.
    """
    monkeypatch.setattr(workers, "_heartbeat_running", lambda: True)

    async def _connect(sid: str = "sid-1") -> None:
        clock.now += 0.3
        await workers.claim_socket(sid)

    return _connect


@pytest.mark.asyncio
async def test_an_outage_is_reported_once_it_lasts_the_threshold(
    redis, clock, monitoring
):
    await _beats(_THRESHOLD - 1, clock)
    assert monitoring == [], "a run shorter than the threshold files nothing"

    with captured_logs(level="WARNING") as records:
        await _beats(1, clock)

    assert monitoring == [_OUTAGE]
    (outage,) = [r for r in records if runtime_logging.SENTRY_SKIP not in r["extra"]]
    assert outage["level"].name == "WARNING"
    seconds = (_THRESHOLD - 1) * storage_config.worker_renewal_interval
    assert f"out of reach for {seconds} s" in outage["message"]
    assert f"{_THRESHOLD} worker heartbeats in a row failed" in outage["message"]


@pytest.mark.asyncio
async def test_a_long_outage_is_reported_once(redis, clock, monitoring):
    await _beats(_THRESHOLD * 50, clock)

    assert monitoring == [_OUTAGE]


@pytest.mark.asyncio
async def test_the_first_beat_that_succeeds_reports_the_outage_over(
    redis, clock, monitoring
):
    await _beats(_THRESHOLD + 10, clock)
    redis.error = None

    with captured_logs(level="WARNING") as records:
        await _beats(1, clock)

    assert monitoring == [_OUTAGE, _RECOVERY]
    (recovery,) = records
    assert recovery["level"].name == "WARNING"
    assert "Redis is back after 3 min out of reach" in recovery["message"]
    assert f"{_THRESHOLD + 10} worker heartbeats failed" in recovery["message"]

    await _beats(5, clock)
    assert monitoring == [_OUTAGE, _RECOVERY], "only the first success reports"


@pytest.mark.asyncio
async def test_a_blip_files_nothing_and_starts_the_count_again(
    redis, clock, monitoring
):
    await _beats(_THRESHOLD - 1, clock)
    redis.error = None
    await _beats(1, clock)
    redis.error = RedisConnectionError("Connection refused.")
    await _beats(_THRESHOLD - 1, clock)

    assert monitoring == []

    await _beats(1, clock)
    assert monitoring == [_OUTAGE]


@pytest.mark.asyncio
async def test_a_failure_that_is_not_redis_out_of_reach_does_not_count(
    redis, clock, monitoring
):
    redis.error = RuntimeError("not a connection problem")
    await _beats(_THRESHOLD * 2, clock)

    assert _OUTAGE not in monitoring
    assert all(kind == "exc" for kind, _ in monitoring), "each fault is its own"


@pytest.mark.asyncio
async def test_connects_during_a_blip_do_not_count_as_heartbeats(
    redis, clock, monitoring, connect
):
    await _beats(1, clock)
    for _ in range(_THRESHOLD * 3):
        await connect()

    assert monitoring == [], "page loads during a blip file nothing"

    await _beats(_THRESHOLD - 2, clock)
    assert monitoring == [], "only the timed beats make up the run"

    await _beats(1, clock)
    assert monitoring == [_OUTAGE]


@pytest.mark.asyncio
async def test_a_connect_that_reaches_redis_reports_the_outage_over(
    redis, clock, monitoring, connect
):
    await _beats(_THRESHOLD, clock)
    redis.error = None

    await connect()

    assert monitoring == [_OUTAGE, _RECOVERY]
