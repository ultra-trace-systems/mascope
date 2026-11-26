"""
Unit tests for :func:`mascope_backend.db.retry.retry_on_connection_error`, the
retry a run's terminal write goes through while the database refuses a
connection. Which errors it retries is pinned in ``test_finalize_run.py``.
"""

import pytest
from asyncpg.exceptions import TooManyConnectionsError

from mascope_backend.db import retry


@pytest.fixture
def sleeps(monkeypatch):
    waited = []

    async def _sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(retry.asyncio, "sleep", _sleep)
    return waited


@pytest.mark.asyncio
async def test_it_answers_what_the_write_answers_once_it_goes_through(sleeps):
    calls = []

    async def _write():
        calls.append(None)
        if len(calls) < 3:
            raise TooManyConnectionsError("sorry, too many clients already")
        return "written"

    assert await retry.retry_on_connection_error(_write, "write it") == "written"
    assert len(calls) == 3
    assert sleeps == list(retry.CONNECTION_RETRY_DELAYS_S[:2])


@pytest.mark.asyncio
async def test_each_retry_is_logged_with_what_it_writes(monkeypatch, sleeps):
    warned = []
    monkeypatch.setattr(retry.runtime.logger, "warning", warned.append)
    calls = []

    async def _write():
        calls.append(None)
        if len(calls) < 2:
            raise TooManyConnectionsError("sorry, too many clients already")

    await retry.retry_on_connection_error(_write, "mark run 'r' failed")

    [message] = warned
    assert message.startswith("Could not mark run 'r' failed (attempt 1)")
    assert "retrying in 1 s" in message
