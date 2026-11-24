"""Unit tests for identification claims: the rules a claim keeps and its level.

A claim is a confirmed verdict that names a structure, or at 3b a class
(``docs/dev/identification_claims.md`` sections 5.1, 5.2 and 5.4). Its level,
the number and the scale's letter, follows from the evidence and what the
person says it showed, under mapping version 2, and is fixed when the verdict
is recorded; the request bodies of both verification routes refuse a claim
that breaks a rule, each refusal naming it. A level-1 claim on a batch peak
names the time window it covers. See ``claims.py``.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mascope_backend.api.new.peak_assignments.claims import (
    CLAIM_KEY,
    LEVEL_MAPPING_KEY,
    LEVEL_MAPPING_VERSION,
    LIFT_INDEPENDENT_LINE,
    REFUTES_KEY,
    claim_level,
    claim_problem,
    level_reaches,
    mapping_context,
    refutation,
    statement,
    window_problem,
)
from mascope_backend.api.new.peak_assignments.schemas import (
    VerifyAssignmentBody,
    VerifyBatchPeakBody,
)


GLUCOSE = "WQZGKKKJIJFFOK-GASJEMHNSA-N"
T0 = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 8, 8, tzinfo=timezone.utc)


def _label(stated):
    return stated.label if stated else None


# --- The level: version 2 ----------------------------------------------------------


def test_the_mapping_is_at_version_two():
    assert LEVEL_MAPPING_VERSION == 2


@pytest.mark.parametrize("line", ["msms", "separation", "instrument"])
def test_a_reference_standard_with_an_independent_line_states_one(line):
    stated = claim_level("confirmed", "reference_standard", GLUCOSE, None, line)
    assert stated.label == "1"
    assert (stated.level, stated.letter, stated.lift) == (1, None, None)


def test_a_reference_standard_with_no_independent_line_states_2b():
    """Decided 2026-11-17: 2b, its lift the line that would take it to 1."""
    stated = claim_level("confirmed", "reference_standard", GLUCOSE)
    assert stated.label == "2b"
    assert stated.lift == LIFT_INDEPENDENT_LINE == "an independent line"


@pytest.mark.parametrize(
    ("evidence_level", "detail", "label", "names"),
    [
        ("msms", "library", "2a", "structure"),
        ("msms", "fragments", "2b", "structure"),
        ("msms", "loss", "3b", "class"),
        ("orthogonal", "separation", "2b", "structure"),
        ("orthogonal", "data_and_list", "3a", "structure"),
        ("orthogonal", "class", "3b", "class"),
        ("orthogonal", "context_list", "3c", "structure"),
        ("orthogonal", "homologue", "3d", "structure"),
        ("pattern", "split_pinned", "4a", "formula"),
        ("pattern", "split_open", "4b", "formula"),
    ],
)
def test_what_the_evidence_showed_decides_the_letter(
    evidence_level, detail, label, names
):
    stated = statement("confirmed", evidence_level, detail)
    assert (stated.label, stated.names) == (label, names)
    assert stated.evidence and stated.lift


def test_a_pattern_confirmation_that_says_nothing_of_the_split_states_it_open():
    """The level assumes no split nobody said was pinned, and a client that
    predates the detail still records the formula it confirmed."""
    assert claim_level("confirmed", "pattern", None).label == "4b"
    assert claim_level("confirmed", "pattern", GLUCOSE).label == "4b"


def test_a_class_claim_states_3b_with_no_structure():
    assert claim_level("confirmed", "msms", None, "loss").label == "3b"
    assert claim_level("confirmed", "orthogonal", None, "class").label == "3b"


def test_a_class_claim_naming_a_structure_is_never_computed():
    with pytest.raises(ValueError, match="names a class"):
        claim_level("confirmed", "msms", GLUCOSE, "loss")


@pytest.mark.parametrize("inchikey", [None, GLUCOSE])
def test_a_visual_confirmation_states_no_level_of_its_own(inchikey):
    """It raises nothing: a reader takes the engine's level."""
    assert claim_level("confirmed", "visual", inchikey) is None


@pytest.mark.parametrize("verdict", ["rejected", "unsure"])
@pytest.mark.parametrize("evidence_level", [None, "reference_standard", "pattern"])
def test_a_rejection_or_an_unsure_verdict_states_no_level(verdict, evidence_level):
    assert claim_level(verdict, evidence_level, None) is None


@pytest.mark.parametrize(
    ("evidence_level", "detail"),
    [
        ("reference_standard", None),
        ("msms", "library"),
        ("msms", "fragments"),
        ("orthogonal", "separation"),
        ("orthogonal", "context_list"),
    ],
)
def test_a_structure_level_without_a_structure_is_never_computed(
    evidence_level, detail
):
    """The bodies refuse it first; the mapping does not paper over a write
    that slipped past them."""
    with pytest.raises(ValueError, match="names a structure"):
        claim_level("confirmed", evidence_level, None, detail)


def test_the_statement_joins_what_the_context_records():
    assert mapping_context() == {LEVEL_MAPPING_KEY: LEVEL_MAPPING_VERSION}
    stated = statement("confirmed", "reference_standard")
    context = mapping_context(
        {"mz": 181.07}, stated=stated, evidence_detail=None, independent_line=None
    )
    assert context["mz"] == 181.07
    assert context[LEVEL_MAPPING_KEY] == 2
    assert context[CLAIM_KEY] == {
        "evidence_detail": None,
        "independent_line": None,
        "evidence": stated.evidence,
        "lift": "an independent line",
    }
    assert REFUTES_KEY not in context


# --- The rules -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("evidence_level", "detail", "line"),
    [
        ("reference_standard", None, None),
        ("reference_standard", None, "msms"),
        ("msms", "library", None),
        ("msms", "fragments", None),
        ("orthogonal", "separation", None),
        ("orthogonal", "data_and_list", None),
        ("orthogonal", "context_list", None),
        ("orthogonal", "homologue", None),
    ],
)
def test_a_level_one_two_or_three_naming_a_candidate_names_a_structure(
    evidence_level, detail, line
):
    problem = claim_problem("confirmed", evidence_level, None, None, detail, line)
    assert problem is not None and "requires claimed_inchikey" in problem
    assert (
        claim_problem("confirmed", evidence_level, GLUCOSE, "glucose", detail, line)
        is None
    )


@pytest.mark.parametrize(
    ("evidence_level", "detail"), [("msms", "loss"), ("orthogonal", "class")]
)
def test_a_class_claim_names_its_class_and_no_structure(evidence_level, detail):
    assert (
        claim_problem("confirmed", evidence_level, None, "a carboxylic acid", detail)
        is None
    )
    unnamed = claim_problem("confirmed", evidence_level, None, None, detail)
    assert unnamed is not None and "requires claimed_name" in unnamed
    keyed = claim_problem("confirmed", evidence_level, GLUCOSE, "glucose", detail)
    assert keyed is not None and "send no claimed_inchikey" in keyed


@pytest.mark.parametrize("evidence_level", ["msms", "orthogonal"])
def test_an_msms_or_orthogonal_confirmation_says_what_it_showed(evidence_level):
    problem = claim_problem("confirmed", evidence_level, GLUCOSE, None)
    assert problem is not None and "requires evidence_detail" in problem


@pytest.mark.parametrize(
    ("evidence_level", "detail"),
    [("msms", "separation"), ("pattern", "loss"), ("orthogonal", "split_pinned")],
)
def test_a_detail_is_one_of_its_evidence_levels_words(evidence_level, detail):
    problem = claim_problem("confirmed", evidence_level, GLUCOSE, None, detail)
    assert problem is not None and f"evidence_detail on '{evidence_level}'" in problem


@pytest.mark.parametrize(
    ("verdict", "evidence_level"),
    [
        ("confirmed", "reference_standard"),
        ("confirmed", "visual"),
        ("rejected", "msms"),
        ("unsure", "pattern"),
    ],
)
def test_a_detail_goes_with_a_confirmation_that_has_words_for_it(
    verdict, evidence_level
):
    problem = claim_problem(verdict, evidence_level, None, None, "library")
    assert problem is not None and "evidence_detail goes with" in problem


@pytest.mark.parametrize(
    ("verdict", "evidence_level"),
    [
        ("confirmed", "msms"),
        ("confirmed", "pattern"),
        ("rejected", "reference_standard"),
    ],
)
def test_an_independent_line_goes_with_a_confirmed_reference_standard(
    verdict, evidence_level
):
    problem = claim_problem(verdict, evidence_level, None, None, None, "msms")
    assert problem is not None and "independent_line goes with" in problem


def test_an_independent_line_is_one_of_the_three():
    """The rule itself, for a caller the request body's type does not guard."""
    problem = claim_problem(
        "confirmed", "reference_standard", GLUCOSE, None, None, "intuition"
    )
    assert problem == "independent_line is one of msms, separation, instrument"


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
    assert claim_problem(verdict, "reference_standard", None, None) is None


def test_a_name_goes_with_an_inchikey():
    problem = claim_problem("confirmed", "pattern", None, "glucose")
    assert problem is not None and "claimed_name requires claimed_inchikey" in problem


# --- The window ----------------------------------------------------------------------


def _one():
    return statement("confirmed", "reference_standard", None, "msms")


def test_a_level_one_batch_claim_names_its_window():
    assert window_problem(_one(), T0, T1) is None
    assert window_problem(_one(), T0, T0) is None
    missing = window_problem(_one(), None, None)
    assert missing is not None and "requires window_start_utc" in missing


def test_a_window_has_both_ends_in_order():
    assert "go together" in window_problem(_one(), T0, None)
    assert "go together" in window_problem(_one(), None, T1)
    assert "is after" in window_problem(_one(), T1, T0)


@pytest.mark.parametrize(
    "stated",
    [
        statement("confirmed", "reference_standard"),
        statement("confirmed", "msms", "library"),
        statement("confirmed", "pattern", "split_pinned"),
        None,
    ],
    ids=["2b", "2a", "4a", "none"],
)
def test_only_a_level_one_claim_names_a_window(stated):
    assert window_problem(stated, None, None) is None
    problem = window_problem(stated, T0, T1)
    assert problem is not None and "scope a level-1 claim" in problem


def test_a_windowed_claims_level_reaches_only_the_samples_inside_it():
    claim = {"window_start_utc": T0, "window_end_utc": T1}
    assert level_reaches(claim, T0)
    assert level_reaches(claim, T1)
    assert level_reaches(claim, T0 + timedelta(days=3))
    assert not level_reaches(claim, T0 - timedelta(seconds=1))
    assert not level_reaches(claim, T1 + timedelta(seconds=1))
    # A sample with no acquisition time cannot be placed inside it.
    assert not level_reaches(claim, None)
    # Read off a model as off its dict.
    assert not level_reaches(SimpleNamespace(**claim), T1 + timedelta(days=1))


def test_a_claim_with_no_window_reaches_every_sample():
    claim = {"window_start_utc": None, "window_end_utc": None}
    assert level_reaches(claim, T0)
    assert level_reaches(claim, None)


# --- The refutation ------------------------------------------------------------------


def _live(verdict="confirmed", level=1, letter=None):
    return SimpleNamespace(
        assignment_verification_id="av-1",
        verdict=verdict,
        level=level,
        level_letter=letter,
        evidence_level="reference_standard",
        claimed_inchikey=GLUCOSE,
        claimed_name="D-glucose",
        verified_utc=T0,
    )


def test_a_rejection_on_a_standard_records_the_confirmation_it_refutes():
    refuted = refutation(
        "rejected", "reference_standard", _live(), "assignment_verification_id"
    )
    assert refuted == {
        "assignment_verification_id": "av-1",
        "level": 1,
        "level_letter": None,
        "evidence_level": "reference_standard",
        "claimed_inchikey": GLUCOSE,
        "claimed_name": "D-glucose",
        "verified_utc": T0.isoformat(),
    }
    context = mapping_context(refutes=refuted)
    assert context[REFUTES_KEY] == refuted


@pytest.mark.parametrize(
    ("verdict", "evidence_level", "live"),
    [
        ("rejected", "visual", _live()),
        ("rejected", None, _live()),
        ("unsure", "reference_standard", _live()),
        ("confirmed", "reference_standard", _live()),
        ("rejected", "reference_standard", _live(verdict="rejected", level=None)),
        ("rejected", "reference_standard", None),
    ],
    ids=["visual", "no-basis", "unsure", "confirmed", "live-rejection", "none-live"],
)
def test_only_a_rejection_on_a_standard_of_a_live_confirmation_refutes(
    verdict, evidence_level, live
):
    assert (
        refutation(verdict, evidence_level, live, "assignment_verification_id") is None
    )


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


def test_a_body_takes_a_class_claim(body):
    parsed = body(
        verdict="confirmed",
        evidence_level="msms",
        evidence_detail="loss",
        claimed_name="a carboxylic acid",
    )
    assert (parsed.evidence_detail, parsed.claimed_inchikey) == ("loss", None)


def test_a_body_refuses_a_level_two_naming_no_structure(body):
    with pytest.raises(
        ValidationError, match="states level 2a requires claimed_inchikey"
    ):
        body(verdict="confirmed", evidence_level="msms", evidence_detail="library")


def test_a_body_refuses_a_reference_standard_confirmation_naming_no_structure(body):
    with pytest.raises(ValidationError, match="requires claimed_inchikey"):
        body(verdict="confirmed", evidence_level="reference_standard")


def test_a_body_refuses_a_detail_outside_the_vocabulary(body):
    with pytest.raises(ValidationError, match="evidence_detail"):
        body(
            verdict="confirmed",
            evidence_level="msms",
            evidence_detail="hunch",
            claimed_inchikey=GLUCOSE,
        )


def test_a_body_refuses_a_line_outside_the_vocabulary(body):
    with pytest.raises(ValidationError, match="independent_line"):
        body(
            verdict="confirmed",
            evidence_level="reference_standard",
            independent_line="intuition",
            claimed_inchikey=GLUCOSE,
        )


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
        verdict="confirmed",
        evidence_level="msms",
        evidence_detail="fragments",
        claimed_inchikey=f" {GLUCOSE}\n",
    )
    assert parsed.claimed_inchikey == GLUCOSE


def _level_one_batch(**fields):
    return _batch_body(
        verdict="confirmed",
        evidence_level="reference_standard",
        independent_line="msms",
        claimed_inchikey=GLUCOSE,
        **fields,
    )


def test_a_batch_body_takes_a_level_one_claim_with_its_window():
    parsed = _level_one_batch(window_start_utc=T0, window_end_utc=T1)
    assert (parsed.window_start_utc, parsed.window_end_utc) == (T0, T1)


def test_a_batch_body_reads_a_window_without_a_zone_as_utc():
    parsed = _level_one_batch(
        window_start_utc="2026-11-01T08:00:00", window_end_utc="2026-11-08T08:00:00"
    )
    assert (parsed.window_start_utc, parsed.window_end_utc) == (T0, T1)


def test_a_batch_body_refuses_a_level_one_claim_without_its_window():
    with pytest.raises(ValidationError, match="requires window_start_utc"):
        _level_one_batch()
    with pytest.raises(ValidationError, match="is after"):
        _level_one_batch(window_start_utc=T1, window_end_utc=T0)


def test_a_batch_body_refuses_a_window_on_any_other_claim():
    with pytest.raises(ValidationError, match="scope a level-1 claim"):
        _batch_body(
            verdict="confirmed",
            evidence_level="reference_standard",
            claimed_inchikey=GLUCOSE,
            window_start_utc=T0,
            window_end_utc=T1,
        )


def test_a_per_sample_body_names_no_window():
    """A per-sample claim is scoped to its sample by its key: the field is
    not the body's, and a sent one is not kept."""
    parsed = _assignment_body(
        verdict="confirmed",
        evidence_level="reference_standard",
        independent_line="separation",
        claimed_inchikey=GLUCOSE,
        window_start_utc=T0.isoformat(),
    )
    assert not hasattr(parsed, "window_start_utc")
