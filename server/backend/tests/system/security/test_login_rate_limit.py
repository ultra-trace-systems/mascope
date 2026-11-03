"""Tests: per-account login throttling (``enforce_login_rate_limit``).

Complements the per-IP login limiter: it caps attempts against a single
identifier regardless of source IP, so a distributed credential-stuffing attack
on one account is blunted once the network firewall is removed. Fails open on
Redis errors and no-ops on an empty login identifier. Keys are
hashed (never the raw identifier) and a successful login clears the counter so
only failed attempts count.
"""

import hashlib

import pytest
from fastapi import HTTPException

from mascope_backend.api.lib import rate_limit as rl


class FakeRedis:
    """Minimal in-memory stand-in for the fixed-window Redis operations."""

    def __init__(self, *, fail: bool = False):
        self.counts: dict[str, int] = {}
        self.fail = fail

    async def incr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return 42

    async def delete(self, key: str) -> int:
        return 1 if self.counts.pop(key, None) is not None else 0


@pytest.fixture
def fake_redis(monkeypatch):
    # Patches the client's private ``_client`` attribute (what the ``client``
    # property returns) rather than a public seam - if the storage client is
    # ever refactored, this fixture is why these tests break.
    redis = FakeRedis()
    monkeypatch.setattr(rl.redis_storage_client, "_client", redis)
    return redis


async def _call_n(identifier, n, *, times):
    """Attempt ``n`` logins as ``identifier``, returning the last raised status (or None)."""
    status_code = None
    for _ in range(n):
        try:
            await rl.enforce_login_rate_limit(identifier, times=times, seconds=60)
        except HTTPException as exc:
            status_code = exc.status_code
    return status_code


@pytest.mark.asyncio
async def test_blocks_account_after_limit(fake_redis):
    # First two attempts pass; the third is blocked with 429 + Retry-After.
    assert await _call_n("victim@test.com", 2, times=2) is None
    with pytest.raises(HTTPException) as exc:
        await rl.enforce_login_rate_limit("victim@test.com", times=2, seconds=60)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "42"


@pytest.mark.asyncio
async def test_distinct_accounts_are_independent(fake_redis):
    await _call_n("victim@test.com", 3, times=2)  # drive victim over the limit
    # A different account is unaffected.
    assert await _call_n("someone-else@test.com", 2, times=2) is None


@pytest.mark.asyncio
async def test_identifier_is_normalized(fake_redis):
    # Same account with different casing/whitespace shares one counter.
    await _call_n("  Victim@Test.com ", 1, times=2)
    await _call_n("victim@test.com", 1, times=2)
    assert await _call_n("VICTIM@TEST.COM", 1, times=2) == 429


@pytest.mark.asyncio
async def test_redis_key_is_hashed_and_fixed_size(fake_redis):
    """Keys embed a digest, never the raw (attacker-controlled) identifier.

    Hashing keeps key size fixed no matter how long the submitted username is,
    and keeps the typed value (occasionally a password) out of Redis keyspace.
    """
    identifier = "victim@test.com" + "x" * 10_000
    await _call_n(identifier, 1, times=5)

    (key,) = fake_redis.counts.keys()
    assert identifier not in key
    assert "victim" not in key
    digest = hashlib.sha256(identifier.strip().lower().encode()).hexdigest()
    assert key == f"mascope:ratelimit:auth-login-account:{digest}"


@pytest.mark.asyncio
async def test_successful_login_clears_the_counter(fake_redis):
    """clear_login_rate_limit resets the account budget (failures-only count).

    Driven over the limit, then cleared as on_after_login does on a successful
    sign-in: the account may immediately attempt again.
    """
    assert await _call_n("victim@test.com", 3, times=2) == 429  # over the limit

    await rl.clear_login_rate_limit("victim@test.com")
    assert await _call_n("victim@test.com", 2, times=2) is None  # budget restored


@pytest.mark.asyncio
async def test_clear_normalizes_like_the_limiter(fake_redis):
    """Clearing with a differently-cased identifier hits the same counter."""
    await _call_n("victim@test.com", 2, times=1)

    await rl.clear_login_rate_limit("  Victim@Test.com ")
    assert fake_redis.counts == {}


@pytest.mark.asyncio
async def test_clear_swallows_redis_errors(monkeypatch):
    """Clearing is best-effort: a Redis outage must not fail the login."""
    monkeypatch.setattr(rl.redis_storage_client, "_client", FakeRedis(fail=True))

    async def failing_delete(key):
        raise ConnectionError("redis down")

    monkeypatch.setattr(rl.redis_storage_client._client, "delete", failing_delete)
    await rl.clear_login_rate_limit("victim@test.com")  # never raises


@pytest.mark.asyncio
async def test_empty_identifier_is_noop(fake_redis):
    # Nothing to key on: never counted, never raises.
    assert await _call_n("", 5, times=1) is None
    assert fake_redis.counts == {}


@pytest.mark.asyncio
async def test_fails_open_on_redis_error(monkeypatch):
    monkeypatch.setattr(rl.redis_storage_client, "_client", FakeRedis(fail=True))
    # Redis unavailable must not lock users out.
    assert await _call_n("victim@test.com", 5, times=1) is None
