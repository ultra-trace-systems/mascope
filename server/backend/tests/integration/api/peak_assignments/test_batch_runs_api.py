"""Integration tests for batch runs: the implicit fold run, the snapshot a new
run takes of the current one, reads of the ledger and the series as an earlier
run left them, the in-flight refusal, retention, and the rebuild as a run.

Two samples in a batch of their own, folded. See ``batch_runs.py``.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from mascope_backend.api.new.peak_assignments import batch_peaks_routes
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
    rebuild_batch_ledger,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_FOLD,
    ACTION_REBUILD,
    ACTION_SEARCH,
    BATCH_RUN_KEEP,
    RUN_IN_FLIGHT_CODE,
    BatchRunInFlightException,
    complete_run,
    fail_run,
    start_run,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakRun,
    BatchPeakRunAnchor,
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
            filename=f"orbi-batch-runs-{name}-{sample_file_id}.zarr",
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
            sample_item_name=f"Batch runs {name}",
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
async def folded(async_session_factory, pa_test_data):
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
                sample_batch_name=f"Batch runs {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        s1 = await _seed_sample(session, batch_id, "S1", _ROWS["S1"], now)
        s2 = await _seed_sample(session, batch_id, "S2", _ROWS["S2"], now)
        await session.commit()
    assert await fold_sample_into_batch_peaks(s1) == batch_id
    assert await fold_sample_into_batch_peaks(s2) == batch_id
    return {"batch_id": batch_id, "s1": s1, "s2": s2}


@pytest.fixture
def assignment_enabled(monkeypatch):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


async def _runs(client, batch_id):
    response = await client.get(f"/api/batch-peaks/batch/{batch_id}/runs")
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _ledger(client, batch_id, **params):
    response = await client.get(
        f"/api/batch-peaks/batch/{batch_id}", params={"min_n_present": 1, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_the_first_fold_mints_the_current_fold_run(guest_client, folded):
    runs = await _runs(guest_client, folded["batch_id"])
    assert len(runs) == 1
    [run] = runs
    assert run["action"] == ACTION_FOLD
    assert run["status"] == "completed"
    assert run["current"] is True
    assert run["engine"] == "mascope"
    # The live ledger is that run: reading it by id is reading the live ledger.
    live = await _ledger(guest_client, folded["batch_id"])
    by_run = await _ledger(
        guest_client, folded["batch_id"], batch_peak_run_id=run["batch_peak_run_id"]
    )
    assert by_run == live
    assert len(live) == 2


async def test_a_new_run_snapshots_the_current_one_and_takes_over_when_complete(
    guest_client, async_session_factory, folded
):
    batch_id = folded["batch_id"]
    [fold_run] = await _runs(guest_client, batch_id)
    run_id = await start_run(batch_id, ACTION_SEARCH, config={"mz_precision_ppm": 2})
    runs = await _runs(guest_client, batch_id)
    assert [(r["action"], r["status"], r["current"]) for r in runs] == [
        (ACTION_SEARCH, "running", False),
        (ACTION_FOLD, "completed", True),
    ]
    assert runs[0]["config"] == {"mz_precision_ppm": 2}
    # The fold run's state is captured, anchor by anchor, members as arrays.
    async with async_session_factory() as session:
        captured = (
            (
                await session.execute(
                    select(BatchPeakRunAnchor).where(
                        BatchPeakRunAnchor.batch_peak_run_id
                        == fold_run["batch_peak_run_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(captured) == 2
        glucose = next(a for a in captured if a.consensus_formula == "C6H12O6")
        assert sorted(glucose.members["sample_item_ids"]) == sorted(
            [folded["s1"], folded["s2"]]
        )
        assert glucose.members["sample_peak_ids"] == ["p1", "p1"]
        assert glucose.n_present == 2
        assert (
            await session.get(BatchPeakRun, fold_run["batch_peak_run_id"])
        ).snapshot_utc
    # Meanwhile the live ledger moves on: the search rewrites the consensus.
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(
                BatchPeak.sample_batch_id == batch_id,
                BatchPeak.consensus_formula == "C6H12O6",
            )
            .values(consensus_formula="C7H14O7")
        )
        await session.commit()
    await complete_run(batch_id, run_id, summary={"anchors_annotated": 1})
    runs = await _runs(guest_client, batch_id)
    assert [(r["action"], r["status"], r["current"]) for r in runs] == [
        (ACTION_SEARCH, "completed", True),
        (ACTION_FOLD, "completed", False),
    ]
    assert runs[0]["summary"] == {"anchors_annotated": 1}
    # Reading the ledger as the fold run left it comes off the snapshot.
    live = await _ledger(guest_client, batch_id)
    assert {r["consensus_formula"] for r in live} == {"C7H14O7", None}
    before = await _ledger(
        guest_client, batch_id, batch_peak_run_id=fold_run["batch_peak_run_id"]
    )
    assert {r["consensus_formula"] for r in before} == {"C6H12O6", None}
    assert all(r["batch_peak_run_id"] == fold_run["batch_peak_run_id"] for r in before)
    assert [r["mz"] for r in before] == sorted(r["mz"] for r in before)
    # ... and so does the series.
    response = await guest_client.post(
        "/api/batch-peaks/records/series",
        json={
            "sample_batch_id": batch_id,
            "min_n_present": 1,
            "batch_peak_run_id": fold_run["batch_peak_run_id"],
        },
    )
    assert response.status_code == 200, response.text
    series = {r["consensus_formula"]: r["peak_series"] for r in response.json()["data"]}
    assert sorted(series["C6H12O6"]["intensities"]) == [4000.0, 5000.0]
    assert series["C6H12O6"]["tiers"] == ["assigned", "assigned"]
    # Narrowed to one sample, the arrays narrow with it.
    response = await guest_client.post(
        "/api/batch-peaks/records/series",
        json={
            "sample_item_ids": [folded["s2"]],
            "batch_peak_run_id": fold_run["batch_peak_run_id"],
        },
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert [
        r["peak_series"]["sample_item_ids"] for r in rows if r["consensus_formula"]
    ] == [[folded["s2"]]]


async def test_a_second_operation_is_refused_while_one_runs(
    editor_client, folded, assignment_enabled
):
    batch_id = folded["batch_id"]
    run_id = await start_run(batch_id, ACTION_REBUILD)
    with pytest.raises(BatchRunInFlightException):
        await start_run(batch_id, ACTION_SEARCH)
    # The routes refuse on the request, before any task is launched.
    response = await editor_client.post(f"/api/batch-peaks/batch/{batch_id}/backfill")
    assert response.status_code == 409, response.text
    assert RUN_IN_FLIGHT_CODE in response.text
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/search-untargeted"
    )
    assert response.status_code == 409, response.text
    # A failed run frees the ledger and never becomes current.
    await fail_run(batch_id, run_id, "the engine exploded")
    runs = await _runs(editor_client, batch_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "the engine exploded"
    assert runs[0]["current"] is False
    assert runs[1]["current"] is True
    assert await start_run(batch_id, ACTION_SEARCH)


async def test_completion_prunes_beyond_the_keep(guest_client, folded):
    batch_id = folded["batch_id"]
    for _ in range(BATCH_RUN_KEEP + 3):
        run_id = await start_run(batch_id, ACTION_SEARCH)
        await complete_run(batch_id, run_id)
    runs = await _runs(guest_client, batch_id)
    assert len(runs) == BATCH_RUN_KEEP + 1
    assert runs[0]["current"] is True
    assert all(r["current"] is False for r in runs[1:])
    # The implicit fold run was the oldest and is gone.
    assert all(r["action"] == ACTION_SEARCH for r in runs)


async def test_an_unknown_or_foreign_run_is_not_found(
    guest_client, folded, pa_test_data
):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{folded['batch_id']}",
        params={"min_n_present": 1, "batch_peak_run_id": "nope"},
    )
    assert response.status_code == 404, response.text
    [run] = await _runs(guest_client, folded["batch_id"])
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{pa_test_data['sample_batch_id']}",
        params={"min_n_present": 1, "batch_peak_run_id": run["batch_peak_run_id"]},
    )
    assert response.status_code == 404, response.text


async def test_a_rebuild_is_a_run(guest_client, folded):
    batch_id = folded["batch_id"]
    outcome = await rebuild_batch_ledger(batch_id, user_id=None)
    assert outcome["status"] == "success", outcome
    runs = await _runs(guest_client, batch_id)
    assert [(r["action"], r["status"], r["current"]) for r in runs] == [
        (ACTION_REBUILD, "completed", True),
        (ACTION_FOLD, "completed", False),
    ]
    assert runs[0]["summary"] == {"samples_folded": 2, "samples_failed": 0}
    # The ledger the rebuild produced is the live one, and the fold run's
    # state is readable behind it.
    before = await _ledger(
        guest_client, batch_id, batch_peak_run_id=runs[1]["batch_peak_run_id"]
    )
    assert len(before) == 2


async def test_the_route_stub_is_not_needed_for_the_runs_read(
    guest_client, folded, monkeypatch
):
    """The runs listing is a plain read, open to a guest, unaffected by the
    task routes' stubs."""
    monkeypatch.setattr(batch_peaks_routes, "compute_batch_peaks", None)
    assert len(await _runs(guest_client, folded["batch_id"])) == 1
