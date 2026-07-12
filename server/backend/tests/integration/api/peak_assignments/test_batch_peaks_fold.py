"""Integration tests for the batch-peak fold-in controller.

Seeds a two-sample batch (a shared m/z, two unique m/z, and an unassigned peak)
and folds each sample, asserting the load-bearing behaviours end-to-end against a
real database: append-only anchor stability, cross-sample consensus, unassigned
peaks as first-class batch peaks, and idempotent re-folding.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    get_batch_peak_series,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    Dataset,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id

pytestmark = pytest.mark.asyncio

# (peak_id, mz, neutral_formula, ion_formula, role, tier, fit, intensity, mz_err_ppm)
_SPECS = {
    "A": [
        ("A1", 181.0707, "C6H12O6", "C6H13O6+", "M0", "identified", 0.95, 5000.0, 1.0),
        ("A2", 200.0500, "C10H8O", "C10H9O+", "M0", "candidate", 0.60, 800.0, 1.0),
        ("A3", 250.1000, None, None, "unassigned", "unassigned", None, 300.0, None),
    ],
    "B": [
        # B1 shares A1's m/z -> must snap to the SAME anchor (append-only).
        ("B1", 181.0707, "C6H12O6", "C6H13O6+", "M0", "identified", 0.90, 4500.0, 1.0),
        ("B2", 300.2000, "C12H10", "C12H11+", "M0", "identified", 0.85, 2000.0, 1.0),
    ],
}


async def _seed(session, now):
    ws, ds, batch = gen_id(), gen_id(), gen_id()
    session.add(
        Workspace(
            workspace_id=ws,
            workspace_name=f"Batch Peak WS {ws}",
            workspace_status="active",
            workspace_utc_created=now,
            workspace_utc_modified=now,
        )
    )
    session.add(
        Dataset(dataset_id=ds, workspace_id=ws, dataset_name="BP DS", dataset_utc_created=now)
    )
    session.add(
        SampleBatch(
            sample_batch_id=batch,
            dataset_id=ds,
            sample_batch_name="BP Batch",
            sample_batch_utc_created=now,
        )
    )
    samples = {}
    for name, rows in _SPECS.items():
        sf, si, run = gen_id(), gen_id(), gen_id()
        session.add(
            SampleFile(
                sample_file_id=sf,
                filename=f"orbi-bp-test-{name}-{sf}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 7, 4, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=si,
                sample_batch_id=batch,
                sample_file_id=sf,
                sample_item_name=f"BP Sample {name}",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run,
                sample_item_id=si,
                engine_version="0.1.0-test",
                status="completed",
                peak_assignment_run_utc_created=now,
                peak_assignment_run_utc_completed=now,
            )
        )
        for pid, mz, nf, ionf, role, tier, fit, inten, err in rows:
            session.add(
                PeakAssignment(
                    peak_assignment_id=gen_id(32),
                    peak_assignment_run_id=run,
                    sample_item_id=si,
                    sample_peak_id=pid,
                    sample_peak_mz=mz,
                    sample_peak_intensity=inten,
                    role=role,
                    assigned_formula=nf,
                    ion_formula=ionf,
                    source=("database" if nf else None),
                    fit_score=fit,
                    mz_error_ppm=err,
                    tier=tier,
                )
            )
        samples[name] = si
    await session.commit()
    return batch, samples


@pytest_asyncio.fixture
async def seeded(async_session_factory, patch_db, pa_sample_view):
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        return await _seed(session, now)


async def _batch_peaks(session_factory, batch):
    async with session_factory() as s:
        return (
            await s.execute(select(BatchPeak).where(BatchPeak.sample_batch_id == batch))
        ).scalars().all()


async def test_fold_is_append_only_with_cross_sample_consensus(
    async_session_factory, seeded
):
    batch, samples = seeded

    # --- Fold sample A: three anchors (two assigned, one unassigned). ---
    assert await fold_sample_into_batch_peaks(samples["A"]) == batch
    peaks_a = await _batch_peaks(async_session_factory, batch)
    assert len(peaks_a) == 3
    anchor181 = next(p for p in peaks_a if p.consensus_formula == "C6H12O6")
    id181, mz181 = anchor181.batch_peak_id, anchor181.mz
    assert anchor181.n_present == 1

    # --- Fold sample B: B1 snaps to the existing 181 anchor, B2 mints one. ---
    assert await fold_sample_into_batch_peaks(samples["B"]) == batch
    peaks_b = {p.batch_peak_id: p for p in await _batch_peaks(async_session_factory, batch)}
    assert len(peaks_b) == 4  # 181 (shared) + 200 + 250 + 300

    # Append-only: the shared anchor keeps its id AND its frozen m/z.
    assert id181 in peaks_b
    assert peaks_b[id181].mz == mz181

    shared = peaks_b[id181]
    assert shared.n_present == 2
    assert shared.consensus_formula == "C6H12O6"
    assert shared.consensus_tier == "identified"
    assert shared.support_fraction == pytest.approx(1.0)

    async with async_session_factory() as s:
        occ = (
            await s.execute(
                select(BatchPeakOccurrence).where(
                    BatchPeakOccurrence.batch_peak_id == id181
                )
            )
        ).scalars().all()
    assert len(occ) == 2
    assert {o.sample_item_id for o in occ} == {samples["A"], samples["B"]}


async def test_unassigned_peak_is_a_first_class_batch_peak(async_session_factory, seeded):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    peaks = await _batch_peaks(async_session_factory, batch)
    unassigned = [p for p in peaks if p.consensus_formula is None]
    assert len(unassigned) == 1
    assert unassigned[0].consensus_tier == "unassigned"
    assert unassigned[0].n_present == 1  # still a drawable trace


async def test_refold_is_idempotent(async_session_factory, seeded):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])
    await fold_sample_into_batch_peaks(samples["A"])  # re-fold A

    peaks = await _batch_peaks(async_session_factory, batch)
    assert len(peaks) == 4
    shared = next(p for p in peaks if p.consensus_formula == "C6H12O6")
    assert shared.n_present == 2

    async with async_session_factory() as s:
        occ = (
            await s.execute(
                select(BatchPeakOccurrence).where(
                    BatchPeakOccurrence.batch_peak_id == shared.batch_peak_id
                )
            )
        ).scalars().all()
    assert len(occ) == 2  # not duplicated
    assert len({o.sample_item_id for o in occ}) == 2


async def test_series_full_load_applies_occupancy_filter(async_session_factory, seeded):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])

    # Default occupancy (present in >= 2 samples): only the shared 181 peak.
    res = await get_batch_peak_series(sample_batch_id=batch)
    assert res["results"] == 1
    rec = res["data"][0]
    assert rec["consensus_formula"] == "C6H12O6"
    assert rec["consensus_tier"] == "identified"
    assert rec["n_present"] == 2
    series = rec["peak_series"]
    assert set(series["sample_item_ids"]) == {samples["A"], samples["B"]}
    assert set(series["intensities"]) == {5000.0, 4500.0}
    assert series["tiers"] == ["identified", "identified"]

    # min_n_present=1 keeps every batch peak (181 + 200 + 250 + 300).
    res_all = await get_batch_peak_series(sample_batch_id=batch, min_n_present=1)
    assert res_all["results"] == 4


async def test_series_sample_slice_ignores_occupancy(async_session_factory, seeded):
    batch, samples = seeded
    await fold_sample_into_batch_peaks(samples["A"])
    await fold_sample_into_batch_peaks(samples["B"])

    # A single-sample slice returns sample A's three peaks, each series limited to A.
    res = await get_batch_peak_series(sample_item_ids=[samples["A"]])
    assert res["results"] == 3
    for rec in res["data"]:
        assert rec["peak_series"]["sample_item_ids"] == [samples["A"]]
