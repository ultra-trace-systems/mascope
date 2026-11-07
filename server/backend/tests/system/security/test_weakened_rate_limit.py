"""Tests: the weakened target's limiter switch, wired into every limit.

``MASCOPE_PENTEST_RATE_LIMIT_OFF`` (``mascope_backend.weakened_controls``) lets
the security suite's weakened target show that RL-01 to RL-03 fail when the
backend's limiter is gone. On a demo stack it must turn off each kind of limit
the backend has (per address, per account, per user), and count nothing while
it does; without the demo stack's marker the same setting must leave every one
of them refusing as before. The guard's switch is pinned in
``tests/integration/api/test_request_origin.py``.
"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from mascope_backend.api.lib import rate_limit as rl
from mascope_backend.weakened_controls import DEMO_STACK_MARKER, RATE_LIMIT_OFF


class CountingRedis:
    """The fixed window's Redis commands, counted in memory."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int, nx: bool = False) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return 42


@pytest.fixture
def redis(monkeypatch):
    # The client's private attribute, as test_login_rate_limit.py patches it.
    fake = CountingRedis()
    monkeypatch.setattr(rl.redis_storage_client, "_client", fake)
    return fake


def _request() -> Request:
    return Request(
        {"type": "http", "headers": [(b"x-real-ip", b"203.0.113.7")], "client": None}
    )


async def _per_address():
    await rl.rate_limit(times=1, seconds=60, scope="probe")(_request())


async def _per_account():
    await rl.enforce_login_rate_limit("victim@test.com", times=1, seconds=60)


async def _per_user():
    await rl.enforce_user_rate_limit(7, times=1, seconds=60, scope="probe-user")


LIMITS = [
    pytest.param(_per_address, id="per-address"),
    pytest.param(_per_account, id="per-account"),
    pytest.param(_per_user, id="per-user"),
]


async def _refusals(limit, attempts: int = 3) -> int:
    refused = 0
    for _ in range(attempts):
        try:
            await limit()
        except HTTPException as exc:
            assert exc.status_code == 429
            refused += 1
    return refused


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", LIMITS)
async def test_the_switch_on_a_demo_stack_turns_the_limit_off(
    monkeypatch, redis, limit
):
    monkeypatch.setenv(DEMO_STACK_MARKER, "1")
    monkeypatch.setenv(RATE_LIMIT_OFF, "1")
    assert await _refusals(limit) == 0
    # Nothing counted: a limiter switched back on starts from a clean window.
    assert redis.counts == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", LIMITS)
@pytest.mark.parametrize("marker", [None, "0"])
async def test_the_switch_off_a_demo_stack_leaves_the_limit_on(
    monkeypatch, redis, limit, marker
):
    """A production-like environment that carries the switch still refuses."""
    if marker is None:
        monkeypatch.delenv(DEMO_STACK_MARKER, raising=False)
    else:
        monkeypatch.setenv(DEMO_STACK_MARKER, marker)
    monkeypatch.setenv(RATE_LIMIT_OFF, "1")
    assert await _refusals(limit) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", LIMITS)
async def test_a_demo_stack_without_the_switch_keeps_the_limit(
    monkeypatch, redis, limit
):
    monkeypatch.setenv(DEMO_STACK_MARKER, "1")
    monkeypatch.delenv(RATE_LIMIT_OFF, raising=False)
    assert await _refusals(limit) == 2
