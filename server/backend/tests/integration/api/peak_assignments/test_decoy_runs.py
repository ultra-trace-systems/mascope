"""A decoy run is never read as the sample's result.

A decoy run is the in-app engine's own run, newer than the run it is a decoy of
and written by the same engine, so every reader that takes a sample's latest
completed run would take it unless it reads the mark on the row. Each test seeds
a sample whose control run is followed by a decoy that reads its peaks
differently, and asks one reader which of the two it read: the batch fold, the
ledger read that names no run, the runs listing, the batch's sample status, the
centre a seeded re-score outside a run takes, the decoy's own plan, the verdict
endpoint and the recalibration pool. The assign and batch-search routes refuse a
decoy they cannot run before any run exists.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    PeakAssignmentDecoy,
)
from mascope_backend.api.new.peak_assignments.engine import (
    MASS_CALIBRATION_KEY,
    PATTERN_SCORING_KEY,
    pattern_scoring_for,
    pattern_scoring_snapshot,
)
from mascope_backend.api.new.peak_assignments.mass_gate import MassCalibration
from mascope_backend.api.new.peak_assignments.profiles import RESOLVED_PROFILE_KEY
from mascope_backend.api.new.peak_assignments.sample_status import (
    get_batch_sample_assignment_status,
)
from mascope_backend.api.new.peak_assignments.service import (
    _create_run,
    _latest_run_pattern_scoring,
    get_peak_assignment_runs,
    get_peak_assignments,
    plan_sample_decoy,
)
from mascope_backend.db import (
    AssignmentVerification,
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    Dataset,
    InstrumentFunction,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.mass_accuracy import SampleMassAccuracy


pytestmark = pytest.mark.asyncio

ARM = PeakAssignmentDecoy(mz_shift_ppm=9.0)


@pytest.fixture
def feature_enabled(monkeypatch):
    """Force the peak assignment feature on - the writes are gated."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


def _stored_config(mu_ppm: float, **settings) -> dict:
    """A completed in-app run's stored config, with the calibration it recorded."""
    accuracy = SampleMassAccuracy(mu_ppm=mu_ppm, sigma_ppm=0.2, anchors=30)
    match_params = SimpleNamespace(mz_tolerance=5.0, isotope_abundance_threshold=0.01)
    config = PeakAssignmentConfig(**settings).model_dump()
    config[RESOLVED_PROFILE_KEY] = {"profile": "ESI_POS", "mz_precision_ppm": 3.0}
    config[PATTERN_SCORING_KEY] = pattern_scoring_snapshot(
        pattern_scoring_for(match_params, accuracy, "orbi"), accuracy
    )
    config[MASS_CALIBRATION_KEY] = MassCalibration(
        mu_ppm=mu_ppm, sigma_ppm=0.1, anchors=120, gate_sigma_ppm=0.54
    ).snapshot()
    return config


def _row(run_id, sample_item_id, peak_id, mz, formula, *, evidence=0.9):
    """An assigned monoisotopic row, or an unassigned one with no formula."""
    return PeakAssignment(
        peak_assignment_id=gen_id(32),
        peak_assignment_run_id=run_id,
        sample_item_id=sample_item_id,
        sample_peak_id=peak_id,
        sample_peak_mz=mz,
        sample_peak_intensity=5000.0,
        role="M0" if formula else "unassigned",
        assigned_formula=formula,
        ion_formula=f"{formula}H+" if formula else None,
        isotope_label="M0" if formula else None,
        source="database" if formula else None,
        fit_score=0.95 if formula else None,
        mz_error_ppm=0.2 if formula else None,
        tier="assigned" if formula else "unassigned",
        provenance={"evidence": evidence, "p_correct": 0.9} if formula else {},
    )


@pytest_asyncio.fixture
async def decoyed(async_session_factory, pa_test_data, pa_sample_view):
    """A sample with a control run and a newer decoy of it that reads it wrong.

    Its own dataset and batch, removed afterwards with everything the fold
    wrote: the recalibration pool reads every sample of its instrument, and a
    verdict left behind would be counted by a later test. The file name is a
    TOF's, which is what that pool resolves the instrument from.
    """
    now = datetime.now(timezone.utc)
    ids = {
        "dataset": gen_id(),
        "batch": gen_id(),
        "function": gen_id(32),
        "file": gen_id(),
        "sample": gen_id(),
        "control": gen_id(),
        "decoy": gen_id(),
    }
    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=pa_test_data["workspace_id"],
                dataset_name=f"Decoy Runs {ids['dataset']}",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Decoy Runs Batch",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            InstrumentFunction(
                instrument_function_id=ids["function"],
                instrument="tof-test",
                method_file="decoy-runs.meth",
                datetime_utc=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=ids["file"],
                instrument_function_id=ids["function"],
                filename=f"tof_decoy-runs-{ids['file']}.h5",
                instrument="tof-test",
                datetime=datetime(2026, 11, 5, 12, 0, 0),
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
                sample_item_name="Decoy Runs Sample",
                sample_item_type="sample",
                polarity="+",
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=now,
            )
        )
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=ids["control"],
                sample_item_id=ids["sample"],
                engine="mascope",
                engine_version="test",
                status="completed",
                config=_stored_config(-0.2),
                peak_assignment_run_utc_created=now - timedelta(hours=1),
                peak_assignment_run_utc_completed=now - timedelta(minutes=55),
            )
        )
        # Newer, the same engine, and a calibration of its own that no reader
        # outside the decoy may take.
        session.add(
            PeakAssignmentRun(
                peak_assignment_run_id=ids["decoy"],
                sample_item_id=ids["sample"],
                engine="mascope",
                engine_version="test",
                status="completed",
                decoy=ARM.arm,
                config={
                    **_stored_config(+8.8, decoy=ARM),
                    "decoy_arm": {"control_run_id": ids["control"]},
                },
                peak_assignment_run_utc_created=now - timedelta(minutes=10),
                peak_assignment_run_utc_completed=now - timedelta(minutes=5),
            )
        )
        await session.flush()
        control_rows = [
            _row(ids["control"], ids["sample"], "peak-1", 181.0707, "C6H12O6"),
            _row(ids["control"], ids["sample"], "peak-2", 203.0526, "C8H10O5"),
            _row(ids["control"], ids["sample"], "peak-3", 250.1000, None),
        ]
        decoy_rows = [
            _row(ids["decoy"], ids["sample"], "peak-1", 181.0723, "C7H16O5"),
            _row(ids["decoy"], ids["sample"], "peak-9", 300.2000, "C14H29NO4"),
        ]
        session.add_all(control_rows + decoy_rows)
        await session.commit()
        ids["control_rows"] = [row.peak_assignment_id for row in control_rows]
        ids["decoy_rows"] = [row.peak_assignment_id for row in decoy_rows]

    yield ids

    async with async_session_factory() as session:
        for statement in (
            delete(AssignmentVerification).where(
                AssignmentVerification.sample_item_id == ids["sample"]
            ),
            delete(BatchPeakOccurrence).where(
                BatchPeakOccurrence.sample_item_id == ids["sample"]
            ),
            delete(BatchPeak).where(BatchPeak.sample_batch_id == ids["batch"]),
            delete(BatchPeakRun).where(BatchPeakRun.sample_batch_id == ids["batch"]),
            delete(PeakAssignment).where(
                PeakAssignment.sample_item_id == ids["sample"]
            ),
            delete(PeakAssignmentRun).where(
                PeakAssignmentRun.sample_item_id == ids["sample"]
            ),
            delete(SampleItem).where(SampleItem.sample_item_id == ids["sample"]),
            delete(SampleFile).where(SampleFile.sample_file_id == ids["file"]),
            delete(InstrumentFunction).where(
                InstrumentFunction.instrument_function_id == ids["function"]
            ),
            delete(SampleBatch).where(SampleBatch.sample_batch_id == ids["batch"]),
            delete(Dataset).where(Dataset.dataset_id == ids["dataset"]),
        ):
            await session.execute(statement)
        await session.commit()


async def test_the_fold_reads_the_control_and_not_the_newer_decoy(
    async_session_factory, decoyed
):
    await fold_sample_into_batch_peaks(decoyed["sample"])

    async with async_session_factory() as session:
        members = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.sample_item_id == decoyed["sample"]
                    )
                )
            )
            .scalars()
            .all()
        )
        formulas = set(
            (
                await session.execute(
                    select(BatchPeak.consensus_formula).where(
                        BatchPeak.sample_batch_id == decoyed["batch"]
                    )
                )
            ).scalars()
        )

    assert {member.sample_peak_id for member in members} == {
        "peak-1",
        "peak-2",
        "peak-3",
    }
    assert {member.peak_assignment_id for member in members} == set(
        decoyed["control_rows"]
    )
    assert "C7H16O5" not in formulas
    assert "C6H12O6" in formulas


async def test_a_ledger_read_that_names_no_run_reads_the_control(decoyed):
    ledger = await get_peak_assignments(sample_item_id=decoyed["sample"])

    assert {row["peak_assignment_run_id"] for row in ledger["data"]} == {
        decoyed["control"]
    }
    # Named by its id, the decoy is served as it is.
    named = await get_peak_assignments(
        sample_item_id=decoyed["sample"], peak_assignment_run_id=decoyed["decoy"]
    )
    assert {row["sample_peak_id"] for row in named["data"]} == {"peak-1", "peak-9"}


async def test_the_runs_listing_leaves_a_decoy_out_unless_asked(guest_client, decoyed):
    url = f"/api/peak-assignments/sample/{decoyed['sample']}/runs"

    default = await guest_client.get(url)
    asked = await guest_client.get(url, params={"include_decoys": "true"})

    assert default.status_code == 200, default.text
    assert [run["peak_assignment_run_id"] for run in default.json()["data"]] == [
        decoyed["control"]
    ]
    assert [
        (run["peak_assignment_run_id"], run["decoy"]) for run in asked.json()["data"]
    ] == [(decoyed["decoy"], "mz_shift_ppm=+9"), (decoyed["control"], None)]


async def test_the_batch_s_sample_status_reads_the_control(decoyed):
    status = await get_batch_sample_assignment_status(decoyed["batch"])

    (record,) = [
        row for row in status["data"] if row["sample_item_id"] == decoyed["sample"]
    ]
    assert record["run"]["peak_assignment_run_id"] == decoyed["control"]


async def test_a_reader_outside_a_run_centres_where_the_control_scored(decoyed):
    scoring = await _latest_run_pattern_scoring(decoyed["sample"])

    assert scoring["mu_ppm"] == pytest.approx(-0.2)


async def test_a_decoy_is_planned_against_the_control_and_not_another_decoy(
    decoyed,
):
    sample = await fetch_sample(decoyed["sample"])

    plan = await plan_sample_decoy(sample, PeakAssignmentConfig(decoy=ARM))

    assert plan.control_run_id == decoyed["control"]
    assert plan.calibration.mu_ppm == pytest.approx(-0.2)


async def test_a_decoy_run_is_marked_on_its_row(async_session_factory, decoyed):
    decoy = await _create_run(decoyed["sample"], PeakAssignmentConfig(decoy=ARM))
    control = await _create_run(decoyed["sample"], PeakAssignmentConfig())

    async with async_session_factory() as session:
        marks = dict(
            (
                await session.execute(
                    select(
                        PeakAssignmentRun.peak_assignment_run_id,
                        PeakAssignmentRun.decoy,
                    ).where(
                        PeakAssignmentRun.peak_assignment_run_id.in_(
                            (
                                decoy.peak_assignment_run_id,
                                control.peak_assignment_run_id,
                            )
                        )
                    )
                )
            ).all()
        )
    assert marks == {
        decoy.peak_assignment_run_id: "mz_shift_ppm=+9",
        control.peak_assignment_run_id: None,
    }
    runs = await get_peak_assignment_runs(
        sample_item_id=decoyed["sample"], include_decoys=True
    )
    assert {run["decoy"] for run in runs["data"]} == {None, "mz_shift_ppm=+9"}


async def test_a_verdict_on_a_decoy_row_is_refused(
    editor_client, decoyed, feature_enabled, async_session_factory
):
    response = await editor_client.post(
        f"/api/peak-assignments/sample/{decoyed['sample']}/verify",
        json={
            "peak_assignment_id": decoyed["decoy_rows"][0],
            "verdict": "rejected",
            "evidence_level": "visual",
        },
    )

    assert response.status_code == 422, response.text
    assert "decoy run" in response.text
    async with async_session_factory() as session:
        verdicts = (
            await session.execute(
                select(AssignmentVerification).where(
                    AssignmentVerification.sample_item_id == decoyed["sample"]
                )
            )
        ).all()
    assert verdicts == []


async def test_the_recalibration_pool_counts_no_verdict_of_a_decoy_run(
    owner_client, decoyed, feature_enabled, async_session_factory
):
    async def pool():
        response = await owner_client.post(
            "/api/peak-assignments/calibration/tof/recalibrate"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        return body["n_pos"] + body["n_neg"]

    async def verdict(row_index: int, rows: str):
        # Written past the endpoint, which refuses the decoy's rows: the pool
        # has to hold on its own.
        async with async_session_factory() as session:
            row = await session.get(PeakAssignment, decoyed[rows][row_index])
            session.add(
                AssignmentVerification(
                    assignment_verification_id=gen_id(32),
                    sample_item_id=row.sample_item_id,
                    peak_assignment_id=row.peak_assignment_id,
                    peak_assignment_run_id=row.peak_assignment_run_id,
                    sample_peak_id=row.sample_peak_id,
                    assigned_formula=row.assigned_formula,
                    ionization_mechanism_id=row.ionization_mechanism_id,
                    verdict="confirmed",
                    evidence_level="visual",
                    fit_score=row.fit_score,
                    evidence=0.9,
                    verified_utc=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    before = await pool()
    await verdict(1, "decoy_rows")
    with_decoy = await pool()
    await verdict(1, "control_rows")

    assert with_decoy == before
    assert await pool() == before + 1


async def test_the_assign_route_refuses_a_decoy_that_cannot_bound_its_control(
    editor_client, decoyed, feature_enabled, async_session_factory
):
    # The control searched 3 ppm and matched lines at 5: a 4 ppm shift leaves
    # every true formula in reach.
    response = await editor_client.post(
        f"/api/peak-assignments/sample/{decoyed['sample']}/assign",
        json={"config": {"decoy": {"mz_shift_ppm": 4.0}}},
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "decoy_refused"
    assert "inside the 5 ppm window" in detail["reason"]
    async with async_session_factory() as session:
        runs = (
            await session.execute(
                select(PeakAssignmentRun.peak_assignment_run_id).where(
                    PeakAssignmentRun.sample_item_id == decoyed["sample"]
                )
            )
        ).all()
    assert len(runs) == 2


async def test_the_batch_search_takes_no_decoy(editor_client, decoyed, feature_enabled):
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{decoyed['batch']}/search-untargeted",
        json={"config": {"decoy": {"swap_adducts": True}}},
    )

    assert response.status_code == 422, response.text
    assert "decoy" in response.text
