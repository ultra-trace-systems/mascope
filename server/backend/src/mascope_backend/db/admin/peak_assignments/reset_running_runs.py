"""
Database operation for reconciling peak assignment runs stuck under a task.

A ``PeakAssignmentRun`` an engine owns is created ``pending`` or ``running`` and
moved to ``completed`` or ``failed`` by the engine's own success/failure paths.
Neither path survives the process dying underneath it - a worker restart, an OOM
kill, or a cancelled background task (``CancelledError`` is a ``BaseException``,
so the engine's ``except Exception`` finalizer does not run). Such a run stays
non-terminal forever: invisible to the read model (which only serves the latest
*completed* run) but never cleaned up either, while durable admission keeps
refusing new work on its sample.

Both in-flight states need this. The assign endpoints answer synchronously, so
the run row is created ``pending`` in the request that asked for it and only
becomes ``running`` when the background task adopts it; a process that dies in
between strands a ``pending`` row exactly as a mid-run death strands a
``running`` one.

This resets them at startup, mirroring
:func:`~mascope_backend.db.admin.batch.reset_processing_status.reset_stuck_processing_batches`.
Startup runs in the main process before any worker is spawned, so nothing can
legitimately be held by a server task at that moment - every such row is a
leftover.

**That argument covers the engine-owned states and nothing else.** An imported
run assembles under ``importing``, at a remote client's pace and with no server
task attached, so it is *not* a leftover just because no worker owns it - and a
routine deploy that failed it would kill a live upload and leave the client
appending to a ``failed`` run. The status is deliberately outside this reset,
which is why the filter below names the in-flight states exactly rather than
"not terminal". Abandoned imports are released by the import abandon endpoint,
or reclaimed by ``prune_peak_assignment_runs`` under its own
``keep_importing_hours`` grace.

They are marked ``failed`` rather than adopted as ``completed``. The engine
writes its whole ledger in a single insert, so "this run has rows" does imply
"its ledger committed in full" - but not the converse: a run whose sample
yielded no assignments at all skips the insert entirely, and is then
indistinguishable from one that died before reaching it. ``failed`` is also the
recoverable direction (re-run the sample) where a wrongly-claimed ``completed``
is not, and the rows an interrupted run left behind are reclaimed by
``prune_peak_assignment_runs`` rather than kept alive by adopting the run.

That last part is a two-way dependency, not a convenience: the prune holds runs
still in a non-terminal state for a deliberately long grace, because it cannot
tell an abandoned ``running`` row from one a live worker is about to write its
ledger into. This reset is what makes the distinction - it only ever runs where
nothing can legitimately be ``running`` - so it is the normal path by which an
interrupted run becomes reclaimable, and the prune's long grace is only the
backstop for a server that never restarts.

``BatchPeakRun`` needs the same reconciliation, and the argument for it is
simpler. A batch run is opened ``running`` by the operation that starts it and
closed by that operation's own success/failure paths, and every one of them is a
server task - a batch import is a background task like a rebuild or a search, not
a client assembling a run at its own pace. So the table has no equivalent of
``importing``: at startup, a ``running`` row is always a leftover.

Leaving one costs more there than on the per-sample table. ``start_run`` refuses
a second operation while one is in flight, so a batch whose run was interrupted
answers 409 to every rebuild, search, import and curation from then on, with no
way out from the app. ``is_current`` is deliberately untouched by the reset, as
it is by ``fail_run``: an interrupted run never held it (a run is opened
``is_current = 0`` and only adopts it on completion), so the batch keeps the
ledger and the snapshot its last good run left.

Entry Points:
- Async: `reset_running_peak_assignment_runs()` / `reset_running_batch_peak_runs()`
- Sync: `run_reset_running_peak_assignment_runs()` /
  `run_reset_running_batch_peak_runs()` for CLI and scripts
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import update

from mascope_backend.db import BatchPeakRun, PeakAssignmentRun, async_session
from mascope_backend.db.admin.peak_assignments.prune_runs import IN_FLIGHT_STATUSES
from mascope_backend.runtime import runtime


STUCK_RUN_ERROR = "Interrupted: the server restarted while this run was in progress."

#: The one non-terminal state a batch run has. Spelled out rather than shared
#: with the per-sample tuple: that one also carries 'pending', which the batch
#: table has no equivalent of, and 'importing', which is a client's to hold.
BATCH_RUN_IN_FLIGHT_STATUS = "running"

STUCK_BATCH_RUN_ERROR = (
    "Interrupted: the server restarted while this batch operation was in progress."
)


async def reset_running_peak_assignment_runs() -> dict:
    """
    Mark peak assignment runs left under a server task as failed.

    Called at application startup to recover from abnormal termination during a
    run. Safe to call when there is nothing to reset.

    Never raises. This is housekeeping on a table that a recent migration added,
    so it can legitimately be absent - a database not yet upgraded to this head,
    or one rolled back past it - and startup runs it in the same try block as the
    other init tasks. Reclaiming stale rows is not worth refusing to boot over,
    so a failure is logged and swallowed.

    :return: Operation results with the count of reset runs
    :rtype: dict
    """
    try:
        async with async_session() as session:
            update_result = await session.execute(
                update(PeakAssignmentRun)
                # Exactly the states a server task owns. Shared with the
                # retention prune, which holds the same rows under its long
                # in-flight grace, so the two cannot drift apart. An 'importing'
                # run belongs to a client, not to a worker this startup
                # replaced, and is deliberately absent from both.
                .where(PeakAssignmentRun.status.in_(IN_FLIGHT_STATUSES))
                .values(
                    status="failed",
                    error=STUCK_RUN_ERROR,
                    peak_assignment_run_utc_completed=datetime.now(timezone.utc),
                )
            )

            reset_count = update_result.rowcount
            await session.commit()
    except Exception as error:
        message = f"Could not reset interrupted peak assignment runs: {error}"
        runtime.logger.warning(message)
        return {
            "status": "skipped",
            "message": message,
            "data": {"reset_count": 0},
        }

    if reset_count == 0:
        message = "No interrupted peak assignment runs found"
    else:
        message = f"Reset {reset_count} interrupted peak assignment run(s) to 'failed'"
    runtime.logger.debug(message)

    return {
        "status": "success",
        "message": message,
        "data": {
            "reset_count": reset_count,
        },
    }


def run_reset_running_peak_assignment_runs() -> dict:
    """
    Synchronous wrapper for CLI and script entry points.

    :return: Operation results with the count of reset runs
    :rtype: dict
    """
    return asyncio.run(reset_running_peak_assignment_runs())


async def reset_running_batch_peak_runs() -> dict:
    """
    Mark batch peak runs left under a server task as failed.

    Called at application startup, beside the per-sample reset and for the same
    reason. Safe to call when there is nothing to reset.

    Never raises, on the same grounds as its per-sample sibling: this is
    housekeeping on a table a recent migration added, and reclaiming stale rows
    is not worth refusing to boot over.

    :return: Operation results with the count of reset runs
    :rtype: dict
    """
    try:
        async with async_session() as session:
            update_result = await session.execute(
                update(BatchPeakRun)
                # `is_current` is not touched: an interrupted run never held it,
                # so the batch keeps the ledger and snapshot its last good run
                # left. Marked failed rather than completed, as `fail_run` does -
                # a run that died mid-write has no summary and its ledger writes
                # are partial, and failed is the direction that can be re-run.
                .where(BatchPeakRun.status == BATCH_RUN_IN_FLIGHT_STATUS)
                .values(
                    status="failed",
                    error=STUCK_BATCH_RUN_ERROR,
                    batch_peak_run_utc_completed=datetime.now(timezone.utc),
                )
            )

            reset_count = update_result.rowcount
            await session.commit()
    except Exception as error:
        message = f"Could not reset interrupted batch peak runs: {error}"
        runtime.logger.warning(message)
        return {
            "status": "skipped",
            "message": message,
            "data": {"reset_count": 0},
        }

    if reset_count == 0:
        message = "No interrupted batch peak runs found"
    else:
        message = f"Reset {reset_count} interrupted batch peak run(s) to 'failed'"
    runtime.logger.debug(message)

    return {
        "status": "success",
        "message": message,
        "data": {
            "reset_count": reset_count,
        },
    }


def run_reset_running_batch_peak_runs() -> dict:
    """
    Synchronous wrapper for CLI and script entry points.

    :return: Operation results with the count of reset runs
    :rtype: dict
    """
    return asyncio.run(reset_running_batch_peak_runs())
