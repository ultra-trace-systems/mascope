import pandas as pd
import pytest

from mascope_tools.composition.exceptions import CompositionFinderException
from mascope_tools.composition.finder import (
    assign_compositions,
    replace_atom_with_isotope,
)
from mascope_tools.composition.models import CompositionSearchConfig
from mascope_tools.composition.utils import (
    combine_formula_and_ionization,
    parse_atom_count_ranges,
    parse_ionization,
    to_hill_order,
)


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
