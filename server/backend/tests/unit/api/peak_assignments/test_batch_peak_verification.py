"""Unit tests for the batch-level verdict's pure parts.

No DB: the claim an anchor makes, staleness against it, what the served record
carries, the context snapshot, and the request body's guards. See
``batch_peak_verification.py``.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mascope_backend.api.new.peak_assignments.batch_peak_verification import (
    AnchorClaim,
    claim_of,
    context_snapshot,
    is_stale,
    with_current_claim,
)
from mascope_backend.api.new.peak_assignments.schemas import VerifyBatchPeakBody


def _anchor(**over):
    base = dict(
        batch_peak_id="bp-1",
        mz=181.0707,
        consensus_formula="C6H12O6",
        consensus_ion_formula="C6H13O6+",
        ionization_mechanism_id="m1",
        consensus_tier="assigned",
        best_fit_score=0.95,
        support_fraction=1.0,
        n_present=2,
        is_ambiguous=0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _record(**over):
    base = dict(
        batch_peak_verification_id="v1",
        batch_peak_id="bp-1",
        assigned_formula="C6H12O6",
        ionization_mechanism_id="m1",
        verdict="confirmed",
        superseded_utc=None,
    )
    base.update(over)
    return base


def test_an_absent_anchor_claims_nothing():
    assert claim_of(None) == AnchorClaim(present=False)


def test_the_claim_is_the_consensus_formula_and_mechanism():
    claim = claim_of(_anchor())
    assert (claim.present, claim.formula, claim.ionization_mechanism_id, claim.mz) == (
        True,
        "C6H12O6",
        "m1",
        181.0707,
    )


def test_a_live_verdict_on_the_present_claim_is_current():
    assert is_stale(_record(), claim_of(_anchor())) is False


def test_a_live_verdict_on_a_claim_the_anchor_left_is_stale():
    assert is_stale(_record(), claim_of(_anchor(consensus_formula="C7H14O7"))) is True
    assert is_stale(_record(), claim_of(_anchor(ionization_mechanism_id="m2"))) is True


def test_mechanisms_compare_null_safely():
    record = _record(ionization_mechanism_id=None)
    assert is_stale(record, claim_of(_anchor(ionization_mechanism_id=None))) is False
    assert is_stale(record, claim_of(_anchor(ionization_mechanism_id="m1"))) is True


def test_a_verdict_outlives_its_anchor_and_reads_as_stale():
    served = with_current_claim(_record(), claim_of(None))
    assert served["anchor_present"] is False
    assert served["stale"] is True
    assert served["current_formula"] is None


def test_a_superseded_verdict_is_history_not_stale():
    record = _record(superseded_utc="2026-09-04T00:00:00Z")
    assert is_stale(record, claim_of(_anchor(consensus_formula="X"))) is False
    assert is_stale(record, claim_of(None)) is False


def test_the_served_record_carries_the_present_claim():
    served = with_current_claim(
        _record(),
        claim_of(_anchor(consensus_formula="C7H14O7", ionization_mechanism_id="m2")),
    )
    assert served["current_formula"] == "C7H14O7"
    assert served["current_ionization_mechanism_id"] == "m2"
    assert served["stale"] is True
    # The record itself is untouched: the judgment stays about what was judged.
    assert served["assigned_formula"] == "C6H12O6"
    assert served["verdict"] == "confirmed"


def test_the_context_snapshot_is_what_the_human_saw():
    assert context_snapshot(_anchor(is_ambiguous=1)) == {
        "mz": 181.0707,
        "consensus_formula": "C6H12O6",
        "consensus_ion_formula": "C6H13O6+",
        "ionization_mechanism_id": "m1",
        "consensus_tier": "assigned",
        "best_fit_score": 0.95,
        "support_fraction": 1.0,
        "n_present": 2,
        "is_ambiguous": True,
    }


def test_confirming_needs_an_evidence_level_and_the_formula_judged():
    with pytest.raises(ValidationError, match="evidence_level"):
        VerifyBatchPeakBody(
            batch_peak_id="bp-1", verdict="confirmed", expected_formula="C6H12O6"
        )
    with pytest.raises(ValidationError, match="expected_formula"):
        VerifyBatchPeakBody(
            batch_peak_id="bp-1", verdict="confirmed", evidence_level="pattern"
        )
    body = VerifyBatchPeakBody(
        batch_peak_id="bp-1",
        verdict="confirmed",
        evidence_level="pattern",
        expected_formula="C6H12O6",
    )
    assert body.expected_formula == "C6H12O6"


def test_rejecting_needs_the_formula_judged_but_no_evidence():
    with pytest.raises(ValidationError, match="expected_formula"):
        VerifyBatchPeakBody(batch_peak_id="bp-1", verdict="rejected")
    body = VerifyBatchPeakBody(
        batch_peak_id="bp-1", verdict="rejected", expected_formula="C6H12O6"
    )
    assert body.evidence_level is None


def test_unsure_needs_neither():
    body = VerifyBatchPeakBody(batch_peak_id="bp-1", verdict="unsure")
    assert body.expected_formula is None
    assert body.evidence_level is None
