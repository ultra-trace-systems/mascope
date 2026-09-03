import pandas as pd
import pytest

from mascope_tools.composition.exceptions import CompositionFinderException
from mascope_tools.composition.finder import (
    _other_candidate_formulas,
    assign_compositions,
    find_compositions,
    replace_atom_with_isotope,
)
from mascope_tools.composition.models import CompositionSearchConfig
from mascope_tools.composition.utils import (
    combine_formula_and_ionization,
    parse_atom_count_ranges,
    parse_ionization,
    to_hill_order,
)


# Protonated glucose, C6H12O6 + H+ (approximate; the exact prediction comes back
# on the search result, so nothing here depends on this literal's last digits).
PROTONATED_GLUCOSE_MZ = 181.070664


def test_replace_atom_with_isotope():
    """Test the replace_atom_with_isotope function with various cases."""
    # ion, isotope label, expected output
    test_cases = [
        ("C6H12O6+", "13C", "[13C]C5H12O6+"),  # single carbon replacement
        ("C6H12O6+", "13C3", "[13C]3C3H12O6+"),  # multiple carbon replacement
        (
            "C6H12O6+",
            "13C3+2H",
            "[13C]3[2H]C3H11O6+",
        ),  # carbon and hydrogen replacement
        (
            "C6H12O6+",
            "13C3+2H2",
            "[13C]3[2H]2C3H10O6+",
        ),  # carbon and multiple hydrogen replacement
    ]
    for ion, isotope_label, expected in test_cases:
        result = replace_atom_with_isotope(ion, isotope_label)
        assert result == expected, (
            f"replace_atom_with_isotope({ion}, {isotope_label}) = {result}, expected {expected}"
        )

    # Cases that should raise ValueError
    error_cases = [
        ("C6H12O6+", "15N"),  # no nitrogen in formula
        ("C6H12O6+", "Ultra"),  # invalid isotope label
    ]
    for ion, isotope_label in error_cases:
        with pytest.raises(ValueError):
            replace_atom_with_isotope(ion, isotope_label)


def test_parse_atom_count_ranges_accepts_bracket_first_isotopes():
    atoms = parse_atom_count_ranges("C0-50 H0-100 [15N]0-1 [13C]0-2")
    symbols = [atom.symbol for atom in atoms]

    assert symbols == ["C", "H", "[15N]", "[13C]"]
    assert atoms[2].min_count == 0
    assert atoms[2].max_count == 1
    assert atoms[3].min_count == 0
    assert atoms[3].max_count == 2


def test_parse_atom_count_ranges_rejects_legacy_element_first_isotopes():
    with pytest.raises(CompositionFinderException, match="Invalid isotope format"):
        parse_atom_count_ranges("C0-50 H0-100 N[15]0-1")


def test_parse_atom_count_ranges_rejects_malformed_tokens():
    with pytest.raises(CompositionFinderException, match="Invalid element count range"):
        parse_atom_count_ranges("C0-50 [15]0-1")


def test_assign_compositions_no_matches():
    """assign_compositions should not raise when no peaks match any composition."""
    # Use m/z values far outside what C0-2 H0-2 can produce with H+ ionization
    peaks = pd.DataFrame({"mz": [9999.0, 9998.0], "intensity": [100.0, 100.0]})
    config = CompositionSearchConfig(
        ionizations="H+",
        element_count_ranges="C0-2 H0-2",
        mass_range_ppm=5.0,
    )
    matches, log_messages = assign_compositions(peaks, config)

    assert isinstance(matches, pd.DataFrame)
    assert len(matches) == 2
    assert "mz" in matches.columns
    assert "formula" in matches.columns
    assert "ion" in matches.columns
    assert "isotope_label" in matches.columns
    assert (matches["formula"] == "---").all()
    assert (matches["ion"] == "---").all()


def test_to_hill_order_places_isotopes_first():
    formula = to_hill_order({"O": 3, "[15N]": 1})
    assert formula == "[15N]O3"


def test_to_hill_order_places_isotope_before_same_plain_element():
    formula = to_hill_order({"C": 5, "[13C]": 1, "H": 12, "O": 6})
    assert formula == "[13C]C5H12O6"


def test_to_hill_order_normalizes_element_first_isotope_keys():
    formula = to_hill_order({"O": 3, "N[15]": 1})
    assert formula == "[15N]O3"


def test_to_hill_order_keeps_standard_hill_order_for_regular_elements():
    formula = to_hill_order({"H": 12, "O": 6, "C": 6})
    assert formula == "C6H12O6"


def test_combine_formula_and_ionization_accepts_isotope_formula():
    ionization_mechanism = parse_ionization("+")
    ion_formula = combine_formula_and_ionization("[15N]O3", ionization_mechanism)
    assert ion_formula == "[15N]O3+"


def _protonated_glucose_search(shift_ppm: float):
    """Search a peak sitting `shift_ppm` off protonated glucose's exact m/z."""
    config = CompositionSearchConfig(
        ionizations="+H+",
        element_count_ranges="C0-10 H0-20 O0-10",
        mass_range_ppm=5.0,
    )
    target_mz = PROTONATED_GLUCOSE_MZ * (1 + shift_ppm * 1e-6)
    results = [
        r for r in find_compositions(target_mz, config) if r["formula"] == "C6H12O6"
    ]
    assert len(results) == 1
    predicted_mz = results[0]["neutral_mass"] + parse_ionization("+H+").mass
    return target_mz, predicted_mz, results[0]


def test_composition_error_ppm_is_signed_observed_minus_predicted():
    # A peak 3 ppm BELOW the composition it matches must report -3 ppm. The
    # engine persists this as PeakAssignment.mz_error_ppm whenever a row has no
    # isotope-envelope error, next to the targeted stage's signed match_mz_error,
    # and the UI both shows it and recovers the predicted m/z from it.
    _, _, low = _protonated_glucose_search(-3.0)
    assert low["composition_error_ppm"] == pytest.approx(-3.0, abs=1e-2)

    _, _, high = _protonated_glucose_search(3.0)
    assert high["composition_error_ppm"] == pytest.approx(3.0, abs=1e-2)


def test_predicted_mz_is_recoverable_from_composition_error_ppm():
    # The chart recovers theoretical_mz = observed_mz / (1 + mz_error_ppm/1e6);
    # that is exact only because the ppm error is signed AND relative to the
    # prediction rather than to the observation.
    for shift_ppm in (-3.0, 3.0):
        target_mz, predicted_mz, result = _protonated_glucose_search(shift_ppm)
        recovered = target_mz / (1 + result["composition_error_ppm"] / 1e6)
        assert recovered == pytest.approx(predicted_mz, rel=1e-12)


def test_composition_results_are_ranked_by_error_magnitude():
    # find_compositions returns best-first, and "best" is the smallest deviation
    # in either direction - not the most negative one.
    config = CompositionSearchConfig(
        ionizations="+H+",
        element_count_ranges="C0-40 H0-80 N0-10 O0-20",
        mass_range_ppm=20.0,
    )
    results = find_compositions(PROTONATED_GLUCOSE_MZ, config)
    magnitudes = [abs(r["composition_error_ppm"]) for r in results]
    assert magnitudes == sorted(magnitudes)
    assert any(r["composition_error_ppm"] < 0 for r in results), (
        "expected at least one candidate below the observed m/z, so the ordering "
        "is actually exercised on signed values"
    )


# `other_candidates` is the shortlist shown beside a committed assignment, so the
# composition that won the peak must never be in it. It cannot be taken
# positionally: `find_compositions` ranks by mass error, while the winner is
# whatever survives `apply_heuristic_rules` and then ranks first on
# `match_isotopic_pattern`'s isotope-pattern score.
def test_other_candidates_excludes_the_chosen_composition():
    comp_results = [
        {"formula": "C4H8N2O"},
        {"formula": "C5H10O2"},
        {"formula": "C6H14N"},
    ]

    # The isotope pattern promoted the second-closest composition.
    assert _other_candidate_formulas(comp_results, "C5H10O2") == "C4H8N2O, C6H14N"


def test_other_candidates_keeps_the_mass_closest_composition_when_it_loses():
    # Dropping index 0 also hid the mass-closest formula, which is exactly the
    # runner-up worth seeing when the isotope pattern demoted it.
    comp_results = [{"formula": "C4H8N2O"}, {"formula": "C5H10O2"}]

    assert _other_candidate_formulas(comp_results, "C5H10O2") == "C4H8N2O"


def test_other_candidates_keeps_every_composition_when_none_was_chosen():
    comp_results = [{"formula": "C4H8N2O"}, {"formula": "C5H10O2"}]

    assert _other_candidate_formulas(comp_results) == "C4H8N2O, C5H10O2"


def test_other_candidates_is_empty_when_the_winner_stood_alone():
    assert _other_candidate_formulas([{"formula": "C5H10O2"}], "C5H10O2") == ""
    assert _other_candidate_formulas([]) == ""


def test_assign_compositions_enumerates_only_the_targets(monkeypatch):
    """With ``targets`` given, compositions are enumerated for those peaks alone
    while every peak still gets a result row - the rest as unmatched."""
    from mascope_tools.composition import finder

    enumerated = []

    def fake_find_compositions(target_mz, config):
        enumerated.append(target_mz)
        return []

    monkeypatch.setattr(finder, "find_compositions", fake_find_compositions)
    peaks = pd.DataFrame(
        {"mz": [100.0, 200.0, 300.0], "intensity": [100.0, 100.0, 100.0]}
    )
    config = CompositionSearchConfig(
        ionizations="H+", element_count_ranges="C0-2 H0-2", mass_range_ppm=5.0
    )

    matches, _ = finder.assign_compositions(peaks, config, targets=[200.0])

    assert enumerated == [200.0]
    assert len(matches) == 3
    assert (matches["formula"] == "---").all()
