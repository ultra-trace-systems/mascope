"""Integration tests for a pass of the batch stages as one batch run (MAS-1011).

Two samples in a batch of their own, folded, each with a peak nothing has
assigned. The stages' own work is stood in for where a test proves the pass
rather than a stage. What is proven: a pass is one batch run with one
snapshot, every step of every stage writing into it, and six passes keep the
five before the current one; its summary lists every stage with its status
and outcome, those before a later start as not run; it folds first exactly
where ``needs_fold`` says, or where asked; its progress is one process with a
packet per stage start, every stage's own packets nested under it; a person's
cancel, between stages or inside the search, ends it ``cancelled`` and lets
the next pass start at once; a task the event loop cancels leaves no run
running; a ``peak_assignment_reload`` follows each stage that rewrites
members, and one ends the pass; the settings a pass's stage ran on are what
the next pass given none reads; and the routes that start and stop a pass.
See ``batch_stages.assign_batch``.
"""

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone

import anyio
import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from mascope_backend.api.lib import api_features
from mascope_backend.api.lib import utils as api_utils
from mascope_backend.api.new.peak_assignments import (
    batch_fold,
    batch_peaks_controller,
    batch_peaks_routes,
    batch_stages,
    batch_staleness,
    batch_untargeted,
)
from mascope_backend.api.new.peak_assignments.batch_fold import rebuild_batch_ledger
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_ASSIGN,
    ACTION_IMPORT,
    ACTION_SEARCH,
    BATCH_RUN_KEEP,
    RUN_IN_FLIGHT_CODE,
    complete_run,
    fail_run,
    request_cancel,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_stages import (
    INTERRUPTED_ERROR,
    STAGES,
    assign_batch,
    check_no_run_in_flight,
    needs_fold,
    stage_standing,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.db import (
    BatchPeakRun,
    BatchPeakRunAnchor,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

ORDER = [spec.stage.value for spec in STAGES]
REWRITING = [spec.stage.value for spec in STAGES if spec.rewrites]

# (peak id, mz, formula, ion formula, tier, fit, intensity)
_ROWS = {
    "S1": [
        ("p1", 181.0707, "C6H12O6", "C6H13O6+", "assigned", 0.95, 5000.0),
        ("p3", 200.1234, None, None, "unassigned", None, 300.0),
    ],
    "S2": [
        ("p1", 181.0708, "C6H12O6", "C6H13O6+", "assigned", 0.90, 4000.0),
        ("p4", 312.2345, None, None, "unassigned", None, 250.0),
    ],
}


async def _seed_sample(session, batch_id, name, rows, now):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-batch-assign-{name}-{sample_file_id}.zarr",
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
            sample_item_name=f"Batch assign {name}",
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
            sample_batch_name=f"Batch assign {batch_id}",
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
    """Every stage's work stood in for by a recorder: what ran, in order, the
    call each ran with, its counts, and a hook per stage that runs inside it."""
    ran: list[str] = []
    calls: dict[str, object] = {}
    hooks: dict[str, object] = {}

    def make(spec):
        async def run(call):
            ran.append(spec.stage.value)
            calls[spec.stage.value] = call
            hook = hooks.get(spec.stage.value)
            if hook is not None:
                outcome = await hook(call)
                if outcome is not None:
                    return [outcome]
            return [
                {
                    "status": "success",
                    "message": f"{spec.label} ran.",
                    "data": {"stood_in": spec.stage.value},
                }
            ]

        return dataclasses.replace(spec, run=run)

    monkeypatch.setattr(
        batch_stages,
        "STAGE_BY_NAME",
        {spec.stage: make(spec) for spec in STAGES},
    )
    return {"ran": ran, "calls": calls, "hooks": hooks}


@pytest.fixture
def assignment_enabled(monkeypatch):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


async def _runs(async_session_factory, batch_id) -> list[BatchPeakRun]:
    async with async_session_factory() as session:
        return (
            (
                await session.execute(
                    select(BatchPeakRun)
                    .where(BatchPeakRun.sample_batch_id == batch_id)
                    .order_by(BatchPeakRun.batch_peak_run_utc_created)
                )
            )
            .scalars()
            .all()
        )


async def _snapshots(async_session_factory, batch_id) -> dict[str, int]:
    """Snapshot rows per run of the batch."""
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(BatchPeakRunAnchor.batch_peak_run_id, func.count())
                .join(
                    BatchPeakRun,
                    BatchPeakRun.batch_peak_run_id
                    == BatchPeakRunAnchor.batch_peak_run_id,
                )
                .where(BatchPeakRun.sample_batch_id == batch_id)
                .group_by(BatchPeakRunAnchor.batch_peak_run_id)
            )
        ).all()
    return {run_id: n for run_id, n in rows}


def _stages(run: BatchPeakRun) -> dict[str, dict]:
    return {entry["stage"]: entry for entry in run.summary["stages"]}


async def _states(batch_id) -> dict[str, dict]:
    standing = await stage_standing(batch_id)
    return {entry["stage"]: entry for entry in standing["stages"]}


async def _pass(batch_id, **kwargs):
    kwargs.setdefault("process_id", "pass-1")
    return await assign_batch(sample_batch_id=batch_id, **kwargs)


# --- one run per pass --------------------------------------------------------------


async def test_a_pass_of_the_real_stages_is_one_batch_run_with_one_snapshot(
    folded, async_session_factory, monkeypatch
):
    """Every step of every stage writes into the pass's run: one run, one
    snapshot (of the run current when it started), and every record a stage
    wrote names it. Each stage's own packets nest under the pass's process."""
    batch_id = folded["batch_id"]
    before = await _runs(async_session_factory, batch_id)
    assert [r.action for r in before] == ["fold"]
    children = []
    real = batch_untargeted.send_progress_user_notification

    async def nested(notification, *args, **kwargs):
        children.append(notification)
        return await real(notification, *args, **kwargs)

    monkeypatch.setattr(batch_untargeted, "send_progress_user_notification", nested)

    outcome = await _pass(batch_id)
    assert outcome["status"] in ("success", "partial"), outcome["message"]
    runs = await _runs(async_session_factory, batch_id)
    assert len(runs) == 2
    fold_run, run = runs
    assert run.action == ACTION_ASSIGN
    assert run.status == "completed"
    assert run.is_current == 1
    assert outcome["data"]["batch_peak_run_id"] == run.batch_peak_run_id
    # One snapshot: the ledger as the fold run left it, taken as the pass began.
    assert set(await _snapshots(async_session_factory, batch_id)) == {
        fold_run.batch_peak_run_id
    }
    # Every stage ran inside it. The fixture holds no spectra, so the search
    # reads neither sample: it skips both, names them, and is current with
    # them, as every other stage is (MAS-1060).
    stages = _stages(run)
    assert [stages[name]["status"] for name in ORDER] == ["done"] * len(ORDER)
    assert stages["fold"]["status"] == "not_run"
    states = await _states(batch_id)
    assert {e["state"] for e in states.values()} == {"current"}
    assert states["search"]["skipped_samples"] == [
        {"sample_item_id": sample_item_id, "peaks_read": False}
        for sample_item_id in sorted([folded["s1"], folded["s2"]])
    ]
    # Each step's outcome is kept, with the thresholds or settings it ran on.
    assert len(stages["isotope_lines"]["outcomes"]) == 3  # strip, stamp, judge
    assert stages["search"]["outcomes"][0]["config"]["mz_precision_ppm"] == (
        PeakAssignmentConfig().mz_precision_ppm
    )
    assert stages["search"]["settings"] is not None
    # The stage rows name the pass's run.
    async with async_session_factory() as session:
        named = {
            r.batch_peak_run_id
            for r in (await batch_staleness.stage_states(session, batch_id)).values()
            if r.stage in {s.stage.value for s in STAGES if s.actions}
        }
    assert named == {run.batch_peak_run_id}
    # The search's packets are a child's of the pass's process.
    assert children
    assert {n.parent_id for n in children} == {"pass-1"}
    assert {n.process_id for n in children} != {"pass-1"}


async def test_six_passes_keep_the_five_before_the_current_one(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    for _ in range(6):
        await _pass(batch_id, from_stage="water_ladders")
    runs = await _runs(async_session_factory, batch_id)
    assert [r.action for r in runs] == [ACTION_ASSIGN] * (BATCH_RUN_KEEP + 1)
    assert [bool(r.is_current) for r in runs] == [False] * BATCH_RUN_KEEP + [True]
    # One snapshot per pass: each of the five kept holds the ledger as the
    # pass after it found it.
    assert set(await _snapshots(async_session_factory, batch_id)) == {
        r.batch_peak_run_id for r in runs[:-1]
    }


async def test_the_summary_lists_every_stage_and_a_later_start_the_earlier_as_not_run(
    folded, stand_ins, async_session_factory, guest_client
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    await _pass(batch_id, from_stage="series")
    runs = await _runs(async_session_factory, batch_id)
    first, later = _stages(runs[-2]), _stages(runs[-1])
    assert list(first) == ["fold"] + ORDER
    for name in ORDER:
        entry = first[name]
        assert entry["status"] == "done"
        assert entry["phase"]
        assert entry["utc_started"] <= entry["utc_ended"]
        assert entry["outcomes"][0]["data"] == {"stood_in": name}
        assert entry["state"] == "current"
    assert runs[-1].summary["first_stage"] == "series"
    assert runs[-1].summary["from_stage"] == "series"
    assert runs[-1].config["from_stage"] == "series"
    assert [later[name]["status"] for name in ORDER] == ["not_run"] * 4 + ["done"] * 6
    assert later["fold"]["status"] == "not_run"
    assert runs[-1].summary["samples"] == sorted([folded["s1"], folded["s2"]])
    assert set(runs[-1].summary["library"]) == {"target_collections", "reference_lists"}
    # The runs listing serves them.
    response = await guest_client.get(f"/api/batch-peaks/batch/{batch_id}/runs")
    assert response.status_code == 200, response.text
    newest = response.json()["data"][0]
    assert newest["action"] == ACTION_ASSIGN
    assert [e["stage"] for e in newest["summary"]["stages"]] == ["fold"] + ORDER
    assert newest["cancel_requested_utc"] is None


async def test_a_pass_with_nothing_to_do_opens_no_run(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    stand_ins["ran"].clear()
    outcome = await _pass(batch_id)
    assert outcome["status"] == "skipped"
    assert stand_ins["ran"] == []
    assert len(await _runs(async_session_factory, batch_id)) == 2


async def test_a_steps_own_route_records_its_pass_under_its_action(
    folded, stand_ins, async_session_factory
):
    """The pass a step's route starts is one run too, under the action the
    step's run always had."""
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    await _pass(batch_id, from_stage="search", action=ACTION_SEARCH)
    runs = await _runs(async_session_factory, batch_id)
    assert [r.action for r in runs] == ["fold", ACTION_ASSIGN, ACTION_SEARCH]
    assert _stages(runs[-1])["search"]["status"] == "done"


# --- folding first -----------------------------------------------------------------


async def _complete_pass(async_session_factory, batch_id):
    """The batch's newest completed pass."""
    runs = await _runs(async_session_factory, batch_id)
    return next(r for r in reversed(runs) if r.summary and "stages" in r.summary)


async def test_a_ledger_folds_built_needs_no_fold_nor_after_a_pass(folded, stand_ins):
    batch_id = folded["batch_id"]
    assert await needs_fold(batch_id) == []
    await _pass(batch_id)
    assert await needs_fold(batch_id) == []


async def test_a_batch_with_no_batch_peaks_needs_a_fold(
    async_session_factory, pa_test_data
):
    async with async_session_factory() as session:
        batch_id = await _new_batch(session, pa_test_data, datetime.now(timezone.utc))
        await session.commit()
    assert await needs_fold(batch_id) == ["no_batch_peaks"]


async def test_an_import_current_needs_a_fold(folded, stand_ins):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    await complete_run(
        batch_id, await start_run(batch_id, ACTION_IMPORT, engine="peaky")
    )
    assert await needs_fold(batch_id) == ["import"]


async def test_a_library_changed_since_the_last_pass_needs_a_fold(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    collection_id, compound_id = gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCollection(
                target_collection_id=collection_id,
                target_collection_name=f"Assign library {collection_id}",
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
    # A collection the batch now names.
    assert await needs_fold(batch_id) == ["library_changed"]
    await _pass(batch_id, fold="always")
    assert await needs_fold(batch_id) == []
    # A compound of it edited: the collection keeps no modification time, so
    # its contents are what is compared.
    async with async_session_factory() as session:
        await session.execute(
            update(TargetCompound)
            .where(TargetCompound.target_compound_id == compound_id)
            .values(target_compound_formula="C6H12O7")
        )
        await session.commit()
    assert await needs_fold(batch_id) == ["library_changed"]


async def test_a_last_pass_under_another_engine_needs_a_fold(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    last = await _complete_pass(async_session_factory, batch_id)
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeakRun)
            .where(BatchPeakRun.batch_peak_run_id == last.batch_peak_run_id)
            .values(engine_version="0.0.1-older")
        )
        await session.commit()
    assert await needs_fold(batch_id) == ["engine_changed"]


async def test_only_a_completed_pass_is_the_last_one(
    folded, stand_ins, async_session_factory
):
    """A cancelled pass read a library, but need not have folded it in: the
    library is compared with the last pass that completed."""
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    last = await _complete_pass(async_session_factory, batch_id)

    async def cancels(call):
        await request_cancel(batch_id)

    stand_ins["hooks"]["water_ladders"] = cancels
    await _pass(batch_id, from_stage="water_ladders")
    cancelled = (await _runs(async_session_factory, batch_id))[-1]
    assert cancelled.status == "cancelled"
    # The cancelled pass recorded another library than the completed one did.
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeakRun)
            .where(BatchPeakRun.batch_peak_run_id == last.batch_peak_run_id)
            .values(
                summary={
                    **last.summary,
                    "library": {"target_collections": {"gone": "x"}},
                }
            )
        )
        await session.commit()
    assert await needs_fold(batch_id) == ["library_changed"]


async def test_a_rebuild_on_its_own_that_is_cancelled_closes_its_run(
    folded, async_session_factory, monkeypatch
):
    batch_id = folded["batch_id"]

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(batch_fold, "backfill_sample_batch_peaks", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await rebuild_batch_ledger(batch_id)
    run = (await _runs(async_session_factory, batch_id))[-1]
    assert run.action == "rebuild"
    assert run.status == "failed"
    await check_no_run_in_flight(sample_batch_id=batch_id)


async def test_a_pass_that_needs_a_fold_folds_first_and_runs_every_stage(
    folded, stand_ins, async_session_factory, monkeypatch
):
    """The fold runs inside the pass's run, without the rebuild's own ratio
    stability, which the pass's stage grades after the co-variation."""
    batch_id = folded["batch_id"]
    graded = []

    async def grading(*args, **kwargs):
        graded.append(args)

    monkeypatch.setattr(batch_fold, "_grade_continuity_after_backfill", grading)
    await _pass(batch_id)
    await complete_run(
        batch_id, await start_run(batch_id, ACTION_IMPORT, engine="peaky")
    )
    stand_ins["ran"].clear()
    await _pass(batch_id, from_stage="batch_checks")
    assert stand_ins["ran"] == ORDER
    run = (await _runs(async_session_factory, batch_id))[-1]
    assert run.action == ACTION_ASSIGN
    assert run.summary["fold"] == {"ran": True, "reasons": ["import"]}
    assert run.summary["first_stage"] == "fold"
    fold = _stages(run)["fold"]
    assert fold["status"] == "done"
    assert fold["reasons"] == ["import"]
    assert fold["outcomes"][0]["data"]["samples_folded"] == 2
    assert graded == []
    assert await needs_fold(batch_id) == []
    # A rebuild on its own still grades it.
    await rebuild_batch_ledger(batch_id)
    assert len(graded) == 1


async def test_fold_always_folds_whatever_the_ledger(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    assert await needs_fold(batch_id) == []
    await _pass(batch_id, fold="always")
    run = (await _runs(async_session_factory, batch_id))[-1]
    assert run.summary["fold"] == {"ran": True, "reasons": ["asked"]}
    assert _stages(run)["fold"]["status"] == "done"
    assert run.config["fold"] == "always"


async def test_a_fold_mode_no_pass_knows_is_refused(folded, stand_ins):
    with pytest.raises(Exception, match="folds 'auto' or 'always'"):
        await _pass(folded["batch_id"], fold="never")


# --- progress ----------------------------------------------------------------------


@pytest.fixture
def packets(monkeypatch):
    sent = []

    async def capture(notification, *args, **kwargs):
        sent.append(notification)

    monkeypatch.setattr(batch_stages, "send_progress_user_notification", capture)
    return sent


async def test_a_pass_sends_one_packet_per_stage_start_under_one_process(
    folded, stand_ins, packets
):
    batch_id = folded["batch_id"]
    await _pass(batch_id, process_id="pass-p")
    assert [p.data["stage"] for p in packets] == ORDER
    assert {p.process_id for p in packets} == {"pass-p"}
    assert {p.type for p in packets} == {"assign_batch"}
    assert {p.status for p in packets} == {"pending"}
    assert [p.data["stage_index"] for p in packets] == list(range(len(ORDER)))
    assert {p.data["n_stages"] for p in packets} == {len(ORDER)}
    assert {p.data["stage_progress"] for p in packets} == {0.0}
    assert packets[0].data["phase"] == batch_stages.PHASE_SOURCE
    assert packets[-1].data["phase"] == batch_stages.PHASE_CROSS_CHECK
    # Each stage runs as a child of the pass's process.
    assert {call.parent_id for call in stand_ins["calls"].values()} == {"pass-p"}

    packets.clear()
    await _pass(batch_id, process_id="pass-q", fold="always")
    assert [p.data["stage"] for p in packets] == ["fold"] + ORDER
    assert packets[0].data["phase"] == batch_stages.PHASE_GATHER
    assert {p.data["n_stages"] for p in packets} == {len(ORDER) + 1}


async def test_the_pass_reloads_after_each_rewriting_stage_and_at_its_end(
    folded, stand_ins, assignment_enabled, monkeypatch
):
    batch_id = folded["batch_id"]
    reloads = []

    async def reload(record_type, room):
        reloads.append((record_type, room, list(stand_ins["ran"])))

    async def quiet(*args, **kwargs):
        return None

    monkeypatch.setattr(api_utils, "emit_record_reload", reload)
    monkeypatch.setattr(api_features, "handle_notifications", quiet)
    await _pass(batch_id, independent_transaction=True)
    assert {(r[0], tuple(r[1])) for r in reloads} == {("peak_assignment", (batch_id,))}
    # After each stage that rewrites members, then once at the end.
    assert [r[2][-1] for r in reloads] == REWRITING + [ORDER[-1]]

    reloads.clear()
    stand_ins["ran"].clear()
    await _pass(batch_id, independent_transaction=True, fold="always")
    assert [r[2][-1] if r[2] else "fold" for r in reloads][0] == "fold"
    assert len(reloads) == len(REWRITING) + 2


# --- cancel ------------------------------------------------------------------------


async def test_a_pass_cancelled_between_stages_ends_cancelled_and_frees_the_batch(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)

    async def cancels(call):
        assert (await request_cancel(batch_id)) is not None

    stand_ins["hooks"]["series"] = cancels
    stand_ins["ran"].clear()
    outcome = await _pass(batch_id, from_stage="search")
    assert outcome["status"] == "cancelled"
    assert stand_ins["ran"] == ["search", "isotope_lines", "series"]
    run = (await _runs(async_session_factory, batch_id))[-1]
    assert run.status == "cancelled"
    assert run.is_current == 0
    assert run.cancel_requested_utc is not None
    stages = _stages(run)
    assert [stages[n]["status"] for n in ORDER[2:]] == ["done"] * 3 + ["cancelled"] * 5
    # What it wrote stands, and the stages it did not reach are stale.
    states = await _states(batch_id)
    assert states["series"]["state"] == "current"
    for name in ORDER[5:]:
        assert states[name]["state"] == "stale"
        assert states[name]["stale_reason"] == "upstream:series"
    assert "running" not in {e["state"] for e in states.values()}
    # The next pass starts at once, where this one stopped.
    await check_no_run_in_flight(sample_batch_id=batch_id)
    stand_ins["hooks"].clear()
    stand_ins["ran"].clear()
    await _pass(batch_id)
    assert stand_ins["ran"] == ORDER[5:]


async def test_a_pass_cancelled_inside_the_search_stops_between_samples(
    folded, stand_ins, async_session_factory, monkeypatch, only_these_stages
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    only_these_stages("search")
    searched = []

    async def search_sample(sample_item_id, *args, **kwargs):
        searched.append(sample_item_id)
        await request_cancel(batch_id)
        return [], 0, set()

    monkeypatch.setattr(batch_untargeted, "_search_sample", search_sample)
    outcome = await _pass(batch_id, from_stage="search")
    assert outcome["status"] == "cancelled"
    # Two samples hold a peak nothing assigned: the cancel asked during the
    # first is read before the second.
    assert len(searched) == 1
    run = (await _runs(async_session_factory, batch_id))[-1]
    assert run.status == "cancelled"
    stages = _stages(run)
    assert stages["search"]["status"] == "cancelled"
    assert stages["isotope_lines"]["status"] == "cancelled"
    assert stages["known_species"]["status"] == "not_run"
    states = await _states(batch_id)
    assert states["search"]["state"] == "stale"
    assert states["search"]["stale_reason"] == "interrupted"
    assert "running" not in {e["state"] for e in states.values()}
    await check_no_run_in_flight(sample_batch_id=batch_id)


async def test_a_pass_cancelled_during_its_fold_runs_no_stage(
    folded, stand_ins, async_session_factory, monkeypatch
):
    batch_id = folded["batch_id"]
    clear = batch_peaks_controller.clear_engine_verdicts

    async def cancels_then_clears(sample_batch_id):
        await request_cancel(sample_batch_id)
        await clear(sample_batch_id)

    monkeypatch.setattr(batch_fold, "clear_engine_verdicts", cancels_then_clears)
    outcome = await _pass(batch_id, fold="always")
    assert outcome["status"] == "cancelled"
    assert stand_ins["ran"] == []
    run = (await _runs(async_session_factory, batch_id))[-1]
    assert run.status == "cancelled"
    stages = _stages(run)
    assert stages["fold"]["status"] == "done"
    assert {stages[name]["status"] for name in ORDER} == {"cancelled"}
    await check_no_run_in_flight(sample_batch_id=batch_id)


def _hold(stand_ins, stage):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(call):
        entered.set()
        await release.wait()

    stand_ins["hooks"][stage] = hold
    return entered, release


async def _assert_closed_as_interrupted(async_session_factory, batch_id):
    runs = await _runs(async_session_factory, batch_id)
    assert "running" not in {r.status for r in runs}
    run = runs[-1]
    assert run.action == ACTION_ASSIGN
    assert run.status == "cancelled"
    assert run.error == INTERRUPTED_ERROR
    assert _stages(run)["continuity"]["status"] == "cancelled"
    states = await _states(batch_id)
    assert states["continuity"]["stale_reason"] == "interrupted"
    assert "running" not in {e["state"] for e in states.values()}
    await check_no_run_in_flight(sample_batch_id=batch_id)


async def test_a_task_the_event_loop_cancels_leaves_no_run_running(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    entered, _ = _hold(stand_ins, "continuity")
    task = asyncio.create_task(_pass(batch_id))
    await asyncio.wait_for(entered.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_closed_as_interrupted(async_session_factory, batch_id)


async def test_a_pass_whose_scope_is_cancelled_again_and_again_closes_its_run(
    folded, stand_ins, async_session_factory
):
    """A background task runs inside the app's anyio task group, whose
    cancellation is level-triggered: every await after it raises again. The
    run's closing write is shielded, and goes on in a task of its own."""
    batch_id = folded["batch_id"]
    entered, _ = _hold(stand_ins, "continuity")
    before = asyncio.all_tasks()
    async with anyio.create_task_group() as group:
        group.start_soon(_pass, batch_id)
        await asyncio.wait_for(entered.wait(), 30)
        group.cancel_scope.cancel()
    # The shielded writes run on in tasks of their own.
    await asyncio.gather(*(asyncio.all_tasks() - before - {asyncio.current_task()}))
    await _assert_closed_as_interrupted(async_session_factory, batch_id)


# --- settings ----------------------------------------------------------------------


async def test_a_passs_own_settings_are_not_what_the_next_pass_reads(
    folded, stand_ins, async_session_factory
):
    """Settings one pass was given are recorded on its stages, and the next
    pass given none runs on the batch's own, not on them (MAS-1014): the
    batch saved none here, so the defaults."""
    batch_id = folded["batch_id"]
    await _pass(batch_id, config=PeakAssignmentConfig(mz_precision_ppm=1.5))
    run = (await _runs(async_session_factory, batch_id))[-1]
    assert run.config["settings"]["mz_precision_ppm"] == 1.5
    assert _stages(run)["search"]["settings"]["mz_precision_ppm"] == 1.5
    assert _stages(run)["co_variation"].get("settings") is None
    await fold_sample_into_batch_peaks(folded["s1"])
    await _pass(batch_id)
    assert stand_ins["calls"]["search"].config == PeakAssignmentConfig()
    assert stand_ins["calls"]["series"].config == PeakAssignmentConfig()


# --- routes ------------------------------------------------------------------------


async def test_the_assign_route_starts_a_pass(
    editor_client,
    guest_client,
    folded,
    assignment_enabled,
    async_session_factory,
    monkeypatch,
):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
    batch_id = folded["batch_id"]
    url = f"/api/batch-peaks/batch/{batch_id}/assign"
    assert (await guest_client.post(url)).status_code == 403
    response = await editor_client.post(url)
    assert response.status_code == 202, response.text
    # The stand-in task closes nothing: the run the route opened stays
    # running until it is closed here.
    await fail_run(batch_id, launched[0]["batch_peak_run_id"], "stood in")
    response = await editor_client.post(
        url,
        json={
            "from_stage": "series",
            "fold": "always",
            "config": {"mz_precision_ppm": 2.5},
        },
    )
    assert response.status_code == 202, response.text
    assert [k["from_stage"] for k in launched] == [None, "series"]
    assert [k["fold"] for k in launched] == ["auto", "always"]
    assert {k["action"] for k in launched} == {ACTION_ASSIGN}
    # It answers with the run it opened and handed the pass, so a client can
    # wait on it.
    [answered] = response.json()["data"]
    assert answered["batch_peak_run_id"] == launched[1]["batch_peak_run_id"]
    assert answered["status"] == "running"
    assert answered["action"] == ACTION_ASSIGN
    run = (await _runs(async_session_factory, batch_id))[-1]
    assert run.batch_peak_run_id == answered["batch_peak_run_id"]
    assert run.status == "running"
    assert run.config["from_stage"] == "series"
    assert run.config["fold"] == "always"
    assert run.config["settings"]["mz_precision_ppm"] == 2.5
    assert launched[1]["config"].mz_precision_ppm == 2.5
    assert launched[0]["independent_transaction"] is True
    await fail_run(batch_id, run.batch_peak_run_id, "stood in")
    for refused in ({"fold": "never"}, {"from_stage": "everything"}):
        response = await editor_client.post(url, json=refused)
        assert response.status_code == 422, response.text
    assert len(launched) == 2


async def test_a_second_press_of_the_assign_route_is_refused_on_the_request(
    editor_client, folded, assignment_enabled, async_session_factory, monkeypatch
):
    """Two presses before the first pass's task starts: the second is
    refused with a 409 itself, and the run the first answered with is on the
    batch's runs for a client to wait on."""
    batch_id = folded["batch_id"]
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
    url = f"/api/batch-peaks/batch/{batch_id}/assign"
    first = await editor_client.post(url, json={"fold": "always"})
    assert first.status_code == 202, first.text
    second = await editor_client.post(url, json={"fold": "always"})
    assert second.status_code == 409, second.text
    assert RUN_IN_FLIGHT_CODE in second.text
    assert len(launched) == 1
    [answered] = first.json()["data"]
    runs = await editor_client.get(f"/api/batch-peaks/batch/{batch_id}/runs")
    assert runs.status_code == 200, runs.text
    listed = {r["batch_peak_run_id"]: r["status"] for r in runs.json()["data"]}
    assert listed[answered["batch_peak_run_id"]] == "running"
    await fail_run(batch_id, answered["batch_peak_run_id"], "stood in")


async def test_the_assign_route_runs_the_pass_in_the_run_it_answered_with(
    editor_client, folded, stand_ins, assignment_enabled, async_session_factory
):
    """The real task behind the route: it runs and closes the run the route
    opened, and opens none of its own."""
    batch_id = folded["batch_id"]
    before = {r.batch_peak_run_id for r in await _runs(async_session_factory, batch_id)}
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/assign", json={"fold": "always"}
    )
    assert response.status_code == 202, response.text
    [answered] = response.json()["data"]
    runs = await _runs(async_session_factory, batch_id)
    new = [r for r in runs if r.batch_peak_run_id not in before]
    assert [r.batch_peak_run_id for r in new] == [answered["batch_peak_run_id"]]
    assert new[0].status == "completed"
    assert stand_ins["ran"] == ORDER


async def test_the_assign_route_answers_nothing_to_run_without_a_pass(
    editor_client, folded, stand_ins, assignment_enabled, monkeypatch
):
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
    url = f"/api/batch-peaks/batch/{batch_id}/assign"
    response = await editor_client.post(url)
    assert response.status_code == 202, response.text
    assert response.json()["data"] == []
    assert "nothing to run" in response.json()["message"]
    assert launched == []
    # Asked to fold, it has something to do.
    response = await editor_client.post(url, json={"fold": "always"})
    assert len(launched) == 1


async def test_a_pass_handed_its_run_closes_it_whatever_there_is_to_do(
    folded, stand_ins, async_session_factory
):
    """The route opened the run: the pass runs it and closes it, so a client
    waiting on it sees it end, even where nothing was left to do by then."""
    batch_id = folded["batch_id"]
    await _pass(batch_id)
    run_id = await start_run(batch_id, ACTION_ASSIGN)
    before = len(await _runs(async_session_factory, batch_id))
    outcome = await _pass(batch_id, batch_peak_run_id=run_id)
    assert outcome["status"] == "skipped"
    runs = await _runs(async_session_factory, batch_id)
    assert len(runs) == before
    run = next(r for r in runs if r.batch_peak_run_id == run_id)
    assert run.status == "completed"
    assert {e["status"] for e in run.summary["stages"]} == {"not_run"}


async def test_a_pass_refused_its_arguments_closes_the_run_it_was_handed(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    run_id = await start_run(batch_id, ACTION_ASSIGN)
    with pytest.raises(Exception, match="folds 'auto' or 'always'"):
        await _pass(batch_id, fold="never", batch_peak_run_id=run_id)
    run = next(
        r
        for r in await _runs(async_session_factory, batch_id)
        if r.batch_peak_run_id == run_id
    )
    assert run.status == "failed"
    assert "folds 'auto' or 'always'" in run.error
    assert stand_ins["ran"] == []
    await check_no_run_in_flight(sample_batch_id=batch_id)


async def test_the_cancel_route_asks_the_running_pass_to_stop(
    editor_client, guest_client, folded, stand_ins, assignment_enabled
):
    batch_id = folded["batch_id"]
    url = f"/api/batch-peaks/batch/{batch_id}/assign/cancel"
    # No pass runs: nothing to stop.
    assert (await editor_client.post(url)).status_code == 404
    entered, release = _hold(stand_ins, "search")
    task = asyncio.create_task(_pass(batch_id))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        assert (await guest_client.post(url)).status_code == 403
        response = await editor_client.post(url)
        assert response.status_code == 200, response.text
        run = response.json()["data"][0]
        assert run["status"] == "running"
        assert run["cancel_requested_utc"] is not None
    finally:
        release.set()
        outcome = await task
    assert outcome["status"] == "cancelled"
    assert "series" not in stand_ins["ran"]


async def test_a_cancel_never_reaches_a_rebuild(folded, async_session_factory):
    batch_id = folded["batch_id"]
    run_id = await start_run(batch_id, "rebuild")
    assert await request_cancel(batch_id) is None
    await complete_run(batch_id, run_id)
