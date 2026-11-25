"""The chemistry context's Van Krevelen windows: a floored factor, and a gate on request.

The windows exist because an unconstrained grid fits nitrogen- and oxygen-stuffed
formulas to any peak within tolerance - 13-17% of the measured baseline's
committed formulas carry five or more nitrogens. What they must not do is judge
the small and the halogenated molecules a CIMS source is most sensitive to,
which is why they read effective counts and stand down below a carbon floor.

Since decision MAS-979 they grade a formula rather than refuse it
(``context_window_plausibility``, read through ``graded_in``): under a wrong
context the gate removed genuine species (#3188). ``rule_context_ratios``
refuses only where a caller opts in to the gate, and the gate's tests below ask
it so; the factor reads the same windows.
"""

import polars as pl
import pytest

from mascope_tools.composition import formula_shape
from mascope_tools.composition import profiles as P
from mascope_tools.composition.formula_shape import (
    graded_in,
    graded_run,
    listed_in,
    spare_listed,
)
from mascope_tools.composition.heuristic_filter import (
    _plausibility,
    apply_heuristic_rules,
    context_window_plausibility,
    element_counts,
    formula_plausibility,
    rule_context_ratios,
)
from mascope_tools.composition.models import HeuristicFilterConfig
from mascope_tools.composition.utils import formula_identity


AMBIENT = P.AMBIENT_AIR.ratio_windows()


def _kept(formulas, windows=AMBIENT, gate=True):
    """The formulas the rule keeps, as a set: the gate, unless ``gate`` is off."""
    frame = pl.DataFrame({"formula": list(formulas)})
    config = HeuristicFilterConfig(
        context_ratio_windows=windows, context_windows_gate=gate
    )
    mask, _ = rule_context_ratios(frame, heuristics_config=config)
    return set(frame.filter(mask).get_column("formula").to_list())


def _factor(formula, windows=AMBIENT, nox_skeleton=False):
    return context_window_plausibility(
        element_counts(formula), windows, nox_skeleton=nox_skeleton
    )


class TestTheWindowsGrade:
    """#3188: outside a window a formula's score falls by the floored factor,
    and nothing is refused."""

    def test_the_rule_refuses_nothing_unless_the_gate_is_asked_for(self):
        stuffed = ["C10H20N5O3", "C5H10O9", "C4H14O2", "CH2O9"]
        assert _kept(stuffed, gate=False) == set(stuffed)

    def test_a_formula_inside_every_window_keeps_its_plausibility(self):
        assert _factor("C10H16O7") == 1.0
        with graded_in(AMBIENT):
            assert formula_plausibility("C10H16O7") == _plausibility("C10H16O7", None)

    def test_a_formula_outside_a_window_is_lowered_by_the_floored_factor(self):
        # N/C 0.5 against the ambient ceiling of 0.4: a quarter of the window's
        # width past its edge is half credit, so 0.1 past a width of 0.4 is 0.5.
        assert _factor("C10H20N5O3") == pytest.approx(0.5)
        bare = _plausibility("C10H20N5O3", None)
        with graded_in(AMBIENT):
            graded = formula_plausibility("C10H20N5O3")
        assert graded == pytest.approx(
            max(formula_shape.PLAUSIBILITY_FLOOR, bare * 0.5)
        )
        assert formula_plausibility("C10H20N5O3") == bare  # the block has ended

    def test_far_outside_a_window_the_factor_is_the_floor_not_zero(self):
        # O/C 1.8 against a ceiling of 1.5 on a width of 1.5: 0.3 past it,
        # short of half credit's 0.375. O/C 2.4 is past twice that.
        assert _factor("C5H10O9") == pytest.approx(0.6)
        assert _factor("C5H10O12") == pytest.approx(formula_shape.PLAUSIBILITY_FLOOR)
        with graded_in(AMBIENT):
            assert formula_plausibility("C5H10O12") >= formula_shape.PLAUSIBILITY_FLOOR

    def test_the_floor_is_the_tables(self, monkeypatch):
        monkeypatch.setattr(formula_shape, "PLAUSIBILITY_FLOOR", 0.01)
        # O/C 2.4, past twice the span; and a C1 formula the valence checks fail.
        assert _factor("C5H10O12") == pytest.approx(0.01)
        assert _factor("CH2O9") == pytest.approx(0.01)

    def test_the_span_is_the_tables(self, monkeypatch):
        monkeypatch.setattr(formula_shape, "CONTEXT_WINDOW_SPAN", 0.5)
        # 0.1 past a width of 0.4 is now an eighth of the way to half credit.
        assert _factor("C10H20N5O3") == pytest.approx(0.75)

    def test_below_the_carbon_floor_the_valence_checks_read_one_or_the_floor(self):
        assert _factor("CH4N2O") == 1.0
        assert _factor("CH2O9") == formula_shape.PLAUSIBILITY_FLOOR

    def test_a_carbon_free_formula_is_not_judged(self):
        assert _factor("HNO3") == 1.0

    def test_no_windows_is_the_identity(self):
        assert _factor("C10H20N5O3", {}) == 1.0
        with graded_in({}):
            assert formula_plausibility("C10H20N5O3") == _plausibility(
                "C10H20N5O3", None
            )

    def test_the_skeleton_reading_lifts_an_organonitrate_inside_the_windows(self):
        # A C5 dinitrate: O/C 1.6 as written, a C5 diol skeleton inside.
        assert _factor("C5H8N2O8") < 1.0
        assert _factor("C5H8N2O8", nox_skeleton=True) == 1.0


#: Dinitrophenol, a cited list's compound: an aromatic's H/C 0.67 and DBE/C 1.0
#: sit just outside ambient air's 0.7 and 0.75.
DINITROPHENOL = "C6H4N2O5"


class TestAListNamedNeutralIsNotGraded:
    """Decision MAS-1280: a neutral a reference list the run read names is
    spared the windows' grade, as it is spared the caps on its composition. A
    cited list naming the compound is stronger evidence than a shape prior."""

    def test_dinitrophenol_sits_outside_the_ambient_windows(self):
        assert _factor(DINITROPHENOL) < 0.5

    def test_a_listed_neutral_keeps_its_universal_plausibility(self):
        bare = _plausibility(DINITROPHENOL, None)
        listed = [formula_identity("C6H4O5N2")]  # one neutral, however spelled
        with graded_in(AMBIENT):
            assert formula_plausibility(DINITROPHENOL) < bare
        with graded_in(AMBIENT, listed=listed):
            assert formula_plausibility(DINITROPHENOL) == bare
            # Every other formula is graded as before: a target-only name too.
            assert formula_plausibility("C10H20N5O3") < _plausibility(
                "C10H20N5O3", None
            )

    def test_a_run_spares_them_from_where_it_reads_its_lists(self):
        bare = _plausibility(DINITROPHENOL, None)
        with graded_in(AMBIENT):
            windows = graded_run().windows
            assert formula_plausibility(DINITROPHENOL) < bare
            spare_listed([DINITROPHENOL])
            assert formula_plausibility(DINITROPHENOL) == bare
            assert graded_run().windows == windows  # the context is unchanged
        assert graded_run() is None
        spare_listed([DINITROPHENOL])  # outside a run's block: nothing to spare
        assert graded_run() is None

    def test_another_known_sets_pass_spares_its_own_lists(self):
        # A neighbour's first pass, read for a thin file's pool.
        bare = _plausibility(DINITROPHENOL, None)
        with graded_in(AMBIENT, listed=[DINITROPHENOL]):
            with listed_in([]):
                assert formula_plausibility(DINITROPHENOL) < bare
            assert formula_plausibility(DINITROPHENOL) == bare
        with listed_in([DINITROPHENOL]):  # outside a run: the universal bands
            assert formula_plausibility(DINITROPHENOL) == bare
            assert graded_run() is None


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
            HeuristicFilterConfig(
                use_senior=True,
                context_ratio_windows=AMBIENT,
                context_windows_gate=True,
            ),
        )
        assert [row["formula"] for row in kept] == ["C10H16O7"]

    def test_without_the_gate_the_chain_keeps_both(self):
        candidates = [{"formula": "C10H16O7"}, {"formula": "C10H20N5O3"}]
        kept, _ = apply_heuristic_rules(
            candidates,
            HeuristicFilterConfig(use_senior=True, context_ratio_windows=AMBIENT),
        )
        assert [row["formula"] for row in kept] == ["C10H16O7", "C10H20N5O3"]

    def test_the_default_config_filters_as_it_did_before_contexts(self):
        candidates = [{"formula": "C10H20N5O3"}]
        kept, _ = apply_heuristic_rules(candidates, HeuristicFilterConfig())
        assert [row["formula"] for row in kept] == ["C10H20N5O3"]
