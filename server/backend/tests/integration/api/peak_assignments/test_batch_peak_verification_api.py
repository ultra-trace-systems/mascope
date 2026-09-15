"""Integration tests for batch-level verdicts.

A batch of its own with two samples folded into the batch ledger, so one anchor
(C6H12O6) has two members and one (unassigned) has a single member. Verdicts are
recorded against the anchor over HTTP and read back per batch and per sample.
See ``batch_peak_verification.py``.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.api.new.peak_assignments.batch_peak_verification import (
    CLAIM_CHANGED_CODE,
    UNASSIGNED_ANCHOR_CODE,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.db import (
    BatchPeak,
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
    "S2": [
        ("p1", 181.0708, "C6H12O6", "C6H13O6+", "assigned", 0.90, 4000.0),
    ],
}


async def _seed_sample(session, batch_id, name, rows, now):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-anchor-verdict-{name}-{sample_file_id}.zarr",
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
            sample_item_name=f"Anchor verdict {name}",
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
async def anchors(async_session_factory, pa_test_data):
    """Two samples in a batch of their own, folded: one assigned anchor with two
    members, one unassigned anchor with one."""
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
                sample_batch_name=f"Anchor verdicts {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        s1 = await _seed_sample(session, batch_id, "S1", _ROWS["S1"], now)
        s2 = await _seed_sample(session, batch_id, "S2", _ROWS["S2"], now)
        await session.commit()
    assert await fold_sample_into_batch_peaks(s1) == batch_id
    assert await fold_sample_into_batch_peaks(s2) == batch_id
    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(BatchPeak)
                    .where(BatchPeak.sample_batch_id == batch_id)
                    .order_by(BatchPeak.mz)
                )
            )
            .scalars()
            .all()
        )
        assigned = next(bp for bp in rows if bp.consensus_formula == "C6H12O6")
        unassigned = next(bp for bp in rows if bp.consensus_formula is None)
        found = {
            "batch_id": batch_id,
            "s1": s1,
            "s2": s2,
            "assigned": assigned.batch_peak_id,
            "unassigned": unassigned.batch_peak_id,
            "n_present": assigned.n_present,
        }
    return found


@pytest.fixture
def assignment_enabled(monkeypatch):
    """The write routes are behind the feature flag, which the test environment
    leaves off; the reads answer either way."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


def _verify(client, batch_id, **body):
    return client.post(f"/api/batch-peaks/batch/{batch_id}/verify", json=body)


def _retract(client, batch_id, **body):
    return client.post(f"/api/batch-peaks/batch/{batch_id}/retract", json=body)


async def _verdicts(client, batch_id):
    response = await client.get(f"/api/batch-peaks/batch/{batch_id}/verdicts")
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _context(client, sample_item_id):
    response = await client.get(
        f"/api/batch-peaks/sample/{sample_item_id}/anchor-context"
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_an_editor_records_a_verdict_on_the_anchor(
    editor_client, anchors, assignment_enabled
):
    response = await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="confirmed",
        evidence_level="pattern",
        expected_formula="C6H12O6",
        note="seen in both",
    )
    assert response.status_code == 201, response.text
    record = response.json()["data"][0]
    assert record["verdict"] == "confirmed"
    assert record["evidence_level"] == "pattern"
    assert record["note"] == "seen in both"
    assert record["assigned_formula"] == "C6H12O6"
    assert record["batch_peak_id"] == anchors["assigned"]
    assert record["sample_batch_id"] == anchors["batch_id"]
    assert record["verified_by"] is not None
    assert record["superseded_utc"] is None
    assert record["stale"] is False
    assert record["anchor_present"] is True
    assert record["current_formula"] == "C6H12O6"
    # What the human saw, pinned on the record.
    assert record["context"]["consensus_formula"] == "C6H12O6"
    assert record["context"]["n_present"] == anchors["n_present"] == 2


async def test_a_new_verdict_supersedes_the_live_one(
    editor_client, guest_client, anchors, assignment_enabled
):
    first = await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="confirmed",
        evidence_level="visual",
        expected_formula="C6H12O6",
    )
    assert first.status_code == 201, first.text
    second = await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="rejected",
        expected_formula="C6H12O6",
    )
    assert second.status_code == 201, second.text
    rows = await _verdicts(guest_client, anchors["batch_id"])
    # Newest first, history kept, exactly one live row.
    assert [r["verdict"] for r in rows] == ["rejected", "confirmed"]
    assert [r["verdict"] for r in rows if r["superseded_utc"] is None] == ["rejected"]


async def test_the_formula_judged_must_be_the_present_claim(
    editor_client, anchors, assignment_enabled
):
    response = await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="rejected",
        expected_formula="C7H14O7",
    )
    assert response.status_code == 409, response.text
    assert CLAIM_CHANGED_CODE in response.text
    assert "C6H12O6" in response.text


async def test_an_unassigned_anchor_has_no_claim_to_judge(
    editor_client, anchors, assignment_enabled
):
    response = await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["unassigned"],
        verdict="unsure",
    )
    assert response.status_code == 422, response.text
    assert UNASSIGNED_ANCHOR_CODE in response.text


async def test_confirming_without_the_formula_is_refused_by_the_body(
    editor_client, anchors, assignment_enabled
):
    response = await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="confirmed",
        evidence_level="pattern",
    )
    assert response.status_code == 422, response.text
    assert "expected_formula" in response.text


async def test_a_retract_returns_the_species_to_unverified(
    editor_client, guest_client, anchors, assignment_enabled
):
    await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="confirmed",
        evidence_level="pattern",
        expected_formula="C6H12O6",
    )
    response = await _retract(
        editor_client, anchors["batch_id"], batch_peak_id=anchors["assigned"]
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"] == 1
    rows = await _verdicts(guest_client, anchors["batch_id"])
    assert len(rows) == 1
    assert rows[0]["superseded_utc"] is not None
    # Nothing live: zero, not an error.
    again = await _retract(
        editor_client, anchors["batch_id"], batch_peak_id=anchors["assigned"]
    )
    assert again.status_code == 200, again.text
    assert again.json()["results"] == 0


async def test_a_moved_consensus_reads_as_stale(
    editor_client, guest_client, async_session_factory, anchors, assignment_enabled
):
    await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="confirmed",
        evidence_level="pattern",
        expected_formula="C6H12O6",
    )
    async with async_session_factory() as session:
        await session.execute(
            update(BatchPeak)
            .where(BatchPeak.batch_peak_id == anchors["assigned"])
            .values(consensus_formula="C7H14O7")
        )
        await session.commit()
    rows = await _verdicts(guest_client, anchors["batch_id"])
    assert rows[0]["stale"] is True
    assert rows[0]["assigned_formula"] == "C6H12O6"
    assert rows[0]["current_formula"] == "C7H14O7"
    # Judging the new formula does not touch the old verdict: a machine recompute
    # never supersedes a human label, so both stay live, each about its own claim.
    response = await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="unsure",
        expected_formula="C7H14O7",
    )
    assert response.status_code == 201, response.text
    live = [
        r
        for r in await _verdicts(guest_client, anchors["batch_id"])
        if r["superseded_utc"] is None
    ]
    assert sorted(r["assigned_formula"] for r in live) == ["C6H12O6", "C7H14O7"]
    # Retracting without a claim clears every live verdict on the anchor.
    cleared = await _retract(
        editor_client, anchors["batch_id"], batch_peak_id=anchors["assigned"]
    )
    assert cleared.json()["results"] == 2


async def test_a_verdict_outlives_its_anchor(
    editor_client, guest_client, async_session_factory, anchors, assignment_enabled
):
    await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="confirmed",
        evidence_level="pattern",
        expected_formula="C6H12O6",
    )
    async with async_session_factory() as session:
        await session.execute(
            delete(BatchPeak).where(BatchPeak.batch_peak_id == anchors["assigned"])
        )
        await session.commit()
    rows = await _verdicts(guest_client, anchors["batch_id"])
    assert len(rows) == 1
    assert rows[0]["anchor_present"] is False
    assert rows[0]["stale"] is True
    assert rows[0]["current_formula"] is None
    # ... and can still be withdrawn.
    response = await _retract(
        editor_client, anchors["batch_id"], batch_peak_id=anchors["assigned"]
    )
    assert response.json()["results"] == 1


async def test_the_anchor_context_of_a_sample_lists_the_verdicts_that_reach_it(
    editor_client, guest_client, anchors, assignment_enabled
):
    assert await _context(guest_client, anchors["s1"]) == []
    await _verify(
        editor_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="confirmed",
        evidence_level="pattern",
        expected_formula="C6H12O6",
    )
    # Both samples hold a member of the judged anchor; the unassigned anchor has
    # no verdict, so S1's second peak has no row.
    for sample in ("s1", "s2"):
        rows = await _context(guest_client, anchors[sample])
        assert [
            (r["sample_peak_id"], r["batch_peak_id"], r["verdict"], r["stale"])
            for r in rows
        ] == [("p1", anchors["assigned"], "confirmed", False)]
    await _retract(
        editor_client, anchors["batch_id"], batch_peak_id=anchors["assigned"]
    )
    assert await _context(guest_client, anchors["s1"]) == []


async def test_a_guest_cannot_record_or_retract_a_verdict(
    guest_client, anchors, assignment_enabled
):
    response = await _verify(
        guest_client,
        anchors["batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="unsure",
    )
    assert response.status_code == 403, response.text
    response = await _retract(
        guest_client, anchors["batch_id"], batch_peak_id=anchors["assigned"]
    )
    assert response.status_code == 403, response.text


async def test_an_anchor_of_another_batch_is_not_found(
    editor_client, anchors, pa_test_data, assignment_enabled
):
    response = await _verify(
        editor_client,
        pa_test_data["sample_batch_id"],
        batch_peak_id=anchors["assigned"],
        verdict="unsure",
    )
    assert response.status_code == 404, response.text
