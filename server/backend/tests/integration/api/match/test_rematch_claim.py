"""Integration tests for the cross-process rematch claim.

The claim is a session-level Postgres advisory lock, so a second holder
refused while the first holds, and the lock gone from Postgres once the
holder exits, can only be pinned against a real Postgres. Each claim opens
its own pooled connection, the same shape as two claims in two workers. The
rematch's use of it is covered in
``tests/integration/api/sample/test_batch_status_never_lower.py``.
"""

import asyncio

import anyio
import pytest
from sqlalchemy import text

from mascope_backend.api.controllers.match.lib import rematch_claim as claim_module
from mascope_backend.api.controllers.match.lib.rematch_claim import rematch_claim
from mascope_backend.db import async_session


async def _held(sample_batch_id: str) -> int:
    """How many connections hold the batch's claim in Postgres."""
    async with async_session() as session:
        return await session.scalar(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                " AND classid = hashtext('mascope_rematch')::oid"
                " AND objid = hashtext(:batch)::oid AND objsubid = 2"
            ),
            {"batch": sample_batch_id},
        )


@pytest.mark.asyncio
async def test_a_second_claim_is_refused_while_the_first_holds():
    async with rematch_claim("rc-batch-1") as first:
        assert first is True
        async with rematch_claim("rc-batch-1") as second:
            assert second is False
        # A refused claim does not release the holder's lock
        async with rematch_claim("rc-batch-1") as third:
            assert third is False


@pytest.mark.asyncio
async def test_the_claim_is_released_on_exit():
    async with rematch_claim("rc-batch-2") as first:
        assert first is True
        assert await _held("rc-batch-2") == 1
    # Unlocked on its connection, not left on one the pool hands out again
    assert await _held("rc-batch-2") == 0
    async with rematch_claim("rc-batch-2") as again:
        assert again is True


@pytest.mark.asyncio
async def test_the_claim_is_released_when_the_run_raises():
    with pytest.raises(RuntimeError):
        async with rematch_claim("rc-batch-3") as acquired:
            assert acquired is True
            raise RuntimeError("boom")
    assert await _held("rc-batch-3") == 0


@pytest.mark.asyncio
async def test_batches_are_claimed_apart():
    async with rematch_claim("rc-batch-4") as one:
        assert one is True
        async with rematch_claim("rc-batch-5") as other:
            assert other is True


@pytest.mark.asyncio
@pytest.mark.parametrize("taken", [True, False], ids=["held", "refused"])
async def test_a_cancelled_claim_closes_its_session(monkeypatch, taken):
    """The app runs a rematch inside the http middlewares' anyio task group,
    whose cancellation is level-triggered: every await of the cancelled task
    raises again, so a release made of two shielded awaits stops at the first
    and never closes the session (#3190)."""
    sessions = []

    def recording_session():
        session = async_session()
        sessions.append(session)
        return session

    monkeypatch.setattr(claim_module, "async_session", recording_session)
    batch = f"rc-batch-cancel-{taken}"
    holder = None
    if not taken:
        # Another run holds the batch, so the cancelled claim is a refused one
        holder = rematch_claim(batch)
        assert await holder.__aenter__() is True
    inside = asyncio.Event()

    async def run():
        async with rematch_claim(batch) as acquired:
            assert acquired is taken
            inside.set()
            await asyncio.Event().wait()

    before = asyncio.all_tasks()
    async with anyio.create_task_group() as tg:
        tg.start_soon(run)
        await inside.wait()
        tg.cancel_scope.cancel()
    # The shielded release runs on past the cancelled task: wait for it
    await asyncio.gather(*(asyncio.all_tasks() - before - {asyncio.current_task()}))

    if holder is not None:
        await holder.__aexit__(None, None, None)
    # The cancelled claim's session (the last one, a held claim's only one)
    # has given its connection back, and no claim is left in Postgres
    cancelled = sessions[-1]
    assert not cancelled.in_transaction()
    assert await _held(batch) == 0


@pytest.mark.asyncio
async def test_a_failed_unlock_drops_the_connection_and_its_lock(monkeypatch):
    """An unlock that raises may leave the lock on its connection, and the
    pool's reset (a rollback) does not release a session-level lock: the
    release must drop that connection, not hand it back to the pool."""
    sessions = []

    def failing_unlock_session():
        session = async_session()
        sessions.append(session)
        execute = session.execute

        async def execute_failing_unlock(statement, *args, **kwargs):
            if "pg_advisory_unlock" in str(statement):
                raise RuntimeError("unlock failed")
            return await execute(statement, *args, **kwargs)

        session.execute = execute_failing_unlock
        return session

    monkeypatch.setattr(claim_module, "async_session", failing_unlock_session)
    batch = "rc-batch-failed-unlock"
    with pytest.raises(RuntimeError, match="unlock failed"):
        async with rematch_claim(batch) as acquired:
            assert acquired is True
            assert await _held(batch) == 1
    session = sessions[-1]
    assert not session.in_transaction()
    assert await _held(batch) == 0
    monkeypatch.undo()
    async with rematch_claim(batch) as again:
        assert again is True
