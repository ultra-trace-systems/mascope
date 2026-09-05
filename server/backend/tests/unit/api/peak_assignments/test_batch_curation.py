"""Unit tests for the batch curation's pure parts.

No DB: the pinned consensus, the pin itself and what it archives, the registry
lookup, the derived row's alternatives naming their registry index, and the
task's closing message. See ``batch_curation.py`` and ``batch_peaks.py``.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.batch_curation import (
    ACTION_PROMOTE_IDENTITY,
    curation_outcome,
    manual_pin,
    registry_entry,
    restore_member,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    compute_consensus,
    manual_pin_of,
    member_state,
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
