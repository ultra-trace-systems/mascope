"""Integration tests for the batch curation: pin, propagate, recompute, release.

Two samples in a batch of their own read the same m/z as two different
formulas, so the anchor's registry holds both identities and the vote picks the
brighter, better-fitting one. Pinning the other on the anchor re-measures it in
the dissenting sample (the seeded scorer stubbed to reach its peak), the anchor
claims it, and a release puts everything back. An identity that is an isotope
line of an ion whose monoisotopic line is another batch peak is refused, with
that batch peak named. See ``batch_curation.py``.

Each write to the live ledger, the curation's steps among them, moves the
current run's ``ledger_utc_modified``: the stamp a client's copy of the ledger
is compared by, since none of these writes makes a run.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import select, update
from stage_stubs import (  # the tests root, on sys.path
    frame,
    mechanism,
    stub_sample_reads,
    stub_seeded_scorer,
)
from test_utils import captured_logs

from mascope_backend.api.new.peak_assignments import (
    batch_curation,
    batch_peaks_routes,
    batch_staleness,
    batch_untargeted,
    model_readers,
    pipeline,
    sample_inputs,
)
from mascope_backend.api.new.peak_assignments.batch_coherence import (
    _write_records as _write_co_variation_records,
)
from mascope_backend.api.new.peak_assignments.batch_curation import (
    ISOTOPE_LINE_CODE,
    NOT_CURATED_CODE,
    UNKNOWN_CANDIDATE_CODE,
    IsotopeLineException,
    _archive_displaced,
    _pin,
    curation_outcome,
    release_batch_peak_curation,
    run_batch_peak_curation,
)
from mascope_backend.api.new.peak_assignments.batch_import import write_engine_verdicts
from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
    _write_records as _write_stamping_records,
)
from mascope_backend.api.new.peak_assignments.batch_peak_verification import (
    CLAIM_CHANGED_CODE,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    manual_pin_of,
    member_state,
    resolve_candidate,
    tier_name,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    clear_engine_verdicts,
    fold_sample_into_batch_peaks,
    recompute_batch_consensus,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_SEARCH,
    complete_run,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_series import _write_records
from mascope_backend.api.new.peak_assignments.batch_stages import (
    begin_stage,
    finish_stage,
    stage_standing,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import BatchStage
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    Annotation,
    Reading,
    _apply_search_rows,
    _propagate_to_sample,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    SampleMassAccuracy,
    evidence_for,
    tier_for_evidence,
)
from mascope_backend.api.new.peak_assignments.tiering import DETECT_DEFAULT, LineGate
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    BatchPeakRun,
    IonizationMechanism,
    IonizationMode,
    PeakAssignment,
    PeakAssignmentRun,
    SampleBatch,
    SampleFile,
    SampleItem,
)
from mascope_backend.db.id import gen_id
from mascope_tools.composition.envelope_tolerance import (
    FORM_CENTRE_WIDTH,
    LEVEL_OWN,
)
from mascope_tools.composition.heuristic_filter import formula_plausibility
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM


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


def _stub_reads(monkeypatch):
    """What the stage list reads of each sample: its one peak, its mode's
    mechanism and canned match params (``stage_stubs``)."""

    async def match_params(sample_item_id):
        return SimpleNamespace(isotope_abundance_threshold=0.01, mz_tolerance=5.0)

    stub_sample_reads(
        monkeypatch,
        lambda sample_item_id: frame([("p1", 181.0707, 5000.0)]),
        [mechanism("[M+H]+", MECH)],
    )
    monkeypatch.setattr(pipeline, "default_match_params", match_params)


@pytest.fixture
def stubbed_scorer(monkeypatch):
    """The seeded scorer reaches p1 in whichever sample it is measured on, with
    a fit of its own; the match params are canned."""
    calls = []

    async def seeded(sample, seeds, params, *, config=None):
        calls.append((sample.sample_item_id, set(seeds)))
        (formula, mech) = next(iter(seeds))
        return (
            {(formula, mech): "ion-x"},
            {"ion-x": 0.81},
            {("ion-x", "p1"): {"mz_error_ppm": 0.9}},
            pd.DataFrame({"x": [1]}),
        )

    _stub_reads(monkeypatch)
    stub_seeded_scorer(
        monkeypatch,
        seeded,
        lambda sample_item_id: frame([("p1", 181.0707, 5000.0)]),
    )
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
        "samples_failed": 0,
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
    async def unreached(sample, seeds, params, **kwargs):
        return {}, {}, {}, pd.DataFrame()

    _stub_reads(monkeypatch)
    monkeypatch.setattr(pipeline, "score_seeds", unreached)
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


async def test_a_sample_that_cannot_be_measured_keeps_its_identity_and_the_pin_stands(
    async_session_factory, anchor, stubbed_scorer, monkeypatch
):
    """The pin is written before the walk. A sample whose measurement raises is
    counted, skipped and keeps what it read before; the other dissenting sample
    is re-pointed; the archive on the pin names exactly the member that moved,
    so a release restores exactly it; and the consensus is recomputed."""
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        s3 = await _seed_sample(
            session,
            anchor["batch_id"],
            "S3",
            [("p1", 181.0707, "C6H12O6", "C6H13O6+", "assigned", 0.90, 3000.0)],
            now,
        )
        await session.commit()
    assert await fold_sample_into_batch_peaks(s3) == anchor["batch_id"]
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    other = next(i for i, e in enumerate(bp.candidates) if e["formula"] == "C7H14O7")

    scorer = pipeline.score_seeds  # the fixture's canned pairing

    async def failing(sample, seeds, params, **kwargs):
        if sample.sample_item_id == anchor["s1"]:
            raise FileNotFoundError("S1's peak file has moved")
        return await scorer(sample, seeds, params, **kwargs)

    monkeypatch.setattr(pipeline, "score_seeds", failing)

    counts = await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        other,
        "C6H12O6",
        PeakAssignmentConfig(),
        user_id=7,
    )

    assert counts == {
        "formula": "C7H14O7",
        "samples_measured": 1,
        "members_repointed": 1,
        "samples_failed": 1,
    }
    # S2 already carried the identity; S1 raised before its stub could answer.
    assert [call[0] for call in stubbed_scorer] == [s3]

    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    pin = manual_pin_of(bp)
    assert pin["candidate"] == other
    assert bp.consensus_formula == "C7H14O7"
    assert [state["sample_item_id"] for state in pin["displaced_members"]] == [s3]
    members = await _members(async_session_factory, anchor["batch_peak_id"])
    assert (
        resolve_candidate(bp.candidates, members[anchor["s1"]].candidate)["formula"]
        == "C6H12O6"
    )
    assert (
        resolve_candidate(bp.candidates, members[s3].candidate)["formula"] == "C7H14O7"
    )
    outcome = curation_outcome(counts, anchor["batch_id"])
    assert outcome["status"] == "partial"
    assert (
        "1 sample could not be measured and keeps its previous identity."
        in outcome["message"]
    )


# --- measured before it is pinned (step 3.4c) ------------------------------------


def _measure_url(anchor):
    return f"/api/batch-peaks/batch/{anchor['batch_id']}/measure-candidate"


async def test_an_alternative_is_measured_on_the_sample_and_nothing_is_written(
    async_session_factory, guest_client, anchor, stubbed_scorer, assignment_enabled
):
    response = await guest_client.get(
        _measure_url(anchor),
        params={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "sample_item_id": anchor["s1"],
        },
    )
    assert response.status_code == 200, response.text
    [entry] = response.json()["data"]
    evidence = evidence_for(0.81, "C7H14O7")
    assert entry["assigned_formula"] == "C7H14O7"
    assert entry["sample_peak_id"] == "p1"
    assert entry["fit_score"] == pytest.approx(0.81)
    assert entry["evidence"] == pytest.approx(evidence)
    assert entry["plausibility"] == pytest.approx(
        formula_plausibility("C7H14O7"), abs=1e-4
    )
    assert entry["tier"] == tier_for_evidence(
        evidence,
        candidate_threshold=PeakAssignmentConfig().candidate_threshold,
        assigned_threshold=PeakAssignmentConfig().assigned_threshold,
    )
    assert entry["mz_error_ppm"] == pytest.approx(0.9)
    assert entry["takes_member"] is True
    assert entry["blocked_reason"] is None
    # Beside it, what S1 reads now.
    assert entry["current"]["assigned_formula"] == "C6H12O6"
    assert entry["current"]["fit_score"] == pytest.approx(0.95)
    assert entry["current"]["tier"] == "assigned"
    assert entry["current"]["evidence"] == pytest.approx(evidence_for(0.95, "C6H12O6"))
    # Measured on S1 alone, and nothing moved.
    assert stubbed_scorer == [(anchor["s1"], {("C7H14O7", MECH)})]
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    assert manual_pin_of(bp) is None
    assert bp.consensus_formula == "C6H12O6"
    members = await _members(async_session_factory, anchor["batch_peak_id"])
    s1 = members[anchor["s1"]]
    assert resolve_candidate(bp.candidates, s1.candidate)["formula"] == "C6H12O6"
    assert s1.fit_score == pytest.approx(0.95)


async def test_the_pin_writes_what_was_measured_and_records_it(
    async_session_factory, guest_client, anchor, stubbed_scorer, assignment_enabled
):
    measured = (
        await guest_client.get(
            _measure_url(anchor),
            params={
                "batch_peak_id": anchor["batch_peak_id"],
                "candidate": anchor["other"],
                "sample_item_id": anchor["s1"],
            },
        )
    ).json()["data"][0]
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
        measured_sample_item_id=anchor["s1"],
    )
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    members = await _members(async_session_factory, anchor["batch_peak_id"])
    s1 = members[anchor["s1"]]
    # The member reads exactly what the preview said it would.
    assert s1.fit_score == pytest.approx(measured["fit_score"])
    assert tier_name(s1.tier) == measured["tier"]
    # And the pin keeps the measurement it was made on.
    record = manual_pin_of(bp)["measured"]
    assert record["sample_item_id"] == anchor["s1"]
    assert record["candidate"] == anchor["other"]
    assert record["fit_score"] == pytest.approx(measured["fit_score"])
    assert record["evidence"] == pytest.approx(measured["evidence"])
    assert record["tier"] == measured["tier"]
    assert record["plausibility"] == pytest.approx(measured["plausibility"])
    assert record["current"]["assigned_formula"] == "C6H12O6"


async def test_the_pin_records_the_measurement_under_its_own_configuration(
    async_session_factory, anchor, stubbed_scorer
):
    """A pin measured under a configuration with other bands records the tier
    those bands give, the one it writes on the member."""
    strict = PeakAssignmentConfig(candidate_threshold=0.95, assigned_threshold=0.99)
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        strict,
        measured_sample_item_id=anchor["s1"],
    )
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    members = await _members(async_session_factory, anchor["batch_peak_id"])
    assert tier_name(members[anchor["s1"]].tier) == "below_assignability"
    assert manual_pin_of(bp)["measured"]["tier"] == "below_assignability"


async def test_the_measurement_and_the_pin_score_under_the_pins_configuration(
    anchor, stubbed_scorer, monkeypatch
):
    """The configuration resolves the chemistry the seeded scoring is centred
    at (the reagent lines its profile claims), so the preview and the
    propagation hand it on alike: both run the person's-choice stages under
    the pin's configuration."""
    run_stages = pipeline.run_stages
    configs = []

    async def recording(ctx):
        configs.append((ctx.path, ctx.config))
        return await run_stages(ctx)

    monkeypatch.setattr(batch_untargeted, "run_stages", recording)
    strict = PeakAssignmentConfig(candidate_threshold=0.95, assigned_threshold=0.99)
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        strict,
        measured_sample_item_id=anchor["s1"],
    )
    # One for the measurement on S1, one for the propagation to it.
    assert len(configs) == 2
    assert all(
        path is pipeline.PERSONS_CHOICE and config is strict for path, config in configs
    )


async def test_a_pin_without_a_measured_sample_records_none(
    async_session_factory, anchor, stubbed_scorer
):
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    assert "measured" not in manual_pin_of(bp)


async def test_a_measurement_that_fails_does_not_cost_the_pin(
    async_session_factory, anchor, stubbed_scorer, monkeypatch
):
    async def broken(*args, **kwargs):
        raise RuntimeError("the preview broke")

    monkeypatch.setattr(batch_curation, "measure_on_sample", broken)
    counts = await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
        measured_sample_item_id=anchor["s1"],
    )
    assert counts["members_repointed"] == 1
    bp = await _anchor_row(async_session_factory, anchor["batch_peak_id"])
    pin = manual_pin_of(bp)
    assert pin["candidate"] == anchor["other"]
    assert "measured" not in pin


async def test_an_alternative_the_envelope_does_not_reach_says_the_sample_keeps_its_own(
    guest_client, anchor, assignment_enabled, monkeypatch
):
    async def unreached(sample, seeds, params, **kwargs):
        (formula, mech) = next(iter(seeds))
        # The ion is built, its envelope pairs to some other peak.
        return (
            {(formula, mech): "ion-x"},
            {"ion-x": 0.7},
            {("ion-x", "p9"): {"mz_error_ppm": 3.0}},
            pd.DataFrame(),
        )

    _stub_reads(monkeypatch)
    monkeypatch.setattr(pipeline, "score_seeds", unreached)
    response = await guest_client.get(
        _measure_url(anchor),
        params={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "sample_item_id": anchor["s1"],
        },
    )
    assert response.status_code == 200, response.text
    [entry] = response.json()["data"]
    assert entry["takes_member"] is False
    assert entry["fit_score"] is None
    assert entry["tier"] is None
    assert entry["mz_error_ppm"] is None
    assert "does not reach this sample's peak" in entry["blocked_reason"]
    assert entry["current"]["assigned_formula"] == "C6H12O6"


async def test_an_identity_the_sample_already_reads_is_not_measured_again(
    guest_client, anchor, stubbed_scorer, assignment_enabled
):
    response = await guest_client.get(
        _measure_url(anchor),
        params={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "sample_item_id": anchor["s2"],
        },
    )
    assert response.status_code == 200, response.text
    [entry] = response.json()["data"]
    assert entry["takes_member"] is False
    assert entry["blocked_reason"] == "this sample already reads this identity"
    assert entry["fit_score"] == pytest.approx(0.60)
    assert entry["tier"] == "candidate"
    assert stubbed_scorer == []


async def test_an_identity_with_no_adduct_is_not_measured(
    async_session_factory, guest_client, anchor, stubbed_scorer, assignment_enabled
):
    async with async_session_factory() as session:
        bp = await session.get(BatchPeak, anchor["batch_peak_id"])
        registry = [dict(entry) for entry in bp.candidates]
        registry[anchor["other"]]["ionization_mechanism_id"] = None
        bp.candidates = registry
        await session.commit()
    response = await guest_client.get(
        _measure_url(anchor),
        params={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "sample_item_id": anchor["s1"],
        },
    )
    assert response.status_code == 200, response.text
    [entry] = response.json()["data"]
    assert entry["takes_member"] is False
    assert "names no adduct" in entry["blocked_reason"]
    assert stubbed_scorer == []


async def test_the_measurement_refuses_what_the_pin_would(
    guest_client, anchor, stubbed_scorer, assignment_enabled, monkeypatch
):
    url = _measure_url(anchor)
    unknown = await guest_client.get(
        url,
        params={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": 5,
            "sample_item_id": anchor["s1"],
        },
    )
    assert unknown.status_code == 422, unknown.text
    assert UNKNOWN_CANDIDATE_CODE in unknown.text

    elsewhere = await guest_client.get(
        url,
        params={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "sample_item_id": "not-a-member",
        },
    )
    assert elsewhere.status_code in (403, 404), elsewhere.text

    monkeypatch.delenv("MASCOPE_PEAK_ASSIGNMENT")
    dark = await guest_client.get(
        url,
        params={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "sample_item_id": anchor["s1"],
        },
    )
    assert dark.status_code == 403, dark.text
    assert "Peak assignment is disabled" in dark.text
    assert stubbed_scorer == []


async def test_a_sample_of_the_batch_without_a_member_is_a_404(
    async_session_factory, guest_client, anchor, stubbed_scorer, assignment_enabled
):
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        s3 = await _seed_sample(session, anchor["batch_id"], "S3", [], now)
        await session.commit()
    response = await guest_client.get(
        _measure_url(anchor),
        params={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "sample_item_id": s3,
        },
    )
    assert response.status_code == 404, response.text
    assert "holds no member" in response.text


async def test_the_curate_route_hands_the_measured_sample_to_the_task(
    editor_client, anchor, assignment_enabled, monkeypatch
):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "curate_batch_peak", fake_task)
    accepted = await editor_client.post(
        f"/api/batch-peaks/batch/{anchor['batch_id']}/curate",
        json={
            "batch_peak_id": anchor["batch_peak_id"],
            "candidate": anchor["other"],
            "expected_formula": "C6H12O6",
            "sample_item_id": anchor["s1"],
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert launched[0]["measured_sample_item_id"] == anchor["s1"]


async def test_the_curate_task_passes_the_measured_sample_on(anchor, monkeypatch):
    seen = {}

    async def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return {
            "formula": "C7H14O7",
            "samples_measured": 0,
            "members_repointed": 0,
            "samples_failed": 0,
        }

    monkeypatch.setattr(batch_curation, "run_batch_peak_curation", fake_run)
    await batch_curation.curate_batch_peak(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        measured_sample_item_id=anchor["s1"],
    )
    assert seen["measured_sample_item_id"] == anchor["s1"]


# --- an isotope line's identity ------------------------------------------------
#
# One run reads glucose's M0 at 181.0707 and its 13C line at 182.0741, so the
# batch holds two batch peaks and the line's registry holds the line's own
# identity, C6H12O6 as [13C]C5H13O6+. Its mechanism is a real [M+H]+, so the
# ion can be generated and its monoisotopic line found.


async def _proton_mechanism_id(session) -> str:
    existing = (
        await session.execute(
            select(IonizationMechanism).where(
                IonizationMechanism.ionization_mechanism == "[M+H]+"
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.ionization_mechanism_id
    mechanism_id = gen_id()
    session.add(
        IonizationMechanism(
            ionization_mechanism_id=mechanism_id,
            ionization_mechanism_polarity="+",
            ionization_mechanism="[M+H]+",
        )
    )
    await session.flush()
    return mechanism_id


@pytest_asyncio.fixture
async def line_batch(async_session_factory, pa_test_data):
    now = datetime.now(timezone.utc)
    batch_id = gen_id()
    async with async_session_factory() as session:
        mechanism_id = await _proton_mechanism_id(session)
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
                sample_batch_name=f"Batch curation line {batch_id}",
                sample_batch_utc_created=now,
            )
        )
        sample_item_id = await _seed_sample(session, batch_id, "L1", [], now)
        await session.flush()
        run_id = (
            await session.execute(
                select(PeakAssignmentRun.peak_assignment_run_id).where(
                    PeakAssignmentRun.sample_item_id == sample_item_id
                )
            )
        ).scalar_one()
        m0_id = gen_id(32)
        for row_id, pid, mz, role, ion, owner in (
            (m0_id, "p1", 181.0707, "M0", "C6H13O6+", None),
            (gen_id(32), "p2", 182.0741, "iso_child", "[13C]C5H13O6+", m0_id),
        ):
            session.add(
                PeakAssignment(
                    peak_assignment_id=row_id,
                    peak_assignment_run_id=run_id,
                    sample_item_id=sample_item_id,
                    sample_peak_id=pid,
                    sample_peak_mz=mz,
                    sample_peak_intensity=9000.0 if role == "M0" else 600.0,
                    role=role,
                    assigned_formula="C6H12O6",
                    ion_formula=ion,
                    ionization_mechanism_id=mechanism_id,
                    source="untargeted",
                    fit_score=0.95,
                    mz_error_ppm=0.4,
                    tier="assigned",
                    owner_peak_assignment_id=owner,
                    provenance={},
                )
            )
        await session.commit()
    assert await fold_sample_into_batch_peaks(sample_item_id) == batch_id
    async with async_session_factory() as session:
        anchors = {
            round(peak.mz): peak
            for peak in (
                await session.execute(
                    select(BatchPeak).where(BatchPeak.sample_batch_id == batch_id)
                )
            )
            .scalars()
            .all()
        }
    m0, line = anchors[181], anchors[182]
    assert line.isotopologue_of == m0.batch_peak_id
    return {
        "batch_id": batch_id,
        "m0": m0,
        "line": line,
        "m0_entry": next(
            i for i, e in enumerate(m0.candidates) if e["ion_formula"] == "C6H13O6+"
        ),
        "line_entry": next(
            i
            for i, e in enumerate(line.candidates)
            if e["ion_formula"] == "[13C]C5H13O6+"
        ),
    }


async def test_an_isotope_lines_identity_is_refused_naming_its_monoisotopic_peak(
    editor_client, line_batch, assignment_enabled, monkeypatch
):
    launched = []

    async def fake_task(**kwargs):
        launched.append(kwargs)

    monkeypatch.setattr(batch_peaks_routes, "curate_batch_peak", fake_task)
    url = f"/api/batch-peaks/batch/{line_batch['batch_id']}/curate"
    m0 = line_batch["m0"]

    refused = await editor_client.post(
        url,
        json={
            "batch_peak_id": line_batch["line"].batch_peak_id,
            "candidate": line_batch["line_entry"],
            "expected_formula": "C6H12O6",
        },
    )
    assert refused.status_code == 422, refused.text
    assert ISOTOPE_LINE_CODE in refused.text
    assert f"the batch peak at m/z {m0.mz:.4f} ('{m0.batch_peak_id}')" in refused.text
    assert launched == []

    # The same identity on its monoisotopic line is pinned as ever.
    accepted = await editor_client.post(
        url,
        json={
            "batch_peak_id": m0.batch_peak_id,
            "candidate": line_batch["m0_entry"],
            "expected_formula": "C6H12O6",
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert len(launched) == 1


async def test_the_task_refuses_an_isotope_line_and_writes_no_pin(
    async_session_factory, line_batch, stubbed_scorer
):
    """The task checks again under the fold lock, as it checks the claim: the
    registry may have moved since the route's check."""
    line = line_batch["line"]
    with pytest.raises(IsotopeLineException):
        await run_batch_peak_curation(
            line_batch["batch_id"],
            line.batch_peak_id,
            line_batch["line_entry"],
            "C6H12O6",
            PeakAssignmentConfig(),
        )
    assert stubbed_scorer == []
    after = await _anchor_row(async_session_factory, line.batch_peak_id)
    assert manual_pin_of(after) is None


async def test_the_measurement_says_an_isotope_lines_identity_is_refused(
    async_session_factory, guest_client, line_batch, stubbed_scorer, assignment_enabled
):
    line = line_batch["line"]
    [sample_item_id] = list(
        (await _members(async_session_factory, line.batch_peak_id)).keys()
    )
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{line_batch['batch_id']}/measure-candidate",
        params={
            "batch_peak_id": line.batch_peak_id,
            "candidate": line_batch["line_entry"],
            "sample_item_id": sample_item_id,
        },
    )
    assert response.status_code == 200, response.text
    [entry] = response.json()["data"]
    assert entry["takes_member"] is False
    m0 = line_batch["m0"]
    assert f"('{m0.batch_peak_id}')" in entry["blocked_reason"]
    assert stubbed_scorer == []


async def test_a_batch_peak_of_another_mode_is_not_the_lines_parent(
    async_session_factory, editor_client, line_batch, assignment_enabled
):
    """Batch peaks are kept per ionization mode. One of another mode at the
    monoisotopic m/z - nearer it than the line's own M0 - is not the ion's."""
    m0 = line_batch["m0"]
    async with async_session_factory() as session:
        mode_id = gen_id()
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Batch curation other mode {mode_id}",
                ionization_mode_polarity="+",
                ionization_mechanism_ids=[],
            )
        )
        await session.flush()
        session.add(
            BatchPeak(
                batch_peak_id=gen_id(),
                sample_batch_id=line_batch["batch_id"],
                ionization_mode_id=mode_id,
                mz=181.07066,
                mz_tol_ppm=m0.mz_tol_ppm,
                n_present=1,
            )
        )
        await session.commit()

    refused = await editor_client.post(
        f"/api/batch-peaks/batch/{line_batch['batch_id']}/curate",
        json={
            "batch_peak_id": line_batch["line"].batch_peak_id,
            "candidate": line_batch["line_entry"],
        },
    )
    assert refused.status_code == 422, refused.text
    assert f"('{m0.batch_peak_id}')" in refused.text


async def test_a_batch_peak_of_another_instrument_type_is_not_the_lines_parent(
    async_session_factory, editor_client, line_batch, assignment_enabled
):
    """Batch peaks are kept per instrument type as well. One of the batch's
    other instrument at the monoisotopic m/z, in the line's own mode and nearer
    the m/z than its M0, is another trace and not the ion's."""
    m0 = line_batch["m0"]
    other_unit = (
        "sum_peak_areas"
        if m0.intensity_variable == "sum_peak_heights"
        else "sum_peak_heights"
    )
    async with async_session_factory() as session:
        session.add(
            BatchPeak(
                batch_peak_id=gen_id(),
                sample_batch_id=line_batch["batch_id"],
                ionization_mode_id=m0.ionization_mode_id,
                mz=181.07066,
                mz_tol_ppm=m0.mz_tol_ppm,
                intensity_variable=other_unit,
                n_present=1,
            )
        )
        await session.commit()

    refused = await editor_client.post(
        f"/api/batch-peaks/batch/{line_batch['batch_id']}/curate",
        json={
            "batch_peak_id": line_batch["line"].batch_peak_id,
            "candidate": line_batch["line_entry"],
        },
    )
    assert refused.status_code == 422, refused.text
    assert f"('{m0.batch_peak_id}')" in refused.text


# --- the ledger stamp: every write to the live ledger moves it -------------------

#: Older than any write a test makes, so a stamp past it is one the write moved.
_LONG_AGO = datetime(2000, 1, 1, tzinfo=timezone.utc)


async def _set_stamp(session_factory, batch_id, when=_LONG_AGO):
    async with session_factory() as session:
        await session.execute(
            update(BatchPeakRun)
            .where(BatchPeakRun.sample_batch_id == batch_id)
            .values(ledger_utc_modified=when)
        )
        await session.commit()


async def _stamps(session_factory, batch_id):
    """Each run's stamp, by whether it is the current run."""
    async with session_factory() as session:
        runs = (
            await session.execute(
                select(BatchPeakRun.is_current, BatchPeakRun.ledger_utc_modified).where(
                    BatchPeakRun.sample_batch_id == batch_id
                )
            )
        ).all()
    return {bool(current): stamp for current, stamp in runs}


async def _moved(session_factory, batch_id):
    stamp = (await _stamps(session_factory, batch_id))[True]
    return stamp is not None and stamp > _LONG_AGO


async def test_the_runs_listing_serves_the_stamp_the_first_fold_set(
    async_session_factory, guest_client, anchor
):
    response = await guest_client.get(
        f"/api/batch-peaks/batch/{anchor['batch_id']}/runs"
    )
    assert response.status_code == 200, response.text
    [run] = response.json()["data"]
    stamp = (await _stamps(async_session_factory, anchor["batch_id"]))[True]
    assert stamp is not None
    assert datetime.fromisoformat(run["ledger_utc_modified"]) == stamp


async def test_a_sample_folded_again_moves_the_stamp(async_session_factory, anchor):
    await _set_stamp(async_session_factory, anchor["batch_id"])
    assert await fold_sample_into_batch_peaks(anchor["s1"]) == anchor["batch_id"]
    assert await _moved(async_session_factory, anchor["batch_id"])


async def test_a_deferred_consensus_recompute_moves_the_stamp(
    async_session_factory, anchor
):
    await _set_stamp(async_session_factory, anchor["batch_id"])
    await recompute_batch_consensus(anchor["batch_id"], {anchor["batch_peak_id"]})
    assert await _moved(async_session_factory, anchor["batch_id"])


@pytest.mark.parametrize("writer", ["clear", "import"])
async def test_an_engines_verdicts_cleared_or_written_move_the_stamp(
    async_session_factory, anchor, writer
):
    await _set_stamp(async_session_factory, anchor["batch_id"])
    if writer == "clear":
        await clear_engine_verdicts(anchor["batch_id"])
    else:
        await write_engine_verdicts(anchor["batch_id"], {})
    assert await _moved(async_session_factory, anchor["batch_id"])


async def test_each_step_of_a_curation_moves_the_stamp(
    async_session_factory, anchor, stubbed_scorer, monkeypatch
):
    """The pin, the archive of what the members read, and the release, each in
    a transaction of its own; the release's recompute is left out, so the
    release's own write is the one seen."""
    batch_id, batch_peak_id = anchor["batch_id"], anchor["batch_peak_id"]
    await _set_stamp(async_session_factory, batch_id)
    await _pin(batch_id, batch_peak_id, anchor["other"], "C6H12O6", None)
    assert await _moved(async_session_factory, batch_id)

    members = await _members(async_session_factory, batch_peak_id)
    await _set_stamp(async_session_factory, batch_id)
    await _archive_displaced(
        batch_id, batch_peak_id, [member_state(members[anchor["s1"]])]
    )
    assert await _moved(async_session_factory, batch_id)

    async def no_recompute(sample_batch_id, batch_peak_ids):
        return 0

    monkeypatch.setattr(batch_curation, "recompute_batch_consensus", no_recompute)
    await _set_stamp(async_session_factory, batch_id)
    await release_batch_peak_curation(batch_id, batch_peak_id)
    assert await _moved(async_session_factory, batch_id)


async def test_a_series_detections_records_move_the_stamp(
    async_session_factory, anchor
):
    await _set_stamp(async_session_factory, anchor["batch_id"])
    touched = await _write_records(
        anchor["batch_id"], {anchor["batch_peak_id"]: {"homologues": 2}}
    )
    assert touched == {anchor["batch_peak_id"]}
    assert await _moved(async_session_factory, anchor["batch_id"])


async def test_a_co_variation_readings_records_move_the_stamp(
    async_session_factory, anchor
):
    await _set_stamp(async_session_factory, anchor["batch_id"])
    await _write_co_variation_records(
        anchor["batch_id"], {anchor["batch_peak_id"]: {"verdict": "co_varying"}}
    )
    assert await _moved(async_session_factory, anchor["batch_id"])


async def test_an_isotopologue_stampings_records_move_the_stamp(
    async_session_factory, anchor
):
    await _set_stamp(async_session_factory, anchor["batch_id"])
    await _write_stamping_records(
        anchor["batch_id"],
        {anchor["batch_peak_id"]: {"formula": "C6H12O6", "predicted": []}},
        {},
    )
    assert await _moved(async_session_factory, anchor["batch_id"])


async def test_a_measured_identity_written_onto_a_sample_moves_the_stamp(
    async_session_factory, anchor, stubbed_scorer
):
    """What the curation and the search both write onto another sample's
    members."""
    await _set_stamp(async_session_factory, anchor["batch_id"])
    assigned = await _propagate_to_sample(
        anchor["batch_id"],
        anchor["s1"],
        {anchor["batch_peak_id"]: Annotation("C7H14O7", "C7H15O7+", MECH, "M0", None)},
        PeakAssignmentConfig(),
        only_unassigned=False,
    )
    assert assigned == 1
    assert await _moved(async_session_factory, anchor["batch_id"])


async def test_a_searchs_rows_move_the_stamp_and_the_completed_run_holds_it(
    async_session_factory, anchor
):
    """The search writes while its run is open, onto the ledger the current
    run names; the run that completes takes the stamp of its own."""
    batch_id = anchor["batch_id"]
    run_id = await start_run(batch_id, ACTION_SEARCH)
    await _set_stamp(async_session_factory, batch_id)
    row = {
        "sample_peak_id": "p1",
        "assigned_formula": "C7H14O7",
        "ion_formula": "C7H15O7+",
        "ionization_mechanism_id": MECH,
        "tier": "candidate",
        "fit_score": 0.7,
        "role": "M0",
    }
    written = await _apply_search_rows(
        batch_id,
        anchor["s1"],
        [Reading(anchor["batch_peak_id"], anchor["s1"], row, None)],
    )
    assert written == 1
    assert await _moved(async_session_factory, batch_id)

    await complete_run(batch_id, run_id)
    async with async_session_factory() as session:
        run = await session.get(BatchPeakRun, run_id)
    assert run.is_current == 1
    assert run.ledger_utc_modified == run.batch_peak_run_utc_completed


async def test_a_pin_is_asked_the_spectrums_rules_as_a_hand_assignment_is(
    async_session_factory, anchor, stubbed_scorer, monkeypatch
):
    """A person's choice, on the batch as on a sample's row: the stage list's
    PERSONS_CHOICE path. S1's run asked its rows' lines, and the pinned
    formula's lines are not in S1's spectrum, so the pin holds S1's member at
    candidate, where its evidence alone bands it assigned - as a hand
    assignment of it on S1's row is held (``test_manual_curation``)."""
    from mascope_backend.api.new.peak_assignments.tiering import (
        DETECT_DEFAULT,
        LineGate,
    )

    gate = LineGate(5.0, 0.01, 3.0, DETECT_DEFAULT, 0.1, False)
    async with async_session_factory() as session:
        run = (
            await session.execute(
                select(PeakAssignmentRun).where(
                    PeakAssignmentRun.sample_item_id == anchor["s1"]
                )
            )
        ).scalar_one()
        run.config = {
            **(run.config or {}),
            "tiering": {
                "version": 11,
                "absent_line": {"spectrum": True, **gate.record()},
            },
        }
        await session.commit()
    passes = []
    run_stages = pipeline.run_stages

    async def recording(ctx):
        passes.append(ctx)
        return await run_stages(ctx)

    monkeypatch.setattr(batch_untargeted, "run_stages", recording)
    # A noise estimate, so the ion's 13C line is one the spectrum would show,
    # and a peak above it, so its m/z is inside what the spectrum reads.
    monkeypatch.setattr(
        sample_inputs,
        "load_sample_peaks",
        lambda sample: frame([("p1", 181.0707, 5000.0), ("p9", 250.0, 50.0)]).assign(
            signal_to_noise=100.0
        ),
    )
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )

    (ctx,) = passes
    assert ctx.path is pipeline.PERSONS_CHOICE
    assert ctx.ran == list(pipeline.STAGES)
    (row,) = [row for row in ctx.judged.rows if row["sample_peak_id"] == "p1"]
    assert [reason["rule"] for reason in row["provenance"]["tier_reasons"]] == [
        "absent_line"
    ]
    members = await _members(async_session_factory, anchor["batch_peak_id"])
    assert tier_name(members[anchor["s1"]].tier) == "candidate"


def _record_passes(monkeypatch):
    """The stage-list passes the batch's measurements run, in order."""
    passes = []
    run_stages = pipeline.run_stages

    async def recording(ctx):
        passes.append(ctx)
        return await run_stages(ctx)

    monkeypatch.setattr(batch_untargeted, "run_stages", recording)
    # A noise estimate, so the ion's 13C line is one the spectrum would show,
    # and a peak above it, so its m/z is inside what the spectrum reads.
    monkeypatch.setattr(
        sample_inputs,
        "load_sample_peaks",
        lambda sample: frame([("p1", 181.0707, 5000.0), ("p9", 250.0, 50.0)]).assign(
            signal_to_noise=100.0
        ),
    )
    return passes


async def _no_run_on(session_factory, sample_item_id):
    """Leave the sample with no completed in-app run, as one the ingest folded
    straight into the batch ledger is."""
    async with session_factory() as session:
        await session.execute(
            update(PeakAssignmentRun)
            .where(PeakAssignmentRun.sample_item_id == sample_item_id)
            .values(status="failed")
        )
        await session.commit()


def _reasons(ctx, peak_id="p1"):
    (row,) = [row for row in ctx.judged.rows if row["sample_peak_id"] == peak_id]
    return [reason["rule"] for reason in row["provenance"].get("tier_reasons", [])]


def _fold_kept(detect_snr):
    """What a run-less fold keeps for a sample (``model_readers.record_sample_model``):
    its model and a line-share tolerance whose own fit set the detectability
    threshold the absent-line rule asks lines from."""
    model = MeasurementModel.v1(
        SampleMassAccuracy(mu_ppm=0.0, sigma_ppm=0.2, anchors=30, anchor_ions=9),
        INSTRUMENT_FALLBACK_SIGMA_PPM["orbi"],
    )
    return {
        "measurement_model": model.record(),
        "envelope_tolerance": {
            "level": LEVEL_OWN,
            "form": FORM_CENTRE_WIDTH,
            "centre": 0.0,
            "floor": 0.05,
            "detect_snr": detect_snr,
        },
    }


@pytest.mark.parametrize("kept", [None, 2.5], ids=["nothing kept", "fold's model"])
async def test_a_pin_in_a_sample_no_run_read_is_asked_as_a_propagation_is(
    async_session_factory, anchor, stubbed_scorer, monkeypatch, kept
):
    """MAS-1046: S1 has no run, so nothing recorded a gate for its lines. The
    pin is asked them at the gate a run of S1 would ask at, the one a
    propagated reading of S1 is asked at, and the absent 13C line holds S1's
    member at candidate as it holds the propagated reading. Where the ingest
    fold kept S1's model, its tolerance's own threshold is the gate's."""
    await _no_run_on(async_session_factory, anchor["s1"])
    if kept is not None:
        await model_readers.record_sample_model(anchor["s1"], _fold_kept(kept))
    passes = _record_passes(monkeypatch)
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )
    (pin,) = passes
    assert pin.path is pipeline.PERSONS_CHOICE
    assert pin.recorded is None
    assert _reasons(pin) == ["absent_line"]
    assert pin.tiering == {"rules": "spectrum", "asked": 1, "capped": 1}
    members = await _members(async_session_factory, anchor["batch_peak_id"])
    assert tier_name(members[anchor["s1"]].tier) == "candidate"

    # The same reading propagated onto S1, under a run's rules.
    await _propagate_to_sample(
        anchor["batch_id"],
        anchor["s1"],
        {anchor["batch_peak_id"]: Annotation("C6H12O6", "C6H13O6+", MECH, "M0", None)},
        PeakAssignmentConfig(),
        only_unassigned=False,
    )
    propagated = passes[-1]
    assert propagated.path is pipeline.PROPAGATION
    assert "absent_line" in _reasons(propagated)
    assert pin.line_gate == LineGate.from_record(propagated.tiering)
    if kept is not None:
        assert pin.line_gate.detect_snr == kept


async def test_a_pin_is_asked_at_the_gate_its_samples_run_recorded(
    async_session_factory, anchor, stubbed_scorer, monkeypatch
):
    """Where S1's run recorded the gate it asked lines at, the pin is asked
    at that one, not at the one its measurement would give."""
    recorded = LineGate(15.0, 0.02, 4.0, DETECT_DEFAULT, 0.2, True)
    async with async_session_factory() as session:
        await session.execute(
            update(PeakAssignmentRun)
            .where(PeakAssignmentRun.sample_item_id == anchor["s1"])
            .values(
                config={
                    "tiering": {
                        "version": 11,
                        "absent_line": {"spectrum": True, **recorded.record()},
                    }
                }
            )
        )
        await session.commit()
    passes = _record_passes(monkeypatch)
    await run_batch_peak_curation(
        anchor["batch_id"],
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )
    (pin,) = passes
    assert pin.line_gate == recorded


# --- the batch stages --------------------------------------------------------------


async def _stages_ran(batch_id, *stages):
    """Record stages as run and current, as a pass leaves them."""
    for stage in stages:
        _, started = await begin_stage(batch_id, stage)
        await finish_stage(batch_id, stage, started, run_id=None)


async def _stale_reasons(batch_id):
    standing = await stage_standing(batch_id)
    return {
        entry["stage"]: entry["stale_reason"]
        for entry in standing["stages"]
        if entry["state"] == "stale"
    }


async def test_a_pin_and_its_release_leave_every_stage_that_ran_stale(
    editor_client, anchor, stubbed_scorer, assignment_enabled
):
    """A pin re-points members in every sample and moves its batch peak's
    reading, and its release puts both back: every stage reads them, so each
    leaves every stage that ran stale."""
    batch_id = anchor["batch_id"]
    await _stages_ran(batch_id, BatchStage.SEARCH, BatchStage.CO_VARIATION)
    await run_batch_peak_curation(
        batch_id,
        anchor["batch_peak_id"],
        anchor["other"],
        "C6H12O6",
        PeakAssignmentConfig(),
    )
    assert await _stale_reasons(batch_id) == {
        "search": "curation",
        "co_variation": "curation",
    }

    await _stages_ran(batch_id, BatchStage.SEARCH, BatchStage.CO_VARIATION)
    response = await editor_client.post(
        f"/api/batch-peaks/batch/{batch_id}/release-curation",
        json={"batch_peak_id": anchor["batch_peak_id"]},
    )
    assert response.status_code == 200, response.text
    assert await _stale_reasons(batch_id) == {
        "search": "curation",
        "co_variation": "curation",
    }


@pytest.mark.parametrize("recompute_fails", [False, True])
async def test_an_interrupted_pin_still_leaves_every_stage_stale(
    anchor, stubbed_scorer, monkeypatch, recompute_fails
):
    """The members re-pointed before the interruption are committed: the
    stages are stale though the walk did not finish, and whether or not the
    consensus pass on the way out did."""
    batch_id = anchor["batch_id"]
    await _stages_ran(batch_id, BatchStage.SERIES)

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    async def fails(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(batch_curation, "_propagate_to_sample", cancelled)
    if recompute_fails:
        monkeypatch.setattr(batch_curation, "recompute_batch_consensus", fails)
    with pytest.raises(asyncio.CancelledError):
        await run_batch_peak_curation(
            batch_id,
            anchor["batch_peak_id"],
            anchor["other"],
            "C6H12O6",
            PeakAssignmentConfig(),
        )
    assert await _stale_reasons(batch_id) == {"series": "curation"}


async def test_an_interrupted_pin_whose_stale_mark_fails_stays_a_cancellation(
    anchor, stubbed_scorer, monkeypatch
):
    """The database gone as the pin is cancelled: the consensus pass on the
    way out fails, and so does the stages' mark after it. The mark's failure
    is logged rather than raised, so the cancellation still reaches the
    caller as what it is, not as a database error."""
    batch_id = anchor["batch_id"]
    await _stages_ran(batch_id, BatchStage.SERIES)

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    async def fails(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(batch_curation, "_propagate_to_sample", cancelled)
    monkeypatch.setattr(batch_curation, "recompute_batch_consensus", fails)
    monkeypatch.setattr(batch_staleness, "mark_every_stage_stale", fails)
    with captured_logs("WARNING") as logs, pytest.raises(asyncio.CancelledError):
        await run_batch_peak_curation(
            batch_id,
            anchor["batch_peak_id"],
            anchor["other"],
            "C6H12O6",
            PeakAssignmentConfig(),
        )
    assert any(
        record["message"].startswith("Could not mark the batch stages")
        and "the database went away" in record["message"]
        for record in logs
    )
    # Nothing could be written: the stage reads as it did.
    assert await _stale_reasons(batch_id) == {}
