"""Batch-peak series read service -- the columnar feed for the batch overview.

Mirrors ``get_match_ion_series``: one record per batch peak carrying its consensus
(m/z, formula, tier) once, plus a ``peak_series`` of parallel per-sample arrays.
This keeps chart-data responses for large batches small. See
``docs/dev/peak_assignment_batch.md``.
"""

from __future__ import annotations

from sqlalchemy import select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.db import BatchPeak, BatchPeakOccurrence, async_session


def _empty_series() -> dict:
    return {
        "sample_item_ids": [],
        "sample_peak_ids": [],
        "intensities": [],
        "tiers": [],
    }


def _batch_peak_meta(bp) -> dict:
    """The scalar consensus metadata of a batch peak (no per-sample series).

    ``max_intensity`` and ``satellite_of`` are member aggregates materialized on
    the row at fold time, so the ledger can offer an intensity column and fold
    isotopologue satellites under their M0 without joining the occurrence table
    it is defined by not joining. ``intensity_variable`` rides along because it
    is what names the unit ``max_intensity`` is in (heights or areas, per
    instrument type).
    """
    return {
        "batch_peak_id": bp.batch_peak_id,
        "sample_batch_id": bp.sample_batch_id,
        "ionization_mode_id": bp.ionization_mode_id,
        "mz": bp.mz,
        "consensus_formula": bp.consensus_formula,
        "consensus_ion_formula": bp.consensus_ion_formula,
        "ionization_mechanism_id": bp.ionization_mechanism_id,
        "consensus_tier": bp.consensus_tier,
        "best_fit_score": bp.best_fit_score,
        "support_fraction": bp.support_fraction,
        "n_present": bp.n_present,
        "is_ambiguous": bool(bp.is_ambiguous),
        "intensity_variable": bp.intensity_variable,
        "max_intensity": bp.max_intensity,
        "satellite_of": bp.satellite_of,
    }


@api_controller()
async def get_batch_peak_series(
    sample_batch_id: str | None = None,
    sample_item_ids: list[str] | None = None,
    batch_peak_ids: list[str] | None = None,
    tier: str | None = None,
    min_n_present: int = 2,
) -> dict:
    """Retrieve per-sample batch-peak data in a compact columnar form.

    Returns one record per batch peak carrying the consensus metadata once, plus a
    ``peak_series`` object of parallel arrays (``sample_item_ids``,
    ``sample_peak_ids``, ``intensities``, ``tiers``) holding the per-sample values
    -- the batch-overview trace for that peak. ``sample_peak_ids`` is the member's
    own peak in that sample, so a chart point can be followed back to the sample
    peak it was folded from.

    Batch peaks are scoped by ``sample_batch_id`` (the full-batch load) or by an
    explicit ``sample_item_ids`` list (a single-sample slice, for incremental
    chart append); ``batch_peak_ids`` further narrows to specific peaks. The
    occupancy filter ``min_n_present`` drops singleton/noise batch peaks from the
    default drawable set on a full-batch load; pass ``min_n_present=1`` to include
    every peak. It is not applied to sample-slice or explicit-peak requests.

    :param sample_batch_id: Batch whose batch peaks to load
    :param sample_item_ids: Restrict to batch peaks seen in these samples, and
        restrict each series to these samples
    :param batch_peak_ids: Restrict to these batch peaks
    :param tier: Filter by consensus tier
    :param min_n_present: Occupancy floor for the full-batch load
    :return: Dictionary with status, message, results count, and series data
    """
    full_load = not sample_item_ids and not batch_peak_ids

    async with async_session() as session:
        bp_query = select(BatchPeak)
        if sample_batch_id:
            bp_query = bp_query.where(BatchPeak.sample_batch_id == sample_batch_id)
        if sample_item_ids:
            bp_query = bp_query.where(
                BatchPeak.batch_peak_id.in_(
                    select(BatchPeakOccurrence.batch_peak_id)
                    .where(BatchPeakOccurrence.sample_item_id.in_(sample_item_ids))
                    .distinct()
                )
            )
        if batch_peak_ids:
            bp_query = bp_query.where(BatchPeak.batch_peak_id.in_(batch_peak_ids))
        if tier:
            bp_query = bp_query.where(BatchPeak.consensus_tier == tier)
        if full_load and min_n_present and min_n_present > 1:
            bp_query = bp_query.where(BatchPeak.n_present >= min_n_present)

        bp_rows = (await session.execute(bp_query)).scalars().all()
        requested_ids = [bp.batch_peak_id for bp in bp_rows]

        # Slim per-(peak, sample) rows grouped into parallel arrays per peak.
        series_by_peak: dict[str, dict[str, list]] = {}
        if requested_ids:
            occ_query = select(
                BatchPeakOccurrence.batch_peak_id,
                BatchPeakOccurrence.sample_item_id,
                BatchPeakOccurrence.sample_peak_id,
                BatchPeakOccurrence.intensity,
                BatchPeakOccurrence.tier,
            ).where(BatchPeakOccurrence.batch_peak_id.in_(requested_ids))
            if sample_item_ids:
                occ_query = occ_query.where(
                    BatchPeakOccurrence.sample_item_id.in_(sample_item_ids)
                )
            for (
                batch_peak_id,
                sample_item_id,
                sample_peak_id,
                intensity,
                occ_tier,
            ) in (await session.execute(occ_query)).all():
                series = series_by_peak.setdefault(batch_peak_id, _empty_series())
                series["sample_item_ids"].append(sample_item_id)
                series["sample_peak_ids"].append(sample_peak_id)
                series["intensities"].append(intensity)
                series["tiers"].append(occ_tier)

        data = [
            {
                **_batch_peak_meta(bp),
                "peak_series": series_by_peak.get(bp.batch_peak_id, _empty_series()),
            }
            for bp in bp_rows
        ]

    return {
        "status": "success",
        "message": f"Retrieved {len(data)} batch peak{'s' if len(data) != 1 else ''}",
        "results": len(data),
        "data": data,
    }


@api_controller()
async def get_batch_peak_ledger(
    sample_batch_id: str,
    tier: str | None = None,
    min_n_present: int = 2,
) -> dict:
    """Metadata-only list of a batch's batch peaks -- the ledger table feed.

    One row per batch peak with its consensus (m/z, formula, tier, prevalence,
    brightest member, isotopologue parent) but WITHOUT the per-sample series, so
    a 1000+ row ledger stays cheap (it never touches the occurrence table). The
    chart fetches series only for the rows the user selects.

    ``satellite_of`` is one hop: it names the batch peak whose family this one
    belongs to as its own members observed it. A satellite whose parent is
    itself a satellite is left as observed, and a parent this call filtered out
    (by tier, or by the occupancy floor) is simply not in the response -- both
    are the reader's to resolve, which it can because it holds the whole list.
    """
    async with async_session() as session:
        query = select(BatchPeak).where(BatchPeak.sample_batch_id == sample_batch_id)
        if tier:
            query = query.where(BatchPeak.consensus_tier == tier)
        if min_n_present and min_n_present > 1:
            query = query.where(BatchPeak.n_present >= min_n_present)
        bp_rows = (await session.execute(query.order_by(BatchPeak.mz))).scalars().all()

    data = [_batch_peak_meta(bp) for bp in bp_rows]
    return {
        "status": "success",
        "message": f"Retrieved {len(data)} batch peak{'s' if len(data) != 1 else ''}",
        "results": len(data),
        "data": data,
    }
