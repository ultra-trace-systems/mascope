"""The probable-blend reason through the fold: the consensus reads its members'
acquisition times off the batch's samples, records the blend on the anchor,
and the ledger, the chart's series and an earlier run's snapshot serve it
(step 4.8, MAS-320).

One species at one m/z in twelve samples of a batch: the first six acquired
read it as one formula, the last six as another, at interleaved intensities,
so only acquisition time separates the two. The samples are folded out of
acquisition order: the order the test reads is the samples' clock, not the
fold's.
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
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SEARCH,
    complete_run,
    start_run,
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

EARLY, LATE = "C6H12O6", "C7H16O5"
MZ = 181.0707
N = 12
T0 = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
#: The order the samples are folded in: not the order they were acquired in.
FOLD_ORDER = [7, 2, 11, 0, 5, 9, 3, 10, 1, 6, 8, 4]


async def _seed(session, formula_of, *, timing="hourly", id_of=None):
    """A batch of :data:`N` samples, each with one assigned peak at :data:`MZ`
    reading ``formula_of(i)``.

    :param timing: ``hourly``: the i-th is a file acquired i hours after
        :data:`T0`. ``segments``: all are segments of one file acquired at
        :data:`T0`, the i-th starting at 10 i seconds.
    :param id_of: The i-th sample's id, from a prefix unique to the batch;
        generated where None.
    """
    now = datetime.now(timezone.utc)
    prefix = gen_id()[:8]
    shared_file = gen_id()
    ws, ds, batch = gen_id(), gen_id(), gen_id()
    session.add(
        Workspace(
            workspace_id=ws,
            workspace_name=f"Blend WS {ws}",
            workspace_status="active",
            workspace_utc_created=now,
            workspace_utc_modified=now,
        )
    )
    session.add(
        Dataset(
            dataset_id=ds,
            workspace_id=ws,
            dataset_name="Blend",
            dataset_utc_created=now,
        )
    )
    session.add(
        SampleBatch(
            sample_batch_id=batch,
            dataset_id=ds,
            sample_batch_name="Blend",
            sample_batch_utc_created=now,
        )
    )
    samples = []
    for i in range(N):
        segment = timing == "segments"
        sf = shared_file if segment else gen_id()
        si = id_of(prefix, i) if id_of else gen_id()
        run = gen_id()
        acquired = T0 if segment else T0 + timedelta(hours=i)
        if not segment or i == 0:
            session.add(
                SampleFile(
                    sample_file_id=sf,
                    filename=f"orbi-blend-test-{sf}.zarr",
                    instrument="orbi-test",
                    datetime=acquired.replace(tzinfo=None),
                    datetime_utc=acquired,
                    length=600.0 if segment else 60.0,
                    range=[50.0, 500.0],
                    polarity="+",
                )
            )
        session.add(
            SampleItem(
                sample_item_id=si,
                sample_batch_id=batch,
                sample_file_id=sf,
                sample_item_name=f"Blend sample {i}",
                sample_item_type="sample",
                polarity="+",
                t0=10.0 * i if segment else None,
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
        formula = formula_of(i)
        session.add(
            PeakAssignment(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=run,
                sample_item_id=si,
                sample_peak_id="P1",
                sample_peak_mz=MZ,
                # Interleaved: neither formula is the brighter.
                sample_peak_intensity=1000.0 + 250.0 * ((i * 5) % N),
                role="M0",
                assigned_formula=formula,
                ion_formula=f"{formula}H+",
                source="database",
                fit_score=0.9,
                mz_error_ppm=1.0,
                tier="assigned",
            )
        )
        samples.append(si)
    await session.commit()
    return batch, samples


async def _fold(batch_and_samples):
    batch, samples = batch_and_samples
    for i in FOLD_ORDER:
        assert await fold_sample_into_batch_peaks(samples[i]) == batch
    return batch


async def _anchor(session_factory, batch):
    async with session_factory() as s:
        (anchor,) = (
            (
                await s.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch)
                )
            )
            .scalars()
            .all()
        )
    return anchor


@pytest_asyncio.fixture
async def blocked(async_session_factory, patch_db, pa_sample_view):
    """The first six acquired read EARLY, the last six LATE."""
    async with async_session_factory() as session:
        return await _fold(
            await _seed(session, lambda i: EARLY if i < N // 2 else LATE)
        )


@pytest_asyncio.fixture
async def alternating(async_session_factory, patch_db, pa_sample_view):
    """EARLY and LATE alternate in acquisition order."""
    async with async_session_factory() as session:
        return await _fold(await _seed(session, lambda i: EARLY if i % 2 else LATE))


# The record is read off the stored provenance by the name the consensus
# writes, not through the constant: a renamed key orphans every record a
# server already holds.
def _record(anchor):
    return (anchor.provenance or {}).get("blend")


async def test_two_formulas_apart_in_time_mark_the_anchor_a_blend(
    async_session_factory, blocked
):
    anchor = await _anchor(async_session_factory, blocked)
    record = _record(anchor)
    assert record is not None
    assert record["axis"] == "time"
    assert set(record["formulas"]) == {EARLY, LATE}
    assert record["formulas"][0] == anchor.consensus_formula
    assert record["time"]["runs"] == 2
    assert record["time"]["earlier"] == EARLY
    assert record["intensity"]["separates"] is False
    assert record["n"] == [6, 6]


async def test_the_segments_of_one_file_are_read_in_the_order_they_start(
    async_session_factory, patch_db, pa_sample_view
):
    # One file, so one acquisition time: only where each segment starts orders
    # them. Their ids interleave the two formulas, so an order that fell back
    # on the ids would read no blend.
    async with async_session_factory() as session:
        batch = await _fold(
            await _seed(
                session,
                lambda i: EARLY if i < N // 2 else LATE,
                timing="segments",
                id_of=lambda prefix, i: f"{prefix}-{(i * 7) % N:02d}",
            )
        )
    record = _record(await _anchor(async_session_factory, batch))
    assert record is not None
    assert record["time"]["runs"] == 2
    assert record["time"]["earlier"] == EARLY


async def test_the_same_formulas_interleaved_in_time_are_no_blend(
    async_session_factory, alternating
):
    anchor = await _anchor(async_session_factory, alternating)
    assert anchor.is_ambiguous  # a tie...
    assert _record(anchor) is None  # ...and no axis parts them


async def test_the_ledger_the_series_and_an_earlier_run_serve_the_record(
    async_session_factory, blocked
):
    batch = blocked
    stored = _record(await _anchor(async_session_factory, batch))

    live = (await get_batch_peak_ledger(sample_batch_id=batch))["data"]
    assert [row["blend"] for row in live] == [stored]
    series = (await get_batch_peak_series(sample_batch_id=batch))["data"]
    assert [row["blend"] for row in series] == [stored]

    # The run the folds wrote is snapshotted when the next one starts.
    run_id = await start_run(batch, ACTION_SEARCH)
    await complete_run(batch, run_id)
    later = await start_run(batch, ACTION_SEARCH)
    await complete_run(batch, later)
    earlier = (
        await get_batch_peak_ledger(sample_batch_id=batch, batch_peak_run_id=run_id)
    )["data"]
    assert [row["blend"] for row in earlier] == [stored]


async def test_an_anchor_with_no_blend_serves_none(async_session_factory, alternating):
    live = (await get_batch_peak_ledger(sample_batch_id=alternating))["data"]
    assert [row["blend"] for row in live] == [None]
