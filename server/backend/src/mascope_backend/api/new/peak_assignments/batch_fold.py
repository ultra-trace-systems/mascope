"""A sample folded into its batch's ledger without a run.

The ingest fold: Stage A in memory, the unexplained peaks as placeholders,
and the whole handed to the batch fold with nothing written to
``peak_assignment``.
"""

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.new.peak_assignments.admission import (
    _sample_assignments_in_flight,
    assignment_claim,
    in_flight_run_id,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.api.new.peak_assignments.sample_inputs import ineligible_reason
from mascope_backend.runtime import runtime


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
    # Imported here: the stage list composes this module's helpers.
    from mascope_backend.api.new.peak_assignments.pipeline import (
        INGEST_FOLD,
        RunContext,
        keep_fold,
        run_stages,
    )

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
