"""Compare Mascope Tools composition candidates with ChemInfo query results."""

import httpx
import pytest

from mascope_backend.api.new.cheminfo.config import cheminfo_config
from mascope_backend.api.new.cheminfo.utils import (
    to_cheminfo_ionization_format,
    to_custom_element_format,
    to_explicit_isotope_format,
)
from mascope_tools.composition import CompositionSearchConfig
from mascope_tools.composition.finder import find_compositions
from mascope_tools.composition.utils import (
    normalize_formula_with_isotopes,
    parse_composition,
    to_hill_order,
)


def _normalize_formula(formula: str) -> str:
    """Normalize formulas for cross-system comparisons."""
    if formula is None or formula == "":
        return "()"

    custom_formula = to_custom_element_format(formula)
    normalized_formula = normalize_formula_with_isotopes(custom_formula)
    return to_hill_order(parse_composition(normalized_formula))


def _build_formula_ranges(formula_ranges_addition: str) -> str:
    """Build the formula ranges string for queries, combining defaults with any
    test-specific additions."""
    formula_ranges = cheminfo_config.DEFAULT_FORMULA_RANGE
    if formula_ranges_addition:
        formula_ranges = f"{formula_ranges} {formula_ranges_addition}"
    return formula_ranges


def _fetch_cheminfo_formulas(
    mz: float, formula_ranges: str, ionization_mechanisms: list[str]
) -> set[str]:
    """Fetch candidate formulas from ChemInfo for a given m/z and formula ranges."""
    explicit_ranges, _ = to_explicit_isotope_format(formula_ranges)
    ionizations = ",".join(
        [to_cheminfo_ionization_format(i) for i in ionization_mechanisms]
    )

    params = {
        "mass": mz,
        "ionizations": ionizations,
        "precision": cheminfo_config.DEFAULT_MZ_PRECISION,
        "ranges": explicit_ranges,
        "allowNeutral": "false",
    }

    with httpx.Client(timeout=cheminfo_config.REQUEST_TIMEOUT) as client:
        response = client.get(
            f"{cheminfo_config.BASE_URL}/v1/mfFromMonoisotopicMass",
            params=params,
        )
        response.raise_for_status()
        payload = response.json()

    return {
        _normalize_formula((row.get("mf") or "()")) for row in payload.get("result", [])
    }


def _find_mascope_formulas(
    mz: float, formula_ranges: str, ionization_mechanisms: list[str]
) -> set[str]:
    """Fetch candidate formulas from Mascope Tools
    for a given m/z and formula ranges."""
    explicit_ranges, _ = to_explicit_isotope_format(formula_ranges)
    config = CompositionSearchConfig(
        ionizations=",".join(ionization_mechanisms),
        mass_range_ppm=cheminfo_config.DEFAULT_MZ_PRECISION,
        element_count_ranges=explicit_ranges,
        max_result_rows=1000,
    )

    results = find_compositions(mz, config)
    return {_normalize_formula(result.get("formula", "")) for result in results}


class TestDirectCompositionAssignment:
    """Validate m/z → formula with '-' (electron capture) ionization only.

    Uses only the trivial '-' mechanism to isolate formula finding and parsing
    from ionization mechanism handling.
    """

    # All test case masses computed assuming "-" ionization
    TEST_CASES = [
        # Basic test cases
        (46.00603, "CH2O2", ""),
        (62.99619, "HNO3", ""),
        (90.03224, "C3H6O3", ""),
        (227.00313, "C3H5N3O9", ""),
        (317.15923, "C13H23N3O6", ""),
        (323.07322, "C10H15N2O10", ""),
        (456.36089, "C30H48O3", ""),
        # Extended test cases, covering non-default parameters
        (62.9854, "O3^N", "^N0-1"),
        (78.9189, "Br", "Br0-1"),
        (80.9168, "Br", "[81Br]0-1"),
        (96.96010, "HSO4", "S0-1"),
        (141.92850, "CH3I", "I0-1"),
        (195.90023, "CF3I", "I0-1 F0-3"),
        (539.75763, "C15H12Br4O2", "Br0-4"),
    ]

    IONIZATION_MECHANISMS = ["-"]

    @pytest.mark.parametrize(
        "mz, expected_formula, formula_ranges_addition", TEST_CASES
    )
    def test_composition_set_matches_cheminfo(
        self,
        mz: float,
        expected_formula: str,
        formula_ranges_addition: str,
    ):
        """Compare full formula sets returned by ChemInfo and Mascope Tools."""
        formula_ranges = _build_formula_ranges(formula_ranges_addition)

        cheminfo_formulas = _fetch_cheminfo_formulas(
            mz, formula_ranges, self.IONIZATION_MECHANISMS
        )
        mascope_formulas = _find_mascope_formulas(
            mz, formula_ranges, self.IONIZATION_MECHANISMS
        )
        expected_normalized = _normalize_formula(expected_formula)

        assert expected_normalized in cheminfo_formulas, (
            f"Expected formula {expected_formula!r} is missing"
            f" from ChemInfo results for m/z {mz}."
        )
        assert expected_normalized in mascope_formulas, (
            f"Expected formula {expected_formula!r} is missing"
            f" from Mascope Tools results for m/z {mz}."
        )

        missing_from_mascope = sorted(cheminfo_formulas - mascope_formulas)
        extra_in_mascope = sorted(mascope_formulas - cheminfo_formulas)

        assert mascope_formulas == cheminfo_formulas, (
            f"Formula set mismatch for m/z {mz}. "
            f"Missing in Mascope: {missing_from_mascope}. "
            f"Extra in Mascope: {extra_in_mascope}."
        )


class TestIonizationMechanismCompositionAssignment:
    """Validate m/z → neutral formula with various ionization mechanisms.

    Tests that ionization mechanism handling correctly derives neutral formulas
    from m/z values produced by different ionization processes.
    """

    # (m/z, expected_neutral_formula, ionization_mechanism, formula_ranges_addition)
    # m/z values computed from known neutral masses using the specified mechanism.
    TEST_CASES = [
        # +H+ (protonation, [M+H]+)
        (47.01276, "CH2O2", "+H+", ""),
        (64.00292, "HNO3", "+H+", ""),
        (64.99995, "[15N]HO3", "+H+", "[15N]0-1"),
        (91.03897, "C3H6O3", "+H+", ""),
        # + (electron loss, M+)
        (46.00493, "CH2O2", "+", ""),
        (47.00829, "[13C]H2O2", "+", "[13C]0-1"),
        (62.99509, "HNO3", "+", ""),
        # -H+ (H removal, [M-H]-)
        (89.02441, "C3H6O3", "-H+", ""),
        # +Br- (bromide adduct, [M+Br]-)
        (124.92437, "CH2O2", "+Br-", ""),
        # +NO3- (nitrate adduct, [M+NO3]-)
        (107.99384, "CH2O2", "+NO3-", ""),
        # +(CH4N2O)H+ (uronium adduct, [M+uronium+H]+)
        (124.03528, "HNO3", "+(CH4N2O)H+", ""),
    ]

    @pytest.mark.parametrize(
        "mz, expected_formula, ionization, formula_ranges_addition", TEST_CASES
    )
    def test_composition_set_matches_cheminfo(
        self,
        mz: float,
        expected_formula: str,
        ionization: str,
        formula_ranges_addition: str,
    ):
        """Compare formula sets using a specific ionization mechanism."""
        formula_ranges = _build_formula_ranges(formula_ranges_addition)
        ionization_mechanisms = [ionization]

        cheminfo_formulas = _fetch_cheminfo_formulas(
            mz, formula_ranges, ionization_mechanisms
        )
        mascope_formulas = _find_mascope_formulas(
            mz, formula_ranges, ionization_mechanisms
        )
        expected_normalized = _normalize_formula(expected_formula)

        assert expected_normalized in cheminfo_formulas, (
            f"Expected formula {expected_formula!r} is missing"
            f" from ChemInfo results"
            f" for m/z {mz} with ionization {ionization!r}."
        )
        assert expected_normalized in mascope_formulas, (
            f"Expected formula {expected_formula!r} is missing"
            f" from Mascope Tools results"
            f" for m/z {mz} with ionization {ionization!r}."
        )

        missing_from_mascope = sorted(cheminfo_formulas - mascope_formulas)
        extra_in_mascope = sorted(mascope_formulas - cheminfo_formulas)

        assert mascope_formulas == cheminfo_formulas, (
            f"Formula set mismatch for m/z {mz}"
            f" with ionization {ionization!r}. "
            f"Missing in Mascope: {missing_from_mascope}. "
            f"Extra in Mascope: {extra_in_mascope}."
        )
