"""
Integration tests for supersession on the verify endpoint.

`assignment_verification` is append-only, and every consumer used to re-derive
"the current verdict" as the latest by `verified_utc` within a stable identity.
The frontend did that; `recalibrate_instrument` did not, and fit its curve on
the whole history - so a user who changed their mind contributed one label to
*each* class, at an identical evidence value.

Recording a verdict now stamps `superseded_utc` on the one it replaces, in the
same transaction, and a partial unique index keeps exactly one live row per
identity. These tests cover both halves at the API level: that the history
survives with its score snapshot intact, and that the label pool sees only the
current verdict.

The fixture seeds a sample of its own rather than reusing `pa_test_data`,
because the recalibration endpoint resolves the instrument from the *filename*
(`get_instrument_type` splits on '_' and looks for 'orbi'/'tof'), and the
shared fixture's synthetic name resolves to neither.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import (
    AssignmentVerification,
    Dataset,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


@pytest.fixture
def feature_enabled(monkeypatch):
    """Force the peak assignment feature on - verify and recalibrate are gated."""
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


@pytest_asyncio.fixture
async def supersede_data(async_session_factory, pa_test_data):
    """Seed a tof-resolvable sample with two assignments, and clean up after.

    Function-scoped and self-deleting: the recalibration endpoint gathers
    labels across every sample on the instrument, so a row left behind would
    show up in the counts of any later test that calls it.

    :return: Dict with the sample and assignment ids the tests address.
    """
    now = datetime.now(timezone.utc)
    dataset_id = gen_id()
    sample_batch_id = gen_id()
    sample_file_id = gen_id()
    sample_item_id = gen_id()
    run_id = gen_id()
    m0_assignment_id = gen_id(32)
    other_assignment_id = gen_id(32)

    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=pa_test_data["workspace_id"],
                dataset_name="Verdict Supersede Dataset",
                dataset_utc_created=now,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=sample_batch_id,
                dataset_id=dataset_id,
                sample_batch_name="Verdict Supersede Batch",
                sample_batch_utc_created=now,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=sample_file_id,
                # Leading segment before '_' carries the instrument: 'tof' here,
                # so these labels land in the tof pool and not the shared
                # fixture's (which resolves to no instrument at all).
                filename=f"tof-supersede_{sample_file_id}.zarr",
                instrument="tof-test",
                datetime=datetime(2026, 8, 1, 12, 0, 0),
                datetime_utc=now,
                length=60.0,
                range=[50.0, 500.0],
                polarity="+",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=sample_item_id,
                sample_batch_id=sample_batch_id,
                sample_file_id=sample_file_id,
                sample_item_name="Verdict Supersede Sample",
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
                config={"run_untargeted": True},
                peak_assignment_run_utc_created=now - timedelta(hours=1),
                peak_assignment_run_utc_completed=now - timedelta(minutes=55),
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=m0_assignment_id,
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id="sp-1",
                sample_peak_mz=181.0707,
                sample_peak_intensity=5000.0,
                role="M0",
                assigned_formula="C6H12O6",
                ion_formula="C6H13O6+",
                isotope_label="M0",
                source="database",
                fit_score=0.95,
                mz_error_ppm=1.2,
                abundance_error=0.05,
                tier="assigned",
                # `evidence` is what the fit uses as the score, and the
                # recalibration query skips rows without one.
                provenance={"evidence": 0.87, "p_correct": 0.93},
            )
        )
        session.add(
            PeakAssignment(
                peak_assignment_id=other_assignment_id,
                peak_assignment_run_id=run_id,
                sample_item_id=sample_item_id,
                sample_peak_id="sp-2",
                sample_peak_mz=250.1234,
                sample_peak_intensity=900.0,
                role="M0",
                assigned_formula="C10H16O",
                ion_formula="C10H17O+",
                isotope_label="M0",
                source="database",
                fit_score=0.80,
                mz_error_ppm=2.0,
                abundance_error=0.09,
                tier="assigned",
                provenance={"evidence": 0.55, "p_correct": 0.61},
            )
        )
        await session.commit()

    yield {
        "sample_item_id": sample_item_id,
        "m0_assignment_id": m0_assignment_id,
        "other_assignment_id": other_assignment_id,
    }

    async with async_session_factory() as session:
        await session.execute(
            delete(AssignmentVerification).where(
                AssignmentVerification.sample_item_id == sample_item_id
            )
        )
        await session.commit()


async def _verify(client, sample_item_id, assignment_id, verdict, evidence_level):
    """POST one verdict and return the parsed response body."""
    response = await client.post(
        f"/api/peak-assignments/sample/{sample_item_id}/verify",
        json={
            "peak_assignment_id": assignment_id,
            "verdict": verdict,
            "evidence_level": evidence_level,
        },
    )
    # 201: a verdict that supersedes another is still a new record, not an edit
    # of the one it replaces - which is the whole point of keeping both.
    assert response.status_code == 201, response.text
    return response.json()


async def _verifications(client, sample_item_id):
    """GET the verdict history for a sample, newest first."""
    response = await client.get(
        f"/api/peak-assignments/sample/{sample_item_id}/verifications"
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


# --- Supersession ----------------------------------------------------------


@pytest.mark.asyncio
async def test_first_verdict_is_live(editor_client, supersede_data, feature_enabled):
    """A verdict on an identity nobody has judged is current on arrival."""
    body = await _verify(
        editor_client,
        supersede_data["sample_item_id"],
        supersede_data["m0_assignment_id"],
        "confirmed",
        "reference_standard",
    )
    assert body["data"][0]["superseded_utc"] is None


@pytest.mark.asyncio
async def test_changing_your_mind_supersedes_the_first_verdict(
    editor_client, supersede_data, feature_enabled
):
    """The replaced verdict is stamped and kept; only the new one is live.

    Both rows survive - this is the audit trail, not a correction that erases
    what was said - but exactly one of them answers "what is the verdict now".
    """
    sample_item_id = supersede_data["sample_item_id"]
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["m0_assignment_id"],
        "confirmed",
        "reference_standard",
    )
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["m0_assignment_id"],
        "rejected",
        "msms",
    )

    records = await _verifications(editor_client, sample_item_id)
    assert len(records) == 2
    live = [r for r in records if r["superseded_utc"] is None]
    assert len(live) == 1
    assert live[0]["verdict"] == "rejected"
    superseded = [r for r in records if r["superseded_utc"] is not None]
    assert superseded[0]["verdict"] == "confirmed"


@pytest.mark.asyncio
async def test_the_superseded_row_keeps_its_own_score_snapshot(
    editor_client, supersede_data, feature_enabled
):
    """A retracted verdict keeps the score it was judged against.

    This is why the row is stamped rather than deleted: the pair it carries is
    still a valid observation about the model's calibration at that score, and
    the snapshot is not recoverable once the assignment is re-run.
    """
    sample_item_id = supersede_data["sample_item_id"]
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["m0_assignment_id"],
        "confirmed",
        "reference_standard",
    )
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["m0_assignment_id"],
        "rejected",
        "msms",
    )

    records = await _verifications(editor_client, sample_item_id)
    superseded = next(r for r in records if r["superseded_utc"] is not None)
    assert superseded["evidence"] == 0.87
    assert superseded["p_correct"] == 0.93
    assert superseded["evidence_level"] == "reference_standard"


@pytest.mark.asyncio
async def test_a_third_verdict_supersedes_only_the_second(
    editor_client, supersede_data, feature_enabled
):
    """Supersession chains: each write stamps the live row, not the whole history.

    A row already superseded keeps the timestamp it was given, so the history
    reads as a chain of intervals rather than collapsing onto the latest write.
    """
    sample_item_id = supersede_data["sample_item_id"]
    for verdict in ("confirmed", "rejected", "unsure"):
        await _verify(
            editor_client,
            sample_item_id,
            supersede_data["m0_assignment_id"],
            verdict,
            "visual" if verdict != "confirmed" else "reference_standard",
        )

    records = await _verifications(editor_client, sample_item_id)
    assert len(records) == 3
    by_verdict = {r["verdict"]: r for r in records}
    assert by_verdict["unsure"]["superseded_utc"] is None
    # The first verdict was stamped when the second arrived, so its marker is
    # earlier than the second's - not equal to it.
    assert (
        by_verdict["confirmed"]["superseded_utc"]
        < by_verdict["rejected"]["superseded_utc"]
    )


@pytest.mark.asyncio
async def test_a_different_assignment_keeps_its_own_verdict(
    editor_client, supersede_data, feature_enabled
):
    """Supersession is per identity: judging one peak does not retract another.

    Both assignments here are M0 rows in the same sample with different
    formulas, so a supersede that matched on the sample alone would silently
    retract a verdict the user never revisited.
    """
    sample_item_id = supersede_data["sample_item_id"]
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["m0_assignment_id"],
        "confirmed",
        "reference_standard",
    )
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["other_assignment_id"],
        "rejected",
        "msms",
    )

    records = await _verifications(editor_client, sample_item_id)
    assert len(records) == 2
    assert all(r["superseded_utc"] is None for r in records)


@pytest.mark.asyncio
async def test_only_one_live_row_survives_in_the_database(
    editor_client, supersede_data, feature_enabled, async_session_factory
):
    """The partial unique index holds, checked against the table rather than the API.

    The endpoint could satisfy every assertion above and still have left two
    live rows if the supersede UPDATE matched nothing - the index is what makes
    that unrepresentable, and this reads it directly.
    """
    sample_item_id = supersede_data["sample_item_id"]
    for verdict in ("confirmed", "rejected", "confirmed"):
        await _verify(
            editor_client,
            sample_item_id,
            supersede_data["m0_assignment_id"],
            verdict,
            "reference_standard" if verdict == "confirmed" else "msms",
        )

    async with async_session_factory() as session:
        live = (
            (
                await session.execute(
                    select(AssignmentVerification).where(
                        AssignmentVerification.sample_item_id == sample_item_id,
                        AssignmentVerification.superseded_utc.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(live) == 1
    assert live[0].verdict == "confirmed"


# --- The label pool --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_flipped_verdict_contributes_one_label_not_two(
    editor_client, owner_client, supersede_data, feature_enabled
):
    """Recalibration counts the current verdict once, not the whole history.

    This is the defect the column exists for. Confirm-then-reject on one
    assignment used to add a positive *and* a negative at an identical evidence
    value: a contradictory pair that is noise to a Platt fit, drags held-out
    AUC toward 0.5, and counts toward the minimum-label gates meant to be the
    guardrail. The fit is far below those gates here, so it declines to
    recalibrate - but it reports the pool it gathered either way, which is what
    this asserts.
    """
    sample_item_id = supersede_data["sample_item_id"]
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["m0_assignment_id"],
        "confirmed",
        "reference_standard",
    )
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["m0_assignment_id"],
        "rejected",
        "msms",
    )

    response = await owner_client.post(
        "/api/peak-assignments/calibration/tof/recalibrate"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recalibrated"] is False
    assert body["n_pos"] == 0
    assert body["n_neg"] == 1


@pytest.mark.asyncio
async def test_unsure_still_never_reaches_the_pool(
    editor_client, owner_client, supersede_data, feature_enabled
):
    """'unsure' is not a label, superseded or not.

    A negative control for the filter above: the pool is empty here, so a
    supersede filter that accidentally admitted every row would show up as a
    non-zero count rather than passing quietly.
    """
    sample_item_id = supersede_data["sample_item_id"]
    await _verify(
        editor_client,
        sample_item_id,
        supersede_data["m0_assignment_id"],
        "unsure",
        "visual",
    )

    response = await owner_client.post(
        "/api/peak-assignments/calibration/tof/recalibrate"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["n_pos"] == 0
    assert body["n_neg"] == 0
