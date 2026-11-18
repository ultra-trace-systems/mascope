"""The one test of "the same neutral" and of "the same ion" every pass asks."""

from __future__ import annotations

import pytest

from mascope_tools.composition.utils import formula_identity, ion_identity


@pytest.mark.parametrize(
    ("written", "identity"),
    [
        ("CH3COOH", "C2H4O2"),
        ("C2H4O2", "C2H4O2"),
        ("C1H4N2O1", "CH4N2O"),
        ("C6H4(CH3)2", "C8H10"),
        ("NH3", "H3N"),
        ("(HNO3)2", "H2N2O6"),
        ("[13C]CH4", "C2H4"),
        ("H2O6^N2", "H2O6^N2"),
        ("()", "()"),
        ("not a formula", "not a formula"),
    ],
)
def test_a_neutrals_identity_is_its_composition_in_hill_order(written, identity):
    # An unreadable formula keeps its own text, so it is never merged with another.
    assert formula_identity(written) == identity


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_no_formula_is_no_neutral_and_not_the_empty_one(blank):
    # "()" is a reading: a reagent ion of no molecule. A row that names no
    # formula names nothing, and grouping the two would give a formula-less
    # row the reagent ion's channels.
    assert formula_identity(blank) == ""
    assert formula_identity(blank) != formula_identity("()")


def test_a_labelled_neutral_is_not_its_unlabelled_one():
    assert formula_identity("HN^NO6") != formula_identity("HN2O6")


class TestAnIonsIdentity:
    def test_two_splits_of_one_ion_are_one_ion(self):
        # C10H18O3 through [M+Br]- and C10H19BrO3 through [M-H]- make one ion.
        assert ion_identity("C10H18BrO3-") == ion_identity("C10H18O3Br-")
        assert ion_identity("C10H18BrO3-") == ("C10H18BrO3", "-")

    def test_the_charge_is_part_of_it(self):
        assert ion_identity("C6H11O2-") != ion_identity("C6H11O2+")
        assert ion_identity("C2H8N2++") == ("C2H8N2", "++")

    @pytest.mark.parametrize("nothing", [None, "", 3])
    def test_no_ion_named_is_none(self, nothing):
        assert ion_identity(nothing) is None
