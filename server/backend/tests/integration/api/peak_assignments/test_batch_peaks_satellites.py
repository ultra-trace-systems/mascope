"""Integration tests for the batch-peak ledger's two member aggregates.

A batch peak is a bare m/z anchor: it carries no isotopologue family link of its
own, and the ledger reads batch peaks alone rather than joining the occurrences
they are folded from. So both of the ledger's member-derived columns are rolled
up at fold time and stored on the row -- the brightest member (``max_intensity``)
and the family link (``satellite_of``), whose two hops (an ``iso_child``
member's owning assignment, then the anchor that owning peak folded into in the
same sample) can only be walked where the occurrences are.

Seeds a three-sample batch holding, deliberately, all four cases the vote has to
tell apart: a satellite every sample agrees on, an anchor seen as a satellite in
one sample and assigned in its own right in the other two, a satellite whose
owner is unknown, and an unassigned peak that is neither. See
docs/dev/peak_assignment_batch.md.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    get_batch_peak_ledger,
    get_batch_peak_series,
)
from mascope_backend.db import (
    BatchPeak,
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

# (key, mz, neutral formula, ion formula, role, tier, fit, intensity, owner key)
#
# `m0` / `sat` are the plain family: an M0 and its M+1 satellite, present and
# assigned in every sample. `flip` is the anchor that must NOT fold -- a
# satellite of `solo` in sample A, a peak assigned in its own right in B and C.
# `orphan` is an iso_child whose owner was never won in its run, which the
# engine leaves with a null owner. `bare` is an unassigned peak: still a trace,
# still has an intensity, never anyone's satellite.
_SPECS = {
    "A": [
        ("m0", 181.0707, "C6H12O6", "C6H13O6+", "M0", "assigned", 0.95, 5000.0, None),
        (
            "sat",
            182.0741,
            "C6H12O6",
            "C6H13O6+",
            "iso_child",
            "assigned",
            0.88,
            350.0,
            "m0",
        ),
        (
            "solo",
            299.1900,
            "C12H18O5",
            "C12H19O5+",
            "M0",
            "assigned",
            0.90,
            2000.0,
            None,
        ),
        (
            "flip",
            300.1930,
            "C12H18O5",
            "C12H19O5+",
            "iso_child",
            "assigned",
            0.80,
            150.0,
            "solo",
        ),
        ("bare", 250.1000, None, None, "unassigned", "unassigned", None, 300.0, None),
    ],
    "B": [
        ("m0", 181.0707, "C6H12O6", "C6H13O6+", "M0", "assigned", 0.90, 4500.0, None),
        (
            "sat",
            182.0741,
            "C6H12O6",
            "C6H13O6+",
            "iso_child",
            "assigned",
            0.85,
            400.0,
            "m0",
        ),
        (
            "flip",
            300.1930,
            "C13H24O3",
            "C13H25O3+",
            "M0",
            "assigned",
            0.86,
            900.0,
            None,
        ),
    ],
    "C": [
        ("m0", 181.0707, "C6H12O6", "C6H13O6+", "M0", "assigned", 0.92, 6000.0, None),
        (
            "sat",
            182.0741,
            "C6H12O6",
            "C6H13O6+",
            "iso_child",
            "assigned",
            0.86,
            500.0,
            "m0",
        ),
        (
            "flip",
            300.1930,
            "C13H24O3",
            "C13H25O3+",
            "M0",
            "assigned",
            0.84,
            800.0,
            None,
        ),
        (
            "orphan",
            400.0000,
            "C20H32O4",
            "C20H33O4+",
            "iso_child",
            "assigned",
            0.70,
            120.0,
            None,
        ),
    ],
}

# Every assigned row carries the same m/z error, so the per-sample offset the
# fold applies is one uniform shift and the anchors still line up across
# samples: this suite is about the family link, not about drift.
_MZ_ERROR_PPM = 1.0


async def _seed(session, now):
    """Seed workspace -> dataset -> batch -> three samples with owner links.

    The owner reference is written the way the engine writes it -- the owning
    row's ``peak_assignment_id`` -- which means the M0 of a sample has to be
    inserted before the satellite that names it.
    """
    ws, ds, batch = gen_id(), gen_id(), gen_id()
    session.add(
        Workspace(
            workspace_id=ws,
            workspace_name=f"Batch Peak Satellites WS {ws}",
            workspace_status="active",
            workspace_utc_created=now,
            workspace_utc_modified=now,
        )
    )
    session.add(
        Dataset(
            dataset_id=ds,
            workspace_id=ws,
            dataset_name="BP Satellites DS",
            dataset_utc_created=now,
        )
    )
    session.add(
        SampleBatch(
            sample_batch_id=batch,
            dataset_id=ds,
            sample_batch_name="BP Satellites Batch",
            sample_batch_utc_created=now,
        )
    )
    samples = {}
    for index, (name, rows) in enumerate(_SPECS.items()):
        sf, si, run = gen_id(), gen_id(), gen_id()
        session.add(
            SampleFile(
                sample_file_id=sf,
                filename=f"orbi-bp-sat-{name}-{sf}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 7, 4, 12, 0, 0) + timedelta(hours=index),
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
                sample_item_name=f"BP Satellites Sample {name}",
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
        assignment_ids: dict[str, str] = {}
        for key, mz, nf, ionf, role, tier, fit, inten, owner_key in rows:
            assignment_ids[key] = gen_id(32)
            session.add(
                PeakAssignment(
                    peak_assignment_id=assignment_ids[key],
                    peak_assignment_run_id=run,
                    sample_item_id=si,
                    sample_peak_id=key,
                    sample_peak_mz=mz,
                    sample_peak_intensity=inten,
                    role=role,
                    assigned_formula=nf,
                    ion_formula=ionf,
                    source=("database" if nf else None),
                    fit_score=fit,
                    mz_error_ppm=(_MZ_ERROR_PPM if nf else None),
                    tier=tier,
                    owner_peak_assignment_id=(
                        assignment_ids[owner_key] if owner_key else None
                    ),
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


async def _by_mz(session_factory, batch) -> dict[float, BatchPeak]:
    """The batch's anchors keyed by their frozen m/z, rounded to read as the
    seed does. The seed spaces its peaks whole mass units apart, so rounding
    cannot merge two of them."""
    async with session_factory() as s:
        peaks = (
            (
                await s.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch)
                )
            )
            .scalars()
            .all()
        )
    return {round(p.mz): p for p in peaks}


async def _fold_all(samples):
    for name in ("A", "B", "C"):
        await fold_sample_into_batch_peaks(samples[name])


# --- the family link ---------------------------------------------------------


async def test_satellite_anchor_points_at_its_m0_anchor(async_session_factory, seeded):
    batch, samples = seeded
    await _fold_all(samples)
    peaks = await _by_mz(async_session_factory, batch)

    # The two hops resolved: the M+1 anchor names the anchor its members' owning
    # assignments folded into, which is the M0 anchor of the same family.
    assert peaks[182].satellite_of == peaks[181].batch_peak_id
    # And the M0 itself is nobody's satellite, which is what makes it the row
    # the ledger folds the other under.
    assert peaks[181].satellite_of is None
    # The satellite carries the family's formula -- it is the same species
    # measured at another isotope - which is exactly why an unfolded ledger
    # reads as two rows for one compound.
    assert peaks[182].consensus_formula == peaks[181].consensus_formula


async def test_an_anchor_assigned_in_its_own_right_is_not_folded_away(
    async_session_factory, seeded
):
    batch, samples = seeded
    await _fold_all(samples)
    peaks = await _by_mz(async_session_factory, batch)

    # Seen as a satellite in one sample of three and assigned in its own right in
    # the other two: no majority, so it stays a peak of its own. Folding it under
    # the anchor a single sample named would hide a species from the ledger.
    assert peaks[300].satellite_of is None


async def test_a_later_sample_can_take_the_link_back(async_session_factory, seeded):
    batch, samples = seeded

    # On sample A alone the only evidence is that this is a satellite, and the
    # consensus says so.
    await fold_sample_into_batch_peaks(samples["A"])
    peaks = await _by_mz(async_session_factory, batch)
    assert peaks[300].satellite_of == peaks[299].batch_peak_id

    # Sample B assigns the same anchor in its own right, which is now half the
    # evidence rather than none: no majority, so the link goes.
    await fold_sample_into_batch_peaks(samples["B"])
    peaks = await _by_mz(async_session_factory, batch)
    assert peaks[300].satellite_of is None


async def test_a_satellite_whose_owner_is_unknown_stays_a_top_level_anchor(
    async_session_factory, seeded
):
    batch, samples = seeded
    await _fold_all(samples)
    peaks = await _by_mz(async_session_factory, batch)

    # The engine leaves owner_peak_assignment_id NULL when the family's M0 was
    # not won by the same ion in that run. There is no anchor to point at, and
    # inventing one would put the row under a compound it was never linked to.
    assert peaks[400].satellite_of is None


async def test_an_unassigned_anchor_is_nobody_s_satellite(
    async_session_factory, seeded
):
    batch, samples = seeded
    await _fold_all(samples)
    peaks = await _by_mz(async_session_factory, batch)

    assert peaks[250].consensus_formula is None
    assert peaks[250].satellite_of is None


async def test_refolding_a_sample_leaves_the_link_where_it_was(
    async_session_factory, seeded
):
    batch, samples = seeded
    await _fold_all(samples)
    before = await _by_mz(async_session_factory, batch)

    await fold_sample_into_batch_peaks(samples["A"])
    after = await _by_mz(async_session_factory, batch)

    assert after[182].satellite_of == before[181].batch_peak_id
    assert after[300].satellite_of is None


# --- the brightest member ----------------------------------------------------


async def test_max_intensity_is_the_brightest_member_across_samples(
    async_session_factory, seeded
):
    batch, samples = seeded
    await _fold_all(samples)
    peaks = await _by_mz(async_session_factory, batch)

    assert peaks[181].max_intensity == pytest.approx(6000.0)  # sample C
    assert peaks[182].max_intensity == pytest.approx(500.0)  # sample C
    # A peak seen in one sample only reports that sample's intensity.
    assert peaks[299].max_intensity == pytest.approx(2000.0)


async def test_an_unassigned_anchor_still_reports_an_intensity(
    async_session_factory, seeded
):
    batch, samples = seeded
    await _fold_all(samples)
    peaks = await _by_mz(async_session_factory, batch)

    # Unassigned peaks take a different path through the consensus, and an
    # unlabelled trace is exactly the kind a user sorts the ledger by intensity
    # to find.
    assert peaks[250].max_intensity == pytest.approx(300.0)


# --- the ledger read model ---------------------------------------------------


async def test_the_ledger_serves_both_aggregates(async_session_factory, seeded):
    batch, samples = seeded
    await _fold_all(samples)
    peaks = await _by_mz(async_session_factory, batch)

    # min_n_present=1, as the ledger store asks for it: every anchor is listed
    # and the selection is what the chart bounds.
    res = await get_batch_peak_ledger(sample_batch_id=batch, min_n_present=1)
    rows = {row["batch_peak_id"]: row for row in res["data"]}
    assert len(rows) == len(peaks)

    satellite = rows[peaks[182].batch_peak_id]
    assert satellite["satellite_of"] == peaks[181].batch_peak_id
    assert satellite["max_intensity"] == pytest.approx(500.0)
    # The unit the intensity is in travels with it: heights here, areas on a TOF.
    assert satellite["intensity_variable"] == "sum_peak_heights"
    assert rows[peaks[181].batch_peak_id]["satellite_of"] is None


async def test_the_series_records_carry_the_same_aggregates(
    async_session_factory, seeded
):
    batch, samples = seeded
    await _fold_all(samples)
    peaks = await _by_mz(async_session_factory, batch)

    # The ledger and the series share one projection of a batch peak's scalar
    # metadata, so the chart's records describe a trace the same way the row
    # that selected it does.
    res = await get_batch_peak_series(
        sample_batch_id=batch, batch_peak_ids=[peaks[182].batch_peak_id]
    )
    record = res["data"][0]
    assert record["satellite_of"] == peaks[181].batch_peak_id
    assert record["max_intensity"] == pytest.approx(500.0)
