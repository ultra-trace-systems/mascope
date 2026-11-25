"""The formula-shape table: every reader of a criterion reads it.

Decision MAS-979: one table of hard bound, graded band and tier cap per
criterion (``formula_shape``), and one exemption rule. Each criterion's test
below moves the table's row and sees every reader of it move: the search's raw
ratio rule (the hard bound), the plausibility (the grade), and the shape a tier
cap asks about (the context priors, the oxygen lattice).
"""

from __future__ import annotations

from dataclasses import replace

import polars as pl
import pytest

from mascope_tools.composition import formula_shape
from mascope_tools.composition import profiles as P
from mascope_tools.composition.finder import find_compositions
from mascope_tools.composition.heuristic_filter import (
    _plausibility,
    apply_heuristic_rules,
    element_counts,
    element_ratio_plausibility,
    formula_plausibility,
    rule_element_ratio,
)
from mascope_tools.composition.implausibility import (
    OXYGEN_LATTICE,
    implausible_signatures,
)
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
)
from mascope_tools.composition.utils import calculate_mass, parse_ionization


@pytest.fixture
def table(monkeypatch):
    """Move one ratio row of the table for a test, the cache cleared both ways."""
    _plausibility.cache_clear()

    def move(element, **fields):
        criteria = dict(formula_shape.RATIO_CRITERIA)
        criteria[element] = replace(criteria[element], **fields)
        monkeypatch.setattr(formula_shape, "RATIO_CRITERIA", criteria)
        _plausibility.cache_clear()

    yield move
    _plausibility.cache_clear()


def _refused(formulas):
    """The formulas the search's raw ratio rule refuses under its default."""
    frame = pl.DataFrame({"formula": list(formulas)})
    mask, _ = rule_element_ratio(frame, heuristics_config=HeuristicFilterConfig())
    return set(frame.filter(~mask).get_column("formula").to_list())


class TestNoRatioIsAHardBound:
    def test_no_criterion_carries_one(self):
        assert formula_shape.hard_ratio_bounds() == {}
        assert HeuristicFilterConfig().carbon_element_ratio_range == {}

    def test_the_wiley_bounds_that_went_refuse_nothing(self):
        # S/C 0.25, Cl/C 0.17, N/C 2.5, O/C 2.5, H/C 0.05: each was over a
        # hard bound until the decision.
        assert _refused(["C4H4S", "C6H5Cl", "C2H5N5", "C2H2O5", "C20H"]) == set()


class TestEachCriterionComesFromTheTable:
    """Moving a row moves the hard bound, the grade and the cap that read it."""

    def test_sulfur(self, table):
        thiophene = "C4H4S"  # S/C 0.25
        assert formula_plausibility(thiophene) == 1.0
        table("S", hard=(0.0, 0.1), common=(0.0, 0.1), extended=(0.0, 0.2))
        assert _refused([thiophene]) == {thiophene}
        assert formula_plausibility(thiophene) < 1.0

    def test_the_sulfur_cap(self, monkeypatch):
        counts = element_counts("C4H4S")
        assert P.priors_met(counts, (P.PRIOR_SULFUR,)) == (P.PRIOR_SULFUR,)
        monkeypatch.setattr(formula_shape, "SULFUR_PRIOR_MIN", 2)
        assert P.priors_met(counts, (P.PRIOR_SULFUR,)) == ()

    def test_halogens(self, table):
        chlorobenzene = "C6H5Cl"  # Cl/C 0.17
        assert formula_plausibility(chlorobenzene) == 1.0
        table("Cl", hard=(0.0, 0.05), common=(0.0, 0.05), extended=(0.0, 0.1))
        assert _refused([chlorobenzene]) == {chlorobenzene}
        assert formula_plausibility(chlorobenzene) < 1.0

    def test_oxygen(self, table):
        acid = "C2H2O3"  # O/C 1.5, glyoxylic acid
        assert formula_plausibility(acid) < 1.0  # over the common 1.2
        table("O", hard=(0.0, 1.0), common=(0.0, 2.0), extended=(0.0, 3.0))
        assert _refused([acid]) == {acid}
        assert formula_plausibility(acid) == 1.0

    def test_the_oxygen_cap(self, monkeypatch):
        lattice = "C4H6O6"  # O/C 1.5 on six oxygens
        assert implausible_signatures(lattice) == (OXYGEN_LATTICE,)
        monkeypatch.setattr(formula_shape, "OXYGEN_LATTICE_MIN", 7)
        assert implausible_signatures(lattice) == ()
        monkeypatch.setattr(formula_shape, "OXYGEN_LATTICE_MIN", 5)
        monkeypatch.setattr(formula_shape, "OXYGEN_LATTICE_RATIO", 1.6)
        assert implausible_signatures(lattice) == ()

    def test_nitrogen(self, table):
        rich = "C4H8N6"  # N/C 1.5
        assert formula_plausibility(rich) < 1.0  # over the common 1.3
        table("N", hard=(0.0, 1.0), common=(0.0, 2.0), extended=(0.0, 4.0))
        assert _refused([rich]) == {rich}
        assert formula_plausibility(rich) == 1.0

    def test_the_nitrogen_cap(self, monkeypatch):
        counts = element_counts("C6H8N2O")
        odd = (P.PRIOR_ODD_NITROGEN,)
        assert P.priors_met(counts, odd) == odd
        monkeypatch.setattr(formula_shape, "ODD_NITROGEN_MIN", 3)
        assert P.priors_met(counts, odd) == ()
        monkeypatch.setattr(formula_shape, "ODD_NITROGEN_MIN", 2)
        monkeypatch.setattr(formula_shape, "ODD_NITROGEN_OXYGEN_PER_N", 0.25)
        assert P.priors_met(counts, odd) == ()

    def test_the_floor(self, monkeypatch):
        _plausibility.cache_clear()
        stuffed = "C1H4N6S6O6"
        monkeypatch.setattr(formula_shape, "PLAUSIBILITY_FLOOR", 0.01)
        try:
            assert formula_plausibility(stuffed) < 0.1
        finally:
            _plausibility.cache_clear()

    def test_the_floor_ends_each_ratio_band(self, monkeypatch):
        # H/C 20 is far past the extended band's edge of 6: the taper's floor.
        counts = element_counts("CH20")
        assert element_ratio_plausibility(counts) == pytest.approx(0.1)
        monkeypatch.setattr(formula_shape, "PLAUSIBILITY_FLOOR", 0.01)
        assert element_ratio_plausibility(counts) == pytest.approx(0.01)

    def test_the_co_occurrence_caps(self, monkeypatch):
        # Eleven nitrogens beside two each of P and S trip Rule 6's caps.
        _plausibility.cache_clear()
        nops = "C20H30N11O8P2S2"
        capped = formula_plausibility(nops)
        monkeypatch.setattr(formula_shape, "CO_OCCURRENCE_CAPS", ())
        _plausibility.cache_clear()
        try:
            assert formula_plausibility(nops) > capped
        finally:
            _plausibility.cache_clear()


class TestTheSearchProposesWhatTheRatioRuleRefused:
    """MAS-913 (#3246): chlorine and bromine under C20, sulfur under C10."""

    @pytest.mark.parametrize(
        ("neutral", "grid"),
        [
            ("C6H5Cl", "C1-20 H0-40 O0-4 Cl0-2"),
            ("C6H4Br2", "C1-20 H0-40 O0-4 Br0-2"),
            ("C4H4S", "C1-20 H0-40 O0-4 S0-2"),
            ("C2H6OS", "C1-20 H0-40 O0-4 S0-2"),
        ],
    )
    def test_the_formula_is_proposed(self, neutral, grid):
        mechanism = parse_ionization("[M+H]+")
        target = mechanism.ion_mz(calculate_mass(formula=neutral))
        config = CompositionSearchConfig(
            ionizations="[M+H]+",
            element_count_ranges=grid,
            mass_range_ppm=2.0,
        )
        found = find_compositions(target, config)
        kept, _ = apply_heuristic_rules(found, HeuristicFilterConfig(use_senior=True))
        assert neutral in {candidate["formula"] for candidate in kept}


class TestTheExemptionRule:
    @pytest.mark.parametrize(
        "rule", ["oxygen_lattice", "carbon_free", "context_prior", "nox_skeleton"]
    )
    def test_a_list_spares_a_cap_on_the_composition(self, rule):
        assert formula_shape.RULE_HALF[rule] == formula_shape.HALF_COMPOSITION
        assert formula_shape.exempt(rule, list_named=True)
        assert not formula_shape.exempt(rule, list_named=False)

    @pytest.mark.parametrize(
        "rule",
        [
            "odd_electron",
            "polyhalide_cluster",
            "oxygen_free_cluster",
            "ambiguous_nitrogen",
            "ambiguous_adduct",
        ],
    )
    def test_nothing_spares_a_cap_on_the_ion(self, rule):
        assert formula_shape.RULE_HALF[rule] == formula_shape.HALF_ION
        assert not formula_shape.exempt(rule, list_named=True)
        assert not formula_shape.exempt(rule, list_named=False)

    def test_a_rule_outside_the_table_is_spared_nothing(self):
        assert not formula_shape.exempt("lone_peak", list_named=True)

    def test_moving_a_cap_to_the_other_half_moves_who_is_spared(self, monkeypatch):
        halves = dict(formula_shape.RULE_HALF)
        halves["odd_electron"] = formula_shape.HALF_COMPOSITION
        monkeypatch.setattr(formula_shape, "RULE_HALF", halves)
        assert formula_shape.exempt("odd_electron", list_named=True)
