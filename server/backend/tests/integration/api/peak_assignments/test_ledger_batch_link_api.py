"""A run's own rows carry the batch peak their peak folded into, looked up
when the ledger is read, so the sample ledger can plot a species in the batch
chart. Before the sample is folded there is nothing to carry.

On a batch and sample of its own: the shared fixture's sample is session-wide,
and folding it would give it a derived run every later test would then see.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.db import (
    BatchPeakOccurrence,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def run_sample(async_session_factory, pa_test_data):
    """A batch of its own with one sample and a completed run of three rows:
    glucose's M0 and M+1, and an unassigned peak."""
    now = datetime.now(timezone.utc)
    batch_id, sample_file_id, sample_item_id, run_id = (
        gen_id(),
        gen_id(),
        gen_id(),
        gen_id(),
    )
    m0_id = gen_id(32)
    async with async_session_factory() as session:
        dataset_id = (
            await session.execute(
                select(SampleBatch.dataset_id).where(
                    SampleBatch.sample_batch_id == pa_test_data["sample_batch_id"]
                )
            )
        ).scalar_one()
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name=f"Link {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, "mech-h") is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id="mech-h",
                    ionization_mechanism_polarity="+",
                    ionization_mechanism="H+ (ledger link test)",
                )
            )
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                filename=f"orbi-link-{sample_file_id}.zarr",
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
                sample_item_id=sample_item_id,
                sample_batch_id=batch_id,
                sample_file_id=sample_file_id,
                sample_item_name="Link S1",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                engine_version="0.1.0-test",
                status="completed",
                config={"run_untargeted": True},
                peak_assignment_run_utc_created=now - timedelta(hours=1),
                peak_assignment_run_utc_completed=now - timedelta(minutes=55),
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=m0_id,
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id="peak-1",
                sample_peak_mz=181.0707,
                sample_peak_intensity=5000.0,
                role="M0",
                assigned_formula="C6H12O6",
                ion_formula="C6H13O6+",
                ionization_mechanism_id="mech-h",
                isotope_label="M0",
                source="database",
                fit_score=0.95,
                mz_error_ppm=1.2,
                abundance_error=0.05,
                tier="assigned",
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id="peak-2",
                sample_peak_mz=182.0741,
                sample_peak_intensity=350.0,
                role="iso_child",
                assigned_formula="C6H12O6",
                ion_formula="C6H13O6+",
                ionization_mechanism_id="mech-h",
                isotope_label="M+1",
                source="database",
                fit_score=0.88,
                mz_error_ppm=1.5,
                abundance_error=0.08,
                tier="assigned",
                owner_peak_assignment_id=m0_id,
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id="peak-3",
                sample_peak_mz=250.5,
                sample_peak_intensity=42.0,
                role="unassigned",
                tier="unassigned",
            )
        )
        await session.commit()
    return {"batch_id": batch_id, "sample_item_id": sample_item_id, "run_id": run_id}


async def test_run_rows_carry_their_batch_peak_once_the_sample_is_folded(
    guest_client, run_sample, async_session_factory
):
    sample_item_id = run_sample["sample_item_id"]
    url = f"/api/peak-assignments/sample/{sample_item_id}"

    before = await guest_client.get(url)
    assert before.status_code == 200, before.text
    rows = before.json()["data"]
    assert len(rows) == 3
    assert {row["peak_assignment_run_id"] for row in rows} == {run_sample["run_id"]}
    assert all(row["batch_peak_id"] is None for row in rows)

    assert await fold_sample_into_batch_peaks(sample_item_id) == run_sample["batch_id"]

    after = await guest_client.get(url)
    assert after.status_code == 200, after.text
    rows = after.json()["data"]
    # Still the run's own rows - the derived view is for a sample without one.
    assert {row["peak_assignment_run_id"] for row in rows} == {run_sample["run_id"]}
    async with async_session_factory() as session:
        anchor_by_peak = dict(
            (
                await session.execute(
                    select(
                        BatchPeakOccurrence.sample_peak_id,
                        BatchPeakOccurrence.batch_peak_id,
                    ).where(BatchPeakOccurrence.sample_item_id == sample_item_id)
                )
            ).all()
        )
    assert len(anchor_by_peak) == 3
    for row in rows:
        assert row["batch_peak_id"] == anchor_by_peak[row["sample_peak_id"]]
