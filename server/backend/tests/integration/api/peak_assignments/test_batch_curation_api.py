"""Integration tests for the batch curation: pin, propagate, recompute, release.

Two samples in a batch of their own read the same m/z as two different
formulas, so the anchor's registry holds both identities and the vote picks the
brighter, better-fitting one. Pinning the other on the anchor re-measures it in
the dissenting sample (the seeded scorer stubbed to reach its peak), the anchor
claims it, and a release puts everything back. See ``batch_curation.py``.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.peak_assignments import (
    batch_peaks_routes,
    batch_untargeted,
)
from mascope_backend.api.new.peak_assignments.batch_curation import (
    NOT_CURATED_CODE,
    UNKNOWN_CANDIDATE_CODE,
    run_batch_peak_curation,
)
from mascope_backend.api.new.peak_assignments.batch_peak_verification import (
    CLAIM_CHANGED_CODE,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    manual_pin_of,
    resolve_candidate,
    tier_name,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    fold_sample_into_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    IonizationMechanism,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id


pytestmark = pytest.mark.asyncio

MECH = "mech-h"

# (peak id, mz, formula, ion formula, tier, fit, intensity)
_ROWS = {
    "S1": [("p1", 181.0707, "C6H12O6", "C6H13O6+", "assigned", 0.95, 5000.0)],
    "S2": [("p1", 181.0708, "C7H14O7", "C7H15O7+", "candidate", 0.60, 400.0)],
}


async def _seed_sample(session, batch_id, name, rows, now):
    sample_file_id, sample_item_id, run_id = gen_id(), gen_id(), gen_id()
    session.add(
        SampleFile(
            sample_file_id=sample_file_id,
            filename=f"orbi-batch-curation-{name}-{sample_file_id}.zarr",
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
            sample_item_name=f"Batch curation {name}",
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
                role="M0",
                assigned_formula=formula,
                ion_formula=ion,
                ionization_mechanism_id=MECH,
                source="database",
                fit_score=fit,
                mz_error_ppm=1.0,
                tier=tier,
                provenance={"p_correct": 0.8},
            )
        )
    return sample_item_id


@pytest_asyncio.fixture
async def anchor(async_session_factory, pa_test_data):
    """One anchor with two identities in its registry, the vote on glucose."""
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    async with async_session_factory() as session:
        if await session.get(IonizationMechanism, MECH) is None:
            session.add(
                IonizationMechanism(
                    ionization_mechanism_id=MECH,
                    ionization_mechanism_polarity="+",
                    ionization_mechanism="H+ (batch curation test)",
                )
            )
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
                sample_batch_name=f"Batch curation {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        s1 = await _seed_sample(session, batch_id, "S1", _ROWS["S1"], now)
        s2 = await _seed_sample(session, batch_id, "S2", _ROWS["S2"], now)
        await session.commit()
    assert await fold_sample_into_batch_peaks(s1) == batch_id
    assert await fold_sample_into_batch_peaks(s2) == batch_id
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
        assert len(anchors) == 1, [a.mz for a in anchors]
        bp = anchors[0]
        assert bp.consensus_formula == "C6H12O6"
        registry = list(bp.candidates)
        found = {
            "batch_id": batch_id,
            "s1": s1,
            "s2": s2,
            "batch_peak_id": bp.batch_peak_id,
            "glucose": next(
                i for i, e in enumerate(registry) if e["formula"] == "C6H12O6"
            ),
            "other": next(
                i for i, e in enumerate(registry) if e["formula"] == "C7H14O7"
            ),
        }
    return found


@pytest.fixture
def stubbed_scorer(monkeypatch):
    """The seeded scorer reaches p1 in whichever sample it is measured on, with
    a fit of its own; the match params are canned."""
    calls = []

    async def seeded(sample, seeds, params):
        calls.append((sample.sample_item_id, set(seeds)))
        (formula, mech) = next(iter(seeds))
        return (
            {(formula, mech): "ion-x"},
            {"ion-x": 0.81},
            {("ion-x", "p1"): {"mz_error_ppm": 0.9}},
            pd.DataFrame({"x": [1]}),
        )

    async def match_params(sample_item_id):
        return SimpleNamespace(isotope_abundance_threshold=0.01)

    monkeypatch.setattr(batch_untargeted, "score_seeds", seeded)
    monkeypatch.setattr(batch_untargeted, "default_match_params", match_params)
    return calls


@pytest.fixture
def assignment_enabled(monkeypatch):
    monkeypatch.setenv("MASCOPE_PEAK_ASSIGNMENT", "1")


async def _anchor_row(session_factory, batch_peak_id):
    async with session_factory() as session:
        return await session.get(BatchPeak, batch_peak_id)


async def _members(session_factory, batch_peak_id):
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(BatchPeakOccurrence).where(
                        BatchPeakOccurrence.batch_peak_id == batch_peak_id
                    )
                )
            )
            .scalars()
            .all()
        )
        return {m.sample_item_id: m for m in rows}


async def test_curation_pins_propagates_and_recomputes(
    async_session_factory, anchor, stubbed_scorer
):
    counts = await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
        user_id=7,
    )
    assert counts == {
        "formula": "C7H14O7",
        "samples_measured": 1,
        "members_repointed": 1,
    }
    # Only the dissenting sample was measured: S2 already carried the identity.
    assert [call[0] for call in stubbed_scorer] == [anchor["s1"]]
    assert stubbed_scorer[0][1] == {("C7H14O7", MECH)}

    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    assert bp.consensus_formula == "C7H14O7"
    assert bp.consensus_ion_formula == "C7H15O7+"
    pin = manual_pin_of(bp)
    assert pin["candidate"] == anchor["other"]
    assert pin["user_id"] == 7
    assert pin["previous"]["consensus_formula"] == "C6H12O6"
    # Both members now carry it, so the vote agrees and the support is whole.
    assert bp.provenance["vote_winner"] == "C7H14O7"
    assert bp.support_fraction == pytest.approx(1.0)

    members = await _members(async_session_factory, anchor["batch_peak_id"])
    s1 = members[anchor["s1"]]
    assert resolve_candidate(bp.candidates, s1.candidate)["formula"] == "C7H14O7"
    assert s1.fit_score == pytest.approx(0.81)
    assert tier_name(s1.tier) in ("assigned", "candidate")
    assert s1.p_correct is None  # measured here, not calibrated
    # What S1 read before is archived on the pin, for the release.
    [displaced] = pin["displaced_members"]
    assert displaced["sample_item_id"] == anchor["s1"]
    assert displaced["candidate"] == anchor["glucose"]
    assert displaced["fit_score"] == pytest.approx(0.95)


async def test_a_member_the_measurement_does_not_reach_keeps_its_own(
    async_session_factory, anchor, monkeypatch
):
    async def unreached(sample, seeds, params):
        return {}, {}, {}, pd.DataFrame()

    async def match_params(sample_item_id):
        return SimpleNamespace(isotope_abundance_threshold=0.01)

    monkeypatch.setattr(batch_untargeted, "score_seeds", unreached)
    monkeypatch.setattr(batch_untargeted, "default_match_params", match_params)
    counts = await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )
    assert counts["members_repointed"] == 0
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    # The anchor claims the pin on the one member that carries it; the vote still
    # says glucose, and the record says the two disagree.
    assert bp.consensus_formula == "C7H14O7"
    assert bp.provenance["vote_winner"] == "C6H12O6"
    assert bool(bp.is_ambiguous)
    members = await _members(async_session_factory, anchor["batch_peak_id"])
    s1 = members[anchor["s1"]]
    assert resolve_candidate(bp.candidates, s1.candidate)["formula"] == "C6H12O6"
    assert s1.fit_score == pytest.approx(0.95)


async def test_the_pin_survives_a_refold(async_session_factory, anchor, stubbed_scorer):
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )
    # Folding S1 again restores its engine assignment (glucose) as a member and
    # recomputes the anchor - which honours the pin.
    assert await fold_sample_into_batch_peaks(anchor["s1"]) == anchor["batch_id"]
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    assert bp.consensus_formula == "C7H14O7"
    assert manual_pin_of(bp) is not None
    assert bp.provenance["vote_winner"] == "C6H12O6"


async def test_release_puts_the_members_back_and_lets_the_vote_decide(
    async_session_factory, editor_client, anchor, stubbed_scorer, assignment_enabled
):
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{anchor['batch_id']}/release-curation",
        json={"batch_peak_id": anchor["batch_peak_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == [
        {"restored": 1, "skipped": 0, "formula": "C7H14O7"}
    ]
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    assert manual_pin_of(bp) is None
    assert bp.consensus_formula == "C6H12O6"
    assert "vote_winner" not in bp.provenance
    members = await _members(async_session_factory, anchor["batch_peak_id"])
    s1 = members[anchor["s1"]]
    assert resolve_candidate(bp.candidates, s1.candidate)["formula"] == "C6H12O6"
    assert s1.fit_score == pytest.approx(0.95)
    assert s1.p_correct == pytest.approx(0.8)
    # A second release has nothing to undo.
    again = await editor_client.post(
        f"/api/batch-peaks/batch/{anchor['batch_id']}/release-curation",
        json={"batch_peak_id": anchor["batch_peak_id"]},
    )
    assert again.status_code == 409, again.text
    assert NOT_CURATED_CODE in again.text


async def test_the_ledger_marks_a_curated_anchor(guest_client, anchor, stubbed_scorer):
    before = await guest_client.get(
        f"/api/batch-peaks/batch/{anchor['batch_id']}", params={"min_n_present": 1}
    )
    assert before.json()["data"][0]["curated"] is False
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )
    after = await guest_client.get(
        f"/api/batch-peaks/batch/{anchor['batch_id']}", params={"min_n_present": 1}
    )
    [row] = after.json()["data"]
    assert row["curated"] is True
    assert row["consensus_formula"] == "C7H14O7"


async def test_the_route_checks_the_request_before_launching(
    editor_client, guest_client, anchor, assignment_enabled, monkeypatch
):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "curate_batch_peak", fake_task)
    url = f"/api/batch-peaks/batch/{anchor['batch_id']}/curate"

    unknown = await editor_client.post(
        url, json={"batch_peak_id": anchor["batch_peak_id"], "candidate": 5}
    )
    assert unknown.status_code == 422, unknown.text
    assert UNKNOWN_CANDIDATE_CODE in unknown.text

    moved = await editor_client.post(
        url,
        json={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "expected_formula": "C9H10",
        },
    )
    assert moved.status_code == 409, moved.text
    assert CLAIM_CHANGED_CODE in moved.text

    refused = await guest_client.post(
        url,
        json={"batch_peak_id": anchor["batch_peak_id"], "candidate": anchor["other"]},
    )
    assert refused.status_code == 403, refused.text
    assert launched == []

    accepted = await editor_client.post(
        url,
        json={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "expected_formula": "C6H12O6",
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.headers.get("process-id")
    assert "C7H14O7" in accepted.json()["message"]
    assert len(launched) == 1
    assert launched[0]["candidate"] == anchor["other"]
    assert launched[0]["expected_formula"] == "C6H12O6"


async def test_a_release_on_an_uncurated_anchor_is_refused(
    editor_client, anchor, assignment_enabled
):
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{anchor['batch_id']}/release-curation",
        json={"batch_peak_id": anchor["batch_peak_id"]},
    )
    assert response.status_code == 409, response.text
    assert NOT_CURATED_CODE in response.text
