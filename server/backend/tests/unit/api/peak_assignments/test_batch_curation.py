"""Unit tests for the batch curation's pure parts.

No DB: the pinned consensus, the pin itself and what it archives, the registry
lookup, where an identity's monoisotopic line is and what the refusal of an
isotope line says, the derived row's alternatives naming their registry index,
and the task's closing message. See ``batch_curation.py`` and ``batch_peaks.py``.
"""

from types import SimpleNamespace

import pandas as pd
import pytest

from mascope_backend.api.new.peak_assignments.batch_curation import (
    ACTION_PROMOTE_IDENTITY,
    ISOTOPE_LINE_CODE,
    IsotopeLineException,
    _reading_of,
    candidate_measurement,
    curation_outcome,
    manual_pin,
    monoisotopic_mz,
    registry_entry,
    restore_member,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    compute_consensus,
    manual_pin_of,
    member_state,
    tier_code,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.engine import (
    evidence_for,
    tier_for_evidence,
)
from mascope_backend.api.new.peak_assignments.fold_view import member_detail


def _member(formula, fit, intensity, tier="assigned", **extra):
    return {
        "assigned_formula": formula,
        "ion_formula": f"{formula}+H" if formula else None,
        "ionization_mechanism_id": "mH" if formula else None,
        "tier": tier,
        "fit_score": fit,
        "intensity": intensity,
        "p_correct": None,
        **extra,
    }


GLUCOSE = [_member("C6H12O6", 0.95, 1e5), _member("C6H12O6", 0.90, 9e4)]
OTHER = _member("C7H14O7", 0.6, 1e3, tier="candidate")
PIN = {
    "action": ACTION_PROMOTE_IDENTITY,
    "candidate": 1,
    "formula": "C7H14O7",
    "ion_formula": "C7H14O7+H",
    "ionization_mechanism_id": "mH",
}


# --- the pinned consensus -----------------------------------------------------


def test_without_a_pin_the_vote_decides():
    c = compute_consensus(GLUCOSE + [OTHER])
    assert c.consensus_formula == "C6H12O6"
    assert "manual" not in c.provenance
    assert "vote_winner" not in c.provenance


def test_a_pin_makes_its_formula_the_claim_whatever_the_vote_says():
    c = compute_consensus(GLUCOSE + [OTHER], manual=PIN)
    assert c.consensus_formula == "C7H14O7"
    assert c.consensus_ion_formula == "C7H14O7+H"
    assert c.ionization_mechanism_id == "mH"
    # Measured on the one member that carries it.
    assert c.consensus_tier == "candidate"
    assert c.best_fit_score == pytest.approx(0.6)
    assert c.support_fraction == pytest.approx(1 / 3, rel=1e-3)
    # The vote disagrees, and the record says so rather than hiding it.
    assert c.is_ambiguous
    assert c.provenance["vote_winner"] == "C6H12O6"
    assert c.provenance["manual"] == PIN
    # The displaced winner heads the alternatives.
    assert [a["formula"] for a in c.alternatives] == ["C6H12O6"]


def test_a_pin_the_members_agree_with_reads_as_an_ordinary_consensus():
    members = GLUCOSE + [OTHER]
    pin = {**PIN, "candidate": 0, "formula": "C6H12O6", "ion_formula": "C6H12O6+H"}
    c = compute_consensus(members, manual=pin)
    plain = compute_consensus(members)
    assert c.consensus_formula == plain.consensus_formula == "C6H12O6"
    assert c.consensus_tier == plain.consensus_tier == "assigned"
    assert c.support_fraction == plain.support_fraction
    assert c.is_ambiguous == plain.is_ambiguous is False
    assert c.provenance["vote_winner"] == "C6H12O6"


def test_a_pin_no_member_carries_is_a_candidate_claim_with_no_support():
    c = compute_consensus(GLUCOSE, manual=PIN)
    assert c.consensus_formula == "C7H14O7"
    assert c.consensus_tier == "candidate"
    assert c.best_fit_score is None
    assert c.support_fraction == 0.0
    assert c.is_ambiguous
    assert c.provenance["vote_winner"] == "C6H12O6"
    assert [a["formula"] for a in c.alternatives] == ["C6H12O6"]


def test_a_pin_over_only_unassigned_members_still_stands():
    unassigned = [_member(None, None, 500.0, tier="unassigned")]
    c = compute_consensus(unassigned, manual=PIN)
    assert c.consensus_formula == "C7H14O7"
    assert c.consensus_tier == "candidate"
    assert c.n_present == 1
    assert c.max_intensity == 500.0
    assert c.provenance["manual"] == PIN
    assert c.provenance["vote_winner"] is None


def test_a_malformed_pin_is_no_pin():
    c = compute_consensus(GLUCOSE + [OTHER], manual={"action": "promote_identity"})
    assert c.consensus_formula == "C6H12O6"
    assert "manual" not in c.provenance


# --- the pin and the archive ----------------------------------------------------


def _anchor(**over):
    base = dict(
        batch_peak_id="bp-1",
        mz=181.0707,
        consensus_formula="C6H12O6",
        consensus_ion_formula="C6H13O6+",
        ionization_mechanism_id="mH",
        consensus_tier="assigned",
        consensus_role=None,
        best_fit_score=0.95,
        support_fraction=0.67,
        n_present=3,
        is_ambiguous=0,
        candidates=[
            {
                "formula": "C6H12O6",
                "ion_formula": "C6H13O6+",
                "ionization_mechanism_id": "mH",
            },
            {
                "formula": "C7H14O7",
                "ion_formula": "C7H15O7+",
                "ionization_mechanism_id": "mH",
            },
        ],
        provenance={"agreement": 0.67},
        alternatives=[{"formula": "C7H14O7", "evidence_share": 0.2, "n": 1}],
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_the_pin_names_the_registry_entry_and_archives_the_consensus():
    pin = manual_pin(_anchor(), 1, user_id=7, at="2026-09-03T22:00:00+00:00")
    assert pin["action"] == ACTION_PROMOTE_IDENTITY
    assert pin["candidate"] == 1
    assert (pin["formula"], pin["ion_formula"], pin["ionization_mechanism_id"]) == (
        "C7H14O7",
        "C7H15O7+",
        "mH",
    )
    assert pin["user_id"] == 7
    assert pin["previous"]["consensus_formula"] == "C6H12O6"
    assert pin["previous"]["consensus_tier"] == "assigned"
    assert pin["previous"]["is_ambiguous"] is False
    assert pin["displaced_members"] == []


def test_the_registry_lookup_refuses_what_an_anchor_cannot_be_assigned():
    anchor = _anchor()
    assert registry_entry(anchor, 1)["formula"] == "C7H14O7"
    assert registry_entry(anchor, 2) is None
    assert registry_entry(anchor, -1) is None
    assert registry_entry(anchor, "1") is None
    assert registry_entry(_anchor(candidates=None), 0) is None
    assert registry_entry(_anchor(candidates=[{"formula": None}]), 0) is None


# --- an identity's monoisotopic line ------------------------------------------


def _mechanism(notation, polarity):
    return SimpleNamespace(
        ionization_mechanism_id=f"im-{notation}",
        ionization_mechanism=notation,
        ionization_mechanism_polarity=polarity,
    )


def test_the_monoisotopic_line_is_the_ions_own():
    # Protonated glucose, C6H13O6+.
    assert monoisotopic_mz("C6H12O6", _mechanism("[M+H]+", "+")) == pytest.approx(
        181.0707, abs=1e-3
    )


def test_the_monoisotopic_line_is_not_the_tallest_one():
    """A dibromide's tallest line is its 79Br81Br one, two units up. The batch
    peak holding the ion is the 79Br2 line, which every role and label counts
    from (``engine.monoisotopic_row``)."""
    assert monoisotopic_mz("CH2Br2", _mechanism("[M+H]+", "+")) == pytest.approx(
        172.8596, abs=1e-3
    )


def test_a_labelled_reagents_monoisotopic_line_is_its_labelled_one():
    """The 15N-nitrate adduct's line is the labelled one, a mass unit above the
    plain adduct's. A batch peak there is the ion's M0, so the check reading
    the ion's mass, not the brackets in its formula, lets it be pinned."""
    plain = monoisotopic_mz("C6H12O6", _mechanism("[M+NO3]-", "-"))
    labelled = monoisotopic_mz("C6H12O6", _mechanism("[M+^NO3]-", "-"))
    assert labelled - plain == pytest.approx(0.997, abs=1e-3)


def test_an_ion_that_cannot_be_generated_has_no_line():
    assert monoisotopic_mz("C6H12O6", _mechanism("H+ (not a notation)", "+")) is None


def test_the_refusal_names_the_batch_peak_holding_the_monoisotopic_line():
    line = SimpleNamespace(batch_peak_id="bp-line", mz=182.07404)
    entry = {"formula": "C6H12O6", "ion_formula": "[13C]C5H13O6+"}
    refusal = IsotopeLineException(
        line, entry, 181.07066, SimpleNamespace(batch_peak_id="bp-m0", mz=181.0707)
    )
    assert refusal.status_code == 422
    assert refusal.error_code == ISOTOPE_LINE_CODE
    assert "[13C]C5H13O6+ as an isotope line of C6H12O6" in refusal.detail
    assert "the batch peak at m/z 181.0707 ('bp-m0')" in refusal.detail

    # Where no batch peak holds the line, the message says where it would be.
    orphan = IsotopeLineException(line, entry, 181.07066, None)
    assert "at m/z 181.0707, which no batch peak holds" in orphan.detail


def test_manual_pin_of_reads_only_a_pin_that_names_a_formula():
    assert manual_pin_of(_anchor()) is None
    assert manual_pin_of(_anchor(provenance=None)) is None
    assert manual_pin_of(_anchor(provenance={"manual": {"action": "x"}})) is None
    pin = {"formula": "C7H14O7", "candidate": 1}
    assert manual_pin_of(_anchor(provenance={"manual": pin})) == pin


def test_a_member_state_round_trips_through_restore():
    member = SimpleNamespace(
        sample_item_id="s1",
        candidate=0,
        tier=3,
        fit_score=0.95,
        role=1,
        owner_batch_peak_id=None,
        p_correct=0.9,
    )
    state = member_state(member)
    assert state == {
        "sample_item_id": "s1",
        "candidate": 0,
        "tier": 3,
        "fit_score": 0.95,
        "role": 1,
        "owner_batch_peak_id": None,
        "p_correct": 0.9,
    }
    member.candidate, member.tier, member.fit_score, member.p_correct = 1, 2, 0.5, None
    restore_member(member, state)
    assert (member.candidate, member.tier, member.fit_score, member.p_correct) == (
        0,
        3,
        0.95,
        0.9,
    )


# --- what the inspector is handed ---------------------------------------------


def test_a_derived_rows_alternatives_name_their_registry_index():
    member = SimpleNamespace(
        batch_peak_id="bp-1",
        sample_item_id="s1",
        sample_peak_id="p1",
        mz_delta_ppm=0.0,
        intensity=100.0,
        tier=3,
        role=1,
        candidate=0,
        fit_score=0.95,
        owner_batch_peak_id=None,
        p_correct=None,
    )
    detail = member_detail(member, _anchor())
    assert detail["alternatives"] == [
        {
            "assigned_formula": "C7H14O7",
            "ion_formula": "C7H15O7+",
            "ionization_mechanism_id": "mH",
            "source": "batch",
            "evidence_share": 0.2,
            "n_members": 1,
            "candidate": 1,
        }
    ]


def test_the_outcome_counts_what_was_measured():
    outcome = curation_outcome(
        {"formula": "C7H14O7", "samples_measured": 3, "members_repointed": 2}, "sb-1"
    )
    assert outcome["status"] == "success"
    assert "Pinned C7H14O7" in outcome["message"]
    assert "3 samples" in outcome["message"]
    assert "2 now read it" in outcome["message"]
    assert outcome["_notification_data"] == {"sample_batch_id": "sb-1"}


def test_the_outcome_names_the_samples_it_could_not_measure():
    """A sample that raised still reads whatever it did before the pin, which
    the counts alone would not say."""
    counts = {
        "formula": "C7H14O7",
        "samples_measured": 2,
        "members_repointed": 2,
        "samples_failed": 1,
    }
    outcome = curation_outcome(counts, "sb-1")
    assert outcome["status"] == "partial"
    assert outcome["message"].endswith(
        "1 sample could not be measured and keeps its previous identity."
    )

    two = curation_outcome({**counts, "samples_failed": 2}, "sb-1")
    assert two["message"].endswith(
        "2 samples could not be measured and keep their previous identity."
    )


# --- measured before it is pinned (step 3.4c) ----------------------------------

ENTRY = {
    "formula": "C7H14O7",
    "ion_formula": "C7H15O7+",
    "ionization_mechanism_id": "mH",
}


def _scored(pairing, fit=0.81, lines=None):
    """``score_in_sample``'s four maps for one ion, ``ion-x``."""
    frame = pd.DataFrame(
        lines
        if lines is not None
        else [
            {
                "target_ion_id": "ion-x",
                "mz": 211.0812,
                "relative_abundance": 0.9,
                "sample_peak_id": "p1",
                "target_isotope_formula": "C7H15O7",
                "target_ion_formula": "C7H15O7+",
                "match_mz_error": 0.9,
                "match_abundance_error": 0.02,
            },
            {
                "target_ion_id": "ion-x",
                "mz": 212.0846,
                "relative_abundance": 0.08,
                "sample_peak_id": "p2",
                "target_isotope_formula": "[13C]C6H15O7",
                "target_ion_formula": "C7H15O7+",
                "match_mz_error": 1.1,
                "match_abundance_error": 0.05,
            },
            {
                "target_ion_id": "ion-x",
                "mz": 213.0855,
                "relative_abundance": 0.02,
                "sample_peak_id": None,
                "target_isotope_formula": "C7H15O6[18O]",
                "target_ion_formula": "C7H15O7+",
                "match_mz_error": None,
                "match_abundance_error": None,
            },
            # Another seed's ion in the same frame: none of its lines are this
            # identity's.
            {
                "target_ion_id": "ion-y",
                "mz": 211.0790,
                "relative_abundance": 1.0,
                "sample_peak_id": "p1",
                "target_isotope_formula": "C11H11N2O2",
                "target_ion_formula": "C11H11N2O2+",
                "match_mz_error": -9.0,
                "match_abundance_error": 0.0,
            },
        ]
    )
    return (
        {("C7H14O7", "mH"): "ion-x"},
        {"ion-x": fit},
        pairing,
        frame,
    )


def test_a_measured_candidate_reads_the_pins_numbers_and_its_lines():
    config = PeakAssignmentConfig()
    out = candidate_measurement(
        ENTRY,
        "p1",
        _scored({("ion-x", "p1"): {"mz_error_ppm": 0.9, "abundance_error": 0.02}}),
        config,
    )
    evidence = evidence_for(0.81, "C7H14O7")
    assert out["fit_score"] == pytest.approx(0.81)
    assert out["evidence"] == pytest.approx(evidence)
    assert out["tier"] == tier_for_evidence(
        evidence,
        candidate_threshold=config.candidate_threshold,
        assigned_threshold=config.assigned_threshold,
    )
    assert out["mz_error_ppm"] == pytest.approx(0.9)
    assert out["abundance_error"] == pytest.approx(0.02)
    assert out["takes_member"] is True
    assert out["blocked_reason"] is None
    assert [line["isotope_label"] for line in out["lines"]] == ["M0", "M+1", "M+2"]
    assert [line["sample_peak_id"] for line in out["lines"]] == ["p1", "p2", None]
    assert out["lines_found"] == 2


def test_the_tier_bands_under_the_configuration_the_pin_measures_with():
    strict = PeakAssignmentConfig(candidate_threshold=0.95, assigned_threshold=0.99)
    out = candidate_measurement(
        ENTRY, "p1", _scored({("ion-x", "p1"): {"mz_error_ppm": 0.9}}), strict
    )
    assert out["tier"] == "below_assignability"


def test_a_candidate_whose_envelope_misses_the_members_peak_takes_nothing():
    out = candidate_measurement(
        ENTRY,
        "p1",
        _scored({("ion-x", "p7"): {"mz_error_ppm": 2.0}}),
        PeakAssignmentConfig(),
    )
    assert out["takes_member"] is False
    assert out["fit_score"] is None
    assert out["evidence"] is None
    assert out["tier"] is None
    assert out["mz_error_ppm"] is None
    assert "does not reach this sample's peak" in out["blocked_reason"]
    # The lines are still what the spectrum holds of the ion.
    assert out["lines_found"] == 2


def test_a_candidate_with_no_fit_takes_nothing():
    out = candidate_measurement(
        ENTRY,
        "p1",
        _scored({("ion-x", "p1"): {"mz_error_ppm": 0.9}}, fit=None),
        PeakAssignmentConfig(),
    )
    assert out["takes_member"] is False
    assert out["fit_score"] is None


def test_an_ion_that_cannot_be_built_says_so():
    out = candidate_measurement(
        ENTRY, "p1", ({}, {}, {}, pd.DataFrame()), PeakAssignmentConfig()
    )
    assert out["takes_member"] is False
    assert out["lines"] == []
    assert out["lines_found"] == 0
    assert out["blocked_reason"] == "the ion could not be generated under its adduct"


# C2H8N8 is a formula the heuristic filter finds unlikely (plausibility about
# 0.42), so its evidence, fit x plausibility, is not its fit: what the preview
# shows and the tier the pin writes both follow the evidence.
UNLIKELY = {
    "formula": "C2H8N8",
    "ion_formula": "C2H9N8+",
    "ionization_mechanism_id": "mH",
}


def test_an_unlikely_formula_is_measured_on_its_evidence_not_its_fit():
    config = PeakAssignmentConfig()
    scored = (
        {("C2H8N8", "mH"): "ion-x"},
        {"ion-x": 0.9},
        {("ion-x", "p1"): {"mz_error_ppm": 0.4}},
        pd.DataFrame(),
    )
    out = candidate_measurement(UNLIKELY, "p1", scored, config)
    assert out["plausibility"] == pytest.approx(0.4224, abs=1e-4)
    assert out["fit_score"] == pytest.approx(0.9)
    assert out["evidence"] == pytest.approx(0.9 * out["plausibility"], abs=1e-3)
    # The fit alone would band as assigned; the evidence does not reach a
    # candidate.
    assert config.assigned_threshold <= 0.9
    assert out["evidence"] < config.candidate_threshold
    assert out["tier"] == "below_assignability"


def test_the_current_reading_carries_the_members_evidence():
    anchor = SimpleNamespace(candidates=[ENTRY, UNLIKELY])
    member = SimpleNamespace(
        candidate=1, fit_score=0.9, tier=tier_code("below_assignability")
    )
    current = _reading_of(member, anchor)
    assert current["assigned_formula"] == "C2H8N8"
    assert current["ion_formula"] == "C2H9N8+"
    assert current["fit_score"] == pytest.approx(0.9)
    assert current["evidence"] == pytest.approx(evidence_for(0.9, "C2H8N8"))
    assert current["evidence"] < 0.5
    assert current["tier"] == "below_assignability"


def test_an_unassigned_member_reads_no_evidence():
    anchor = SimpleNamespace(candidates=[ENTRY])
    member = SimpleNamespace(candidate=None, fit_score=None, tier=None)
    current = _reading_of(member, anchor)
    assert current["assigned_formula"] is None
    assert current["evidence"] is None
    assert current["tier"] is None


def test_the_pin_records_the_measurement_it_was_made_on():
    anchor = SimpleNamespace(
        candidates=[ENTRY],
        consensus_formula="C6H12O6",
        consensus_ion_formula="C6H13O6+",
        ionization_mechanism_id="mH",
        consensus_tier="assigned",
        best_fit_score=0.95,
        support_fraction=0.5,
        is_ambiguous=False,
    )
    measured = {"fit_score": 0.81, "tier": "candidate"}
    assert manual_pin(anchor, 0, 7, "now", measured)["measured"] == measured
    assert "measured" not in manual_pin(anchor, 0, 7, "now")
