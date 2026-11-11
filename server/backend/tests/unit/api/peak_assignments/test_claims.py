"""Unit tests for identification claims: the rules a claim keeps and its level.

A claim is a confirmed verdict that names a structure
(``docs/dev/identification_claims.md`` sections 5.1, 5.2 and 5.4). The level
follows from the evidence and is fixed when the verdict is recorded; the
request bodies of both verification routes refuse a claim that breaks a rule,
each refusal naming it. See ``claims.py``.
"""

import pytest
from pydantic import ValidationError

from mascope_backend.api.new.peak_assignments.claims import (
    LEVEL_MAPPING_KEY,
    LEVEL_MAPPING_VERSION,
    claim_level,
    claim_problem,
    mapping_context,
)
from mascope_backend.api.new.peak_assignments.schemas import (
    VerifyAssignmentBody,
    VerifyBatchPeakBody,
)


GLUCOSE = "WQZGKKKJIJFFOK-GASJEMHNSA-N"


# --- The level -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("evidence_level", "level"),
    [("reference_standard", 1), ("msms", 2), ("orthogonal", 3), ("pattern", 4)],
)
def test_a_confirmation_naming_a_structure_states_its_evidences_level(
    evidence_level, level
):
    assert claim_level("confirmed", evidence_level, GLUCOSE) == level


def test_a_pattern_confirmation_without_a_structure_states_four():
    """The formula confirmed, which is what a pattern says."""
    assert claim_level("confirmed", "pattern", None) == 4


@pytest.mark.parametrize("inchikey", [None, GLUCOSE])
def test_a_visual_confirmation_states_no_level_of_its_own(inchikey):
    """It raises nothing: a reader takes the engine's level."""
    assert claim_level("confirmed", "visual", inchikey) is None


@pytest.mark.parametrize("verdict", ["rejected", "unsure"])
@pytest.mark.parametrize("evidence_level", [None, "reference_standard", "pattern"])
def test_a_rejection_or_an_unsure_verdict_states_no_level(verdict, evidence_level):
    assert claim_level(verdict, evidence_level, None) is None


@pytest.mark.parametrize("evidence_level", ["reference_standard", "msms", "orthogonal"])
def test_a_structure_level_without_a_structure_is_never_computed(evidence_level):
    """The bodies refuse it first; the mapping does not paper over a write
    that slipped past them."""
    with pytest.raises(ValueError, match="names a structure"):
        claim_level("confirmed", evidence_level, None)


def test_the_mapping_version_joins_what_the_context_records():
    assert mapping_context() == {LEVEL_MAPPING_KEY: LEVEL_MAPPING_VERSION}
    assert mapping_context({"mz": 181.07}) == {
        "mz": 181.07,
        LEVEL_MAPPING_KEY: LEVEL_MAPPING_VERSION,
    }


# --- The rules -------------------------------------------------------------------


@pytest.mark.parametrize("evidence_level", ["reference_standard", "msms", "orthogonal"])
def test_a_confirmation_on_structure_evidence_names_a_structure(evidence_level):
    problem = claim_problem("confirmed", evidence_level, None, None)
    assert problem is not None and "requires claimed_inchikey" in problem
    assert claim_problem("confirmed", evidence_level, GLUCOSE, "glucose") is None


@pytest.mark.parametrize("evidence_level", ["pattern", "visual"])
def test_a_confirmation_on_formula_evidence_may_name_a_structure_or_not(
    evidence_level,
):
    assert claim_problem("confirmed", evidence_level, None, None) is None
    assert claim_problem("confirmed", evidence_level, GLUCOSE, None) is None


@pytest.mark.parametrize("verdict", ["rejected", "unsure"])
def test_only_a_confirmation_names_a_structure(verdict):
    problem = claim_problem(verdict, "visual", GLUCOSE, None)
    assert problem is not None and "requires verdict 'confirmed'" in problem
    assert claim_problem(verdict, "msms", None, None) is None


def test_a_name_goes_with_an_inchikey():
    problem = claim_problem("confirmed", "pattern", None, "glucose")
    assert problem is not None and "claimed_name requires claimed_inchikey" in problem


# --- The request bodies ----------------------------------------------------------


def _assignment_body(**fields):
    return VerifyAssignmentBody(peak_assignment_id="pa-1", **fields)


def _batch_body(**fields):
    return VerifyBatchPeakBody(
        batch_peak_id="bp-1", expected_formula="C6H12O6", **fields
    )


@pytest.fixture(params=[_assignment_body, _batch_body], ids=["sample", "batch"])
def body(request):
    """Each verification route's body, since both apply the same rules."""
    return request.param


def test_a_body_takes_a_claim(body):
    parsed = body(
        verdict="confirmed",
        evidence_level="reference_standard",
        claimed_inchikey=GLUCOSE,
        claimed_name="  D-glucose ",
    )
    assert parsed.claimed_inchikey == GLUCOSE
    assert parsed.claimed_name == "D-glucose"


def test_a_body_refuses_a_reference_standard_confirmation_naming_no_structure(body):
    with pytest.raises(ValidationError, match="requires claimed_inchikey"):
        body(verdict="confirmed", evidence_level="reference_standard")


def test_a_body_refuses_a_structure_on_a_rejection(body):
    with pytest.raises(ValidationError, match="requires verdict 'confirmed'"):
        body(verdict="rejected", claimed_inchikey=GLUCOSE)


def test_a_body_refuses_a_name_without_an_inchikey(body):
    with pytest.raises(ValidationError, match="claimed_name requires"):
        body(verdict="confirmed", evidence_level="pattern", claimed_name="glucose")


def test_a_blank_name_is_no_name(body):
    parsed = body(verdict="confirmed", evidence_level="pattern", claimed_name="  ")
    assert parsed.claimed_name is None


@pytest.mark.parametrize(
    "inchikey",
    ["WQZGKKKJIJFFOK-GASJEMHNSA", "wqzgkkkjijffok-gasjemhnsa-n", "glucose"],
)
def test_a_body_refuses_what_is_not_an_inchikey(body, inchikey):
    with pytest.raises(ValidationError, match="is not an InChIKey"):
        body(verdict="confirmed", evidence_level="pattern", claimed_inchikey=inchikey)


def test_a_body_strips_the_inchikey(body):
    parsed = body(
        verdict="confirmed", evidence_level="msms", claimed_inchikey=f" {GLUCOSE}\n"
    )
    assert parsed.claimed_inchikey == GLUCOSE
