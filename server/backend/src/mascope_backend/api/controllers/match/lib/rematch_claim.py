"""
Cross-process claim on a batch's rematch.

A rematch holds its batch at ``processing``, and a second one on a batch at
``processing`` is refused. But an edit that flags the batch while the run goes
writes over ``processing`` (it asked for work after the run's matches, so its
flag has to stay), and the status alone then no longer says a run is going: a
second rematch would start beside the first, and whichever finished last would
write its outcome, the older run's included.

The claim is a **session-level Postgres advisory lock** held for the whole
run, so it holds across every worker sharing the database, whatever the
batch's status says. A second rematch that cannot take it is refused as
locked, as one on a batch at ``processing`` is, so the run that finishes is
the only one, and the ``processing`` its final write finds is its own.

As in ``api/new/peak_assignments/admission.py``, the lock belongs to the
connection: the claim keeps its session open for the run and unlocks on that
same connection before returning it to the pool. If the process dies, its
connection dies with it and Postgres releases the lock. The cost is one pooled
connection held per running rematch.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import func, select

from mascope_backend.db import async_session


# Namespace discriminator hashed into the first int of the two-int advisory
# lock key space, so these claims cannot collide with other advisory lock
# users (the match-write locks, the assignment claims).
_REMATCH_CLAIM_NAMESPACE = "mascope_rematch"


def _key(sample_batch_id: str):
    return (
        func.hashtext(_REMATCH_CLAIM_NAMESPACE),
        func.hashtext(sample_batch_id),
    )


@asynccontextmanager
async def rematch_claim(sample_batch_id: str) -> AsyncIterator[bool]:
    """
    Try to claim a batch's rematch across all workers.

    Yields True when the claim was taken, and the caller owns the batch's
    rematch until the context exits; False when another run holds it, and the
    caller must refuse rather than wait.

    :param sample_batch_id: The batch to rematch
    :type sample_batch_id: str
    """
    session = async_session()
    acquired = False
    try:
        # Non-blocking and session-level. The select pins the session's
        # connection until close, the lifetime the lock needs.
        acquired = bool(
            await session.scalar(
                select(func.pg_try_advisory_lock(*_key(sample_batch_id)))
            )
        )
        yield acquired
    finally:
        if acquired:
            # Shielded: a cancelled run still returns a clean connection to
            # the pool, one that holds no lock its next borrower knows nothing
            # about.
            await asyncio.shield(
                session.execute(select(func.pg_advisory_unlock(*_key(sample_batch_id))))
            )
        await asyncio.shield(session.close())
