"""A sample folded into its batch's ledger without a run.

The ingest fold: Stage A in memory, the unexplained peaks as placeholders,
and the whole handed to the batch fold with nothing written to
``peak_assignment``.
"""

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments import model_readers
from mascope_backend.api.new.peak_assignments.admission import (
    _sample_assignments_in_flight,
    assignment_claim,
    in_flight_run_id,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    clear_engine_verdicts,
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_REBUILD,
    close_shielded,
    complete_run,
    fail_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import (
    REASON_REBUILD,
    mark_batch_stale,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.continuity import (
    continuity_outcome,
    run_batch_continuity,
)
from mascope_backend.api.new.peak_assignments.engine import (
    measurement_model_record,
    scoring_snapshot,
)
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.api.new.peak_assignments.pipeline import (
    INGEST_FOLD,
    RunContext,
    ledger_rows,
    run_stages,
)
from mascope_backend.api.new.peak_assignments.row_terms import (
    ENVELOPE_TOLERANCE_KEY,
    MEASUREMENT_MODEL_KEY,
)
from mascope_backend.api.new.peak_assignments.sample_inputs import ineligible_reason
from mascope_backend.db import Sample, async_session
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)


async def fold_sample_peaks_without_run(
    sample_item_id: str, *, defer_consensus_to: set[str] | None = None
) -> str | None:
    """Fold the sample under the same admission an explicit run takes.

    Both paths write the sample's members, and whichever commits second replaces
    the other's. A fold keeps only what Stage A found, so a run's untargeted
    results would vanish from the ledger while its ``peak_assignment`` rows
    stayed - and nothing puts them back, because a later fold recomputes only
    the anchors its own sample touched.

    Stands down rather than refusing, unlike :func:`assign_sample_peaks`: the
    run in flight folds itself when it completes, so the sample is covered
    either way, and the ingest hook has nobody to report a refusal to.

    :param sample_item_id: The sample to fold.
    :param defer_consensus_to: As for :func:`_fold_sample_peaks_without_run`.
    :return: The batch folded into, or None when the sample was skipped.
    """
    # Claimed before the first await: checking and adding either side of one
    # would let two concurrent folds both pass the check.
    if sample_item_id in _sample_assignments_in_flight:
        return None
    _sample_assignments_in_flight.add(sample_item_id)
    try:
        async with assignment_claim("sample", sample_item_id) as acquired:
            if not acquired:
                # Another worker holds the sample - the same stand-down,
                # discovered one process further out.
                return None
            # Checked under the claim so the read and the fold that follows it
            # cannot interleave with a run's own pair.
            if await in_flight_run_id(sample_item_id) is not None:
                runtime.logger.info(
                    f"Batch-ledger fold skipped for sample '{sample_item_id}': "
                    "an assignment run is already in flight for it."
                )
                return None
            return await _fold_sample_peaks_without_run(
                sample_item_id, defer_consensus_to=defer_consensus_to
            )
    finally:
        _sample_assignments_in_flight.discard(sample_item_id)


async def _fold_sample_peaks_without_run(
    sample_item_id: str, *, defer_consensus_to: set[str] | None = None
) -> str | None:
    """Assign a newly processed sample database-first and fold the result straight
    into its batch's batch peaks, writing no per-sample run.

    The ingest path under ``peak_assignment_ingest_ledger = "batch"``: the stage
    list's INGEST_FOLD path (``pipeline.py``), the run's stages with the known
    set alone proposed. Every stage runs as it does for a run of the sample with
    the untargeted search off - the same pre-passes, the same Stage A, the same
    judging rounds and tiering rules, the same identification level - so a
    fold and such a run give every row the same tier. What differs is where
    the rows go: never to ``peak_assignment``, but each detected peak a member
    of an anchor, and the Sample view served from those members
    (``fold_view``). What is not kept is what only a run keeps - per-peak
    alternatives and provenance, mass and abundance error - and an explicit run
    on the sample restores it, replacing the members' links as it folds.

    A sample the engine would refuse a run for (a blank, an unverified
    calibration) is skipped with a log line and nothing is written. One whose
    file name names no instrument class still folds, read as of no class, as
    the fold always read it: the ingest hook has nobody to report a failure to.

    :param sample_item_id: The sample to fold.
    :param defer_consensus_to: As for ``fold_sample_into_batch_peaks``: a
        whole-batch walk collects the anchors touched and recomputes once.
    :return: The batch folded into, or None when the sample was skipped.
    """
    sample = await fetch_sample(sample_item_id)
    if (reason := ineligible_reason(sample)) is not None:
        runtime.logger.info(
            f"Batch-ledger fold skipped for sample '{sample.sample_item_name}': "
            f"{reason}."
        )
        return None

    async def keep(ctx: RunContext) -> str | None:
        return await keep_fold(ctx, defer_consensus_to=defer_consensus_to)

    ctx = await run_stages(
        RunContext(
            path=INGEST_FOLD,
            sample=sample,
            config=PeakAssignmentConfig(run_untargeted=False),
            # The rows are shaped as ledger rows - the fold reads them as such -
            # and stamped with the derived run's id: the run they will never be.
            run_id=fold_run_id(sample_item_id),
            keep=keep,
            strict_instrument=False,
        )
    )
    return ctx.result


#: Roughly how many points along a batch report progress, whatever its size.
#: Every sample reports up to this many; past it every stride-th sample does,
#: and the last one always, so the bar still fills. Integer division puts the
#: true worst case just under twice this (a 199-sample batch reports all 199),
#: which matters only in that the count stops growing with the batch.
#:
#: Bounded at all because the backfill's per-sample cost is not the assignment
#: loop's: a sample with no completed run returns from the fold after two
#: selects, so on the population this button exists for - a batch assigned
#: before batch peaks, most of it unassigned - a packet per sample is a burst of
#: socket traffic with no work between the packets. Each one is a Redis publish
#: plus a room-membership check, and re-runs every registered notification
#: watcher in every subscribed browser. A hundred steps is already finer than a
#: progress bar can draw.
_BACKFILL_PROGRESS_STEPS = 100


def _backfill_progress_notification(
    sample_batch_id: str,
    item_index: int,
    total_samples: int,
    user_id: int | None,
    process_id: str,
    parent_id: str | None,
) -> UserNotification:
    """The pending packet announcing that sample ``item_index`` is being folded.

    Typed as ``compute_batch_peaks`` -- the controller that owns the process,
    not the function that emits it -- so the whole run reports on one channel:
    the browser tracks a process by its id and ends its bar on the terminal
    packet the decorator sends under that same type.
    """
    return UserNotification(
        process_id=process_id,
        parent_id=parent_id,
        type="compute_batch_peaks",
        status="pending",
        message=(
            f"Rebuilding the batch ledger, folding sample {item_index + 1}/{total_samples}."
        ),
        data={
            "sample_batch_id": sample_batch_id,
            "_room_ids": [sample_batch_id],
            "_user_id": user_id,
            "_total_samples": total_samples,
            "_item_index": item_index,
        },
    )


def _continuity_progress_notification(
    sample_batch_id: str,
    total_samples: int,
    user_id: int | None,
    process_id: str,
    parent_id: str | None,
    message: str,
) -> UserNotification:
    """A pending packet naming the continuity pass a rebuild runs once its
    samples are folded, on the rebuild's own channel and with its bar full:
    every sample has been dealt with, and what is left is one pass over the
    whole batch, which the decorator's terminal packet ends."""
    return UserNotification(
        process_id=process_id,
        parent_id=parent_id,
        type="compute_batch_peaks",
        status="pending",
        message=message,
        data={
            "sample_batch_id": sample_batch_id,
            "_room_ids": [sample_batch_id],
            "_user_id": user_id,
            "_total_samples": total_samples,
            "_item_index": max(total_samples - 1, 0),
        },
    )


async def _grade_continuity_after_backfill(
    sample_batch_id: str,
    total_samples: int,
    user_id: int | None,
    process_id: str | None,
    parent_id: str | None,
) -> None:
    """The rebuild's trigger of the batch's continuity pass: once, after the
    last sample is folded and the deferred consensus written, announced on the
    rebuild's channel before and after so the wait does not read as a hang.
    Never failing the rebuild: its samples are folded whatever the grading
    does."""

    def packet(message: str) -> UserNotification | None:
        # As the folds' own packets: no process, no bar to drive; no samples,
        # no bar at all.
        if process_id is None or not total_samples:
            return None
        return _continuity_progress_notification(
            sample_batch_id, total_samples, user_id, process_id, parent_id, message
        )

    before = packet(
        "Rebuilding the batch ledger, grading the ratio stability of its batch peaks."
    )
    if before is not None:
        await send_progress_user_notification(before, 1.0)
    try:
        summary = await run_batch_continuity(sample_batch_id)
        # Worded as the route's outcome is: a pass that skipped, or a batch
        # that is not time-ordered, grades no pair, and "0 pairs" would read
        # as a batch with nothing to pair.
        outcome = continuity_outcome(summary, sample_batch_id)
    except Exception:  # noqa: BLE001 - grading is best-effort
        runtime.logger.exception(
            f"Continuity pass after the rebuild of batch '{sample_batch_id}' failed; "
            "the ledger is rebuilt and its grades are as an earlier pass left them."
        )
        return
    after = packet(f"Rebuilding the batch ledger. {outcome['message']}")
    if after is not None:
        await send_progress_user_notification(after, 1.0)


async def backfill_sample_batch_peaks(
    sample_batch_id: str,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    grade_continuity: bool = True,
) -> tuple[int, int]:
    """Fold every sample of a batch into the batch peaks, in acquisition-time
    order: from its latest completed assignment run where it has one, otherwise
    from a fresh Stage A pass over its peaks with no run written - the way
    ingest folds a sample. The Rebuild batch ledger button, and what populates
    a batch that predates the ledger, was never assigned, or was imported.

    Returns the number of samples folded and the number whose fold raised. The
    two are counted apart because they mean opposite things to whoever asked:
    nothing folded because nothing was assigned is the batch's state, while
    nothing folded because every fold raised is a fault - and the count alone
    cannot tell them apart.

    Reports progress while it runs, on the same terms as a batch assignment: a
    reporting sample sends two pending packets, before and after its fold, so
    the bar steps from ``i/N`` to ``(i + 1)/N``. Every sample reports up to
    :data:`_BACKFILL_PROGRESS_STEPS`, past which the batch is sampled. The
    after-packet is sent for a sample whose fold raised as well -- the bar
    tracks how much of the batch has been dealt with, not how much of it
    succeeded, and stalling it on the one sample the run isolated is exactly the
    reading it must not invite. For the same reason the bar counts samples
    walked, not samples folded: it can fill while nothing was folded at all,
    which is the outcome the terminal notification exists to name.

    :param sample_batch_id: The batch to fold.
    :param user_id: Who to route the progress stream to alongside the batch's
        room; ``None`` sends to the room alone.
    :param process_id: The process the progress belongs to. Without one there is
        no bar to drive -- the browser tracks progress per process id, and a
        generated one would open a new bar per packet -- so the stream is
        skipped entirely rather than emitted anonymously.
    :param parent_id: The owning process, when this backfill is nested in one.
    :param grade_continuity: Grade the batch's ratio stability once the batch
        is folded. False inside a pass of the batch stages, whose ratio
        stability stage grades it after the co-variation it reads.
    :return: ``(folded, failed)`` sample counts.
    :rtype: tuple[int, int]
    """
    # Imported here, as the service imports this module's fold: the two lean on
    # each other and neither can be the other's module-level import.

    async with async_session() as session:
        sample_ids = (
            (
                await session.execute(
                    select(Sample.sample_item_id)
                    .where(Sample.sample_batch_id == sample_batch_id)
                    .order_by(Sample.datetime)
                )
            )
            .scalars()
            .all()
        )

    total_samples = len(sample_ids)
    stride = max(1, total_samples // _BACKFILL_PROGRESS_STEPS)

    folded = 0
    failed = 0
    # The anchors the deferred folds below report touching, recomputed once
    # each at the end instead of once per sample (see recompute_batch_consensus).
    touched: set[str] = set()
    # True only once the loop has walked the whole batch of its own accord. Still
    # False in the finally means we are unwinding, which is the one case where
    # the consensus pass must not replace the exception that got us there.
    walked_the_batch = False
    try:
        for item_index, sample_item_id in enumerate(sample_ids):
            # The last sample always reports, whatever the stride, so the bar ends
            # full rather than a stride short of it.
            reports = process_id is not None and (
                item_index % stride == 0 or item_index == total_samples - 1
            )
            notification = (
                _backfill_progress_notification(
                    sample_batch_id=sample_batch_id,
                    item_index=item_index,
                    total_samples=total_samples,
                    user_id=user_id,
                    process_id=process_id,
                    parent_id=parent_id,
                )
                if reports
                else None
            )
            if notification is not None:
                await send_progress_user_notification(notification)

            try:
                # The run fold answers None for a sample with no completed run;
                # Stage A then folds it as ingest would, skipping a blank or an
                # unverified calibration the same way.
                if (
                    await fold_sample_into_batch_peaks(
                        sample_item_id, defer_consensus_to=touched
                    )
                    is not None
                    or await fold_sample_peaks_without_run(
                        sample_item_id, defer_consensus_to=touched
                    )
                    is not None
                ):
                    folded += 1
            except IntegrityError:
                # Its own branch as a tripwire, not because the cause is known: the
                # fold-in lock plus the delete-then-flush of a re-fold should have
                # made the (batch peak, sample) unique key unreachable, so a
                # violation of THAT key means the serialization did not hold and is
                # worth chasing. The same branch also catches the foreign keys a
                # long backfill can lose under it -- a run pruned, a sample removed --
                # which are ordinary and unrelated. Hence a message that reports
                # which sample and leaves the diagnosis to the logged constraint.
                failed += 1
                runtime.logger.exception(
                    "Batch-peak backfill hit a database constraint on sample "
                    f"'{sample_item_id}'. A violated (batch peak, sample) unique key "
                    "means two folds of it overlapped, which the fold-in lock should "
                    "prevent; a foreign key means a run or sample it referenced went "
                    "away while the backfill ran."
                )
            except Exception:  # noqa: BLE001 - one bad sample must not abort backfill
                # Logged with the traceback: the exception's message alone is often
                # just a key or an index, and the caller only ever sees a count.
                failed += 1
                runtime.logger.exception(
                    f"Batch-peak backfill failed for sample '{sample_item_id}'."
                )

            # Outside the try, so the bar advances past a sample whose fold raised
            # as well as one that folded. `send_progress_user_notification` deep
            # copies, so this is the same object the pre-fold tick was built from.
            if notification is not None:
                await send_progress_user_notification(notification, 1.0)
        walked_the_batch = True
    finally:
        # In a finally because deferring the recompute is what makes this pass
        # load-bearing rather than a tidy-up: every fold above has COMMITTED its
        # occurrences while their anchors still describe the members they had
        # before, and nothing else will fix them - a later arriving fold
        # recomputes only the anchors ITS own sample touched. An anchor a
        # deferred fold MINTED and left unwritten is worse than stale: it keeps
        # its server defaults (tier 'unassigned', n_present 0, no formula), which
        # the ledger's default occupancy floor hides outright while the series
        # endpoint still draws it from the committed occurrences.
        #
        # The vector is cancellation, not the notification calls: both socket
        # layers swallow a Redis failure, but a CancelledError walks past both
        # `except` arms above (it is a BaseException) and out of the loop.
        #
        # In-process only, and deliberately NOT asyncio.shield-ed the way the run
        # finalizer shields its terminal write. Under a level-triggered
        # cancellation (anyio re-cancels a suspended child until its scope is
        # left) the first await here raises again and the pass does nothing;
        # shielding would fix that at the price of making a cancelled backfill
        # uninterruptible for up to one lock-taking transaction per chunk, on a
        # fold lock with no timeout. Neither is free, and the recovery is the
        # same either way and cheap: re-run the backfill. It is idempotent and
        # derives everything from committed occurrences. A SIGKILL, an OOM kill
        # or a worker killed past its stop grace reaches no finally at all.
        #
        # Scoping also retires an incidental sweep the whole-batch pass did: an
        # anchor left memberless by a SAMPLE deletion (its occurrences cascade,
        # the anchor does not) is no longer collected, because no fold's touched
        # set can name it. That is develop's behaviour too - the per-sample
        # recompute was touched-only - so nothing regresses here, but the sample
        # -delete path owns that cleanup and does not yet do it.
        try:
            await recompute_batch_consensus(sample_batch_id, touched)
        except Exception:
            if walked_the_batch:
                raise
            # Already unwinding. Raising here would REPLACE the exception that
            # interrupted the loop - a CancelledError above all, which has to
            # reach the caller as a cancellation and not as a database error.
            runtime.logger.exception(
                "Batch-peak backfill was interrupted and its deferred consensus "
                f"pass then failed for batch '{sample_batch_id}'. The anchors its "
                "committed folds touched still describe their previous members; "
                "re-running the backfill repairs them."
            )

    # Reached only once the batch is walked and its consensus written: the
    # pass grades the whole batch, so it runs once here rather than after
    # each of the deferred folds above.
    if grade_continuity:
        await _grade_continuity_after_backfill(
            sample_batch_id, total_samples, user_id, process_id, parent_id
        )
    return folded, failed


def backfill_outcome(folded: int, failed: int, sample_batch_id: str) -> dict:
    """Report a backfill outcome, distinguishing "folded nothing" from success.

    Folding zero samples is what a batch of blanks, or of samples not yet
    calibrated or peak-detected, looks like, and it is the outcome the person
    who clicked most needs to hear: the request was accepted and did nothing.
    Announced green as "Rebuilt the batch ledger from 0 sample(s)" it reads as
    done, which is how a user arrives back at an empty ledger with no idea why.

    Which of the four things happened is not a count's to say, so the failures
    are carried separately. Nothing folded and nothing raised is the batch's
    state and the message names it; nothing folded because every fold raised is
    a fault, and telling that user why a sample is skipped would be advice
    that cannot help. A run that folded some and dropped others is neither, and
    saying only how many succeeded hides a sample missing from the ledger.

    ``partial`` is the outcome the notification layer maps to a warning
    severity. The word is a stretch for "none of it" -- but the run did finish,
    which rules out ``failed``, and the point of the status is the severity a
    reader sees.

    :param folded: How many of the batch's samples were folded in.
    :param failed: How many raised while folding and were skipped.
    :param sample_batch_id: The batch the backfill ran for.
    :return: The controller result the background-task decorator reports from.
    :rtype: dict
    """
    counts = {"samples_folded": folded, "samples_failed": failed}
    notification_data = {"sample_batch_id": sample_batch_id}

    if folded == 0 and failed == 0:
        return {
            "status": "partial",
            "message": (
                "The batch ledger is empty: none of this batch's samples could "
                "be folded. A sample is skipped when it is a blank, when its m/z "
                "calibration is not verified, or when it has no detected peaks yet."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    if folded == 0:
        return {
            "status": "failed",
            "message": (
                f"The batch ledger was not rebuilt: all {failed} sample(s) failed "
                "to fold. See the server log for the reason."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    if failed:
        return {
            "status": "partial",
            "message": (
                f"Rebuilt the batch ledger from {folded} sample(s); "
                f"{failed} sample(s) failed to fold and are missing from the "
                "result. See the server log for the reason."
            ),
            "data": counts,
            "_notification_data": notification_data,
        }
    return {
        "status": "success",
        "message": f"Rebuilt the batch ledger from {folded} sample(s).",
        "data": counts,
        "_notification_data": notification_data,
    }


async def rebuild_batch_ledger(
    sample_batch_id: str,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    batch_peak_run_id: str | None = None,
) -> dict:
    """The rebuild as a batch run: opened before the first fold (refused if one
    is in flight; the current run's state is snapshotted), closed as completed
    with the outcome's counts - or as failed, the live ledger then being
    whatever the folds left.

    Inside a pass of the batch stages (``batch_peak_run_id``, the pass's run)
    it is the pass's first stage, *Fold the samples*: it opens no run, and
    leaves the ratio stability to the pass's own stage, which grades it after
    the co-variation it reads."""

    async def fold(run_id: str) -> tuple[int, int]:
        await clear_engine_verdicts(sample_batch_id)
        return await backfill_sample_batch_peaks(
            sample_batch_id,
            user_id=user_id,
            process_id=process_id,
            parent_id=parent_id,
            grade_continuity=batch_peak_run_id is None,
        )

    if batch_peak_run_id is not None:
        folded, failed = await fold(batch_peak_run_id)
        outcome = backfill_outcome(folded, failed, sample_batch_id)
    else:
        run_id = await start_run(sample_batch_id, ACTION_REBUILD, user_id=user_id)
        try:
            folded, failed = await fold(run_id)
        except BaseException as exc:
            # BaseException: a cancelled rebuild left running would hold the
            # ledger until the startup reaper runs.
            await close_shielded(
                fail_run(sample_batch_id, run_id, f"{type(exc).__name__}: {exc}"),
                f"rebuild run '{run_id}' as failed",
            )
            raise
        outcome = backfill_outcome(folded, failed, sample_batch_id)
        await complete_run(sample_batch_id, run_id, summary=outcome.get("data"))
    # Each fold marked the stages stale already; the rebuild says why.
    await mark_batch_stale(sample_batch_id, REASON_REBUILD)
    return outcome


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
)
async def compute_batch_peaks(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Rebuild a batch's batch peaks from every one of its samples - from a
    completed assignment run where there is one, otherwise from a fresh Stage A
    pass with no run written.

    This is how a batch that predates the ledger, was never assigned, or was
    imported gets populated into the batch overview. Idempotent -- re-running
    re-folds each sample. Emits ``peak_assignment_reload`` so the Assignments
    chart refreshes, including when nothing was folded: the ledger is then
    correct in being empty, and the notification says why.

    Folds sample by sample and reports as it goes, so the wait is a filling bar
    rather than a spinner: the per-sample packets are pending ones on this same
    ``compute_batch_peaks`` channel, and the decorator's terminal packet ends
    the bar.
    """
    return await rebuild_batch_ledger(
        sample_batch_id,
        user_id=user_id,
        process_id=process_id,
        parent_id=parent_id,
    )


async def keep_fold(
    ctx: RunContext, *, defer_consensus_to: set[str] | None = None
) -> str | None:
    """The ingest fold keeps its ledger as the sample's members in its batch's
    batch peaks, and writes no run (``fold_view`` serves the Sample view from
    them). Its rows are shaped as ledger rows, stamped with the derived run's
    id: the run they will never be. The model it scored and capped them at is
    kept for the sample (``model_readers.record_sample_model``), which every reader
    outside a run reads where the sample has no run.

    :param defer_consensus_to: As for ``fold_sample_into_batch_peaks``: a
        whole-batch walk collects the anchors touched and recomputes once.
    :return: The batch folded into.
    """

    every, committed, _ = ledger_rows(ctx)
    runtime.logger.info(
        f"Stage A assigned {len(committed)} of {len(ctx.peaks_df)} peaks of sample "
        f"'{ctx.sample.sample_item_name}' ({len(ctx.reagent_rows)} claimed by the "
        f"reagent pre-pass, {len(ctx.artifact_rows)} by the artifact one, "
        f"{len(ctx.fragments.fragments)} read as fragments, "
        f"{ctx.tiering['capped']} capped by the tiering rules); folding into the "
        "batch ledger without a run"
    )
    batch = await fold_sample_into_batch_peaks(
        ctx.sample.sample_item_id,
        rows=[SimpleNamespace(**row) for row in every],
        persisted=False,
        defer_consensus_to=defer_consensus_to,
    )
    # The model the fold scored and capped its rows at, kept for the sample,
    # which has no run to record it on: the inspector, a pin and the batch
    # search read the sample at it (MAS-725).
    await model_readers.record_sample_model(
        ctx.sample.sample_item_id,
        {
            MEASUREMENT_MODEL_KEY: measurement_model_record(
                ctx.judged.model,
                ranked_at=ctx.model,
                scoring=scoring_snapshot(ctx.scoring),
                gate=ctx.judged.mass_calibration,
            ),
            ENVELOPE_TOLERANCE_KEY: ctx.envelope.record(),
        },
    )
    return batch
