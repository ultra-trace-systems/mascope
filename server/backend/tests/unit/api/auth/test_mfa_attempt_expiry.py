"""
A pending sign-in's wrong-code counter always gets its expiry.

The shared Redis client retries a command whose reply was lost, so the first
INCR of the counter can return 2. The expiry used to be set only on a count of
1, which left such a counter in Redis with no expiry. It is now set on every
attempt, with NX, so a running countdown is kept.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mascope_backend.api.new.auth.mfa import pending


@pytest.fixture
def client(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(pending, "redis_storage_client", SimpleNamespace(client=fake))
    return fake


@pytest.mark.parametrize("count", [1, 2], ids=["first", "retried-first"])
@pytest.mark.asyncio
async def test_every_attempt_sets_the_expiry_unless_it_runs(client, count):
    client.incr.return_value = count

    await pending.register_failed_attempt("jti-1")

    client.expire.assert_awaited_once_with(
        pending._attempt_key("jti-1"),
        pending.mfa_settings.PENDING_TOKEN_LIFETIME_SECONDS,
        nx=True,
    )
