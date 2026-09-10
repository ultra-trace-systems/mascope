"""Unit tests for the named implausibility signatures (step 2.4).

Contract: name the formula shapes that are the products of a mass search rather
than of a source, from the neutral formula alone, and name nothing else. Each
test states the chemistry it is protecting, because that - not the arithmetic -
is what would be lost if a threshold moved.
"""

import pytest

from mascope_tools.composition.implausibility import (
    CARBON_CLUSTER,
    CARBON_FREE,
    CARBON_FREE_ALLOWLIST,
    OXYGEN_LATTICE,
    OXYGEN_LATTICE_MIN,
    SIGNATURES,
    implausible_signature,
    implausible_signatures,
)


class TestTheCarbonCluster:
    @pytest.mark.parametrize("formula", ["C60", "C10H2", "C24", "C2"])
    def test_a_bare_carbon_skeleton_is_named(self, formula):
        assert CARBON_CLUSTER in implausible_signatures(formula)

    @pytest.mark.parametrize(
        "formula",
        [
            "C6H6",  # benzene, DBE/C 0.67
            "C24H12",  # coronene, the most condensed aromatic a sample sees
            "C10H8",  # naphthalene
            "C6H12O6",  # glucose
        ],
    )
    def test_an_aromatic_is_not_a_cluster(self, formula):
        assert CARBON_CLUSTER not in implausible_signatures(formula)

    def test_a_perfluorinated_chain_is_not_a_cluster(self):
        # The DBE counts fluorine as hydrogen, so PFOA reads 1 DBE over 9
        # carbons rather than 9.5. That convention is what makes a fluorine
        # exemption unnecessary - and is the reason not to add one, because a
        # genuinely cluster-like fluorinated formula should still be named.
        assert implausible_signatures("C9HF17O2") == ()

    def test_a_fluorinated_cluster_is_still_named(self):
        assert CARBON_CLUSTER in implausible_signatures("C20F2")


class TestTheOxygenLattice:
    @pytest.mark.parametrize(
        "formula",
        [
            "C3H4O4",  # malonic acid, O/C 1.33
            "C2H4O3",  # glycolic acid, O/C 1.5
            "CH2O2",  # formic acid, O/C 2.0
            "C2H2O4",  # oxalic acid, O/C 2.0
        ],
    )
    def test_the_small_organic_acids_are_left_alone(self, formula):
        # A CIMS source is built to see these. A rule on the ratio alone takes
        # every one of them, which is why the absolute oxygen count is in it.
        assert OXYGEN_LATTICE not in implausible_signatures(formula)

    @pytest.mark.parametrize("formula", ["C5H7O7", "C8H10O11", "C6H10O9"])
    def test_an_oxygen_rich_backbone_is_named(self, formula):
        assert OXYGEN_LATTICE in implausible_signatures(formula)

    def test_a_high_oxygen_count_on_a_long_backbone_is_not_a_lattice(self):
        # Highly oxidised organic matter is real chemistry; what is not is more
        # oxygens than the carbon skeleton could carry.
        assert implausible_signatures("C20H32O12") == ()

    def test_the_floor_is_where_the_ratio_stops_describing_an_acid(self):
        assert OXYGEN_LATTICE_MIN > 4  # malonic acid's own oxygen count


class TestTheCarbonFreeFormula:
    @pytest.mark.parametrize("formula", ["HS3", "H4N2O5", "S4"])
    def test_a_carbon_free_formula_off_the_list_is_named(self, formula):
        assert CARBON_FREE in implausible_signatures(formula)

    @pytest.mark.parametrize("formula", sorted(CARBON_FREE_ALLOWLIST))
    def test_the_species_these_sources_make_are_spared(self, formula):
        assert CARBON_FREE not in implausible_signatures(formula)

    def test_the_list_is_matched_on_composition_not_on_spelling(self):
        # The curated stage writes H1N1O3 and the untargeted stage HNO3; an
        # allowlist keyed on the string holds for one of them and not the other.
        assert implausible_signatures("H1N1O3") == ()
        assert implausible_signatures("HNO3") == ()


class TestFailingOpen:
    @pytest.mark.parametrize("formula", ["", None, "not a formula", "O3^N"])
    def test_a_formula_that_cannot_be_read_carries_nothing(self, formula):
        # Every chemistry rule here fails open: a row is never demoted on a test
        # that could not run. A labelled reagent's caret is one such formula.
        assert implausible_signatures(formula) == ()


class TestNamingARowOnce:
    def test_a_formula_can_carry_more_than_one(self):
        # C2O8: no hydrogen, so DBE/C is 1.5, and eight oxygens on two carbons.
        assert set(implausible_signatures("C2O8")) == {CARBON_CLUSTER, OXYGEN_LATTICE}

    def test_the_one_it_is_named_by_is_the_first_in_a_fixed_order(self):
        assert implausible_signature("C2O8") == SIGNATURES[0] == CARBON_CLUSTER

    def test_nothing_to_name_is_none(self):
        assert implausible_signature("C6H12O6") is None
