"""
Fixtures for the user-management integration tests.

Everything in this package writes passwords, and a password written by anyone
other than the account holder now requires that account to replace it. The
shared ``test_users`` fixture is session-scoped, so a test that resets one of
those passwords would otherwise leave the account behind the password gate for
the rest of the run - and every later test authenticating as it would be
refused, in a suite that looks unrelated.

This package also asserts rate-limit budgets, so unlike the rest of the ASGI
test setup - where the app lifespan never runs, Redis is never connected, and
every limiter fails open - the limiter here is pointed at an in-memory stub
that actually counts. Without it the budget tests would stay green no matter
what the limits do, and every guarded request would log a connection stack
trace.
"""

import pytest
import pytest_asyncio

from mascope_backend.db.admin.user.require_password_change import (
    clear_password_change_requirement,
)
from mascope_backend.socket.storage import redis_storage_client


class InMemoryRedis:
    """
    The handful of Redis commands the rate limiter and the socket session
    store use, over plain dicts.

    Nothing expires: every test gets a fresh instance, and the window semantics
    under test are the counting and the clears, not the TTL. The socket session
    store is here because every password write and disable evicts the account's
    sockets, which reads the session index (``socket.eviction``).
    """

    def __init__(self):
        self.values: dict[str, int | str] = {}
        self.sets: dict[str, set[str]] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def decr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return 60

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.values.pop(key, None)
            self.sets.pop(key, None)
        return len(keys)

    async def get(self, key: str):
        return self.values.get(key)

    async def setex(self, key: str, seconds: int, value: str) -> bool:
        self.values[key] = value
        return True

    async def sadd(self, key: str, *members: str) -> int:
        before = len(self.sets.setdefault(key, set()))
        self.sets[key].update(members)
        return len(self.sets[key]) - before

    async def srem(self, key: str, *members: str) -> int:
        current = self.sets.get(key, set())
        removed = len(current & set(members))
        current.difference_update(members)
        return removed

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def sismember(self, key: str, member: str) -> bool:
        return member in self.sets.get(key, set())


@pytest.fixture(autouse=True)
def live_rate_limiter(monkeypatch) -> InMemoryRedis:
    """A per-test limiter backend, so budget assertions mean something."""
    stub = InMemoryRedis()
    monkeypatch.setattr(redis_storage_client, "_client", stub)
    return stub


@pytest_asyncio.fixture(autouse=True)
async def clear_password_change_requirements():
    """Release every account from a pending password change after each test."""
    yield
    await clear_password_change_requirement()
