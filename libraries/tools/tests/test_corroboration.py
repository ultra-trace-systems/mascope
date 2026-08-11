"""Unit tests for adduct co-occurrence corroboration (assignment-confidence P3).

Contract: per compound, count the distinct adduct channels that independently support it
and turn that into a bounded, saturating corroboration signal (0 for a lone adduct). See
docs/dev/assignment_confidence.md (P3).
"""

import pytest

from mascope_tools.composition.corroboration import (
    AdductCorroboration,
    adduct_corroboration,
)


def _rec(compound, adduct, tier="identified"):
    return {
        "target_compound_id": compound,
        "ionization_mechanism_id": adduct,
        "tier": tier,
    }


def test_empty_returns_empty():
    assert adduct_corroboration([]) == {}


def test_single_adduct_has_zero_corroboration():
    out = adduct_corroboration([_rec("cmp1", "H+")])
    assert out["cmp1"].n_adducts == 1
    assert out["cmp1"].corroboration == 0.0


def test_corroboration_saturates_with_more_adducts():
    two = adduct_corroboration([_rec("c", "H+"), _rec("c", "NH4+")])["c"]
    three = adduct_corroboration(
        [_rec("c", "H+"), _rec("c", "NH4+"), _rec("c", "Na+")]
    )["c"]
    assert two.corroboration == pytest.approx(0.5)
    assert three.corroboration == pytest.approx(0.75)
    assert three.corroboration > two.corroboration


def test_distinct_adducts_are_deduped_and_sorted():
    out = adduct_corroboration(
        [_rec("c", "H+"), _rec("c", "H+"), _rec("c", "NH4+")]  # H+ twice
    )["c"]
    assert out.n_adducts == 2  # duplicate H+ counted once
    assert out.adducts == ("H+", "NH4+")


def test_untargeted_and_unassigned_rows_are_skipped():
    # rows with no compound id (untargeted / unassigned) contribute nothing
    out = adduct_corroboration([_rec("cmp1", "H+"), _rec(None, "H+"), _rec("", "Na+")])
    assert set(out) == {"cmp1"}


def test_accept_predicate_filters_low_confidence():
    recs = [
        _rec("c", "H+", tier="identified"),
        _rec("c", "NH4+", tier="below_assignability"),
    ]
    accepted = adduct_corroboration(
        recs, accept=lambda r: r["tier"] in {"identified", "candidate"}
    )
    # only the identified adduct counts -> lone adduct -> no corroboration
    assert accepted["c"].n_adducts == 1
    assert accepted["c"].corroboration == 0.0
    # without the filter, both count
    both = adduct_corroboration(recs)
    assert both["c"].n_adducts == 2


def test_accepts_objects_not_just_dicts():
    class Row:
        def __init__(self, c, a):
            self.target_compound_id = c
            self.ionization_mechanism_id = a

    out = adduct_corroboration([Row("c", "H+"), Row("c", "Na+")])
    assert out["c"].corroboration == pytest.approx(0.5)


def test_result_type_and_bounds():
    out = adduct_corroboration([_rec("c", f"a{i}") for i in range(6)])["c"]
    assert isinstance(out, AdductCorroboration)
    assert 0.0 <= out.corroboration < 1.0  # bounded, never reaches 1
