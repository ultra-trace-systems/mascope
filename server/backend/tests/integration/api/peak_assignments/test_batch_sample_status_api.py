"""Each sample's assignment status within a batch, as the sample browser reads
it: a sample with a run of its own, one served from the batch ledger, one the
ledger never saw."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.fold_view import fold_run_id
from mascope_backend.db import (
    IonizationMechanism,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio


def _rows(sample_item_id, assigned):
    """Three peaks; glucose's M0 and M+1 assigned when ``assigned``."""
    run_id = fold_run_id(sample_item_id)
    m0_id = gen_id(32)

    def row(pid, mz, intensity, role, owner=None):
        carries = assigned and role != "unassigned"
        return SimpleNamespace(
            peak_assignment_id=m0_id if pid == "p1" else gen_id(32),
            peak_assignment_run_id=run_id,
            sample_item_id=sample_item_id,
            sample_peak_id=pid,
            sample_peak_mz=mz,
            sample_peak_intensity=intensity,
            sample_peak_tof=None,
            role=role if carries else "unassigned",
            assigned_formula="C6H12O6" if carries else None,
            ion_formula="C6H13O6+" if carries else None,
            ionization_mechanism_id="mech-h" if carries else None,
            source="database" if carries else None,
            tier="assigned" if carries else "unassigned",
            fit_score=0.9 if carries else None,
            mz_error_ppm=None,
            owner_peak_assignment_id=(m0_id if owner else None) if carries else None,
            target_compound_id=None,
            target_ion_id=None,
            provenance=None,
            evidence=None,
            p_correct=None,
        )

    return [
        row("p1", 181.0707, 9000.0, "M0"),
        row("p2", 182.0741, 700.0, "iso_child", owner=True),
        row("p3", 250.1, 300.0, "unassigned"),
    ]


@pytest_asyncio.fixture
async def status_batch(async_session_factory, pa_test_data):
    """S1 folded with assignments and given two completed runs and a pending
    one; S2 folded with nothing assigned; S3 never folded."""
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    samples = {}
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
                sample_batch_name=f"Status {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        if await session.get(IonizationMechanism, "mech-h") is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id="mech-h",
                    ionization_mechanism_polarity="+",
                    ionization_mechanism="H+ (sample status test)",
                )
            )
        for name in ("S1", "S2", "S3"):
            sample_file_id, sample_item_id = gen_id(), gen_id()
            session.add(
                SampleFile(
                    sample_file_id=sample_file_id,
                    filename=f"orbi-status-{name}-{sample_file_id}.zarr",
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
                    sample_item_name=f"Status {name}",
                    sample_item_type="sample",
                    polarity="+",
                    sample_item_utc_created=now,
                )
            )
            samples[name] = sample_item_id
        # S1's runs: an older completed in-app run, a newer completed peaky
        # publish (the one to report), and a pending run that must not count.
        for offset, engine, version, status in (
            (-2, "mascope", "1.0.0", "completed"),
            (-1, "peaky", "0.7.0", "completed"),
            (0, "mascope", "1.0.0", "pending"),
        ):
            session.add(
                PeakAssignmentRun(
                    peak_assignment_run_id=gen_id(),
                    sample_item_id=samples["S1"],
                    engine=engine,
                    engine_version=version,
                    status=status,
                    peak_assignment_run_utc_created=now + timedelta(hours=offset),
                )
            )
        await session.commit()
    for name, assigned in (("S1", True), ("S2", False)):
        assert (
            await fold_sample_into_batch_peaks(
                samples[name], rows=_rows(samples[name], assigned), persisted=False
            )
            == batch_id
        )
    return {"batch_id": batch_id, "samples": samples}


async def test_every_sample_of_the_batch_is_reported_with_its_status(
    guest_client, status_batch
):
    batch_id, samples = status_batch["batch_id"], status_batch["samples"]

    response = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}/sample-status"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"] == 3
    by_sample = {record["sample_item_id"]: record for record in body["data"]}
    assert set(by_sample) == set(samples.values())

    s1 = by_sample[samples["S1"]]
    assert s1["run"]["engine"] == "peaky"
    assert s1["run"]["engine_version"] == "0.7.0"
    assert s1["run"]["peak_assignment_run_utc_created"]
    assert (s1["n_members"], s1["n_assigned"]) == (3, 2)

    s2 = by_sample[samples["S2"]]
    assert s2["run"] is None
    assert (s2["n_members"], s2["n_assigned"]) == (3, 0)

    s3 = by_sample[samples["S3"]]
    assert s3["run"] is None
    assert (s3["n_members"], s3["n_assigned"]) == (0, 0)

    assert "1 with a run of their own" in body["message"]
