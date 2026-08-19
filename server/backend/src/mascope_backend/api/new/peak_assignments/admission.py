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
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import func, select

from mascope_backend.db import PeakAssignmentRun, async_session


# Namespace discriminator hashed into the first int of the two-int advisory
# lock key space, so these claims cannot collide with other advisory lock
# users (e.g. the match-write locks).
_ASSIGNMENT_CLAIM_NAMESPACE = "mascope_peak_assignment"

# Run states that mean "this sample already has work in progress". A run in any
# of them refuses a new one, so neither engine can start a second ledger for the
# same sample behind the first.
#
# Refusing on a status is only safe while that status has a way back out, or a
# row stranded by a crash would block its sample forever. Each has two:
#
# - 'pending' / 'running' belong to a server task: 'pending' from the request
#   that created the run until the task adopts it, 'running' for as long as the
#   engine works. The startup reaper fails both (nothing can legitimately be
#   held by a task before workers spawn), and retention sweeps them after
#   `keep_running_hours`.
# - 'importing' belongs to a client assembling a ledger across requests, so the
#   reaper deliberately cannot touch it - a deploy would kill a live upload.
#   Its releases are the abandon endpoint, for a client that knows the run id or
#   learns it from the refusal below, and retention's `keep_importing_hours`.
#
# Adding a status here means giving it both a reclamation path and a documented
# grace first.
NON_TERMINAL_RUN_STATUSES = ("pending", "running", "importing")


async def in_flight_run_id(
    sample_item_id: str,
    session=None,
    exclude_run_id: str | None = None,
) -> str | None:
    """The id of a non-terminal run for this sample, if one exists.

    Durable admission: unlike the advisory claim, this outlives the request that
    created the run, which is what an import needs and what makes an in-app
    assign and an import refuse each other rather than both proceeding.

    A run left non-terminal by a process that died is released by the startup
    reaper (for engine-owned states), by the import abandon endpoint, or by
    retention's grace - the same paths that already governed those rows.

    :param sample_item_id: The sample to test.
    :param session: An open session to reuse; one is opened when omitted.
    :param exclude_run_id: A run to ignore, for a caller that already owns one.
        The assign endpoint creates its run in the request and hands it to the
        background task, so without this the task would refuse itself: the
        question it has to ask is whether *another* run holds the sample.
    :return: The newest non-terminal run's id, or None when the sample is free.
    """
    filters = [
        PeakAssignmentRun.sample_item_id == sample_item_id,
        PeakAssignmentRun.status.in_(NON_TERMINAL_RUN_STATUSES),
    ]
    if exclude_run_id is not None:
        filters.append(PeakAssignmentRun.peak_assignment_run_id != exclude_run_id)
    query = (
        select(PeakAssignmentRun.peak_assignment_run_id)
        .where(*filters)
        .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc().nullslast())
        .limit(1)
    )
    if session is not None:
        return await session.scalar(query)
    async with async_session() as own_session:
        return await own_session.scalar(query)


async def in_flight_run_ids(
    sample_item_ids: Sequence[str],
    session=None,
) -> dict[str, str]:
    """The same durable admission question, asked about many samples at once.

    A batch has to answer it for every sample it is about to assign, and doing
    that one query per sample would put an unbounded serial round trip in front
    of the response. One ``IN`` clause over the indexed ``sample_item_id`` column
    answers the whole set instead.

    :param sample_item_ids: The samples to test.
    :param session: An open session to reuse; one is opened when omitted.
    :return: Blocked samples mapped to the run that holds them; samples that are
        free are absent from the mapping, so an empty result means "all clear".
    """
    if not sample_item_ids:
        return {}
    query = (
        select(
            PeakAssignmentRun.sample_item_id,
            PeakAssignmentRun.peak_assignment_run_id,
        )
        .where(
            PeakAssignmentRun.sample_item_id.in_(tuple(sample_item_ids)),
            PeakAssignmentRun.status.in_(NON_TERMINAL_RUN_STATUSES),
        )
        # Oldest first, so the newest run wins the per-sample slot on insert and
        # the reported id matches what the single-sample query would name.
        .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.asc().nullsfirst())
    )
    if session is not None:
        rows = (await session.execute(query)).all()
    else:
        async with async_session() as own_session:
            rows = (await own_session.execute(query)).all()
    return {sample_item_id: run_id for sample_item_id, run_id in rows}


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
