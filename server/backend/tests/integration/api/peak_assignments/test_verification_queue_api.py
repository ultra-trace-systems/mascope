"""The verification queue's read and the evidence-weighted refit (MAS-329).

The queue route ranks the rows of the run the ledger shows: stale verdicts
first, then the rows near a tier band edge with spot checks between them,
counting the labels the sample's instrument class and the run's reagent
profile hold near each row. The ranking itself is pinned in
``unit/api/peak_assignments/test_verification_queue.py``; these tests pin what
the service feeds it.

The sample's file resolves to the 'tof' class, and the run's profile is made
up per test, so the label pool the queue counts holds this module's labels
and nobody else's.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.new.peak_assignments import service
from mascope_backend.api.new.peak_assignments.calibration_store import (
    load_calibration,
    save_calibration,
)
from mascope_backend.db import (
    AssignmentCalibration,
    AssignmentVerification,
    Dataset,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.calibration import (
    EVIDENCE_LEVEL_WEIGHTS,
    Calibration,
)


BANDS = {"assigned": 0.75, "candidate": 0.45}


@pytest_asyncio.fixture
async def queue_data(async_session_factory, pa_test_data):
    """A tof sample with a current run, an earlier run and an other-profile run.

    The current run's rows: one near the candidate edge, two far from either
    edge, an isotopologue and a reagent ion at the edge (which take no
    verdict), and two peaks a verdict judged before the run: one the run
    rescored, one it reassigned to another formula. The earlier run carries
    two labels near the edge row's evidence, under the current run's profile;
    the other-profile run one more, which the queue must not count. A second
    sample, whose file names another instrument class, holds a label near it
    under the same profile, which the queue must not count either, and a
    verdict on a peak id the first sample's run also reads, which is no
    verdict of the first sample's. A superseded verdict on a row the run
    rescored is history, not a verdict to judge again.

    :return: Ids the tests address.
    """
    now = datetime.now(timezone.utc)
    profile = f"queue-profile-{gen_id(6)}"
    ids = {
        "dataset": gen_id(),
        "batch": gen_id(),
        "file": gen_id(),
        "sample": gen_id(),
        "run": gen_id(),
        "earlier": gen_id(),
        "other_profile": gen_id(),
        "decoy": gen_id(),
        "imported": gen_id(),
        "assignments": {},
        "other_file": gen_id(),
        "other_sample": gen_id(),
        "other_run": gen_id(),
    }

    def row(peak, evidence, formula="C6H12O6", role="M0", mech=None, run_id=None):
        ids["assignments"][peak] = gen_id(32)
        return PeakAssignment(
            peak_assignment_id=ids["assignments"][peak],
            peak_assignment_run_id=run_id or ids["run"],
            sample_item_id=ids["sample"],
            sample_peak_id=peak,
            sample_peak_mz=100.0 + len(peak),
            sample_peak_intensity=1000.0,
            role=role,
            assigned_formula=formula,
            ionization_mechanism_id=mech,
            source="database",
            fit_score=evidence,
            tier="candidate",
            provenance={"evidence": evidence, "p_correct": 0.5},
        )

    def verdict(
        peak,
        evidence,
        run,
        verdict="confirmed",
        formula="C6H12O6",
        at=None,
        sample=None,
        superseded=None,
    ):
        return AssignmentVerification(
            assignment_verification_id=gen_id(32),
            sample_item_id=sample or ids["sample"],
            superseded_utc=superseded,
            peak_assignment_run_id=run,
            sample_peak_id=peak,
            assigned_formula=formula,
            ionization_mechanism_id=None,
            verdict=verdict,
            evidence_level="visual",
            evidence=evidence,
            verified_utc=at or now - timedelta(days=1),
        )

    def run(
        run_id,
        created,
        *,
        run_profile=profile,
        decoy=None,
        sample=None,
        engine="mascope",
    ):
        return PeakAssignmentRun(
            peak_assignment_run_id=run_id,
            sample_item_id=sample or ids["sample"],
            engine=engine,
            engine_version="0.7.0-test",
            status="completed",
            config={"resolved_profile": {"profile": run_profile}},
            tier_bands=BANDS,
            decoy=decoy,
            peak_assignment_run_utc_created=created,
            peak_assignment_run_utc_completed=created + timedelta(minutes=5),
        )

    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=pa_test_data["workspace_id"],
                dataset_name=f"Queue Dataset {ids['dataset']}",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name=f"Queue Batch {ids['batch']}",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                filename=f"tof-queue_{ids['file']}.zarr",
                instrument="tof-test",
                datetime=datetime(2026, 11, 1, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=ids["sample"],
                sample_batch_id=ids["batch"],
                sample_file_id=ids["file"],
                sample_item_name=f"Queue Sample {ids['sample']}",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["other_file"],
                filename=f"orbi-queue_{ids['other_file']}.zarr",
                instrument="orbi-test",
                datetime=datetime(2026, 11, 1, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=ids["other_sample"],
                sample_batch_id=ids["batch"],
                sample_file_id=ids["other_file"],
                sample_item_name=f"Queue Other Sample {ids['other_sample']}",
                sample_item_type="sample",
                polarity="+",
                sample_item_utc_created=now,
            )
        )
        # The chain first: no relationship orders these inserts by itself.
        await session.flush()
        session.add(
            run(ids["other_run"], now - timedelta(days=2), sample=ids["other_sample"])
        )
        session.add(run(ids["other_profile"], now - timedelta(days=3), run_profile="x"))
        session.add(run(ids["earlier"], now - timedelta(days=2)))
        session.add(run(ids["run"], now - timedelta(hours=1)))
        session.add(run(ids["decoy"], now - timedelta(minutes=30), decoy="mass_offset"))
        session.add(run(ids["imported"], now - timedelta(hours=3), engine="ext"))
        session.add_all(
            [
                row("sp-edge", 0.46),
                row("sp-low", 0.10),
                row("sp-high", 0.97),
                row("sp-iso", 0.46, role="iso_child"),
                row("sp-reagent", 0.46, role="reagent"),
                row("sp-rescored", 0.60),
                row("sp-reassigned", 0.95, formula="C7H8"),
                # The decoy's own reading of an edge peak: no verdict to ask.
                row("sp-decoy", 0.46, run_id=ids["decoy"]),
                # An imported run's edge row: its verdict would teach no curve.
                row("sp-imported", 0.46, run_id=ids["imported"]),
            ]
        )
        await session.flush()
        session.add_all(
            [
                verdict("sp-rescored", 0.52, ids["earlier"], verdict="rejected"),
                verdict("sp-reassigned", 0.88, ids["earlier"]),
                # Labels on peaks the current run does not read: the pool.
                verdict("sp-label-1", 0.47, ids["earlier"]),
                verdict("sp-label-2", 0.48, ids["earlier"], verdict="rejected"),
                verdict("sp-label-3", 0.46, ids["other_profile"]),
                # Another instrument class, the same profile: not this curve's.
                verdict(
                    "sp-label-4", 0.45, ids["other_run"], sample=ids["other_sample"]
                ),
                # Another sample's verdict on a peak id this run reads.
                verdict(
                    "sp-low",
                    0.10,
                    ids["other_run"],
                    formula="C9",
                    sample=ids["other_sample"],
                ),
                # History: replaced long ago, at an evidence the run moved from.
                verdict(
                    "sp-high",
                    0.50,
                    ids["earlier"],
                    superseded=now - timedelta(hours=20),
                ),
            ]
        )
        await session.commit()

    yield ids

    async with async_session_factory() as session:
        samples = (ids["sample"], ids["other_sample"])
        await session.execute(
            delete(AssignmentVerification).where(
                AssignmentVerification.sample_item_id.in_(samples)
            )
        )
        await session.execute(
            delete(PeakAssignment).where(PeakAssignment.sample_item_id.in_(samples))
        )
        await session.execute(
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.sample_item_id.in_(samples)
            )
        )
        await session.execute(
            delete(SampleItem).where(SampleItem.sample_item_id.in_(samples))
        )
        await session.execute(
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"])
        )
        await session.execute(
            delete(Dataset).where(Dataset.dataset_id == ids["dataset"])
        )
        await session.execute(
            delete(SampleFile).where(
                SampleFile.sample_file_id.in_((ids["file"], ids["other_file"]))
            )
        )
        await session.commit()


def _queue_url(sample_item_id: str) -> str:
    return f"/api/peak-assignments/sample/{sample_item_id}/verification-queue"


@pytest.mark.asyncio
async def test_the_queue_ranks_the_latest_runs_rows(guest_client, queue_data):
    response = await guest_client.get(_queue_url(queue_data["sample"]))
    assert response.status_code == 200, response.text
    body = response.json()

    # The latest completed run that is not a decoy, as the ledger reads.
    assert body["peak_assignment_run_id"] == queue_data["run"]
    entries = body["data"]
    assert [(e["sample_peak_id"], e["reason"]) for e in entries[:3]] == [
        ("sp-rescored", "stale"),
        ("sp-reassigned", "stale"),
        ("sp-edge", "band_edge"),
    ]
    assert {e["sample_peak_id"] for e in entries[3:]} == {"sp-low", "sp-high"}
    assert {e["reason"] for e in entries[3:]} == {"spot_check"}
    assert body["counts"] == {"stale": 2, "band_edge": 1, "spot_check": 2}
    assert body["total"] == 5

    stale = {e["sample_peak_id"]: e["stale"] for e in entries[:2]}
    assert stale["sp-rescored"]["kind"] == "rescored"
    assert stale["sp-rescored"]["verdict"] == "rejected"
    assert stale["sp-reassigned"]["kind"] == "reassigned"
    assert stale["sp-reassigned"]["assigned_formula"] == "C6H12O6"
    assert entries[2]["near_band"] == "candidate"
    # Guardrail 5: the queue never carries the calibrated number, nor its input.
    assert "p_correct" not in response.text
    assert "evidence" not in entries[2]


@pytest.mark.asyncio
async def test_the_queue_counts_the_labels_of_the_runs_profile_only(
    guest_client, queue_data
):
    """The edge row at 0.46 has four labels within 0.05 of it; the one fit
    under another profile and the one of another instrument class are not the
    curve's, and not counted."""
    response = await guest_client.get(_queue_url(queue_data["sample"]))
    edge = next(e for e in response.json()["data"] if e["reason"] == "band_edge")
    assert edge["labels_near"] == 2


@pytest.mark.asyncio
async def test_a_verdict_takes_its_row_out_of_the_queue(
    guest_client, editor_client, queue_data, monkeypatch
):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")
    response = await editor_client.post(
        f"/api/peak-assignments/sample/{queue_data['sample']}/verify",
        json={
            "peak_assignment_id": queue_data["assignments"]["sp-edge"],
            "verdict": "rejected",
        },
    )
    assert response.status_code == 201, response.text
    body = (await guest_client.get(_queue_url(queue_data["sample"]))).json()
    assert "sp-edge" not in {e["sample_peak_id"] for e in body["data"]}
    assert body["counts"]["band_edge"] == 0


@pytest.mark.asyncio
async def test_a_named_run_is_ranked_and_a_decoy_or_import_answers_empty(
    guest_client, queue_data
):
    url = _queue_url(queue_data["sample"])
    earlier = await guest_client.get(
        url, params={"peak_assignment_run_id": queue_data["earlier"]}
    )
    assert earlier.status_code == 200, earlier.text
    assert earlier.json()["peak_assignment_run_id"] == queue_data["earlier"]
    assert earlier.json()["data"] == []

    decoy = await guest_client.get(
        url, params={"peak_assignment_run_id": queue_data["decoy"]}
    )
    assert decoy.status_code == 200, decoy.text
    assert decoy.json()["data"] == []

    imported = await guest_client.get(
        url, params={"peak_assignment_run_id": queue_data["imported"]}
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["peak_assignment_run_id"] == queue_data["imported"]
    assert imported.json()["data"] == []
    assert "imported" in imported.json()["message"]

    missing = await guest_client.get(url, params={"peak_assignment_run_id": gen_id()})
    assert missing.status_code == 404

    # A ledger the batch derives for the sample is no run to rank.
    fold = await guest_client.get(
        url, params={"peak_assignment_run_id": f"fold-{queue_data['sample']}"}
    )
    assert fold.status_code == 200, fold.text
    assert fold.json()["data"] == []
    assert fold.json()["peak_assignment_run_id"] is None


@pytest.mark.asyncio
async def test_the_queue_is_read_by_members_only(
    guest_client, async_session_factory, queue_data
):
    """A sample in a workspace the user is no member of is not theirs to rank."""
    now = datetime.now(timezone.utc)
    workspace_id, dataset_id = gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"Queue Closed Workspace {workspace_id}",
                workspace_status="active",
                workspace_utc_created=now,
                workspace_utc_modified=now,
            )
        )
        await session.flush()
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name=f"Queue Closed Dataset {dataset_id}",
                dataset_utc_created=now,
            )
        )
        await session.flush()
        # The queue's sample, moved into the closed workspace for the call.
        batch = await session.get(SampleBatch, queue_data["batch"])
        open_dataset = batch.dataset_id
        batch.dataset_id = dataset_id
        await session.commit()
    try:
        response = await guest_client.get(_queue_url(queue_data["sample"]))
        assert response.status_code in (403, 404), response.text
    finally:
        async with async_session_factory() as session:
            batch = await session.get(SampleBatch, queue_data["batch"])
            batch.dataset_id = open_dataset
            await session.flush()
            await session.execute(
                delete(Dataset).where(Dataset.dataset_id == dataset_id)
            )
            await session.execute(
                delete(Workspace).where(Workspace.workspace_id == workspace_id)
            )
            await session.commit()


# --- the evidence-weighted refit -------------------------------------------------


@pytest.mark.asyncio
async def test_a_refit_weighs_the_labels_and_reports_the_weights(
    async_session_factory, queue_data, monkeypatch
):
    """Forty labels on the tof class, a quarter of them reference standards:
    the refit is weighed by evidence level, reports the ECE before and after,
    and hands the store a curve that records the weights."""
    rng = np.random.default_rng(3)
    levels = ["reference_standard", "msms", "pattern", "visual"]
    async with async_session_factory() as session:
        for i in range(60):
            evidence = float((i + 0.5) / 60)
            session.add(
                AssignmentVerification(
                    assignment_verification_id=gen_id(32),
                    sample_item_id=queue_data["sample"],
                    peak_assignment_run_id=queue_data["earlier"],
                    sample_peak_id=f"sp-fit-{i}",
                    assigned_formula="C6H12O6",
                    verdict="confirmed" if rng.uniform() < evidence else "rejected",
                    evidence_level=levels[i % 4],
                    evidence=evidence,
                    verified_utc=datetime.now(timezone.utc),
                )
            )
        await session.commit()

    saved = AsyncMock()
    monkeypatch.setattr(service, "save_calibration", saved)
    monkeypatch.setattr(
        service, "load_calibration", AsyncMock(return_value=Calibration(a=1.0, b=0.0))
    )
    result = await service.recalibrate_instrument("tof")

    assert result["recalibrated"] is True, result["message"]
    assert result["label_weights"] == dict(EVIDENCE_LEVEL_WEIGHTS)
    assert result["before_ece"] is not None and result["after_ece"] is not None
    curve = saved.await_args.args[0]
    assert curve.label_weights == dict(EVIDENCE_LEVEL_WEIGHTS)


@pytest_asyncio.fixture
async def instrument_class(async_session_factory):
    """A made-up instrument class, so no other curve is the active one."""
    inst = f"cls{gen_id(6).lower()}"
    yield inst
    async with async_session_factory() as session:
        await session.execute(
            delete(AssignmentCalibration).where(
                AssignmentCalibration.instrument == inst
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_the_store_keeps_the_weights_a_curve_was_fit_under(
    instrument_class, async_session_factory
):
    weights = {"reference_standard": 1.0, "visual": 0.25, "unstated": 0.25}
    await save_calibration(
        Calibration(a=4.0, b=-2.0, instrument=instrument_class, label_weights=weights),
        1,
    )
    async with async_session_factory() as session:
        stored = (
            await session.execute(
                select(AssignmentCalibration.label_weights).where(
                    AssignmentCalibration.instrument == instrument_class
                )
            )
        ).scalar_one()
    assert stored == weights
    assert (await load_calibration(instrument_class, 1)).label_weights == weights


@pytest.mark.asyncio
async def test_a_curve_fit_unweighted_stores_no_weights(
    instrument_class, async_session_factory
):
    await save_calibration(Calibration(a=4.0, b=-2.0, instrument=instrument_class), 1)
    assert (await load_calibration(instrument_class, 1)).label_weights is None
