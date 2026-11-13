"""Integration tests for the cross-process rematch claim.

The claim is a session-level Postgres advisory lock, so a second holder
refused while the first holds, and the lock gone from Postgres once the
holder exits, can only be pinned against a real Postgres. Each claim opens
its own pooled connection, the same shape as two claims in two workers. The
rematch's use of it is covered in
``tests/integration/api/sample/test_batch_status_never_lower.py``.
"""

import pytest
from sqlalchemy import text

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
