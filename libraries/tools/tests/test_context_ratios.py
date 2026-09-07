"""``rule_context_ratios``: the chemistry context's Van Krevelen windows.

The rule exists because an unconstrained grid fits nitrogen- and oxygen-stuffed
formulas to any peak within tolerance - 13-17% of the measured baseline's
committed formulas carry five or more nitrogens. What it must not do is reject
the small and the halogenated molecules a CIMS source is most sensitive to,
which is why it reads effective counts and stands down below a carbon floor.
"""

import polars as pl
import pytest

from mascope_tools.composition import profiles as P
from mascope_tools.composition.heuristic_filter import (
    apply_heuristic_rules,
    rule_context_ratios,
)
from mascope_tools.composition.models import HeuristicFilterConfig


AMBIENT = P.AMBIENT_AIR.ratio_windows()


def _kept(formulas, windows=AMBIENT):
    """The formulas the rule keeps, as a set."""
    frame = pl.DataFrame({"formula": list(formulas)})
    config = HeuristicFilterConfig(context_ratio_windows=windows)
    mask, _ = rule_context_ratios(frame, heuristics_config=config)
    return set(frame.filter(mask).get_column("formula").to_list())


class TestTheWindowsApply:
    def test_an_ambient_oxidation_product_is_kept(self):
        # A pinene HOM: the chemistry the ambient window was drawn around.
        assert _kept(["C10H16O7"]) == {"C10H16O7"}

    def test_a_nitrogen_stuffed_formula_is_rejected(self):
        # N/C 0.5 against an ambient window of 0.4; the shape of the measured
        # baseline's worst answers.
        assert _kept(["C10H20N5O3"]) == set()

    def test_an_oxygen_lattice_is_rejected(self):
        # O/C 1.8 on a saturated backbone: fits the mass, is not a molecule
        # ambient air makes.
        assert _kept(["C5H10O9"]) == set()

    def test_a_saturated_polyol_survives_the_upper_h_to_c_edge(self):
        # Glycerol C3H8O3 reads H/C 2.67 and is real signal; the window's
        # ceiling is 2.75 for exactly this reason.
        assert _kept(["C3H8O3"]) == {"C3H8O3"}

    def test_an_over_saturated_formula_is_rejected_on_h_to_c(self):
        assert _kept(["C4H14O2"]) == set()


class TestEffectiveCounts:
    def test_a_halogenated_acid_is_not_rejected_on_hydrogen(self):
        # Trichloroacetic acid C2HCl3O2 reads H/C 0.5 raw - below the 0.7 floor -
        # and (H+X)/C 2.0 effective. Raw counts would reject a real analyte.
        # (C2 sits below the carbon floor, so this checks C3 too.)
        assert _kept(["C3H2Cl4O2"]) == {"C3H2Cl4O2"}

    def test_silicon_counts_as_a_backbone_atom(self):
        # D4 siloxane C8H24O4Si4: H/C 3.0 raw, 2.0 on (C+Si). It is a real
        # contaminant, not a formula the ratio layer should be inventing a
        # verdict about.
        assert _kept(["C8H24O4Si4"], P.INDOOR_AIR.ratio_windows()) == {"C8H24O4Si4"}

    def test_dbe_uses_the_same_effective_counts(self):
        # Benzene C6H6: DBE 4, DBE/C 0.67, inside the ambient 0.75 ceiling.
        # Naphthalene C10H8: DBE 7, DBE/C 0.7 - also in. Anthracene C14H10 is
        # DBE 10, DBE/C 0.71. A combustion context is where high DBE belongs.
        assert _kept(["C6H6", "C10H8"]) == {"C6H6", "C10H8"}
        assert _kept(["C16H10"]) == set()  # DBE/C 0.75+, pure PAH
        assert _kept(["C16H10"], P.COMBUSTION.ratio_windows()) == {"C16H10"}


class TestTheCarbonFloor:
    @pytest.mark.parametrize("formula", ["CH4N2O", "CH2O2", "CH4O", "C2H6O", "C2H4O2"])
    def test_small_molecules_are_not_ratio_judged(self, formula):
        # Urea reads H/C 4.0 and formic acid O/C 2.0. Both are outside every
        # ambient window and both are real; below three carbons the windows say
        # nothing, so they must not be applied.
        assert _kept([formula]) == {formula}

    def test_a_small_molecule_can_still_be_rejected_on_valence(self):
        # The floor is not a hole: an oxygen count no one-carbon skeleton can
        # bond is still out.
        assert _kept(["CH2O9"]) == set()
        assert _kept(["CH4N5"]) == set()


class TestFailOpen:
    def test_no_windows_keeps_everything(self):
        # The identity context, and every caller that names no context at all.
        assert _kept(["C10H20N5O3", "C5H10O9"], {}) == {"C10H20N5O3", "C5H10O9"}

    def test_an_unparseable_formula_is_deferred(self):
        assert _kept(["not-a-formula"]) == {"not-a-formula"}

    def test_an_empty_frame_is_handled(self):
        frame = pl.DataFrame({"formula": []}, schema={"formula": pl.Utf8})
        mask, _ = rule_context_ratios(
            frame,
            heuristics_config=HeuristicFilterConfig(context_ratio_windows=AMBIENT),
        )
        assert mask.len() == 0


class TestInTheFilterChain:
    def test_the_rule_runs_as_part_of_apply_heuristic_rules(self):
        candidates = [
            {"formula": "C10H16O7"},
            {"formula": "C10H20N5O3"},
        ]
        kept, _ = apply_heuristic_rules(
            candidates,
            HeuristicFilterConfig(use_senior=True, context_ratio_windows=AMBIENT),
        )
        assert [row["formula"] for row in kept] == ["C10H16O7"]

    def test_the_default_config_filters_as_it_did_before_contexts(self):
        candidates = [{"formula": "C10H20N5O3"}]
        kept, _ = apply_heuristic_rules(candidates, HeuristicFilterConfig())
        assert [row["formula"] for row in kept] == ["C10H20N5O3"]
