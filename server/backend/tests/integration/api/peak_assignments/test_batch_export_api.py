"""Integration tests for the ledger's flat rows: the paged members route and
the CSV export.

Two samples in a batch of their own, folded; one species seen in both and one
unassigned peak seen in one. See ``batch_export.py``.
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments import batch_peaks_routes
from mascope_backend.api.new.peak_assignments.batch_export import (
    COLUMNS,
    write_batch_ledger_csv,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.temp.storage import user_temp_path
from mascope_backend.db import (
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

# (peak id, mz, formula, ion formula, tier, fit, intensity)
_ROWS = {
    "S1": [
        ("p1", 181.0707, "C6H12O6", "C6H13O6+", "assigned", 0.95, 5000.0),
        ("p3", 200.1234, None, None, "unassigned", None, 300.0),
    ],
    "S2": [("p1", 181.0708, "C6H12O6", "C6H13O6+", "assigned", 0.90, 4000.0)],
}


async def _seed_sample(session, batch_id, name, rows, now):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-ledger-export-{name}-{sample_file_id}.zarr",
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
            sample_item_name=f"Ledger export {name}",
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
            peak_assignment_run_utc_created=now - timedelta(hours=1),
            peak_assignment_run_utc_completed=now - timedelta(minutes=50),
        )
    )
    for pid, mz, formula, ion, tier, fit, intensity in rows:
        session.add(
            PeakAssignment(
                peak_assignment_id=gen_id(32),
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id=pid,
                sample_peak_mz=mz,
                sample_peak_intensity=intensity,
                role="M0" if formula else "unassigned",
                assigned_formula=formula,
                ion_formula=ion,
                source=("database" if formula else None),
                fit_score=fit,
                mz_error_ppm=(1.0 if formula else None),
                tier=tier,
            )
        )
    return sample_item_id


@pytest_asyncio.fixture
async def ledger(async_session_factory, pa_test_data):
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
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
                sample_batch_name=f"Ledger export {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        s1 = await _seed_sample(session, batch_id, "S1", _ROWS["S1"], now)
        s2 = await _seed_sample(session, batch_id, "S2", _ROWS["S2"], now)
        await session.commit()
    assert await fold_sample_into_batch_peaks(s1) == batch_id
    assert await fold_sample_into_batch_peaks(s2) == batch_id
    return {"batch_id": batch_id, "s1": s1, "s2": s2}


async def test_the_members_route_pages_the_flat_ledger(guest_client, ledger):
    first = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members",
        params={"limit": 2, "offset": 0},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["total"], body["results"], body["limit"], body["offset"]) == (
        3,
        2,
        2,
        0,
    )
    assert list(body["data"][0]) == list(COLUMNS)
    second = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members",
        params={"limit": 2, "offset": 2},
    )
    rows = body["data"] + second.json()["data"]
    assert len(rows) == 3
    # Anchor m/z order, then sample: the glucose anchor's two members first.
    assert [r["consensus_formula"] for r in rows] == ["C6H12O6", "C6H12O6", None]
    glucose = [r for r in rows if r["consensus_formula"] == "C6H12O6"]
    assert {r["sample_item_id"] for r in glucose} == {ledger["s1"], ledger["s2"]}
    assert {r["sample_item_name"] for r in glucose} == {
        "Ledger export S1",
        "Ledger export S2",
    }
    assert all(
        r["n_present"] == 2 and r["assigned_formula"] == "C6H12O6" for r in glucose
    )
    unassigned = rows[-1]
    assert unassigned["tier"] == "unassigned" and unassigned["assigned_formula"] is None
    assert unassigned["sample_peak_id"] == "p3"


async def test_the_members_route_narrows_to_a_sample(guest_client, ledger):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members",
        params={"sample_item_id": ledger["s2"]},
    )
    body = response.json()
    assert body["total"] == 1
    assert [r["sample_item_id"] for r in body["data"]] == [ledger["s2"]]


async def test_the_members_route_caps_the_page(guest_client, ledger):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/members",
        params={"limit": 999999},
    )
    assert response.status_code == 422, response.text


async def test_the_export_writes_the_whole_ledger_as_csv(ledger, test_users):
    user_id = test_users["editor"].id
    name, rows = await write_batch_ledger_csv(ledger["batch_id"], user_id)
    assert rows == 3
    assert name.endswith(".csv") and "batch_ledger" in name
    path = user_temp_path(user_id, name, create=False)
    try:
        frame = pd.read_csv(path, sep=";")
        assert list(frame.columns) == list(COLUMNS)
        assert len(frame) == 3
        assert frame["consensus_formula"].fillna("").tolist() == [
            "C6H12O6",
            "C6H12O6",
            "",
        ]
        assert frame["n_present"].tolist() == [2, 2, 1]
    finally:
        os.remove(path)


async def test_the_export_route_launches_for_a_guest(guest_client, ledger, monkeypatch):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "export_batch_ledger", fake_task)
    response = await guest_client.post(
        f"/api/batch-peaks/batch/{ledger['batch_id']}/export"
    )
    assert response.status_code == 202, response.text
    assert response.headers.get("process-id")
    assert len(launched) == 1
    assert launched[0]["sample_batch_id"] == ledger["batch_id"]
