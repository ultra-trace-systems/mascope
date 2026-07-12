"""DB controller: fold a sample's per-sample assignments into the batch peaks.

Wraps the pure :mod:`batch_peaks` engine with the database I/O behind the batch
overview. :func:`fold_sample_into_batch_peaks` runs on the arrival path right
after Stage-A assignment (the assignments are already committed by then), snapping
the sample's peaks into the batch's frozen, append-only anchors and recomputing the
consensus of every touched batch peak. :func:`backfill_sample_batch_peaks` folds a
whole batch's existing runs in time order.

Design: ``docs/dev/peak_assignment_batch.md``.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.instrument_configs.lib import read_instrument_functions
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    Anchor,
    AnchorSet,
    compute_consensus,
    fold_in_sample,
    resolution_adaptive_tol_ppm,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignment,
    PeakAssignmentRun,
    Sample,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_type


def _intensity_variable(filename: str) -> str:
    """The batch peak's intensity unit, from the instrument type (areas for TOF,
    heights for Orbitrap). Defaults to heights if the filename cannot be classified
    -- an unclassifiable file must never abort the fold-in."""
    try:
        instrument_type = get_instrument_type(filename)
    except Exception:  # noqa: BLE001 - classification is best-effort
        instrument_type = None
    return "sum_peak_areas" if instrument_type == "tof" else "sum_peak_heights"


async def _latest_completed_run_id(session, sample_item_id: str) -> str | None:
    return (
        await session.execute(
            select(PeakAssignmentRun.peak_assignment_run_id)
            .where(
                PeakAssignmentRun.sample_item_id == sample_item_id,
                PeakAssignmentRun.status == "completed",
            )
            .order_by(PeakAssignmentRun.peak_assignment_run_utc_created.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _tolerance_fn(resolution_func):
    """Build a resolution-adaptive ``mz -> tol_ppm`` from a per-file resolution
    function (half-FWHM + drift margin); falls back to the margin alone."""

    def tol_fn(mz: float) -> float:
        resolution = None
        if resolution_func is not None:
            try:
                resolution = float(resolution_func(mz))
            except Exception:
                resolution = None
        return resolution_adaptive_tol_ppm(mz, resolution)

    return tol_fn


async def fold_sample_into_batch_peaks(sample_item_id: str) -> str | None:
    """Fold a sample's latest completed assignment into its batch's batch peaks.

    Append-only: each observed peak joins the nearest frozen anchor within its
    resolution-adaptive tolerance (after a per-sample offset correction) or mints a
    new anchor; the consensus of every touched batch peak is then recomputed from
    its members' per-sample assignments. Idempotent -- re-folding a sample replaces
    its prior occurrences and re-derives the affected consensus.

    :returns: the ``sample_batch_id`` (for the caller's reload event) or ``None``
        when there is nothing to fold (unknown sample / no completed run).
    """
    async with async_session() as session:
        sample = (
            await session.execute(
                select(Sample).where(Sample.sample_item_id == sample_item_id)
            )
        ).scalar_one_or_none()
        if sample is None:
            return None
        sample_batch_id = sample.sample_batch_id
        ionization_mode_id = sample.ionization_mode_id
        filename = sample.filename

        run_id = await _latest_completed_run_id(session, sample_item_id)
        if run_id is None:
            return None

        # Every observed peak of the run (assigned or not) folds into a batch peak,
        # so no m/z is dropped from the batch view.
        rows = (
            (
                await session.execute(
                    select(PeakAssignment).where(
                        PeakAssignment.peak_assignment_run_id == run_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return None

        # Per-sample residual mass offset (mu): the median m/z error of assigned
        # peaks. Shifts this sample's peaks onto the shared batch axis before
        # snapping so calibration drift does not split one species across anchors.
        errs = [r.mz_error_ppm for r in rows if r.mz_error_ppm is not None]
        mu_ppm = statistics.median(errs) if errs else 0.0
        mu_factor = 1.0 - mu_ppm / 1e6

        try:
            _, resolution_func = await read_instrument_functions(filename)
        except Exception as exc:  # noqa: BLE001 - resolution is best-effort
            runtime.logger.debug(
                f"No resolution function for '{filename}' ({exc}); "
                "batch-peak tolerance falls back to the drift margin."
            )
            resolution_func = None
        tol_fn = _tolerance_fn(resolution_func)

        # Existing frozen anchors for this (batch, ionization mode).
        existing = (
            (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == sample_batch_id,
                        BatchPeak.ionization_mode_id == ionization_mode_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        anchor_set = AnchorSet(
            [Anchor(bp.batch_peak_id, bp.mz, bp.mz_tol_ppm) for bp in existing]
        )

        # Idempotency: drop this sample's prior occurrences (a re-fold), noting the
        # anchors it touched so their consensus is recomputed even if it now leaves
        # them.
        prior = (
            (
                await session.execute(
                    select(BatchPeakOccurrence)
                    .join(
                        BatchPeak,
                        BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                    )
                    .where(
                        BatchPeakOccurrence.sample_item_id == sample_item_id,
                        BatchPeak.sample_batch_id == sample_batch_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        touched: set[str] = {occ.batch_peak_id for occ in prior}
        for occ in prior:
            await session.delete(occ)
        # Flush the deletes before re-inserting so a re-fold does not collide with
        # this sample's prior rows on the (batch_peak_id, sample_item_id) unique key.
        if prior:
            await session.flush()

        peaks = [
            {
                "mz": float(r.sample_peak_mz) * mu_factor,
                "raw_mz": float(r.sample_peak_mz),
                "row": r,
            }
            for r in rows
        ]
        now = datetime.now(timezone.utc)
        intensity_variable = _intensity_variable(filename)

        folded = fold_in_sample(
            anchor_set, peaks, new_id=lambda: gen_id(16), tol_fn=tol_fn
        )

        new_ids = {f.batch_peak_id for f in folded if f.is_new_anchor}
        anchors_by_id = {a.batch_peak_id: a for a in anchor_set.anchors()}
        for bp_id in new_ids:
            a = anchors_by_id[bp_id]
            session.add(
                BatchPeak(
                    batch_peak_id=a.batch_peak_id,
                    sample_batch_id=sample_batch_id,
                    ionization_mode_id=ionization_mode_id,
                    mz=a.mz,
                    mz_tol_ppm=a.tol_ppm,
                    intensity_variable=intensity_variable,
                    batch_peak_utc_created=now,
                    batch_peak_utc_modified=now,
                )
            )

        for f in folded:
            r = f.peak["row"]
            session.add(
                BatchPeakOccurrence(
                    batch_peak_occurrence_id=gen_id(32),
                    batch_peak_id=f.batch_peak_id,
                    sample_item_id=sample_item_id,
                    sample_peak_id=r.sample_peak_id,
                    peak_assignment_id=r.peak_assignment_id,
                    sample_peak_mz=f.peak["raw_mz"],
                    intensity=r.sample_peak_intensity,
                    tier=r.tier,
                    fit_score=r.fit_score,
                    assigned_formula=r.assigned_formula,
                )
            )
            touched.add(f.batch_peak_id)

        await session.flush()  # make occurrences visible to the consensus recompute
        await _recompute_consensus(session, touched, now)
        await session.commit()

    return sample_batch_id


async def _recompute_consensus(
    session, batch_peak_ids: set[str], now: datetime
) -> None:
    """Recompute (and persist) the consensus of the given batch peaks from their
    members' per-sample assignments. A batch peak left with no members is deleted.
    """
    if not batch_peak_ids:
        return
    id_list = list(batch_peak_ids)
    rows = (
        await session.execute(
            select(
                BatchPeakOccurrence.batch_peak_id,
                BatchPeakOccurrence.assigned_formula,
                BatchPeakOccurrence.tier,
                BatchPeakOccurrence.fit_score,
                BatchPeakOccurrence.intensity,
                PeakAssignment.ion_formula,
                PeakAssignment.ionization_mechanism_id,
                PeakAssignment.provenance,
            )
            .outerjoin(
                PeakAssignment,
                PeakAssignment.peak_assignment_id
                == BatchPeakOccurrence.peak_assignment_id,
            )
            .where(BatchPeakOccurrence.batch_peak_id.in_(id_list))
        )
    ).all()

    members_by_peak: dict[str, list] = defaultdict(list)
    for r in rows:
        prov = r.provenance if isinstance(r.provenance, dict) else {}
        members_by_peak[r.batch_peak_id].append(
            {
                "assigned_formula": r.assigned_formula,
                "ion_formula": r.ion_formula,
                "ionization_mechanism_id": r.ionization_mechanism_id,
                "tier": r.tier,
                "fit_score": r.fit_score,
                "intensity": r.intensity,
                "p_correct": prov.get("p_correct"),
            }
        )

    for bp_id in id_list:
        bp = await session.get(BatchPeak, bp_id)
        if bp is None:
            continue
        members = members_by_peak.get(bp_id, [])
        if not members:
            await session.delete(bp)
            continue
        c = compute_consensus(members)
        bp.consensus_formula = c.consensus_formula
        bp.consensus_ion_formula = c.consensus_ion_formula
        bp.ionization_mechanism_id = c.ionization_mechanism_id
        bp.consensus_tier = c.consensus_tier
        bp.best_fit_score = c.best_fit_score
        bp.support_fraction = c.support_fraction
        bp.n_present = c.n_present
        bp.is_ambiguous = int(c.is_ambiguous)
        bp.alternatives = c.alternatives
        bp.provenance = c.provenance
        bp.batch_peak_utc_modified = now


async def backfill_sample_batch_peaks(sample_batch_id: str) -> int:
    """Fold every sample of a batch (that has a completed run) into the batch
    peaks, in acquisition-time order. Used to seed batch peaks for batches assigned
    before this feature. Returns the number of samples folded.
    """
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

    folded = 0
    for sample_item_id in sample_ids:
        try:
            if await fold_sample_into_batch_peaks(sample_item_id) is not None:
                folded += 1
        except Exception as exc:  # noqa: BLE001 - one bad sample must not abort backfill
            runtime.logger.warning(
                f"Batch-peak backfill failed for sample '{sample_item_id}': {exc}"
            )
    return folded


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
    """Backfill a batch's batch peaks from its samples' existing completed
    assignment runs, without re-running assignment.

    This is how a batch assigned before batch peaks existed (or after a bulk
    import) gets populated into the batch overview. Idempotent -- re-running
    re-folds each sample. Emits ``peak_assignment_reload`` on success so the
    Assignments chart refreshes.
    """
    folded = await backfill_sample_batch_peaks(sample_batch_id)
    return {
        "status": "success",
        "message": f"Computed batch peaks from {folded} assigned sample(s).",
        "data": {"samples_folded": folded},
        "_notification_data": {"sample_batch_id": sample_batch_id},
    }
