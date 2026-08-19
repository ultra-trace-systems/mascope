"""
Batch-level peak assignment orchestration.

Runs the peak-centric assignment engine over every eligible sample in a sample
batch, mirroring the targeted ``match_compute_batch`` controller. Each sample
gets its own PeakAssignmentRun (the engine is per-sample); this layer sequences
the samples, filters the ones the engine cannot usefully assign, isolates
per-sample failures, tracks progress, and aggregates a batch status.

The filtering is :class:`BatchAssignmentPartition`, and it is a value rather
than a step: the endpoint computes it to answer synchronously with which samples
will be assigned and which will be skipped, then hands the same object to this
task. Sharing the value - not just the rule that produces it - is what makes
"what was reported is what runs" structural instead of a coincidence of two
evaluations of the same predicate at two different moments.

Deliberately orthogonal to the targeted-match batch-status machine: assignment
is additive and does NOT take the SampleBatch 'processing' lock, so it can run
alongside (or independently of) matching. It does take its own admission
control (below), which is what the lock would otherwise have provided.

**Cost.** A batch multiplies every per-sample cost by the number of samples, so
it is the one entry point where an unconsidered default is expensive. Two things
bound it:

- ``default_batch_config`` runs **Stage A only**. The untargeted stage is the
  documented scaling risk and is opt-in per batch, exactly as it is for ingest
  (``auto_assign_sample_peaks``). A caller that wants Stage B across a batch has
  to ask for it.
- The batch holds a slot in a semaphore for its whole duration and registers
  itself in an in-flight set, so a second assignment of the same batch is
  refused outright rather than silently queued behind a multi-minute run.
  The set bounds one worker; a cross-process advisory-lock claim
  (``admission.assignment_claim``) extends the same refusal across every
  worker sharing the database.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy import select

from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.peak_assignments.admission import assignment_claim
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.service import (
    assign_sample_peaks,
    ineligible_reason,
)
from mascope_backend.db import Sample, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)


# Batch assignments allowed to run concurrently in this worker. Each one holds a
# slot for its entire duration (tens of minutes on a large batch) while running
# per-sample work that opens database sessions in turn, so more than one at a
# time competes for the same pool the interactive request path needs. Mirrors
# `_auto_process_gate` in the sample auto-processing controller, which exists for
# the same reason.
_BATCH_ASSIGNMENT_CONCURRENCY = 1
_batch_assignment_gate = asyncio.Semaphore(_BATCH_ASSIGNMENT_CONCURRENCY)

# Batches with an assignment in flight in this worker. The semaphore alone would
# make a duplicate request wait silently for the whole first run; this refuses it
# immediately instead, which is what a double-clicked menu item deserves.
_batch_assignments_in_flight: set[str] = set()


@dataclass(frozen=True)
class BatchAssignmentPartition:
    """Which samples of a batch will be assigned, and why the rest will not.

    Computed once, in the request, and carried into the background task, so the
    partition a caller is told about is the one that executes. Recomputing it
    behind the response would let the two disagree - a sample calibrated in
    between would silently join a run the caller was told skipped it.

    Plain ids rather than ``Sample`` rows: the task runs after the request's
    session is gone, and detached ORM instances do not survive that crossing.
    """

    #: Sample ids to assign, in the order the task will visit them.
    admitted: tuple[str, ...]
    #: (sample_item_id, reason) for each sample the engine cannot usefully assign.
    skipped: tuple[tuple[str, str], ...]
    #: Samples in the batch, including both partitions - the denominator the
    #: batch result reports against.
    total: int

    def skipped_payload(self) -> list[dict]:
        """The skips as response records, one object per skipped sample."""
        return [
            {"sample_item_id": sample_item_id, "reason": reason}
            for sample_item_id, reason in self.skipped
        ]


async def partition_batch_samples(sample_batch_id: str) -> BatchAssignmentPartition:
    """Split a batch's samples into the ones worth assigning and the ones not.

    A blank sample carries no peaks and a sample whose m/z calibration is
    unverified would produce meaningless mass errors, so both are skipped rather
    than driven through a full run that fails (see
    :func:`~mascope_backend.api.new.peak_assignments.service.ineligible_reason`).
    Cheap enough to answer inside the request: one indexed query plus a pure
    function of each row.

    :param sample_batch_id: The batch to partition.
    :return: The admitted ids, the skips with their reasons, and the total.
    """
    async with async_session() as session:
        all_samples = (
            (
                await session.execute(
                    select(Sample)
                    .where(Sample.sample_batch_id == sample_batch_id)
                    # Deterministic order, so progress ("sample 3 of 50") maps to
                    # something stable and a re-run visits samples the same way.
                    .order_by(Sample.sample_item_name, Sample.sample_item_id)
                )
            )
            .scalars()
            .all()
        )

    admitted: list[str] = []
    skipped: list[tuple[str, str]] = []
    for sample in all_samples:
        reason = ineligible_reason(sample)
        if reason is None:
            admitted.append(sample.sample_item_id)
        else:
            skipped.append((sample.sample_item_id, reason))
            runtime.logger.debug(
                f"Skipping sample '{sample.sample_item_name}' for assignment: {reason}."
            )

    return BatchAssignmentPartition(
        admitted=tuple(admitted),
        skipped=tuple(skipped),
        total=len(all_samples),
    )


async def _fetch_admitted_samples(
    sample_item_ids: tuple[str, ...],
) -> tuple[list[Sample], list[str]]:
    """Load the admitted samples, in partition order.

    :param sample_item_ids: The admitted ids, in the order to visit them.
    :return: (sample rows in that order, ids that no longer resolve). A sample
        deleted between the response and the task is reported rather than
        dropped, so the counts still add up to the partition that was promised.
    """
    if not sample_item_ids:
        return [], []
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(Sample).where(Sample.sample_item_id.in_(sample_item_ids))
                )
            )
            .scalars()
            .all()
        )
    by_id = {sample.sample_item_id: sample for sample in rows}
    samples = [by_id[key] for key in sample_item_ids if key in by_id]
    missing = [key for key in sample_item_ids if key not in by_id]
    return samples, missing


def default_batch_config() -> PeakAssignmentConfig:
    """Configuration a batch assignment uses when the caller supplies none.

    Stage A only. Batch cost scales with the number of samples, and Stage B
    (untargeted composition enumeration) is the documented scaling risk, so it is
    something a caller opts into per batch rather than the default a menu click
    resolves to. The per-sample default stays the full two-stage engine, where
    the user is looking at one spectrum and the cost is seconds.

    :return: The default batch run configuration.
    :rtype: PeakAssignmentConfig
    """
    return PeakAssignmentConfig(run_untargeted=False)


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("peak_assignment", "sample_batch_id")],
    error_notification_rooms=["sample_batch_id"],
    error_reload=[("peak_assignment", "sample_batch_id")],
)
async def assign_sample_batch_peaks(
    sample_batch_id: str,
    config: PeakAssignmentConfig | None = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
    partition: BatchAssignmentPartition | None = None,
) -> dict:
    """
    Run the peak assignment engine over every eligible sample in a sample batch.

    Eligible samples are assigned in sequence, each with its own
    PeakAssignmentRun. A single failing sample (corrupt file, unreadable
    metadata, transient DB error) fails only that sample, never the rest of the
    batch. Samples the engine cannot usefully assign - blank ones, and ones whose
    m/z calibration is unverified - are filtered out by ``ineligible_reason``
    before the engine is called, and counted as skipped.

    Refuses immediately if the batch is already being assigned - by this
    worker (in-flight set) or by any other process sharing the database
    (advisory-lock claim).

    :param sample_batch_id: ID of the sample batch to assign
    :param config: Optional run configuration applied to every sample. Resolved
        once here (see ``default_batch_config``, which is Stage A only) and
        passed to every sample, so one decision is recorded on every run rather
        than each sample re-resolving its own defaults.
    :param independent_transaction: Flag for transaction/event handling
    :param user_id: Current user triggered operation (for user notifications)
    :param process_id: Process identifier for progress tracking
    :param parent_id: Parent process identifier
    :param partition: The eligibility split to execute, when the caller already
        computed and reported one. Omitted, it is computed here - which is what
        a caller with nobody to report to wants.
    :return: Batch data with per-sample counts and a status message
    """
    # Claim the batch before the first await. Checking here and adding after the
    # sample query would let two concurrent requests - the double-clicked menu
    # item - both pass the check, and the first to finish would then clear the
    # marker out from under the second.
    if sample_batch_id in _batch_assignments_in_flight:
        return await _already_running_result(sample_batch_id)
    _batch_assignments_in_flight.add(sample_batch_id)
    try:
        async with assignment_claim("batch", sample_batch_id) as acquired:
            if not acquired:
                # Another worker holds the claim - the same refusal, discovered
                # one process further out.
                return await _already_running_result(sample_batch_id)
            return await _run_batch_assignment(
                sample_batch_id=sample_batch_id,
                config=config,
                user_id=user_id,
                process_id=process_id,
                parent_id=parent_id,
                partition=partition,
            )
    finally:
        _batch_assignments_in_flight.discard(sample_batch_id)


async def _already_running_result(sample_batch_id: str) -> dict:
    """Refusal payload for a batch this worker is already assigning."""
    # Safe to await now: the decision to refuse is already made.
    sample_batch = await fetch_sample_batch(sample_batch_id)
    message = (
        f"Peak assignment is already running for sample batch "
        f"'{sample_batch.sample_batch_name}'."
    )
    runtime.logger.info(message)
    return {
        "status": "skipped",
        "message": message,
        "data": {
            "assigned_samples_count": 0,
            "failed_samples_count": 0,
            "skipped_samples_count": 0,
            "total_samples_count": 0,
        },
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }


async def _run_batch_assignment(
    sample_batch_id: str,
    config: PeakAssignmentConfig | None,
    user_id: int | None,
    process_id: str | None,
    parent_id: str | None,
    partition: BatchAssignmentPartition | None = None,
) -> dict:
    """Body of a batch assignment, run with the in-flight marker already held."""
    sample_batch = await fetch_sample_batch(sample_batch_id)
    sample_batch_name = sample_batch.sample_batch_name

    # Resolve the configuration once, so every sample of the batch runs - and
    # records - the same decision.
    config = config or default_batch_config()

    # The partition the request already reported, when there was one. Samples the
    # engine cannot usefully assign are counted as skipped rather than driven
    # through a full run that fails.
    partition = partition or await partition_batch_samples(sample_batch_id)
    samples, missing_samples = await _fetch_admitted_samples(partition.admitted)

    if not samples:
        skipped_count = len(partition.skipped)
        message = (
            f"Sample batch '{sample_batch_name}' has no samples to assign "
            f"({skipped_count} skipped)."
            if skipped_count
            else f"Sample batch '{sample_batch_name}' has no samples to assign."
        )
        runtime.logger.info(message)
        return {
            "status": "skipped",
            "message": message,
            "data": {
                "assigned_samples_count": 0,
                "failed_samples_count": len(missing_samples),
                "skipped_samples_count": skipped_count,
                "total_samples_count": partition.total,
            },
            "_notification_data": {"sample_batch_id": sample_batch_id},
        }

    runtime.logger.info(
        f"Assigning peaks for sample batch '{sample_batch_name}' "
        f"({len(samples)} eligible of {partition.total} samples, "
        f"untargeted stage {'on' if config.run_untargeted else 'off'})"
    )

    assigned_samples: list[str] = []
    # An admitted sample that no longer resolves was deleted after the partition
    # was taken; it never runs, and counting it as failed keeps the reported
    # outcome accountable for every sample that was promised a run.
    failed_samples: list[str] = list(missing_samples)
    async with _batch_assignment_gate:
        await _assign_eligible_samples(
            samples=samples,
            sample_batch_id=sample_batch_id,
            sample_batch_name=sample_batch_name,
            config=config,
            user_id=user_id,
            process_id=process_id,
            parent_id=parent_id,
            assigned_samples=assigned_samples,
            failed_samples=failed_samples,
        )

    return _batch_result(
        sample_batch_id=sample_batch_id,
        sample_batch_name=sample_batch_name,
        assigned_samples=assigned_samples,
        failed_samples=failed_samples,
        skipped_samples=[sample_item_id for sample_item_id, _ in partition.skipped],
        total_samples_count=partition.total,
    )


async def _assign_eligible_samples(
    samples: list,
    sample_batch_id: str,
    sample_batch_name: str,
    config: PeakAssignmentConfig,
    user_id: int | None,
    process_id: str | None,
    parent_id: str | None,
    assigned_samples: list[str],
    failed_samples: list[str],
) -> None:
    """Assign each eligible sample in turn, isolating per-sample failures.

    Appends to ``assigned_samples`` / ``failed_samples`` in place so a cancelled
    batch still reports what it managed before it stopped.
    """
    total_samples_count = len(samples)
    for item_index, sample in enumerate(samples):
        notification = UserNotification(
            process_id=process_id,
            parent_id=parent_id,
            type="assign_sample_batch_peaks",
            status="pending",
            message=(
                f"Assigning peaks for sample {item_index + 1}/{total_samples_count} "
                f"in sample batch '{sample_batch_name}'."
            ),
            data={
                "sample_batch_id": sample_batch_id,
                "_room_ids": [sample_batch_id],
                "_user_id": user_id,
                "_total_samples": total_samples_count,
                "_item_index": item_index,
            },
        )
        await send_progress_user_notification(notification)

        try:
            await assign_sample_peaks(
                sample_item_id=sample.sample_item_id,
                config=config,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )
            assigned_samples.append(sample.sample_item_id)
        except Exception as e:
            # A per-sample failure must not abort assignment for the rest of the
            # batch. CancelledError is a BaseException and is not caught, so a
            # cancelled batch stops here rather than being logged as N failures.
            runtime.logger.warning(
                f"Peak assignment for sample '{sample.sample_item_name}' failed: {e}"
            )
            failed_samples.append(sample.sample_item_id)

        await send_progress_user_notification(notification, 1.0)


def _batch_result(
    sample_batch_id: str,
    sample_batch_name: str,
    assigned_samples: list[str],
    failed_samples: list[str],
    skipped_samples: list[str],
    total_samples_count: int,
) -> dict:
    """Aggregate per-sample outcomes into the batch response."""
    assigned_count = len(assigned_samples)
    failed_count = len(failed_samples)
    skipped_count = len(skipped_samples)

    if failed_count > 0 and assigned_count == 0:
        status = "failed"
    elif failed_count > 0 and assigned_count > 0:
        status = "partial"
    elif assigned_count == 0 and skipped_count > 0:
        status = "skipped"
    else:
        status = "success"

    message = (
        f"Finished assigning peaks ({status}) for sample batch "
        f"'{sample_batch_name}'. {assigned_count} sample"
        f"{'s' if assigned_count != 1 else ''} assigned, "
        f"{failed_count} failed, {skipped_count} skipped."
    )
    runtime.logger.info(message)

    return {
        "status": status,
        "message": message,
        "data": {
            "assigned_samples_count": assigned_count,
            "failed_samples_count": failed_count,
            "skipped_samples_count": skipped_count,
            "total_samples_count": total_samples_count,
        },
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }
