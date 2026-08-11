"""Unit tests for the graded chemical plausibility (Seven Golden Rules, Phase 3 / P1).

Contract: `chemical_plausibility` / `formula_plausibility` return a per-candidate
plausibility in [0, 1]. It is 0.0 **iff** the Senior/RDBE check (Rule 2) proves the
neutral graph impossible -- under a multi-state, ligand-gated valence model, so a
hypervalent SF6/H2SO4 is feasible and only a genuinely over-saturated formula is cut --
and otherwise it is the element-ratio (Rules 4-5) x heteroatom co-occurrence (Rule 6)
product FLOORED at `PLAUSIBILITY_FLOOR`. It is graded (weighs candidates) rather than a
boolean gate, and is conservative / fail-open: unusual chemistry is never driven to 0.
Numbers are from Kind & Fiehn 2007 (BMC Bioinformatics 8:105), Tables 2-3.
See docs/dev/assignment_confidence.md (P1).
"""

import polars as pl

from mascope_tools.composition.heuristic_filter import (
    PLAUSIBILITY_FLOOR,
    chemical_plausibility,
    element_counts,
    element_ratio_plausibility,
    formula_plausibility,
    heteroatom_probability_plausibility,
    senior_plausibility,
)


# The distinct NEUTRAL target_compound formulas seeded into the demo bundle
# (mascope_demo). Embedded so the validation is hermetic (no DB at test time);
# mirrors how rule_senior was validated. C6H17NO4 is the one over-saturated
# data error (17 H on a C6NO4 skeleton, max 15) -- see the handoff doc.
DEMO_GOLDEN_FORMULAS = [
    "Br",
    "Br2",
    "C10H12O",
    "C10H12O2",
    "C10H14O",
    "C10H14O2",
    "C10H14O3",
    "C10H14O4",
    "C10H14O5",
    "C10H14O6",
    "C10H14O7",
    "C10H14O9",
    "C10H15O5",
    "C10H16O",
    "C10H16O2",
    "C10H16O3",
    "C10H16O4",
    "C10H16O5",
    "C10H16O6",
    "C10H16O7",
    "C10H16O9",
    "C10H17O7",
    "C10H18O2",
    "C10H18O3",
    "C10H18O4",
    "C10H18O5",
    "C10H18O6",
    "C10H19NO2",
    "C10H19NO3",
    "C10H19NO4",
    "C10H19NO5",
    "C10H20O4",
    "C10H21NO4",
    "C10H22O3",
    "C10H22O4",
    "C11H14O4",
    "C12H26O5",
    "C13H18O4",
    "C16H22O4",
    "C20H32O9",
    "C22H42O4",
    "C3H6O3",
    "C3H7NO",
    "C3H8O3",
    "C4H4O2",
    "C4H6O2",
    "C5H8O2",
    "C5H8O3",
    "C6H10O2",
    "C6H11NO",
    "C6H14O3",
    "C6H14O4",
    "C6H17NO4",
    "C6H8O",
    "C6H8O2",
    "C6H8O3",
    "C7H10O3",
    "C7H10O4",
    "C7H12O4",
    "C7H16O3",
    "C8H10O2",
    "C8H12O",
    "C8H12O2",
    "C8H12O3",
    "C8H12O4",
    "C8H12O5",
    "C8H14O2",
    "C8H18O3",
    "C8H18O5",
    "C9H12O2",
    "C9H12O3",
    "C9H12O4",
    "C9H14",
    "C9H14O",
    "C9H14O2",
    "C9H14O3",
    "C9H14O4",
    "C9H14O5",
    "C9H14O6",
    "C9H14O7",
    "C9H15NO",
    "C9H15O6",
    "C9H16O5",
    "C9H16O6",
    "C9H16O7",
    "C9H19NO",
    "C9H20O4",
    "CH2O2",
    "CH4N2O",
    "HNO3",
    "O",
]
DEMO_DATA_ERROR = "C6H17NO4"  # the single over-saturated (impossible) neutral


# --- Senior / RDBE factor (Rule 2) -----------------------------------------


def test_senior_feasible_molecules_full():
    for f in ["C6H6", "C6H12O6", "C8H10N4O2", "H2O", "NH3", "CH4", "CO2"]:
        assert senior_plausibility(element_counts(f)) == 1.0


def test_senior_oversaturated_zero():
    # negative RDBE -> impossible for any structure -> driven to 0
    for f in ["CH5", "C2H8", "C6H17NO4"]:
        assert senior_plausibility(element_counts(f)) == 0.0


def test_senior_radicals_fail_open():
    # odd-electron species can be genuine (APCI/APPI) -> not penalised
    for f in ["CH3", "C2H5", "C10H15O5", "C9H15O6", "Br"]:
        assert senior_plausibility(element_counts(f)) == 1.0


def test_senior_unknown_elements_fail_open():
    for f in ["C2H6Fe", "FeCl3", "C6H5MgBr"]:
        assert senior_plausibility(element_counts(f)) == 1.0


def test_senior_hypervalent_full():
    # S/P/halogen reach 6/5/7 when the formula supplies electronegative ligands
    for f in ["SF6", "PF5", "IF5", "SF5CF3", "PCl5", "IF7", "S2F10"]:
        assert senior_plausibility(element_counts(f)) == 1.0


def test_senior_hypervalent_without_ligands_zero():
    # ... and only then: there is no SH6 or PH6, so the promotion must not be free
    for f in ["SH4", "PH6", "CH6S", "C2H7Cl", "C5H14S"]:
        assert senior_plausibility(element_counts(f)) == 0.0


# --- Element-ratio factor (Rules 4-5) --------------------------------------


def test_ratio_common_range_is_one():
    # typical organics with every ratio inside the common band
    for f in ["C6H12O6", "C10H16O2", "C9H14", "C6H6"]:
        assert element_ratio_plausibility(element_counts(f)) == 1.0


def test_ratio_carbon_free_fails_open():
    # X/C is undefined without carbon -> no ratio penalty
    for f in ["H2O", "NH3", "HNO3", "Br", "O"]:
        assert element_ratio_plausibility(element_counts(f)) == 1.0


def test_ratio_high_oxygen_graded_not_rejected():
    # formic acid CH2O2: O/C = 2 is past the common max (1.2) but inside the
    # extended band (0-3) -> graded down, never near zero
    s = element_ratio_plausibility(element_counts("CH2O2"))
    assert 0.5 < s < 1.0


def test_ratio_monotonic_in_excess():
    # pushing O/C further into the tail lowers the score monotonically
    scores = [
        element_ratio_plausibility({"C": 1, "H": 2, "O": o}) for o in (1, 2, 3, 6)
    ]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 1.0  # O/C = 1 is common
    assert all(s >= PLAUSIBILITY_FLOOR for s in scores)


def test_ratio_extreme_hydrogen_low_but_floored():
    # an absurd H/C (e.g. C1H20) is deep in the tail -> at/above the floor, never 0
    s = element_ratio_plausibility({"C": 1, "H": 20})
    assert PLAUSIBILITY_FLOOR <= s < 0.5


def test_ratio_factor_floored():
    # Four ratios in the tail multiply to 2e-4 -- 500x below the per-ratio floor and
    # indistinguishable from an impossible formula. The product is floored too.
    counts = {"C": 1, "H": 20, "O": 6, "N": 6, "S": 6}
    assert element_ratio_plausibility(counts) == PLAUSIBILITY_FLOOR


# --- Heteroatom co-occurrence factor (Rule 6) ------------------------------


def test_rule6_no_trigger_is_one():
    # ordinary formulas trigger no multi-element restriction
    for f in ["C6H12O6", "C10H19NO4", "C3H7NO", "CH4N2O"]:
        assert heteroatom_probability_plausibility(element_counts(f)) == 1.0


def test_rule6_penalises_excess_phosphorus():
    # OPS all present (>1) with P far above its cap of 3 -> penalised
    counts = {"C": 10, "H": 10, "O": 6, "P": 8, "S": 2}
    s = heteroatom_probability_plausibility(counts)
    assert PLAUSIBILITY_FLOOR <= s < 1.0


def test_rule6_within_caps_is_one():
    # trigger holds but all counts within caps -> no penalty
    counts = {"C": 10, "H": 10, "N": 2, "O": 2, "P": 2, "S": 2}
    assert heteroatom_probability_plausibility(counts) == 1.0


def test_rule6_element_penalised_once():
    # Four of the five nested checks fire on this formula, so multiplying every triggered
    # cap penalised N three times, O three, P four and S three (composite 0.0038). Each
    # element must be penalised once, by the tightest cap placed on it.
    counts = {"C": 20, "H": 20, "N": 12, "O": 25, "P": 7, "S": 4}
    tightest = (4 / 12) * (14 / 25) * (3 / 7) * (3 / 4)
    assert heteroatom_probability_plausibility(counts) == max(
        PLAUSIBILITY_FLOOR, tightest
    )


# --- Combined plausibility --------------------------------------------------


def test_formula_plausibility_bounds():
    for f in DEMO_GOLDEN_FORMULAS + ["CH5", "C2H6Fe", "not-a-formula", ""]:
        s = formula_plausibility(f)
        assert 0.0 <= s <= 1.0


def test_unparseable_fails_open():
    assert formula_plausibility("???") == 1.0


def test_combined_is_the_floored_product_of_the_graded_factors():
    # Senior is not a factor but a switch: 0.0 for an impossible graph, otherwise the two
    # graded factors multiply and the result is floored.
    counts = element_counts("CH2O2")
    assert formula_plausibility("CH2O2") == max(
        PLAUSIBILITY_FLOOR,
        element_ratio_plausibility(counts)
        * heteroatom_probability_plausibility(counts),
    )
    assert formula_plausibility("C6H17NO4") == 0.0
    assert senior_plausibility(element_counts("C6H17NO4")) == 0.0


def test_composite_never_below_floor_unless_impossible():
    # The reported over-count: possible chemistry that scored 0.0037, i.e. 27x BELOW the
    # floor and effectively a hard reject by a layer that only grades.
    counts = {"C": 20, "H": 20, "N": 12, "O": 25, "P": 7, "S": 4}
    formula = "C20H20N12O25P7S4"
    assert element_counts(formula) == counts
    assert heteroatom_probability_plausibility(counts) == PLAUSIBILITY_FLOOR
    assert formula_plausibility(formula) == PLAUSIBILITY_FLOOR
    # 0.0 is still reachable -- it is reserved for a provably impossible graph.
    assert formula_plausibility(DEMO_DATA_ERROR) == 0.0


def test_atmospheric_tracers_are_plausible():
    # SF6/PF5/IF5/PCl5 scored 0.0 -- a hard chemistry veto on real tracers, which in
    # `evidence = fit x plausibility` deletes the assignment however well it fits.
    for f in ["SF6", "PF5", "IF5", "PCl5"]:
        assert formula_plausibility(f) == 1.0
    # SF5CF3 is graded down (F/C = 8 is past Table 2's extended max of 6), not rejected:
    # correct behaviour for a perfluoro species.
    assert formula_plausibility("SF5CF3") > PLAUSIBILITY_FLOOR


# --- Golden validation: nothing real is wrongly rejected -------------------


def test_demo_goldens_are_plausible():
    """Every genuine demo target formula scores well clear of a reject; only the
    known over-saturated data error is driven low."""
    for f in DEMO_GOLDEN_FORMULAS:
        s = formula_plausibility(f)
        if f == DEMO_DATA_ERROR:
            assert s == 0.0, f"{f} should be flagged impossible"
        else:
            assert s > 0.5, f"real demo formula {f} wrongly demoted to {s:.3f}"


def test_demo_radicals_stay_plausible():
    # the fail-open radicals from the handoff doc keep full plausibility
    for f in ["C9H15O6", "C10H15O5", "C10H17O7", "Br"]:
        assert formula_plausibility(f) == 1.0


# --- DataFrame entry point --------------------------------------------------


def test_chemical_plausibility_series():
    df = pl.DataFrame({"formula": ["C6H12O6", "C6H17NO4", "CH2O2"]})
    scores, logs = chemical_plausibility(df)
    assert scores.dtype == pl.Float64
    assert list(scores)[0] == 1.0
    assert list(scores)[1] == 0.0
    assert 0.5 < list(scores)[2] < 1.0
    # the impossible formula is surfaced in the log
    assert any("C6H17NO4" in m for m in logs)


def test_chemical_plausibility_empty():
    scores, logs = chemical_plausibility(pl.DataFrame({"formula": []}))
    assert list(scores) == []
    assert logs == []
