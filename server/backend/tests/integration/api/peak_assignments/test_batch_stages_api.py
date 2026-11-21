"""Integration tests for the batch stages' standing and the pass that runs them.

Two samples in a batch of their own, folded. The stages' own work is stood in
for here (each stage's suite covers it); what is proven is the order and the
staleness rule: a pass runs from the stage it is started at to the end, or
from the first stage that is not current; a stage that finishes is current
and leaves every stage after it stale; a fold, also one that lands while a
stage runs, leaves every stage that ran stale; a stage that raises, is
cancelled or leaves samples unread is stale and the records it wrote carry
its version; each step's route starts the pass at its own stage; the ledger
says where every stage stands; a server restart leaves a running stage stale.
And one pass at a time: while a pass runs, every step's route and a second
pass are refused, also between two stages; a stage whose end fails is
stopped, not left running; every writer of the stage rows takes the batch's
fold lock first; a pass runs every stage on the batch's own settings, the
defaults where it saved none, unless a step's route was given some, and a
pass on other settings than the last pass's runs from the first stage. See
``batch_stages.py``.
"""

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments import (
    batch_peaks_routes,
    batch_rejudge,
    batch_stages,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _acquire_batch_fold_lock,
    fold_sample_into_batch_peaks,
    rebuild_batch_ledger,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SERIES,
    RUN_IN_FLIGHT_CODE,
    complete_run,
    fail_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_stages import (
    STAGES,
    BatchStage,
    assign_batch,
    begin_stage,
    finish_stage,
    hand_over,
    mark_batch_stale,
    running_stage,
    stage_standing,
    stop_stage,
)
from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    settings_hash,
)
from mascope_backend.db import (
    BatchPeak,
    BatchPeakRun,
    BatchPeakStage,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.admin.peak_assignments.reset_running_runs import (
    reset_running_batch_peak_runs,
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
}


async def _seed_sample(session, batch_id, name, rows, now):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-batch-stages-{name}-{sample_file_id}.zarr",
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
            sample_item_name=f"Batch stages {name}",
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
                sample_batch_name=f"Batch stages {batch_id}",
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
def stand_ins(monkeypatch):
    """Every stage's work stood in for by a recorder: what ran, in order, and
    a hook per stage that runs inside it."""
    ran: list[str] = []
    hooks: dict[str, object] = {}

    def make(spec):
        async def run(call):
            ran.append(spec.stage.value)
            hook = hooks.get(spec.stage.value)
            if hook is not None:
                outcome = await hook(call)
                if outcome is not None:
                    return [outcome]
            return [{"status": "success", "message": f"{spec.label} ran."}]

        return dataclasses.replace(spec, run=run)

    monkeypatch.setattr(
        batch_stages,
        "STAGE_BY_NAME",
        {spec.stage: make(spec) for spec in STAGES},
    )
    return {"ran": ran, "hooks": hooks}


async def _states(batch_id) -> dict[str, dict]:
    standing = await stage_standing(batch_id)
    return {entry["stage"]: entry for entry in standing["stages"]}


async def _ran(batch_id, stages):
    """Record stages as run and finished, as a pass would, without the pass:
    the stages before them stay as they were."""
    for stage in stages:
        _, started = await begin_stage(batch_id, BatchStage(stage))
        await finish_stage(batch_id, BatchStage(stage), started, run_id=None)


async def _run(batch_id, from_stage=None):
    return await assign_batch(
        sample_batch_id=batch_id, from_stage=from_stage, process_id="pass-1"
    )


async def test_a_stage_that_never_ran_is_not_run_whatever_folds(folded):
    await fold_sample_into_batch_peaks(folded["s1"])
    states = await _states(folded["batch_id"])
    assert {e["state"] for e in states.values()} == {"not_run"}
    assert (await stage_standing(folded["batch_id"]))["stale_stages"] == []


async def test_a_pass_runs_from_its_stage_to_the_end(folded, stand_ins):
    await _run(folded["batch_id"])
    stand_ins["ran"].clear()
    outcome = await _run(folded["batch_id"], "series")
    assert stand_ins["ran"] == ORDER[4:]
    assert outcome["status"] == "success"
    states = await _states(folded["batch_id"])
    for name in ORDER[4:]:
        assert states[name]["state"] == "current"
        assert states[name]["version"] == 2
    for name in ORDER[:4]:
        assert states[name]["state"] == "current"
        assert states[name]["version"] == 1


async def test_a_pass_asked_past_a_stage_not_current_starts_there(folded, stand_ins):
    """A step pressed while an earlier stage has not run, or is stale, starts
    the pass at that stage: no stage runs on what an earlier one has not
    settled, so the order the steps are pressed in leaves no mark on the
    ledger (MAS-983's Verify)."""
    batch_id = folded["batch_id"]
    outcome = await _run(batch_id, "series")
    assert stand_ins["ran"] == ORDER
    assert "Detect series was asked for" in outcome["message"]
    await fold_sample_into_batch_peaks(folded["s2"])
    stand_ins["ran"].clear()
    await _run(batch_id, "co_variation")
    assert stand_ins["ran"] == ORDER


async def test_a_pass_with_no_stage_named_starts_at_the_first_not_current(
    folded, stand_ins
):
    batch_id = folded["batch_id"]
    await _run(batch_id)
    assert stand_ins["ran"] == ORDER
    # Every stage current: a pass runs nothing.
    stand_ins["ran"].clear()
    outcome = await _run(batch_id)
    assert stand_ins["ran"] == []
    assert outcome["status"] == "skipped"
    # A stage run again leaves the ones after it stale, and the next pass
    # starts at the first of them.
    await begin_stage(batch_id, BatchStage.CO_VARIATION)
    await finish_stage(
        batch_id, BatchStage.CO_VARIATION, datetime.now(timezone.utc), run_id=None
    )
    states = await _states(batch_id)
    assert states["co_variation"]["state"] == "current"
    assert states["continuity"]["state"] == "stale"
    assert states["continuity"]["stale_reason"] == "upstream:co_variation"
    assert states["series"]["state"] == "current"
    await _run(batch_id)
    assert stand_ins["ran"] == ORDER[6:]


async def test_a_fold_leaves_every_stage_that_ran_stale(folded, stand_ins):
    batch_id = folded["batch_id"]
    await _ran(batch_id, ORDER[5:])
    await fold_sample_into_batch_peaks(folded["s2"])
    standing = await stage_standing(batch_id)
    states = {e["stage"]: e for e in standing["stages"]}
    assert standing["stale_stages"] == ORDER[5:]
    for name in ORDER[5:]:
        assert states[name]["stale_reason"] == "fold"
    for name in ORDER[:5]:
        assert states[name]["state"] == "not_run"
    # The pass starts again at the first stage the fold left stale, and the
    # stages that never ran with it.
    await _run(batch_id)
    assert stand_ins["ran"] == ORDER


async def test_a_fold_while_a_stage_runs_leaves_it_stale(folded, stand_ins):
    batch_id = folded["batch_id"]

    async def folds_meanwhile(call):
        await fold_sample_into_batch_peaks(folded["s1"])
        # Still running: the fold's mark is what its end reads.
        assert (await _states(batch_id))["co_variation"]["state"] == "running"

    stand_ins["hooks"]["co_variation"] = folds_meanwhile
    outcome = await _run(batch_id, "co_variation")
    states = await _states(batch_id)
    assert states["co_variation"]["state"] == "stale"
    assert states["co_variation"]["stale_reason"] == "fold"
    # The stages after it ran on the folded ledger and are current.
    for name in ORDER[6:]:
        assert states[name]["state"] == "current"
    assert outcome["status"] == "partial"
    assert "Still stale: Read co-variation" in outcome["message"]


async def test_a_stage_that_raises_stops_the_pass_and_is_stale(folded, stand_ins):
    batch_id = folded["batch_id"]
    await _run(batch_id)

    async def raises(call):
        raise RuntimeError("the store went away")

    stand_ins["hooks"]["series"] = raises
    stand_ins["ran"].clear()
    with pytest.raises(Exception):  # the pass re-raises it, wrapped
        await _run(batch_id, "search")
    assert stand_ins["ran"] == ["search", "isotope_lines", "series"]
    states = await _states(batch_id)
    assert states["search"]["state"] == "current"
    assert states["series"]["state"] == "stale"
    assert states["series"]["stale_reason"] == "interrupted"
    assert states["co_variation"]["stale_reason"] == "upstream:series"
    assert states["series"]["version"] == 2


async def test_a_cancelled_stage_is_stale(folded, stand_ins):
    batch_id = folded["batch_id"]

    async def cancelled(call):
        raise asyncio.CancelledError()

    stand_ins["hooks"]["known_species"] = cancelled
    with pytest.raises(asyncio.CancelledError):
        await _run(batch_id)
    states = await _states(batch_id)
    assert states["water_ladders"]["state"] == "current"
    assert states["known_species"]["state"] == "stale"
    assert states["known_species"]["stale_reason"] == "interrupted"
    assert states["search"]["state"] == "not_run"


async def test_a_stage_that_left_samples_unread_is_stale_and_the_pass_goes_on(
    folded, stand_ins
):
    batch_id = folded["batch_id"]

    async def skips_one(call):
        return {
            "status": "partial",
            "message": "1 skipped.",
            "data": {"samples_failed": 1},
        }

    stand_ins["hooks"]["search"] = skips_one
    await _run(batch_id)
    states = await _states(batch_id)
    assert states["search"]["state"] == "stale"
    assert states["search"]["stale_reason"] == "partial"
    assert states["series"]["state"] == "current"
    stand_ins["ran"].clear()
    stand_ins["hooks"].clear()
    await _run(batch_id)
    assert stand_ins["ran"] == ORDER[2:]


async def test_the_records_a_stage_wrote_carry_its_version(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]

    async def writes_a_record(call):
        # Into the pass's run, as every step of a pass writes.
        run_id = call.batch_peak_run_id
        assert run_id is not None
        async with async_session_factory() as session:
            # The glucose anchor, named: an anchor's update moves its row, so
            # the first of the batch's anchors is not the same one each pass.
            anchor = (
                await session.execute(
                    select(BatchPeak).where(
                        BatchPeak.sample_batch_id == batch_id,
                        BatchPeak.consensus_formula == "C6H12O6",
                    )
                )
            ).scalar_one()
            anchor.provenance = {
                **(anchor.provenance or {}),
                "series": {"formula": "C6H12O6", "batch_peak_run_id": run_id},
                # Another stage's key, of another run: no stage of this pass
                # wrote it, so none stamps it.
                "co_variation": {
                    "formula": "C6H12O6",
                    "batch_peak_run_id": "another-run",
                },
            }
            await session.commit()

    stand_ins["hooks"]["series"] = writes_a_record

    async def record_of(key):
        async with async_session_factory() as session:
            anchors = (
                (
                    await session.execute(
                        select(BatchPeak).where(BatchPeak.sample_batch_id == batch_id)
                    )
                )
                .scalars()
                .all()
            )
        return [a.provenance[key] for a in anchors if (a.provenance or {}).get(key)]

    await _run(batch_id, "series")
    assert [r["version"] for r in await record_of("series")] == [1]
    assert ["version" in r for r in await record_of("co_variation")] == [False]
    await _run(batch_id, "series")
    assert [r["version"] for r in await record_of("series")] == [2]
    states = await _states(batch_id)
    assert states["series"]["version"] == 2


async def _put_series(async_session_factory, batch_id, formula):
    """A series record naming ``formula`` on the batch's glucose anchor."""
    async with async_session_factory() as session:
        anchor = (
            await session.execute(
                select(BatchPeak).where(
                    BatchPeak.sample_batch_id == batch_id,
                    BatchPeak.consensus_formula == "C6H12O6",
                )
            )
        ).scalar_one()
        anchor.provenance = {
            **(anchor.provenance or {}),
            "series": {"formula": formula, "batch_peak_run_id": "run-x"},
        }
        await session.commit()
        return anchor.batch_peak_id


async def test_a_record_a_recompute_leaves_behind_marks_its_stage_stale(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _run(batch_id)
    anchor_id = await _put_series(async_session_factory, batch_id, "C7H14O6")
    await recompute_batch_consensus(batch_id, {anchor_id})
    states = await _states(batch_id)
    assert states["series"]["state"] == "stale"
    assert states["series"]["stale_reason"] == "consensus"
    # Only the stage that wrote the record: the others read nothing that moved.
    assert states["co_variation"]["state"] == "current"
    async with async_session_factory() as session:
        anchor = await session.get(BatchPeak, anchor_id)
    assert anchor.provenance["series"]["stale"] is True
    # A recompute that leaves the record as it was marks nothing again.
    await finish_stage(
        batch_id, BatchStage.SERIES, datetime.now(timezone.utc), run_id=None
    )
    await recompute_batch_consensus(batch_id, {anchor_id})
    assert (await _states(batch_id))["series"]["state"] == "current"


async def test_a_running_stage_is_left_to_its_own_end_by_a_recompute(
    folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]

    async def leaves_a_record_behind(call):
        anchor_id = await _put_series(async_session_factory, batch_id, "C7H14O6")
        await recompute_batch_consensus(batch_id, {anchor_id})

    stand_ins["hooks"]["series"] = leaves_a_record_behind
    await _run(batch_id, "series")
    assert (await _states(batch_id))["series"]["state"] == "current"


async def test_every_stale_record_outlives_the_next_recompute(
    folded, async_session_factory
):
    """Each record a recompute marked stale is read back with its mark by the
    next one, so the mark, and the record, outlive it."""
    batch_id = folded["batch_id"]
    records = {
        "series": {"formula": "X"},
        "co_variation": {"formula": "X"},
        "diagnostic_isotopologues": {"formula": "X"},
        "merged_isotopologue_of": {"batch_peak_id": "elsewhere"},
        "merged_isotopologues": {"formula": "X"},
        "batch_checks": {"ion_formula": "X-"},
        "known_species": {"verdict": "present", "reads": {"role": "reagent"}},
        "water_ladder": {"rung": {}, "reads": {"role": "reagent"}},
        "flat_series": {"verdict": "flat", "formula": "X"},
    }
    async with async_session_factory() as session:
        anchor = (
            await session.execute(
                select(BatchPeak).where(
                    BatchPeak.sample_batch_id == batch_id,
                    BatchPeak.consensus_formula == "C6H12O6",
                )
            )
        ).scalar_one()
        anchor.provenance = {**(anchor.provenance or {}), **records}
        anchor_id = anchor.batch_peak_id
        await session.commit()
    for _ in range(2):
        await recompute_batch_consensus(batch_id, {anchor_id})
        async with async_session_factory() as session:
            provenance = (await session.get(BatchPeak, anchor_id)).provenance
        assert {k for k in records if (provenance.get(k) or {}).get("stale")} == set(
            records
        )


async def test_a_rebuild_leaves_every_stage_stale_and_says_so(folded, stand_ins):
    batch_id = folded["batch_id"]
    await _run(batch_id, "batch_checks")
    await rebuild_batch_ledger(batch_id)
    states = await _states(batch_id)
    assert states["batch_checks"]["stale_reason"] == "rebuild"
    assert states["blends"]["stale_reason"] == "rebuild"


async def test_the_ledger_serves_where_the_stages_stand(
    guest_client, folded, stand_ins, async_session_factory
):
    batch_id = folded["batch_id"]
    await _ran(batch_id, ["batch_checks", "blends"])
    await fold_sample_into_batch_peaks(folded["s1"])
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}", params={"min_n_present": 1}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [e["stage"] for e in body["stages"]] == ORDER
    assert body["stale_stages"] == ["batch_checks", "blends"]
    by = {e["stage"]: e for e in body["stages"]}
    assert by["search"]["state"] == "not_run"
    assert by["blends"]["stale_reason"] == "fold"
    # An earlier run's ledger says where the stages stand now, too.
    async with async_session_factory() as session:
        fold_run = (
            await session.execute(
                select(BatchPeakRun.batch_peak_run_id).where(
                    BatchPeakRun.sample_batch_id == batch_id
                )
            )
        ).scalar_one()
    await complete_run(batch_id, await start_run(batch_id, ACTION_SERIES))
    earlier = await guest_client.get(
        f"/api/batch-peaks/batch/{batch_id}",
        params={"min_n_present": 1, "batch_peak_run_id": fold_run},
    )
    assert earlier.status_code == 200, earlier.text
    assert earlier.json()["stale_stages"] == ["batch_checks", "blends"]


@pytest.fixture
def assignment_enabled(monkeypatch):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


#: Each step's route, the stage it starts the pass at, and the action its pass
#: is recorded under: the step's own, which its run always had (the ratio
#: stability made none, so its pass is an ``assign``).
ROUTES = [
    ("search-untargeted", "search", "search_untargeted"),
    ("detect-series", "series", "detect_series"),
    ("read-co-variation", "co_variation", "read_co_variation"),
    ("recompute-continuity", "continuity", "assign"),
    ("stamp-isotopologues", "isotope_lines", "stamp_isotopologues"),
    ("strip-isotopologues", "isotope_lines", "strip_isotopologues"),
    ("read-batch-checks", "batch_checks", "read_batch_checks"),
    ("decide-known-species", "known_species", "decide_known_species"),
    ("measure-water-ladders", "water_ladders", "measure_water_ladders"),
]


@pytest.mark.parametrize(("path", "stage", "action"), ROUTES)
async def test_each_steps_route_starts_the_stages_at_its_own(
    editor_client, folded, assignment_enabled, monkeypatch, path, stage, action
):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{folded['batch_id']}/{path}"
    )
    assert response.status_code == 202, response.text
    assert [k["from_stage"] for k in launched] == [stage]
    assert launched[0]["action"] == action
    assert launched[0]["independent_transaction"] is True
    # No settings given: the pass reads the batch's own.
    assert launched[0].get("config") is None


@pytest.mark.parametrize("path", ["search-untargeted", "detect-series"])
async def test_a_steps_route_hands_the_settings_it_was_given_to_the_pass(
    editor_client, folded, assignment_enabled, monkeypatch, path
):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
    url = f"/api/batch-peaks/batch/{folded['batch_id']}/{path}"
    response = await editor_client.post(url, json={"config": {"mz_precision_ppm": 2.5}})
    assert response.status_code == 202, response.text
    assert launched[0]["config"].mz_precision_ppm == 2.5
    decoy = await editor_client.post(
        url, json={"config": {"decoy": {"mz_shift_ppm": 9}}}
    )
    assert decoy.status_code == 422, decoy.text
    assert len(launched) == 1


async def test_the_pass_route_starts_where_it_is_asked(
    editor_client,
    guest_client,
    folded,
    assignment_enabled,
    monkeypatch,
    async_session_factory,
):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
    url = f"/api/batch-peaks/batch/{folded['batch_id']}/run-stages"
    assert (await guest_client.post(url)).status_code == 403
    response = await editor_client.post(url)
    assert response.status_code == 202, response.text
    # The route opened the pass's run; the stand-in task closes nothing.
    await fail_run(folded["batch_id"], launched[0]["batch_peak_run_id"], "stood in")
    response = await editor_client.post(
        url, json={"from_stage": "series", "config": {"mz_precision_ppm": 2.5}}
    )
    assert response.status_code == 202, response.text
    await fail_run(folded["batch_id"], launched[1]["batch_peak_run_id"], "stood in")
    assert [k["from_stage"] for k in launched] == [None, "series"]
    # The route reads the batch's settings, records them on the run it
    # opens and hands them on; settings in the request override them.
    assert launched[0]["config"] == PeakAssignmentConfig()
    assert launched[0]["settings_source"] == "batch"
    assert launched[1]["config"].mz_precision_ppm == 2.5
    assert launched[1]["settings_source"] == "request"
    async with async_session_factory() as session:
        configs = [
            (await session.get(BatchPeakRun, k["batch_peak_run_id"])).config
            for k in launched
        ]
    assert [c["settings_source"] for c in configs] == ["batch", "request"]
    assert configs[1]["settings"]["mz_precision_ppm"] == 2.5
    refused = await editor_client.post(url, json={"from_stage": "everything"})
    assert refused.status_code == 422, refused.text
    decoy = await editor_client.post(
        url, json={"config": {"decoy": {"mz_shift_ppm": 9}}}
    )
    assert decoy.status_code == 422, decoy.text
    assert len(launched) == 2


async def test_a_server_restart_leaves_a_running_stage_stale(
    folded, async_session_factory
):
    batch_id = folded["batch_id"]
    await begin_stage(batch_id, BatchStage.SEARCH)
    await reset_running_batch_peak_runs()
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(BatchPeakStage).where(
                    BatchPeakStage.sample_batch_id == batch_id,
                    BatchPeakStage.stage == "search",
                )
            )
        ).scalar_one()
    assert row.state == "stale"
    assert row.stale_reason == "interrupted"


# --- one pass at a time ------------------------------------------------------------


def _hold(stand_ins, stage):
    """Hold a pass inside ``stage`` until released."""
    entered, release = asyncio.Event(), asyncio.Event()

    async def hold(call):
        entered.set()
        await release.wait()

    stand_ins["hooks"][stage] = hold
    return entered, release


REFUSED_WHILE_A_PASS_RUNS = [path for path, _, _ in ROUTES] + [
    "run-stages",
    "assign",
    "backfill",
]


@pytest.mark.parametrize("path", REFUSED_WHILE_A_PASS_RUNS)
async def test_every_steps_route_is_refused_while_a_pass_runs(
    editor_client, folded, assignment_enabled, stand_ins, monkeypatch, path
):
    """The ratio stability opens no batch run, so only the stage's row says a
    pass runs: each step's route, the pass's own and a rebuild refuse on it,
    naming the stage, and launch nothing."""
    batch_id = folded["batch_id"]
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    entered, release = _hold(stand_ins, "continuity")
    first = asyncio.create_task(_run(batch_id))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        monkeypatch.setattr(batch_peaks_routes, "assign_batch", fake_task)
        monkeypatch.setattr(batch_peaks_routes, "compute_batch_peaks", fake_task)
        url = f"/api/batch-peaks/batch/{batch_id}/{path}"
        refused = await editor_client.post(url)
        assert refused.status_code == 409, refused.text
        assert RUN_IN_FLIGHT_CODE in refused.text
        assert "Ratio stability" in refused.text
        assert launched == []
    finally:
        release.set()
        await first
    # Every stage is current after the pass: the whole pass's routes are
    # asked to start somewhere, or they answer that nothing is to run.
    body = {"from_stage": "blends"} if path in ("run-stages", "assign") else None
    admitted = await editor_client.post(url, json=body)
    assert admitted.status_code == 202, admitted.text
    assert len(launched) == 1


async def test_a_second_pass_past_the_routes_refusal_is_refused_at_its_start(
    folded, stand_ins
):
    """A press that reached the route before the first pass opened its run is
    refused when its own run would open, and changes nothing."""
    batch_id = folded["batch_id"]
    entered, release = _hold(stand_ins, "continuity")
    first = asyncio.create_task(_run(batch_id))
    try:
        await asyncio.wait_for(entered.wait(), 30)
        before = await _states(batch_id)
        with pytest.raises(Exception, match="still rewriting this batch's ledger"):
            # Bounded: a second pass let through would plan from the held
            # stage and wait on the same hold.
            await asyncio.wait_for(_run(batch_id, "batch_checks"), 10)
        assert await _states(batch_id) == before
        assert "batch_checks" not in stand_ins["ran"]
    finally:
        release.set()
        await first
    states = await _states(batch_id)
    assert {e["state"] for e in states.values()} == {"current"}
    assert stand_ins["ran"] == ORDER


async def test_between_two_stages_of_a_pass_the_next_one_already_runs(
    folded, stand_ins, monkeypatch, async_session_factory
):
    """Each stage of a pass begins as the one before it ends, in one
    transaction: every write to the stage rows after the first stage's start
    finds one of them running, as a second press reading them would, so no
    moment of the pass admits a second one. Read before each writer takes
    the fold lock, which every writer of the rows takes first."""
    batch_id = folded["batch_id"]
    seen = []
    lock = batch_stages._fold_lock

    async def probing(session, sample_batch_id):
        async with async_session_factory() as other:
            running = await running_stage(other, sample_batch_id)
        seen.append(running.stage if running is not None else None)
        await lock(session, sample_batch_id)

    monkeypatch.setattr(batch_stages, "_fold_lock", probing)
    await _run(batch_id)
    # The first stage's start finds none running; every stage's end finds
    # it running.
    assert seen == [None] + ORDER
    states = await _states(batch_id)
    assert {e["state"] for e in states.values()} == {"current"}
    assert {e["version"] for e in states.values()} == {1}


async def test_a_stage_whose_end_fails_is_stopped_not_left_running(
    folded, stand_ins, monkeypatch
):
    batch_id = folded["batch_id"]

    async def fails_at_the_series(sample_batch_id, stage, *args, **kwargs):
        if stage == BatchStage.SERIES:
            raise RuntimeError("deadlock detected")
        return await hand_over(sample_batch_id, stage, *args, **kwargs)

    with pytest.raises(Exception):  # the pass re-raises it, wrapped
        monkeypatch.setattr(batch_stages, "hand_over", fails_at_the_series)
        await _run(batch_id)
    states = await _states(batch_id)
    assert states["series"]["state"] == "stale"
    assert states["series"]["stale_reason"] == "interrupted"
    assert states["isotope_lines"]["state"] == "current"
    # The stage after it never began.
    assert states["co_variation"]["state"] == "not_run"
    assert "running" not in {e["state"] for e in states.values()}

    # On a batch every stage has run on: the stage after it did not begin,
    # so it is stale for the series' sake, not stopped.
    monkeypatch.setattr(batch_stages, "hand_over", hand_over)
    await _run(batch_id)
    monkeypatch.setattr(batch_stages, "hand_over", fails_at_the_series)
    with pytest.raises(Exception):  # the pass re-raises it, wrapped
        await _run(batch_id, "series")
    states = await _states(batch_id)
    assert states["series"]["stale_reason"] == "interrupted"
    assert states["co_variation"]["state"] == "stale"
    assert states["co_variation"]["stale_reason"] == "upstream:series"
    assert "running" not in {e["state"] for e in states.values()}


async def test_a_pass_interrupted_right_after_a_hand_over_leaves_no_stage_running(
    folded, stand_ins, monkeypatch
):
    """The hand-over committed, beginning the next stage, and the pass was
    cancelled before it went on: the stage it began is stopped too."""
    batch_id = folded["batch_id"]

    async def cancelled_after_the_series(sample_batch_id, stage, *args, **kwargs):
        ended = await hand_over(sample_batch_id, stage, *args, **kwargs)
        if stage == BatchStage.SERIES:
            raise asyncio.CancelledError()
        return ended

    monkeypatch.setattr(batch_stages, "hand_over", cancelled_after_the_series)
    with pytest.raises(asyncio.CancelledError):
        await _run(batch_id)
    states = await _states(batch_id)
    assert states["co_variation"]["state"] == "stale"
    assert states["co_variation"]["stale_reason"] == "interrupted"
    assert "running" not in {e["state"] for e in states.values()}
    assert "co_variation" not in stand_ins["ran"]


WRITERS = ["begin", "hand_over", "finish", "stop", "mark_batch_stale"]


@pytest.mark.parametrize("writer", WRITERS)
async def test_every_writer_of_the_stage_rows_waits_on_the_fold_lock(
    folded, async_session_factory, writer
):
    """A fold marks the stage rows under the batch's fold lock. A writer of
    the rows that locked them first could lock them in another order than
    the fold does, and one of the two would be aborted as a deadlock; each
    takes the fold lock first instead, so it waits while a fold holds it."""
    batch_id = folded["batch_id"]
    _, started = await begin_stage(batch_id, BatchStage.SERIES)
    if writer == "begin":
        await finish_stage(batch_id, BatchStage.SERIES, started, run_id=None)
    calls = {
        "begin": lambda: begin_stage(batch_id, BatchStage.SERIES),
        "hand_over": lambda: hand_over(
            batch_id,
            BatchStage.SERIES,
            started,
            run_id=None,
            then=BatchStage.CO_VARIATION,
        ),
        "finish": lambda: finish_stage(
            batch_id, BatchStage.SERIES, started, run_id=None
        ),
        "stop": lambda: stop_stage(batch_id, BatchStage.SERIES),
        "mark_batch_stale": lambda: mark_batch_stale(batch_id, "rebuild"),
    }
    async with async_session_factory() as holder:
        await _acquire_batch_fold_lock(holder, batch_id)
        task = asyncio.create_task(calls[writer]())
        await asyncio.sleep(0.5)
        assert not task.done()
        await holder.rollback()
    await asyncio.wait_for(task, 10)


async def test_a_rebuild_marks_the_stages_under_the_fold_lock(
    folded, stand_ins, monkeypatch
):
    """The rebuild's own mark goes through the locked writer."""
    batch_id = folded["batch_id"]
    marks = []
    real = batch_stages.mark_batch_stale

    async def recording(sample_batch_id, reason):
        marks.append(reason)
        await real(sample_batch_id, reason)

    from mascope_backend.api.new.peak_assignments import batch_peaks_controller

    monkeypatch.setattr(batch_peaks_controller, "mark_batch_stale", recording)
    await _run(batch_id, "batch_checks")
    await rebuild_batch_ledger(batch_id)
    assert marks == ["rebuild"]


# --- settings ----------------------------------------------------------------------


async def _save_settings(async_session_factory, batch_id, stored):
    """The batch's assignment settings saved as the batch's PATCH stores them."""
    async with async_session_factory() as session:
        batch = await session.get(SampleBatch, batch_id)
        batch.assignment_settings = stored
        await session.commit()


async def _newest_run(async_session_factory, batch_id):
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


@pytest.fixture
def seen_settings(stand_ins, monkeypatch):
    """What each stage, and each judging of a rewriting stage's samples, ran
    on: stage -> its call's config, and the judgings' configs in order."""
    seen: dict[str, object] = {}
    judged: list[object] = []

    def capture(name):
        async def hook(call):
            seen[name] = call.config

        return hook

    for name in ORDER:
        stand_ins["hooks"][name] = capture(name)
    rejudge = batch_rejudge.rejudge_samples

    async def recording(sample_batch_id, samples, config):
        judged.append(config)
        return await rejudge(sample_batch_id, samples, config)

    monkeypatch.setattr(batch_rejudge, "rejudge_samples", recording)
    return {"seen": seen, "judged": judged}


async def test_a_batch_with_no_settings_passes_at_the_defaults(
    folded, seen_settings, async_session_factory
):
    """Nothing saved: every stage, and every judging, runs on the engine's
    defaults, and the run records them, from the batch, with their hash."""
    batch_id = folded["batch_id"]
    await _run(batch_id)
    defaults = PeakAssignmentConfig()
    assert seen_settings["seen"] == dict.fromkeys(ORDER, defaults)
    assert len(seen_settings["judged"]) == 5
    assert all(c == defaults for c in seen_settings["judged"])
    run = await _newest_run(async_session_factory, batch_id)
    assert run.config["settings"] == defaults.model_dump(mode="json", exclude={"decoy"})
    assert run.config["settings_source"] == "batch"
    assert run.summary["settings_hash"] == settings_hash(defaults)
    entries = {e["stage"]: e for e in run.summary["stages"]}
    assert entries["search"]["settings"] == defaults.model_dump(mode="json")
    assert entries["co_variation"].get("settings") is None


async def test_a_pass_runs_every_stage_on_the_batchs_settings(
    folded, seen_settings, async_session_factory
):
    """Saved settings are what every stage of the next pass runs on, whoever
    starts it and from whichever stage: a pass on other settings than the
    last pass's runs from the first stage, though every stage is current,
    since every rewriting stage judges its samples under their bands."""
    batch_id = folded["batch_id"]
    await _run(batch_id)
    await _save_settings(
        async_session_factory,
        batch_id,
        {"mz_precision_ppm": 2.5, "assigned_threshold": 0.8},
    )
    saved = PeakAssignmentConfig(mz_precision_ppm=2.5, assigned_threshold=0.8)
    seen_settings["seen"].clear()
    seen_settings["judged"].clear()
    await _run(batch_id, "co_variation")
    assert seen_settings["seen"] == dict.fromkeys(ORDER, saved)
    assert seen_settings["judged"] == [saved] * 5
    run = await _newest_run(async_session_factory, batch_id)
    assert run.summary["first_stage"] == ORDER[0]
    assert run.config["settings"]["mz_precision_ppm"] == 2.5
    assert run.config["settings"]["assigned_threshold"] == 0.8
    assert run.config["settings_source"] == "batch"
    assert run.summary["settings_hash"] == settings_hash(saved)

    # Ran on them, every stage current: a pass has nothing left to do.
    assert (await _run(batch_id))["status"] == "skipped"
    # Saved again as they are, they hash alike: still nothing to do.
    await _save_settings(
        async_session_factory,
        batch_id,
        {"assigned_threshold": 0.8, "mz_precision_ppm": 2.5, "context": "auto"},
    )
    assert (await _run(batch_id))["status"] == "skipped"


async def test_settings_given_to_a_pass_override_the_batchs_and_say_so(
    folded, seen_settings, async_session_factory
):
    """A step's route given settings runs its pass on them in place of the
    batch's, and its run records that they came from the request. The next
    pass on the batch's own runs from the first stage again."""
    batch_id = folded["batch_id"]
    await _save_settings(async_session_factory, batch_id, {"mz_precision_ppm": 2.5})
    await _run(batch_id)
    seen_settings["seen"].clear()
    given = PeakAssignmentConfig(mz_precision_ppm=1.5)
    await assign_batch(
        sample_batch_id=batch_id,
        from_stage="search",
        config=given,
        process_id="pass-2",
    )
    assert seen_settings["seen"]["search"] == given
    assert seen_settings["seen"]["series"] == given
    run = await _newest_run(async_session_factory, batch_id)
    assert run.config["settings_source"] == "request"
    assert run.config["settings"]["mz_precision_ppm"] == 1.5
    assert run.summary["settings_hash"] == settings_hash(given)
    # The override ran from the first stage: the last pass ran on others.
    assert run.summary["first_stage"] == ORDER[0]

    seen_settings["seen"].clear()
    await _run(batch_id)
    assert seen_settings["seen"]["water_ladders"].mz_precision_ppm == 2.5
    run = await _newest_run(async_session_factory, batch_id)
    assert run.config["settings_source"] == "batch"


async def test_a_pass_from_before_the_batch_kept_settings_compares_nothing(
    folded, stand_ins, async_session_factory
):
    """A pass that recorded no settings hash says nothing of the settings it
    ran on: saving some starts no pass from the first stage."""
    batch_id = folded["batch_id"]
    await _run(batch_id)
    run = await _newest_run(async_session_factory, batch_id)
    async with async_session_factory() as session:
        stored = await session.get(BatchPeakRun, run.batch_peak_run_id)
        summary = dict(stored.summary)
        summary.pop("settings_hash")
        stored.summary = summary
        await session.commit()
    await _save_settings(async_session_factory, batch_id, {"mz_precision_ppm": 2.5})
    assert (await _run(batch_id))["status"] == "skipped"


async def test_settings_a_batch_saved_that_no_longer_read_are_the_defaults(
    folded, seen_settings, async_session_factory
):
    batch_id = folded["batch_id"]
    await _save_settings(async_session_factory, batch_id, {"mz_precision_ppm": "a few"})
    await _run(batch_id)
    assert seen_settings["seen"]["search"] == PeakAssignmentConfig()
    assert (await _states(batch_id))["search"]["state"] == "current"
