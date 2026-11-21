"""Integration tests for the batch's assignment status (MAS-1013).

Two samples in a batch of their own, folded. The stages' own work is stood in
for: what is proven is the read. Each ``state`` is reached and returned for
its fixture - no batch peaks, folded only, running, current, failed,
cancelled, behind; each reason in ``behind`` holds where its change was made
after the pass and nowhere else - a sample folded in, a sample moved out, a
sample's in-app run, a collection attached, the batch's settings saved, an
engine version, a rebuild - and a curation, a verdict and an import leave the batch ``current``; a pass in
flight is served with its process, stage and who started it, to another
user's read too; and the read costs the same queries for 5,000 samples as for
two, in under a second. See ``batch_assignment_status.py``.
"""

import asyncio
import dataclasses
import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import event, insert, select, update

from mascope_backend.api.new.peak_assignments import batch_stages
from mascope_backend.api.new.peak_assignments.batch_assignment_status import (
    get_batch_assignment_status,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_ASSIGN,
    ACTION_IMPORT,
    complete_run,
    fail_run,
    record_progress,
    request_cancel,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_stages import (
    REASON_CONSENSUS,
    REASON_CURATION,
    REASON_REBUILD,
    STAGES,
    BatchStage,
    assign_batch,
    begin_stage,
    finish_stage,
    library_read,
    mark_batch_stale,
    mark_stale,
)
from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    settings_hash,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakRun,
    Dataset,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    Workspace,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

ORDER = [spec.stage.value for spec in STAGES]

# (peak id, mz, formula, ion formula, tier, fit, intensity)
_ROWS = {
    "S1": [
        ("p1", 181.0707, "C6H12O6", "C6H13O6+", "assigned", 0.95, 5000.0),
        ("p3", 200.1234, None, None, "unassigned", None, 300.0),
    ],
    "S2": [("p1", 181.0708, "C6H12O6", "C6H13O6+", "assigned", 0.90, 4000.0)],
    "S3": [("p1", 181.0706, "C6H12O6", "C6H13O6+", "assigned", 0.92, 4500.0)],
}


async def _seed_sample(session, batch_id, name, rows, now):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-assignment-status-{name}-{sample_file_id}.zarr",
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
            sample_item_name=f"Assignment status {name}",
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


async def _new_batch(session, pa_test_data, now) -> str:
    batch_id = gen_id()
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
            sample_batch_name=f"Assignment status {batch_id}",
            sample_batch_utc_created=now,
        )
    )
    return batch_id


@pytest_asyncio.fixture
async def folded(async_session_factory, pa_test_data):
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        batch_id = await _new_batch(session, pa_test_data, now)
        s1 = await _seed_sample(session, batch_id, "S1", _ROWS["S1"], now)
        s2 = await _seed_sample(session, batch_id, "S2", _ROWS["S2"], now)
        await session.commit()
    assert await fold_sample_into_batch_peaks(s1) == batch_id
    assert await fold_sample_into_batch_peaks(s2) == batch_id
    return {"batch_id": batch_id, "s1": s1, "s2": s2, "pa": pa_test_data}


@pytest.fixture
def stand_ins(monkeypatch):
    """Every stage's work stood in for, with a hook per stage that runs inside
    it."""
    hooks: dict[str, object] = {}

    def make(spec):
        async def run(call):
            hook = hooks.get(spec.stage.value)
            if hook is not None:
                outcome = await hook(call)
                if outcome is not None:
                    return [outcome]
            return [{"status": "success", "message": f"{spec.label} ran."}]

        return dataclasses.replace(spec, run=run)

    monkeypatch.setattr(
        batch_stages, "STAGE_BY_NAME", {spec.stage: make(spec) for spec in STAGES}
    )
    return {"hooks": hooks}


@pytest.fixture
def assignment_enabled(monkeypatch):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


async def _status(client, batch_id) -> dict:
    response = await client.get(f"/api/batch-peaks/batch/{batch_id}/assignment-status")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _reasons(status) -> dict[str, dict]:
    return {entry["reason"]: entry for entry in status["behind"]}


async def _pass(batch_id, user_id=None, **kwargs):
    kwargs.setdefault("process_id", "pass-1")
    return await assign_batch(sample_batch_id=batch_id, user_id=user_id, **kwargs)


async def _newest_run(async_session_factory, batch_id) -> BatchPeakRun:
    async with async_session_factory() as session:
        return (
            (
                await session.execute(
                    select(BatchPeakRun)
                    .where(BatchPeakRun.sample_batch_id == batch_id)
                    .order_by(BatchPeakRun.batch_peak_run_utc_created.desc())
                    .limit(1)
                )
            )
            .scalars()
            .one()
        )


def _hold(stand_ins, stage):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(call):
        entered.set()
        await release.wait()

    stand_ins["hooks"][stage] = hold
    return entered, release


# --- the states --------------------------------------------------------------------


async def test_a_batch_with_no_batch_peaks_is_none(
    guest_client, async_session_factory, pa_test_data
):
    async with async_session_factory() as session:
        batch_id = await _new_batch(session, pa_test_data, datetime.now(timezone.utc))
        await session.commit()
    status = await _status(guest_client, batch_id)
    assert status["state"] == "none"
    assert status["n_batch_peaks"] == 0
    assert status["last_pass"] is None
    assert status["running"] is None
    assert status["needs_fold"] is True
    assert status["fold_reasons"] == ["no_batch_peaks"]
    assert status["behind"] == []


async def test_a_ledger_the_folds_built_is_folded_only(guest_client, folded):
    status = await _status(guest_client, folded["batch_id"])
    assert status["state"] == "folded_only"
    assert status["n_samples"] == 2
    assert status["n_batch_peaks"] > 0
    assert status["last_pass"] is None
    assert status["needs_fold"] is False
    assert status["behind"] == []
    # No stage has run, so none is stale.
    assert {e["state"] for e in status["stages"]} == {"not_run"}
    assert status["stale_stages"] == []


async def test_a_completed_pass_leaves_the_batch_current(
    guest_client, folded, stand_ins, test_users, async_session_factory
):
    batch_id = folded["batch_id"]
    editor = test_users["editor"]
    await _pass(batch_id, user_id=editor.id)
    run = await _newest_run(async_session_factory, batch_id)
    status = await _status(guest_client, batch_id)
    assert status["state"] == "current"
    assert status["behind"] == []
    assert status["needs_fold"] is False
    assert status["stale_stages"] == []
    last = status["last_pass"]
    assert last["batch_peak_run_id"] == run.batch_peak_run_id
    assert last["action"] == ACTION_ASSIGN
    assert last["first_stage"] == ORDER[0]
    assert last["started_by"] == {"id": editor.id, "username": editor.username}
    assert last["engine_version"] == status["engine_version"]
    # The defaults: the batch saved no settings.
    assert last["settings_hash"] == settings_hash(PeakAssignmentConfig())
    assert status["settings_hash"] == last["settings_hash"]
    assert last["n_samples"] == 2
    assert last["utc_completed"] is not None
    # Its end wrote the summary whole: the progress it kept as it went is gone.
    assert "progress" not in run.summary
    assert status["stopped_pass"] is None


async def test_a_pass_in_flight_is_running_with_its_stage_and_its_user(
    guest_client, folded, stand_ins, test_users, async_session_factory
):
    """Another user's read sees the pass: its process, its stage, which of
    how many, who started it."""
    batch_id = folded["batch_id"]
    editor = test_users["editor"]
    entered, release = _hold(stand_ins, "continuity")
    task = asyncio.create_task(_pass(batch_id, user_id=editor.id, process_id="p-9"))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        status = await _status(guest_client, batch_id)
        run = await _newest_run(async_session_factory, batch_id)
    finally:
        release.set()
        await task
    assert status["state"] == "running"
    running = status["running"]
    assert running["batch_peak_run_id"] == run.batch_peak_run_id
    assert running["action"] == ACTION_ASSIGN
    assert running["process_id"] == "p-9"
    assert running["started_by"] == {"id": editor.id, "username": editor.username}
    assert running["stage"] == "continuity"
    assert running["label"] == "Ratio stability"
    assert running["stage_index"] == ORDER.index("continuity")
    assert running["n_stages"] == len(ORDER)
    assert running["progress"] == pytest.approx(ORDER.index("continuity") / len(ORDER))
    assert running["utc_started"] is not None
    assert running["utc_stage_started"] is not None
    assert running["cancel_requested_utc"] is None
    assert (await _status(guest_client, batch_id))["state"] == "current"


async def test_a_pass_that_folds_first_is_running_in_its_fold(
    folded, stand_ins, monkeypatch
):
    batch_id = folded["batch_id"]
    entered, release = asyncio.Event(), asyncio.Event()

    async def held_rebuild(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"status": "success", "message": "Rebuilt."}

    from mascope_backend.api.new.peak_assignments import batch_peaks_controller

    monkeypatch.setattr(batch_peaks_controller, "rebuild_batch_ledger", held_rebuild)
    task = asyncio.create_task(_pass(batch_id, fold="always"))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        status = (await get_batch_assignment_status(batch_id))["data"]
    finally:
        release.set()
        await task
    assert status["state"] == "running"
    assert status["running"]["stage"] == "fold"
    assert status["running"]["stage_index"] == 0
    assert status["running"]["n_stages"] == len(ORDER) + 1
    assert status["running"]["progress"] == 0


async def test_a_rebuild_on_its_own_in_flight_is_running(folded, test_users):
    batch_id = folded["batch_id"]
    run_id = await start_run(batch_id, "rebuild", user_id=test_users["editor"].id)
    status = (await get_batch_assignment_status(batch_id))["data"]
    assert status["state"] == "running"
    running = status["running"]
    assert running["batch_peak_run_id"] == run_id
    assert running["action"] == "rebuild"
    # The running run is no pass, so its user is read from the run itself.
    assert running["started_by"] == {
        "id": test_users["editor"].id,
        "username": test_users["editor"].username,
    }
    # A rebuild keeps no progress of its own.
    assert running["stage"] is None
    assert running["process_id"] is None
    assert running["progress"] is None


async def test_a_stage_running_with_no_run_is_running(folded):
    """The routes refuse on a running stage row whatever run there is, and so
    the status says running on one too."""
    batch_id = folded["batch_id"]
    _, started = await begin_stage(batch_id, BatchStage.CO_VARIATION)
    try:
        status = (await get_batch_assignment_status(batch_id))["data"]
    finally:
        await finish_stage(batch_id, BatchStage.CO_VARIATION, started, run_id=None)
    assert status["state"] == "running"
    assert status["running"]["batch_peak_run_id"] is None
    assert status["running"]["stage"] == "co_variation"
    assert status["running"]["label"] == "Read co-variation"


async def test_a_progress_write_onto_a_closed_run_leaves_it(
    guest_client, folded, stand_ins, async_session_factory
):
    """A late progress write, onto a pass already completed or failed, keeps
    the summary its end wrote: the batch still reads that pass."""
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    run = await _newest_run(async_session_factory, batch_id)
    await record_progress(run.batch_peak_run_id, {"stage": "series"})
    assert (await _newest_run(async_session_factory, batch_id)).summary == run.summary
    status = await _status(guest_client, batch_id)
    assert status["state"] == "current"
    assert status["last_pass"]["batch_peak_run_id"] == run.batch_peak_run_id

    failed_id = await start_run(batch_id, ACTION_ASSIGN)
    await fail_run(batch_id, failed_id, "broke")
    failed = await _newest_run(async_session_factory, batch_id)
    await record_progress(failed_id, {"stage": "series"})
    assert (
        await _newest_run(async_session_factory, batch_id)
    ).summary == failed.summary


async def test_a_pass_whose_stage_raises_is_failed(guest_client, folded, stand_ins):
    batch_id = folded["batch_id"]
    await _pass(batch_id)

    async def raises(call):
        raise RuntimeError("series broke")

    stand_ins["hooks"]["series"] = raises
    with pytest.raises(Exception):
        await _pass(batch_id, from_stage="water_ladders")
    status = await _status(guest_client, batch_id)
    assert status["state"] == "failed"
    stopped = status["stopped_pass"]
    assert stopped["status"] == "failed"
    assert stopped["stopped_at"] == "series"
    assert "series broke" in stopped["error"]
    # The pass before it is still the last one completed.
    assert status["last_pass"] is not None
    assert status["last_pass"]["batch_peak_run_id"] != stopped["batch_peak_run_id"]
    # A pass that completes after it ends that.
    stand_ins["hooks"].clear()
    await _pass(batch_id)
    status = await _status(guest_client, batch_id)
    assert status["state"] == "current"
    assert status["stopped_pass"] is None


async def test_a_rebuild_or_an_import_that_failed_is_no_stopped_pass(folded, stand_ins):
    """Only a pass stops the batch at a stage: a rebuild or an import of its
    own that failed is no pass."""
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    for action in ("rebuild", ACTION_IMPORT):
        await fail_run(batch_id, await start_run(batch_id, action), "broke")
    status = (await get_batch_assignment_status(batch_id))["data"]
    assert status["state"] == "current"
    assert status["stopped_pass"] is None


async def test_a_pass_a_person_cancelled_is_cancelled(guest_client, folded, stand_ins):
    batch_id = folded["batch_id"]

    async def cancels(call):
        await request_cancel(batch_id)

    stand_ins["hooks"]["search"] = cancels
    await _pass(batch_id)
    status = await _status(guest_client, batch_id)
    assert status["state"] == "cancelled"
    assert status["stopped_pass"]["status"] == "cancelled"
    assert status["stopped_pass"]["stopped_at"] == "isotope_lines"
    # No pass ever completed.
    assert status["last_pass"] is None


async def test_a_cancel_asked_for_is_served_on_the_running_pass(folded, stand_ins):
    batch_id = folded["batch_id"]
    entered, release = _hold(stand_ins, "search")
    task = asyncio.create_task(_pass(batch_id))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        await request_cancel(batch_id)
        status = (await get_batch_assignment_status(batch_id))["data"]
    finally:
        release.set()
        await task
    assert status["running"]["cancel_requested_utc"] is not None


# --- behind ------------------------------------------------------------------------


async def test_a_sample_folded_after_a_pass_is_one_new_sample(
    guest_client, folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    async with async_session_factory() as session:
        s3 = await _seed_sample(
            session, batch_id, "S3", _ROWS["S3"], datetime.now(timezone.utc)
        )
        await session.commit()
    await fold_sample_into_batch_peaks(s3)
    status = await _status(guest_client, batch_id)
    assert status["state"] == "behind"
    reasons = _reasons(status)
    assert reasons["new_samples"]["count"] == 1
    assert reasons["new_samples"]["sample_item_ids"] == [s3]
    # The fold's stale marks are the new sample's: no reason of their own.
    assert set(reasons) == {"new_samples"}
    assert status["stale_stages"] == ORDER
    assert status["needs_fold"] is False


async def test_a_sample_moved_out_after_a_pass_is_one_removed_sample(
    guest_client, folded, stand_ins, async_session_factory, pa_test_data
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    async with async_session_factory() as session:
        await session.execute(
            update(SampleItem)
            .where(SampleItem.sample_item_id == folded["s2"])
            .values(sample_batch_id=pa_test_data["sample_batch_id"])
        )
        await session.commit()
    status = await _status(guest_client, batch_id)
    assert status["state"] == "behind"
    reasons = _reasons(status)
    assert set(reasons) == {"removed_samples"}
    assert reasons["removed_samples"]["count"] == 1
    assert reasons["removed_samples"]["sample_item_ids"] == [folded["s2"]]


async def _add_run(async_session_factory, sample_item_id, **values):
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        row = {
            "peak_assignment_run_id": gen_id(),
            "sample_item_id": sample_item_id,
            "engine_version": "0.1.0-test",
            "status": "completed",
            "peak_assignment_run_utc_created": now,
            "peak_assignment_run_utc_completed": now,
        }
        session.add(PeakAssignmentRun(**{**row, **values}))
        await session.commit()


async def test_a_samples_run_newer_than_the_pass_is_resampled(
    guest_client, folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    # A decoy run, and another engine's run imported, are no run of the
    # sample's own.
    await _add_run(async_session_factory, folded["s1"], decoy="dalton")
    await _add_run(async_session_factory, folded["s1"], engine="peaky")
    await _add_run(async_session_factory, folded["s2"], status="failed")
    assert (await _status(guest_client, batch_id))["state"] == "current"
    await _add_run(async_session_factory, folded["s1"])
    status = await _status(guest_client, batch_id)
    assert status["state"] == "behind"
    reasons = _reasons(status)
    assert set(reasons) == {"resampled"}
    assert reasons["resampled"]["count"] == 1
    assert reasons["resampled"]["sample_item_ids"] == [folded["s1"]]


async def test_a_samples_run_completed_during_the_pass_is_resampled(
    guest_client, folded, stand_ins, async_session_factory
):
    """Measured from the pass's start: a run that completed while the pass
    was going may have folded what the pass's stages had already read."""
    batch_id = folded["batch_id"]
    entered, release = _hold(stand_ins, "continuity")
    task = asyncio.create_task(_pass(batch_id))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        await _add_run(async_session_factory, folded["s1"])
    finally:
        release.set()
        await task
    run = await _newest_run(async_session_factory, batch_id)
    assert run.status == "completed"
    status = await _status(guest_client, batch_id)
    assert status["state"] == "behind"
    reasons = _reasons(status)
    assert set(reasons) == {"resampled"}
    assert reasons["resampled"]["sample_item_ids"] == [folded["s1"]]


async def test_a_new_samples_run_makes_it_new_not_resampled(
    guest_client, folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    async with async_session_factory() as session:
        s3 = await _seed_sample(
            session, batch_id, "S3", _ROWS["S3"], datetime.now(timezone.utc)
        )
        await session.commit()
    await _add_run(async_session_factory, s3)
    assert set(_reasons(await _status(guest_client, batch_id))) == {"new_samples"}


async def _attach_collection(async_session_factory, batch_id) -> str:
    collection_id, compound_id = gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCollection(
                target_collection_id=collection_id,
                target_collection_name=f"Status library {collection_id}",
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=compound_id,
                target_compound_name="Glucose",
                target_compound_formula="C6H12O6",
            )
        )
        await session.flush()
        session.add(
            TargetCompoundInTargetCollection(
                target_collection_id=collection_id, target_compound_id=compound_id
            )
        )
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=collection_id, sample_batch_id=batch_id
            )
        )
        await session.commit()
    return collection_id


async def test_a_collection_attached_after_a_pass_changes_the_library(
    guest_client, folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    collection_id = await _attach_collection(async_session_factory, batch_id)
    status = await _status(guest_client, batch_id)
    assert status["state"] == "behind"
    reasons = _reasons(status)
    assert set(reasons) == {"library_changed"}
    detail = reasons["library_changed"]["detail"]
    assert detail["target_collections"] == {
        "attached": [collection_id],
        "detached": [],
        "edited": [],
    }
    assert reasons["library_changed"]["count"] == 1
    assert status["needs_fold"] is True
    assert status["fold_reasons"] == ["library_changed"]
    # A pass that folds it in is current again.
    await _pass(batch_id)
    status = await _status(guest_client, batch_id)
    assert status["state"] == "current"
    assert status["needs_fold"] is False


async def test_an_edited_and_a_detached_collection_are_named_as_such(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    edited = await _attach_collection(async_session_factory, batch_id)
    detached = await _attach_collection(async_session_factory, batch_id)
    await _pass(batch_id)
    async with async_session_factory() as session:
        await session.execute(
            update(TargetCompound)
            .where(
                TargetCompound.target_compound_id.in_(
                    select(TargetCompoundInTargetCollection.target_compound_id).where(
                        TargetCompoundInTargetCollection.target_collection_id == edited
                    )
                )
            )
            .values(target_compound_formula="C6H12O7")
        )
        await session.execute(
            TargetCollectionInSampleBatch.__table__.delete().where(
                TargetCollectionInSampleBatch.target_collection_id == detached
            )
        )
        await session.commit()
    status = (await get_batch_assignment_status(batch_id))["data"]
    detail = _reasons(status)["library_changed"]["detail"]["target_collections"]
    assert detail == {"attached": [], "detached": [detached], "edited": [edited]}


async def test_a_pass_under_another_engine_version_is_engine_changed(
    guest_client, folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    run = await _newest_run(async_session_factory, batch_id)
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeakRun)
            .where(BatchPeakRun.batch_peak_run_id == run.batch_peak_run_id)
            .values(engine_version="0.0.1-older")
        )
        await session.commit()
    status = await _status(guest_client, batch_id)
    assert status["state"] == "behind"
    reasons = _reasons(status)
    assert set(reasons) == {"engine_changed"}
    assert reasons["engine_changed"]["detail"] == {
        "pass": "0.0.1-older",
        "now": status["engine_version"],
    }
    assert status["fold_reasons"] == ["engine_changed"]


async def _save_settings(editor_client, batch_id, settings):
    response = await editor_client.patch(
        f"/api/sample/batches/{batch_id}", json={"assignment_settings": settings}
    )
    assert response.status_code == 200, response.text


async def test_settings_saved_after_a_pass_are_settings_changed(
    guest_client, editor_client, folded, stand_ins
):
    """Saving changed settings turns the batch behind with
    ``settings_changed``, and nothing else; a pass on them turns it back to
    current. Saving the settings it ran on again, or putting a batch back on
    the defaults it ran on, changes nothing."""
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    defaults = settings_hash(PeakAssignmentConfig())
    await _save_settings(editor_client, batch_id, {"mz_precision_ppm": 2.5})
    status = await _status(guest_client, batch_id)
    assert status["state"] == "behind"
    reasons = _reasons(status)
    assert set(reasons) == {"settings_changed"}
    now = settings_hash(PeakAssignmentConfig(mz_precision_ppm=2.5))
    assert reasons["settings_changed"]["detail"] == {"pass": defaults, "now": now}
    assert status["settings_hash"] == now
    assert status["last_pass"]["settings_hash"] == defaults
    # No fold: the settings are read by the stages, not by the fold.
    assert status["needs_fold"] is False

    await _pass(batch_id)
    status = await _status(guest_client, batch_id)
    assert status["state"] == "current"
    assert status["behind"] == []
    assert status["last_pass"]["settings_hash"] == now

    await _save_settings(editor_client, batch_id, {"mz_precision_ppm": 2.5})
    assert (await _status(guest_client, batch_id))["state"] == "current"
    await _save_settings(editor_client, batch_id, None)
    assert set(_reasons(await _status(guest_client, batch_id))) == {"settings_changed"}
    await _pass(batch_id)
    await _save_settings(editor_client, batch_id, {})
    assert (await _status(guest_client, batch_id))["state"] == "current"


async def test_a_pass_on_a_requests_own_settings_leaves_it_settings_changed(
    guest_client, folded, stand_ins
):
    """A pass a step's route ran on settings of its own did not run on the
    batch's: the batch stays behind until a pass on its own has."""
    batch_id = folded["batch_id"]
    await _pass(batch_id, config=PeakAssignmentConfig(mz_precision_ppm=1.5))
    status = await _status(guest_client, batch_id)
    assert set(_reasons(status)) == {"settings_changed"}
    await _pass(batch_id)
    assert (await _status(guest_client, batch_id))["state"] == "current"


async def test_a_rebuild_after_a_pass_leaves_its_stages_stale(
    guest_client, folded, stand_ins
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    await mark_batch_stale(batch_id, REASON_REBUILD)
    status = await _status(guest_client, batch_id)
    assert status["state"] == "behind"
    reasons = _reasons(status)
    assert set(reasons) == {"stages_stale"}
    assert reasons["stages_stale"]["count"] == len(ORDER)
    assert reasons["stages_stale"]["detail"][0] == {
        "stage": ORDER[0],
        "stale_reason": REASON_REBUILD,
    }


async def test_a_fold_no_sample_explains_leaves_its_stages_stale(folded, stand_ins):
    """A sample folded again without a run of its own: the stages have not
    seen what the fold wrote."""
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    await fold_sample_into_batch_peaks(folded["s1"])
    status = (await get_batch_assignment_status(batch_id))["data"]
    assert set(_reasons(status)) == {"stages_stale"}
    assert {e["stale_reason"] for e in _reasons(status)["stages_stale"]["detail"]} == {
        "fold"
    }


async def test_a_stage_the_pass_itself_left_stale_is_a_reason(folded, stand_ins):
    """A consensus recompute inside the pass knocked an earlier stage's record
    off: the pass completed, and that stage is still stale."""
    batch_id = folded["batch_id"]

    async def knocks_off(call):
        async with batch_stages.async_session() as session:
            await _acquire_batch_fold_lock(session, batch_id)
            await mark_stale(session, batch_id, ["water_ladders"], REASON_CONSENSUS)
            await session.commit()

    stand_ins["hooks"]["blends"] = knocks_off
    await _pass(batch_id)
    status = (await get_batch_assignment_status(batch_id))["data"]
    assert status["state"] == "behind"
    assert _reasons(status)["stages_stale"]["detail"] == [
        {"stage": "water_ladders", "stale_reason": REASON_CONSENSUS}
    ]


# --- not reasons ------------------------------------------------------------------


async def test_a_curation_after_a_pass_leaves_it_current(
    guest_client, folded, stand_ins, async_session_factory
):
    """A pin marks every stage stale, and the consensus it recomputes can mark
    some again: neither is a reason."""
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    await mark_batch_stale(batch_id, REASON_CURATION)
    async with async_session_factory() as session:
        await _acquire_batch_fold_lock(session, batch_id)
        await mark_stale(session, batch_id, ["search"], REASON_CONSENSUS)
        await session.commit()
    status = await _status(guest_client, batch_id)
    assert status["state"] == "current"
    assert status["behind"] == []
    # The next pass still starts at the first stale stage.
    assert status["stale_stages"] == ORDER


async def test_a_verdict_after_a_pass_leaves_it_current(
    guest_client,
    editor_client,
    folded,
    stand_ins,
    assignment_enabled,
    async_session_factory,
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    async with async_session_factory() as session:
        anchor = (
            await session.execute(
                select(BatchPeak.batch_peak_id).where(
                    BatchPeak.sample_batch_id == batch_id,
                    BatchPeak.consensus_formula == "C6H12O6",
                )
            )
        ).scalar_one()
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/verify",
        json={
            "batch_peak_id": anchor,
            "verdict": "confirmed",
            "evidence_level": "pattern",
            "expected_formula": "C6H12O6",
        },
    )
    assert response.status_code == 201, response.text
    status = await _status(guest_client, batch_id)
    assert status["state"] == "current"
    assert status["behind"] == []


async def test_an_import_after_a_pass_leaves_it_current(
    guest_client, folded, stand_ins
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    await complete_run(
        batch_id, await start_run(batch_id, ACTION_IMPORT, engine="peaky")
    )
    await mark_batch_stale(batch_id, "import")
    status = await _status(guest_client, batch_id)
    assert status["state"] == "current"
    assert status["behind"] == []
    # The next pass folds first, to put Mascope's own view back.
    assert status["fold_reasons"] == ["import"]


# --- access and cost ---------------------------------------------------------------


async def test_a_reader_outside_the_workspace_is_refused(
    guest_client, async_session_factory
):
    """Guest is the read's gate: a member reads it, a batch of a workspace
    the user is no member of is refused."""
    now = datetime.now(timezone.utc)
    workspace_id, dataset_id, batch_id = gen_id(), gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"Status elsewhere {workspace_id}",
                workspace_status="active",
                workspace_utc_created=now,
                workspace_utc_modified=now,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Status elsewhere",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name="Status elsewhere",
                sample_batch_utc_created=now,
            )
        )
        await session.commit()
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}/assignment-status"
    )
    assert response.status_code in (403, 404), response.text


async def test_the_read_costs_the_same_queries_for_5000_samples(
    folded, stand_ins, async_session_factory, async_engine
):
    """One query per kind of thing read, never one per sample: the batch of
    two and a batch of 5,000 send the same statements, and the big one
    answers in under a second."""
    small = folded["batch_id"]
    await _pass(small)

    n = 5000
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        big = await _new_batch(session, folded["pa"], now)
        s1 = await _seed_sample(session, big, "S1", _ROWS["S1"], now)
        await session.commit()
    await fold_sample_into_batch_peaks(s1)
    files = [gen_id() for _ in range(n)]
    items = [gen_id() for _ in range(n)]
    async with async_session_factory() as session:
        await session.execute(
            insert(SampleFile),
            [
                {
                    "sample_file_id": f,
                    "filename": f"orbi-assignment-status-bulk-{f}.zarr",
                    "instrument": "orbi-test",
                    "datetime": datetime(2026, 7, 4, 12, 0, 0),
                    "datetime_utc": now,
                    "length": 60.0,
                    "range": [50.0, 500.0],
                    "polarity": "+",
                }
                for f in files
            ],
        )
        await session.execute(
            insert(SampleItem),
            [
                {
                    "sample_item_id": i,
                    "sample_batch_id": big,
                    "sample_file_id": f,
                    "sample_item_name": f"Bulk {i}",
                    "sample_item_type": "sample",
                    "polarity": "+",
                    "sample_item_utc_created": now,
                }
                for i, f in zip(items, files)
            ],
        )
        await session.commit()
    # A pass that covered all but the last 100, and a run since on 1,000.
    async with async_session_factory() as session:
        library = await library_read(session, big)
    run_id = await start_run(big, ACTION_ASSIGN)
    await complete_run(
        big,
        run_id,
        summary={
            "stages": [],
            "first_stage": ORDER[0],
            "samples": sorted([s1, *items[:-100]]),
            "library": library,
        },
    )
    later = datetime.now(timezone.utc) + timedelta(seconds=1)
    async with async_session_factory() as session:
        await session.execute(
            insert(PeakAssignmentRun),
            [
                {
                    "peak_assignment_run_id": gen_id(),
                    "sample_item_id": i,
                    "engine_version": "0.1.0-test",
                    "status": "completed",
                    "peak_assignment_run_utc_created": later,
                    "peak_assignment_run_utc_completed": later,
                }
                for i in items[:1000]
            ],
        )
        await session.commit()

    async def statements(batch_id) -> tuple[list[str], float]:
        sent: list[str] = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            sent.append(statement.split(" WHERE ")[0])

        event.listen(async_engine.sync_engine, "before_cursor_execute", capture)
        try:
            started = time.perf_counter()
            result = await get_batch_assignment_status(batch_id)
            elapsed = time.perf_counter() - started
        finally:
            event.remove(async_engine.sync_engine, "before_cursor_execute", capture)
        return sent, elapsed, result["data"]

    small_sent, _, _ = await statements(small)
    # Warm, as a bar that reads on every reload is.
    await statements(big)
    big_sent, elapsed, status = await statements(big)
    assert big_sent == small_sent
    assert elapsed < 1.0, elapsed
    reasons = _reasons(status)
    assert status["n_samples"] == n + 1
    assert reasons["new_samples"]["count"] == 100
    assert reasons["resampled"]["count"] == 1000
    assert len(reasons["resampled"]["sample_item_ids"]) == 100
