"""
Database operation for pruning superseded peak assignment runs.

Every assignment run writes **one row per observed peak** of its sample -
including peaks it could not assign, because the ledger is deliberately
complete. Nothing ever removed them: re-assigning a sample added a whole new
run beside the old one, so a sample assigned n times carried n x (its peak
count) rows forever, and `peak_assignment` grows without bound on any
deployment that re-runs assignment routinely.

This reclaims three kinds of run:

- **Superseded completed runs.** The read model only ever serves the *latest
  completed* run for a sample; older ones are reachable only through the run
  selector, for comparing against a previous result. Keeping the newest
  ``keep_per_sample`` of them preserves that while bounding the total. The
  budget is **per (sample, engine)**, not per sample: runs can come from the
  in-app engine or be imported from an external one, and a shared budget makes
  publishing destructive - three republished imports would evict every in-app
  run for that sample, ledger rows cascading with them. Separate quotas let the
  two age out independently and neither starve the other. A second budget,
  ``keep_per_sample_total``, bounds the sum across engines, because `engine` is
  free text a client supplies and one that varies per build would otherwise
  mint a fresh quota on every import; the in-app engine is exempt from it, so
  the total can never eat the history the per-engine quota protects.
- **Terminal non-completed runs** older than ``keep_failed_hours``. A failed run
  is invisible to the read model and can never become visible, so its rows are
  pure waste - but a just-failed run is kept briefly so its error is still
  inspectable.
- **Stale imports** older than ``keep_importing_hours``. An 'importing' run is a
  client assembling a ledger across several requests; one whose client never
  came back holds staged rows and blocks new work on the sample.

A run that has not reached a terminal state yet is deliberately *not* covered by
the failed grace, and the two non-terminal kinds are held to graces of their own
because what is at risk differs. For 'pending'/'running' it is the engine's own
write target: deleting the row mid-flight makes its terminal insert fail on the
foreign key and loses the whole run, so ``keep_running_hours`` is long and
floored at :data:`MIN_KEEP_RUNNING_HOURS`. For 'importing' nothing is executing -
the rows are staged data a client can simply send again, and the abandon
endpoint already offers an immediate way out - so ``keep_importing_hours`` can
be shorter, floored only at :data:`MIN_KEEP_IMPORTING_HOURS` to keep a
long-running upload from being reclaimed underneath itself.

Deleting the run row is enough: ``peak_assignment.peak_assignment_run_id`` is
``ON DELETE CASCADE``, so the ledger goes with it.

Entry Points:
- Async: `prune_peak_assignment_runs()` for use in async code
- Sync: `run_prune_peak_assignment_runs()` for CLI and scripts
"""

import asyncio
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, and_, delete, func, or_, select

from mascope_backend.api.new.peak_assignments.config import IN_APP_ENGINE
from mascope_backend.db import PeakAssignment, PeakAssignmentRun, async_session
from mascope_backend.runtime import runtime


# Newest completed runs kept per (sample, engine). More than one so a user can
# still compare a re-run against what it replaced; small enough that the table
# stays proportional to the dataset rather than to how often assignment is
# re-run.
DEFAULT_KEEP_PER_SAMPLE = 3

# Newest completed runs kept per sample across *all* engines. The per-engine
# quota above cannot bound the table on its own: `engine` is free text a client
# supplies, so one that varies per build or per release mints a fresh quota on
# every import. This is the outer bound that makes the total finite again.
#
# Four engines' worth, so a deployment publishing from a couple of external
# engines beside the in-app one never meets it, while a client cycling engine
# names does.
DEFAULT_KEEP_PER_SAMPLE_TOTAL = 12

# Grace on terminal non-completed runs, so a failure stays inspectable for a day.
DEFAULT_KEEP_FAILED_HOURS = 24

# Statuses a run holds while it may still be executing under a server task. The
# engine creates a run as 'running' and only ever leaves that state through its
# own success/failure path; 'pending' is the column default a row can be created
# with. 'importing' is non-terminal too but has no task behind it, so it is
# reclaimed under its own grace and is deliberately not in this tuple.
IN_FLIGHT_STATUSES = ("pending", "running")

# The status an imported run assembles under, between the request that creates
# it and the one that completes it.
IMPORTING_STATUS = "importing"

# Every status that is not an outcome. Used to keep a non-terminal run out of
# the terminal grace, which would otherwise reclaim an assembling import after
# the (much shorter) failed grace.
NON_TERMINAL_STATUSES = IN_FLIGHT_STATUSES + (IMPORTING_STATUS,)

# Grace on in-flight runs. Far longer than the failed grace because the row is
# the engine's write target: a run deleted while it is still working loses its
# whole ledger when the terminal insert hits the missing foreign key. A single
# Stage B run is bounded only by generous config ceilings (thousands of peaks,
# enumeration exponential in the element-species count), so hours - not minutes -
# are the honest unit for "this can no longer be alive".
DEFAULT_KEEP_RUNNING_HOURS = 72

# Floor under keep_running_hours. Nothing reclaims in-flight rows urgently enough
# to be worth racing a live run, and the startup reaper already moves genuinely
# abandoned ones to 'failed' (where the shorter failed grace applies), so an
# operator shortening this buys nothing and risks a run in progress.
MIN_KEEP_RUNNING_HOURS = 12

# Grace on assembling imports. A day, so an upload interrupted by a network
# problem can be resumed on the client's next working day, while an import
# nobody came back to stops blocking the sample by the following night's pass.
DEFAULT_KEEP_IMPORTING_HOURS = 24

# Floor under keep_importing_hours. Much lower than the in-flight floor because
# far less is at stake: no task is writing into the row, the staged rows are
# data the client still holds and can send again, and the abandon endpoint
# already releases a stuck import immediately. An hour is simply longer than any
# upload should take, so it cannot be reclaimed out from under a live one.
MIN_KEEP_IMPORTING_HOURS = 1

# Runs deleted per committed batch. Each run cascades to one row per peak of its
# sample (thousands), so committing per batch keeps locks and WAL bounded.
DEFAULT_BATCH_SIZE = 25


def _chunked(run_ids: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    """Split a run id selection into batches of at most ``size`` ids.

    Every statement below binds one parameter per run id, and the Postgres wire
    protocol caps a statement at 32767 parameters. The prunable selection is
    unbounded - it is the accumulated backlog this script exists to clear - so an
    unchunked statement is rejected outright on exactly the deployments that need
    the prune most. Chunking is therefore a correctness requirement for the reads
    as much as it is a lock/WAL bound for the deletes.
    """
    for start in range(0, len(run_ids), size):
        yield run_ids[start : start + size]


def _completed_runs_statement() -> Select:
    """Scan completed runs ordered newest-first within each sample.

    Ranked in Python over an ordered scan rather than with a window function, so
    the same code path works on any supported Postgres and stays readable; the
    run table is small relative to the assignment table.

    Newest-first *within a sample* rather than within (sample, engine), because
    two budgets are applied over this one scan and only this order serves both:
    a per-sample ordering is also newest-first within any engine's subset of it,
    while the reverse does not hold. See :func:`_select_prunable_run_ids`.

    NULLS LAST is load-bearing, not cosmetic. The timestamp column is nullable
    and Postgres orders DESC as NULLS FIRST, so a run with no timestamp would
    rank as the *newest*, take a keep slot, and push a genuinely-newest run past
    the cutoff - deleting a live ledger. The run id breaks ties so two
    invocations agree on which run survives, which is what lets the dry run
    promise what a real run will do.
    """
    return (
        select(
            PeakAssignmentRun.peak_assignment_run_id,
            PeakAssignmentRun.sample_item_id,
            PeakAssignmentRun.engine,
        )
        .where(PeakAssignmentRun.status == "completed")
        .order_by(
            PeakAssignmentRun.sample_item_id,
            PeakAssignmentRun.peak_assignment_run_utc_created.desc().nullslast(),
            PeakAssignmentRun.peak_assignment_run_id.desc(),
        )
    )


def _stale_runs_statement(
    failed_cutoff: datetime, running_cutoff: datetime, importing_cutoff: datetime
) -> Select:
    """Select non-completed runs old enough to reclaim under their own grace.

    Age falls back to the created timestamp because an interrupted run may never
    have been given a completed one.

    A terminal run missing both timestamps is treated as ancient rather than
    skipped: both columns are nullable, and ``NULL < cutoff`` is NULL, so
    comparing alone would make such a row immortal - invisible to the read model
    yet never reclaimed. A non-terminal run is deliberately denied that
    treatment: there, a missing timestamp is indistinguishable from a run
    started seconds ago, and neither kind is stranded anyway - the startup
    reaper moves an in-flight run to 'failed', and an import is either finished
    by its client, abandoned, or caught by the importing cutoff once it has one.

    :param failed_cutoff: Terminal runs not touched since this are prunable.
    :param running_cutoff: In-flight runs not touched since this are prunable.
    :param importing_cutoff: Imports not touched since this are prunable.
    :return: Statement yielding prunable non-completed run ids.
    """
    age = func.coalesce(
        PeakAssignmentRun.peak_assignment_run_utc_completed,
        PeakAssignmentRun.peak_assignment_run_utc_created,
    )
    terminal = and_(
        PeakAssignmentRun.status.not_in(NON_TERMINAL_STATUSES),
        or_(age < failed_cutoff, age.is_(None)),
    )
    in_flight = and_(
        PeakAssignmentRun.status.in_(IN_FLIGHT_STATUSES),
        age < running_cutoff,
    )
    importing = and_(
        PeakAssignmentRun.status == IMPORTING_STATUS,
        age < importing_cutoff,
    )
    return select(PeakAssignmentRun.peak_assignment_run_id).where(
        PeakAssignmentRun.status != "completed",
        or_(terminal, in_flight, importing),
    )


async def _select_prunable_run_ids(
    session,
    keep_per_sample: int,
    keep_failed_hours: int,
    keep_running_hours: int = DEFAULT_KEEP_RUNNING_HOURS,
    keep_importing_hours: int = DEFAULT_KEEP_IMPORTING_HOURS,
    keep_per_sample_total: int = DEFAULT_KEEP_PER_SAMPLE_TOTAL,
) -> list[str]:
    """Collect the run ids eligible for pruning.

    :param session: Open async session.
    :param keep_per_sample: Newest completed runs to keep per (sample, engine).
    :param keep_failed_hours: Grace period on terminal non-completed runs.
    :param keep_running_hours: Grace period on in-flight runs.
    :param keep_importing_hours: Grace period on assembling imports.
    :param keep_per_sample_total: Newest completed runs to keep per sample
        across all engines, which the in-app engine's quota is exempt from.
    :return: Run ids to delete.
    """
    prunable: list[str] = []

    # Superseded completed runs, under two budgets applied over one scan.
    #
    # The per-(sample, engine) quota is what keeps a published import from
    # evicting a sample's in-app history: each engine ages out of its own
    # allowance, so republishing costs only the importer's.
    #
    # The per-sample total is what keeps that from being unbounded. `engine` is
    # free text a client supplies, so an engine naming itself per build or per
    # release mints a fresh quota on every import and the table grows without
    # limit - defeating the whole point of the pass. The total caps how many
    # completed runs a sample keeps across all engines.
    #
    # The in-app engine is exempt from the total, and that exemption is the
    # reason the two budgets can coexist: without it a burst of imports would
    # fill the total and start evicting exactly the in-app history the
    # per-engine quota exists to protect. In-app runs remain bounded by their
    # own `keep_per_sample`, so the ceiling is keep_per_sample_total plus
    # keep_per_sample, not unbounded.
    #
    # Counts are of runs *kept*, not runs seen: a run already condemned by the
    # engine quota must not also consume a slot in the total.
    completed = (await session.execute(_completed_runs_statement())).all()

    kept_per_engine: dict[tuple[str, str], int] = {}
    kept_per_sample: dict[str, int] = {}
    for run_id, sample_item_id, engine in completed:
        engine_key = (sample_item_id, engine)
        if kept_per_engine.get(engine_key, 0) >= keep_per_sample:
            prunable.append(run_id)
            continue
        if (
            engine != IN_APP_ENGINE
            and kept_per_sample.get(sample_item_id, 0) >= keep_per_sample_total
        ):
            prunable.append(run_id)
            continue
        kept_per_engine[engine_key] = kept_per_engine.get(engine_key, 0) + 1
        kept_per_sample[sample_item_id] = kept_per_sample.get(sample_item_id, 0) + 1

    now = datetime.now(timezone.utc)
    stale = (
        await session.execute(
            _stale_runs_statement(
                failed_cutoff=now - timedelta(hours=keep_failed_hours),
                running_cutoff=now - timedelta(hours=keep_running_hours),
                importing_cutoff=now - timedelta(hours=keep_importing_hours),
            )
        )
    ).scalars()
    prunable.extend(stale)

    # A run can qualify under both rules only if it is non-completed, which the
    # first pass never selects - but de-duplicate defensively and keep order
    # stable so a dry run reports exactly what a real run would delete.
    return list(dict.fromkeys(prunable))


async def prune_peak_assignment_runs(
    dry_run: bool = False,
    keep_per_sample: int = DEFAULT_KEEP_PER_SAMPLE,
    keep_failed_hours: int = DEFAULT_KEEP_FAILED_HOURS,
    keep_running_hours: int = DEFAULT_KEEP_RUNNING_HOURS,
    keep_importing_hours: int = DEFAULT_KEEP_IMPORTING_HOURS,
    keep_per_sample_total: int = DEFAULT_KEEP_PER_SAMPLE_TOTAL,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """
    Delete superseded and stale peak assignment runs, cascading to their rows.

    Assumes the database engine is already configured.

    :param dry_run: When True, only count what would be deleted; change nothing.
    :type dry_run: bool
    :param keep_per_sample: Newest completed runs to keep per (sample, engine).
    :type keep_per_sample: int
    :param keep_failed_hours: Keep terminal non-completed runs younger than this.
    :type keep_failed_hours: int
    :param keep_running_hours: Keep in-flight runs younger than this; must be at
        least :data:`MIN_KEEP_RUNNING_HOURS`.
    :type keep_running_hours: int
    :param keep_importing_hours: Keep assembling imports younger than this; must
        be at least :data:`MIN_KEEP_IMPORTING_HOURS`.
    :type keep_importing_hours: int
    :param keep_per_sample_total: Newest completed runs to keep per sample
        across all engines; must be at least ``keep_per_sample``. The in-app
        engine's own quota is exempt, so this bounds imported runs.
    :type keep_per_sample_total: int
    :param batch_size: Runs deleted per committed batch.
    :type batch_size: int
    :return: Summary with prunable run count, deleted run count and freed rows.
    :rtype: dict
    """
    if keep_per_sample < 1:
        raise ValueError("keep_per_sample must be at least 1")
    if keep_failed_hours < 0:
        raise ValueError("keep_failed_hours must not be negative")
    if keep_running_hours < MIN_KEEP_RUNNING_HOURS:
        raise ValueError(
            f"keep_running_hours must be at least {MIN_KEEP_RUNNING_HOURS}: "
            "a shorter grace can delete a run that is still executing"
        )
    if keep_importing_hours < MIN_KEEP_IMPORTING_HOURS:
        raise ValueError(
            f"keep_importing_hours must be at least {MIN_KEEP_IMPORTING_HOURS}: "
            "a shorter grace can delete an import that is still uploading"
        )
    if keep_per_sample_total < keep_per_sample:
        raise ValueError(
            "keep_per_sample_total must be at least keep_per_sample: a total "
            "below the per-engine quota would evict runs the per-engine budget "
            "promises to keep"
        )
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    async with async_session() as session:
        prunable = await _select_prunable_run_ids(
            session,
            keep_per_sample,
            keep_failed_hours,
            keep_running_hours,
            keep_importing_hours,
            keep_per_sample_total,
        )

        if not prunable:
            message = "No superseded peak assignment runs to prune."
            runtime.logger.info(message)
            return {
                "status": "success",
                "message": message,
                "prunable_runs": 0,
                "deleted_runs": 0,
                "deleted_assignments": 0,
            }

        # Count the rows those runs hold, so both a dry run and a real run can
        # report the space actually at stake. Chunked over the same batches as
        # the deletes: the selection is unbounded, and a single IN over all of it
        # exceeds the bind-parameter cap long before the backlog is big enough to
        # worry about (see _chunked).
        row_count = 0
        for chunk in _chunked(prunable, batch_size):
            row_count += int(
                await session.scalar(
                    select(func.count())
                    .select_from(PeakAssignment)
                    .where(PeakAssignment.peak_assignment_run_id.in_(chunk))
                )
                or 0
            )

        if dry_run:
            message = (
                f"Would delete {len(prunable)} peak assignment run(s) and "
                f"{row_count} assignment row(s) (keeping the newest "
                f"{keep_per_sample} completed run(s) per sample and engine)."
            )
            runtime.logger.info(message)
            return {
                "status": "dry_run",
                "message": message,
                "prunable_runs": len(prunable),
                "deleted_runs": 0,
                "deleted_assignments": 0,
            }

        deleted_runs = 0
        for chunk in _chunked(prunable, batch_size):
            result = await session.execute(
                delete(PeakAssignmentRun).where(
                    PeakAssignmentRun.peak_assignment_run_id.in_(chunk)
                )
            )
            await session.commit()
            deleted_runs += result.rowcount or 0

        message = (
            f"Deleted {deleted_runs} peak assignment run(s) and about "
            f"{row_count} assignment row(s) (kept the newest {keep_per_sample} "
            f"completed run(s) per sample and engine)."
        )
        runtime.logger.info(message)
        return {
            "status": "success",
            "message": message,
            "prunable_runs": len(prunable),
            "deleted_runs": deleted_runs,
            "deleted_assignments": row_count,
        }


def run_prune_peak_assignment_runs(
    dry_run: bool = False,
    keep_per_sample: int = DEFAULT_KEEP_PER_SAMPLE,
    keep_failed_hours: int = DEFAULT_KEEP_FAILED_HOURS,
    keep_running_hours: int = DEFAULT_KEEP_RUNNING_HOURS,
    keep_importing_hours: int = DEFAULT_KEEP_IMPORTING_HOURS,
    keep_per_sample_total: int = DEFAULT_KEEP_PER_SAMPLE_TOTAL,
) -> dict:
    """
    Synchronous wrapper for CLI and script entry points.

    :param dry_run: When True, only count what would be deleted.
    :param keep_per_sample: Newest completed runs to keep per (sample, engine).
    :param keep_failed_hours: Keep terminal non-completed runs younger than this.
    :param keep_running_hours: Keep in-flight runs younger than this.
    :param keep_importing_hours: Keep assembling imports younger than this.
    :param keep_per_sample_total: Newest completed runs kept per sample across
        all engines.
    :return: Prune summary.
    :rtype: dict
    """
    return asyncio.run(
        prune_peak_assignment_runs(
            dry_run=dry_run,
            keep_per_sample=keep_per_sample,
            keep_failed_hours=keep_failed_hours,
            keep_running_hours=keep_running_hours,
            keep_importing_hours=keep_importing_hours,
            keep_per_sample_total=keep_per_sample_total,
        )
    )
