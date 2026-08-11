"""
Cross-process admission control for peak assignment.

The in-process guards in ``service.py`` / ``batch.py`` (a semaphore plus
in-flight id sets) bound one worker, but production runs several uvicorn
workers: N workers still permitted N concurrent assignments of the same
sample or batch, each producing its own full run and ledger. The claim here
is a **session-level Postgres advisory lock** held for the duration of the
run, so the bound holds across every process sharing the database. The
in-process sets stay in front of it as the cheap fast path - they refuse a
same-worker duplicate without a round trip.

Session-level advisory locks belong to the *connection*, not the
transaction, which dictates the shape of this module: the claim keeps its
session (and therefore its pinned connection) open for the whole run, and
release explicitly unlocks on that same connection before returning it to
the pool. A connection returned while still holding the lock would poison
the pool - its next borrower would hold a lock it knows nothing about. The
payoff of session-level locks is crash-safety: if the process dies, the
connection dies with it and Postgres releases the lock, so no reaper is
needed - which is what makes this preferable to a claims table.

The cost is one pooled connection held per in-flight assignment. That is
bounded by the same admission control this module implements: one batch and
a handful of samples per worker.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import func, select

from mascope_backend.db import async_session


# Namespace discriminator hashed into the first int of the two-int advisory
# lock key space, so these claims cannot collide with other advisory lock
# users (e.g. the match-write locks).
_ASSIGNMENT_CLAIM_NAMESPACE = "mascope_peak_assignment"


@asynccontextmanager
async def assignment_claim(kind: str, resource_id: str) -> AsyncIterator[bool]:
    """Try to claim an assignment resource across all workers.

    Yields True when the claim was acquired - the caller owns the resource
    until the context exits - and False when another holder (any worker, any
    process) already has it, in which case the caller must refuse rather than
    queue.

    :param kind: Resource kind, e.g. ``"sample"`` or ``"batch"``; part of the
        lock key so the two kinds cannot collide.
    :param resource_id: The sample item or sample batch id being claimed.
    """
    session = async_session()
    acquired = False
    try:
        # pg_try_advisory_lock: non-blocking, session-level. The select pins
        # the session's connection, which stays pinned (transaction open,
        # never committed) until close - exactly the lifetime the lock needs.
        acquired = bool(
            await session.scalar(
                select(
                    func.pg_try_advisory_lock(
                        func.hashtext(_ASSIGNMENT_CLAIM_NAMESPACE),
                        func.hashtext(f"{kind}:{resource_id}"),
                    )
                )
            )
        )
        yield acquired
    finally:
        if acquired:
            # Shielded: a cancelled assignment still must return a clean
            # connection to the pool, and the unlock is the last await on the
            # way out.
            await asyncio.shield(
                session.execute(
                    select(
                        func.pg_advisory_unlock(
                            func.hashtext(_ASSIGNMENT_CLAIM_NAMESPACE),
                            func.hashtext(f"{kind}:{resource_id}"),
                        )
                    )
                )
            )
        await asyncio.shield(session.close())
