"""Integration tests for the verdicts of a batch or a dataset in one read.

A dataset of its own, in the shared test workspace, with two batches. Batch A
has four samples whose peak ``p1`` folds into one batch peak: three claim
C6H12O6 and one dissents with another formula. Batch B has one sample. The
verdicts are recorded over HTTP as a user would: S1 is confirmed and then
rejected (the first superseded), S2 rejected, the batch peak confirmed at the
batch level, and S5 in batch B confirmed. S3 has no verdict of its own and so
borrows the batch level's; S4 dissents and borrows nothing. S6, confirmed too,
is in another dataset, which no read here reaches. See
``service.get_scope_verifications``.
"""

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.api.controllers.sample.batches.export.service import (
    build_batch_sheets,
    write_batch_workbook,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.db import (
    AssignmentVerification,
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

GLUCOSE = "C6H12O6"
DISSENT = "C7H16O5"


async def _seed_sample(session, batch_id, name, peaks, acquired, now):
    """One sample with a completed run; ``peaks`` is (peak id, mz, formula)."""
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-scope-verdicts-{name}-{sample_file_id}.zarr",
            instrument="orbi-test",
            datetime=acquired,
            datetime_utc=acquired.replace(tzinfo=timezone.utc),
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
            sample_item_name=f"Scope verdicts {name}",
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
    assignment_ids = {}
    for pid, mz, formula in peaks:
        assignment_ids[pid] = gen_id(32)
        session.add(
            PeakAssignment(
                peak_assignment_id=assignment_ids[pid],
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id=pid,
                sample_peak_mz=mz,
                sample_peak_intensity=1000.0,
                role="M0",
                assigned_formula=formula,
                ion_formula=None,
                source="database",
                fit_score=0.9,
                mz_error_ppm=1.0,
                tier="assigned",
            )
        )
    return sample_item_id, assignment_ids


@pytest.fixture
def assignment_enabled(monkeypatch):
    """The verify routes are behind the feature flag; the reads answer either way."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


@pytest_asyncio.fixture
async def scope(async_session_factory, pa_test_data, editor_client, assignment_enabled):
    """The dataset, folded and judged as the module docstring says."""
    now = datetime.now(timezone.utc)
    dataset_id, other_dataset_id = gen_id(), gen_id()
    batch_a, batch_b, batch_c = gen_id(), gen_id(), gen_id()
    async with async_session_factory() as session:
        for dataset in (dataset_id, other_dataset_id):
            session.add(
                Dataset(
                    dataset_id=dataset,
                    workspace_id=pa_test_data["workspace_id"],
                    dataset_name=f"Scope verdicts {dataset}",
                    dataset_utc_created=now,
                )
            )
        for batch_id, dataset in (
            (batch_a, dataset_id),
            (batch_b, dataset_id),
            (batch_c, other_dataset_id),
        ):
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=dataset,
                    sample_batch_name=f"Scope verdicts {batch_id}",
                    sample_batch_utc_created=now,
                )
            )
        samples = {}
        for index, (name, batch_id, formula) in enumerate(
            [
                ("S1", batch_a, GLUCOSE),
                ("S2", batch_a, GLUCOSE),
                ("S3", batch_a, GLUCOSE),
                ("S4", batch_a, DISSENT),
                ("S5", batch_b, GLUCOSE),
                ("S6", batch_c, GLUCOSE),
            ]
        ):
            samples[name] = await _seed_sample(
                session,
                batch_id,
                name,
                [("p1", 181.0707 + index * 1e-5, formula)],
                datetime(2026, 7, 4, 12, index, 0),
                now,
            )
        await session.commit()
    for sample_item_id, _ in samples.values():
        await fold_sample_into_batch_peaks(sample_item_id)
    async with async_session_factory() as session:
        anchor = (
            await session.execute(
                select(BatchPeak).where(
                    BatchPeak.sample_batch_id == batch_a,
                    BatchPeak.consensus_formula == GLUCOSE,
                )
            )
        ).scalar_one()

    async def verify(name, verdict, evidence_level=None):
        sample_item_id, assignment_ids = samples[name]
        response = await editor_client.post(
            f"/api/peak-assignments/sample/{sample_item_id}/verify",
            json={
                "peak_assignment_id": assignment_ids["p1"],
                "verdict": verdict,
                "evidence_level": evidence_level,
            },
        )
        assert response.status_code == 201, response.text

    await verify("S1", "confirmed", "msms")
    await verify("S1", "rejected")
    await verify("S2", "rejected")
    await verify("S5", "confirmed", "visual")
    await verify("S6", "confirmed", "visual")
    before_batch_verdict = datetime.now(timezone.utc)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_a}/verify",
        json={
            "batch_peak_id": anchor.batch_peak_id,
            "verdict": "confirmed",
            "evidence_level": "pattern",
            "expected_formula": GLUCOSE,
        },
    )
    assert response.status_code == 201, response.text
    yield {
        "dataset_id": dataset_id,
        "batch_a": batch_a,
        "batch_b": batch_b,
        "anchor": anchor.batch_peak_id,
        "anchor_mz": anchor.mz,
        "ids": {name: sample_item_id for name, (sample_item_id, _) in samples.items()},
        "before_batch_verdict": before_batch_verdict,
    }
    async with async_session_factory() as session:
        await session.execute(
            delete(Dataset).where(
                Dataset.dataset_id.in_([dataset_id, other_dataset_id])
            )
        )
        await session.commit()


async def _read(client, path, **params):
    response = await client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _batch(scope):
    return f"/api/peak-assignments/batch/{scope['batch_a']}/verifications"


def _dataset(scope):
    return f"/api/peak-assignments/dataset/{scope['dataset_id']}/verifications"


def _names(scope, rows):
    by_id = {sample_item_id: name for name, sample_item_id in scope["ids"].items()}
    return [
        (by_id[row["sample_item_id"]], row["verdict"], row["borrowed"]) for row in rows
    ]


async def test_a_batch_reads_one_current_verdict_per_judged_peak(guest_client, scope):
    rows = await _read(guest_client, _batch(scope))

    assert _names(scope, rows) == [
        ("S1", "rejected", False),
        ("S2", "rejected", False),
        ("S3", "confirmed", True),
    ]


async def test_each_row_names_its_sample_and_peak(guest_client, scope):
    s1, _, s3 = await _read(guest_client, _batch(scope))

    assert s1["filename"].startswith("orbi-scope-verdicts-S1-")
    assert s1["datetime"] == "2026-07-04T12:00:00"
    assert s1["datetime_utc"].startswith("2026-07-04T12:00:00")
    assert s1["sample_item_name"] == "Scope verdicts S1"
    assert s1["sample_batch_id"] == scope["batch_a"]
    assert s1["sample_peak_id"] == "p1"
    assert s1["sample_peak_mz"] == pytest.approx(181.0707)
    assert s1["assigned_formula"] == GLUCOSE
    assert s1["assignment_verification_id"]
    assert s1["fit_score"] == pytest.approx(0.9)
    assert s1["batch_peak_verification_id"] is None

    assert s3["filename"].startswith("orbi-scope-verdicts-S3-")
    assert s3["sample_peak_mz"] == pytest.approx(181.0707 + 2e-5, abs=1e-6)
    assert s3["evidence_level"] == "pattern"
    assert s3["batch_peak_id"] == scope["anchor"]
    assert s3["batch_peak_verification_id"]
    assert s3["stale"] is False
    assert s3["assignment_verification_id"] is None
    assert s3["fit_score"] is None
    assert s3["context"]["consensus_formula"] == GLUCOSE


async def test_a_dataset_reads_every_batch(guest_client, scope):
    rows = await _read(guest_client, _dataset(scope))

    assert _names(scope, rows) == [
        ("S1", "rejected", False),
        ("S2", "rejected", False),
        ("S3", "confirmed", True),
        ("S5", "confirmed", False),
    ]
    assert rows[-1]["sample_batch_id"] == scope["batch_b"]


async def test_a_superseded_verdict_only_on_request(guest_client, scope):
    rows = await _read(guest_client, _batch(scope), include_superseded=True)

    assert _names(scope, rows) == [
        ("S1", "rejected", False),
        ("S1", "confirmed", False),
        ("S2", "rejected", False),
        ("S3", "confirmed", True),
    ]
    assert rows[0]["superseded_utc"] is None
    assert rows[1]["superseded_utc"] is not None
    assert rows[1]["evidence_level"] == "msms"
    dataset = await _read(guest_client, _dataset(scope), include_superseded=True)
    assert len(dataset) == 5


async def test_the_sample_wins_before_a_filter_applies(guest_client, scope):
    """S1 and S2 sit under the confirmed batch-level verdict too, but each has
    a verdict of its own, so asking for confirmed ones lists S3 alone."""
    rows = await _read(guest_client, _batch(scope), verdict="confirmed")

    assert _names(scope, rows) == [("S3", "confirmed", True)]


async def test_filters_on_evidence_level_and_time(guest_client, scope):
    pattern = await _read(guest_client, _dataset(scope), evidence_level="pattern")
    assert _names(scope, pattern) == [("S3", "confirmed", True)]

    msms = await _read(
        guest_client, _batch(scope), evidence_level="msms", include_superseded=True
    )
    assert _names(scope, msms) == [("S1", "confirmed", False)]

    since = scope["before_batch_verdict"]
    recent = await _read(guest_client, _dataset(scope), since=since.isoformat())
    assert _names(scope, recent) == [("S3", "confirmed", True)]

    # A time without a zone is UTC.
    naive = since.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    assert await _read(guest_client, _dataset(scope), since=naive) == recent


async def test_a_bad_filter_value_is_refused(guest_client, scope):
    response = await guest_client.get(_batch(scope), params={"verdict": "maybe"})
    assert response.status_code == 422


async def test_a_dissenting_sample_borrows_nothing(guest_client, scope):
    """S4's peak folded into the judged batch peak, but claims another formula."""
    rows = await _read(guest_client, _batch(scope))

    assert scope["ids"]["S4"] not in {row["sample_item_id"] for row in rows}


async def test_an_anchor_without_a_registry_reads_the_folded_row(
    guest_client, scope, async_session_factory
):
    """An anchor written before the identity registry existed: the member's
    claim is the ledger row it was folded from."""
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == scope["anchor"])
            .values(candidates=None)
        )
        await session.commit()

    rows = await _read(guest_client, _batch(scope))

    assert _names(scope, rows) == [
        ("S1", "rejected", False),
        ("S2", "rejected", False),
        ("S3", "confirmed", True),
    ]


async def test_a_verdict_whose_row_a_rerun_deleted_keeps_its_mz(
    guest_client, scope, async_session_factory
):
    async with async_session_factory() as session:
        await session.execute(
            update(AssignmentVerification)
            .where(AssignmentVerification.sample_item_id == scope["ids"]["S2"])
            .values(peak_assignment_id=None)
        )
        await session.commit()

    rows = await _read(guest_client, _batch(scope))

    assert rows[1]["peak_assignment_id"] is None
    assert rows[1]["sample_peak_mz"] == pytest.approx(181.0707 + 1e-5, abs=1e-6)


async def test_a_retracted_batch_verdict_reaches_no_sample(
    guest_client, editor_client, scope
):
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{scope['batch_a']}/retract",
        json={"batch_peak_id": scope["anchor"]},
    )
    assert response.status_code == 200, response.text

    rows = await _read(guest_client, _batch(scope), include_superseded=True)

    assert not any(row["borrowed"] for row in rows)


async def test_the_reads_refuse_a_workspace_the_user_is_not_in(
    guest_client, async_session_factory
):
    now = datetime.now(timezone.utc)
    workspace_id, dataset_id, batch_id = gen_id(), gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=f"Scope verdicts elsewhere {workspace_id}",
                workspace_status="active",
                workspace_utc_created=now,
                workspace_utc_modified=now,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Elsewhere",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=dataset_id,
                sample_batch_name="Elsewhere",
                sample_batch_utc_created=now,
            )
        )
        await session.commit()
    try:
        batch = await guest_client.get(
            f"/api/peak-assignments/batch/{batch_id}/verifications"
        )
        dataset = await guest_client.get(
            f"/api/peak-assignments/dataset/{dataset_id}/verifications"
        )
        assert batch.status_code == 403
        assert dataset.status_code == 403
    finally:
        async with async_session_factory() as session:
            await session.execute(
                delete(Workspace).where(Workspace.workspace_id == workspace_id)
            )
            await session.commit()


async def test_the_export_sheet_is_the_batch_read(guest_client, scope, tmp_path):
    rows = await _read(guest_client, _batch(scope))

    sheets = await build_batch_sheets(scope["batch_a"])
    path = tmp_path / "batch.xlsx"
    write_batch_workbook(str(path), sheets)

    with pd.ExcelFile(path) as book:
        assert book.sheet_names[-2:] == ["Verifications", "Provenance"]
        sheet = pd.read_excel(book, "Verifications")
    assert list(sheet.columns) == list(rows[0])
    assert sheet["sample_item_id"].tolist() == [row["sample_item_id"] for row in rows]
    assert sheet["verdict"].tolist() == [row["verdict"] for row in rows]
    assert sheet["borrowed"].tolist() == [row["borrowed"] for row in rows]
    # The batch-level verdict's context, as JSON text.
    assert json.loads(sheet["context"].iloc[2]) == rows[2]["context"]
    assert sheet["sample_peak_mz"].tolist() == pytest.approx(
        [row["sample_peak_mz"] for row in rows]
    )
    # Naive UTC, as Excel stores a time: to the millisecond.
    expected = [
        pd.Timestamp(row["verified_utc"]).tz_convert("UTC").tz_localize(None)
        for row in rows
    ]
    for stored, read in zip(sheet["verified_utc"], expected, strict=True):
        assert abs(stored - read) < pd.Timedelta(milliseconds=1)
