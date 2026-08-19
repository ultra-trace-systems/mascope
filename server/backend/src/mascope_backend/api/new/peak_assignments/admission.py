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

The claim bounds one *request*. Whether a sample may start new work at all is a
separate question, answered from durable run state by :func:`in_flight_run_id`,
because an import spans several requests at a remote client's pace: a claim held
across them would pin a pooled connection to that client's think-time, and a
claim taken per request would leave the sample unclaimed between chunks. Run
state has neither problem, and it is what lets the import and in-app paths refuse
each other. The two are complements, not alternatives - only the claim is
crash-safe within a request, and only run state survives one ending.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import func, select

from mascope_backend.db import PeakAssignmentRun, async_session


# Namespace discriminator hashed into the first int of the two-int advisory
# lock key space, so these claims cannot collide with other advisory lock
# users (e.g. the match-write locks).
_ASSIGNMENT_CLAIM_NAMESPACE = "mascope_peak_assignment"

# Run states that mean "this sample already has work in progress". 'pending' and
# 'running' belong to a server task; 'importing' belongs to a client assembling
# a ledger over several requests. A run in any of them is a reason to refuse a
# new one, so neither engine can start a second ledger for the same sample
# behind the first.
NON_TERMINAL_RUN_STATUSES = ("pending", "running", "importing")


async def in_flight_run_id(sample_item_id: str, session=None) -> str | None:
    """The id of a non-terminal run for this sample, if one exists.

    Durable admission: unlike the advisory claim, this outlives the request that
    created the run, which is what an import needs and what makes an in-app
    assign and an import refuse each other rather than both proceeding.

    A run left non-terminal by a process that died is released by the startup
    reaper (for engine-owned states), by the import abandon endpoint, or by
    retention's grace - the same paths that already governed those rows.

    :param sample_item_id: The sample to test.
    :param session: An open session to reuse; one is opened when omitted.
    :return: The newest non-terminal run's id, or None when the sample is free.
    """
    query = (
        select(PeakAssignmentRun.peak_assignment_run_id)
        .where(
            PeakAssignmentRun.sample_item_id == sample_item_id,
            PeakAssignmentRun.status.in_(NON_TERMINAL_RUN_STATUSES),
        )
        .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc().nullslast())
        .limit(1)
    )
    if session is not None:
        return await session.scalar(query)
    async with async_session() as own_session:
        return await own_session.scalar(query)


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
