"""The charge and the mass a mechanism notation resolves to.

Two things went wrong here before and both are pinned:

- the trailing sign of a mechanism is the charge of the moiety added or
  removed, not of the ion, so ``-H+`` (deprotonation) leaves an anion. It used
  to be read as a cation, which put every deprotonated candidate's predicted M0
  two electron masses light;
- a bracketed labelled isotope in a mechanism (``+[15N]O3-``, the explicit
  form of ``+^NO3-``) used to lose its label and be massed as the unlabelled
  element, 0.997 Da light for the 15N-nitrate reagent.
"""

import numpy as np
import pytest

from mascope_tools.composition import CompositionSearchConfig
from mascope_tools.composition.config import ELECTRON_MASS
from mascope_tools.composition.exceptions import CompositionFinderException
from mascope_tools.composition.finder import find_compositions
from mascope_tools.composition.heuristic_filter import predict_isotopes
from mascope_tools.composition.utils import (
    calculate_mass,
    combine_formula_and_ionization,
    parse_ionization,
)


H = calculate_mass(formula="H")
BR = calculate_mass(formula="Br")
NO3_14N = calculate_mass(formula="NO3")
NO3_15N = calculate_mass(formula="N[15]O3")


@pytest.mark.parametrize(
    ("notation", "addition", "charge", "moiety_mass"),
    [
        ("+H+", True, 1, H - ELECTRON_MASS),
        ("-H+", False, -1, H - ELECTRON_MASS),
        ("+Br-", True, -1, BR + ELECTRON_MASS),
        ("+NO3-", True, -1, NO3_14N + ELECTRON_MASS),
        ("+(CH4N2O)H+", True, 1, calculate_mass(formula="CH5N2O") - ELECTRON_MASS),
        ("+", False, 1, ELECTRON_MASS),
        ("-", True, -1, ELECTRON_MASS),
    ],
)
def test_ion_charge_follows_the_moiety_and_the_direction(
    notation, addition, charge, moiety_mass
):
    mechanism = parse_ionization(notation)
    assert mechanism.addition is addition
    assert mechanism.charge == charge
    assert mechanism.mass == pytest.approx(moiety_mass, abs=1e-9)


def test_deprotonation_leaves_the_anion_mass():
    """Ion m/z of a deprotonated neutral is M - H + m_e, on both paths."""
    mechanism = parse_ionization("-H+")
    neutral = calculate_mass(formula="C9H14O3")
    anion = neutral - H + ELECTRON_MASS
    assert neutral - mechanism.mass == pytest.approx(anion, abs=1e-9)
    ion_formula = combine_formula_and_ionization("C9H14O3", mechanism)
    assert ion_formula.endswith("-"), ion_formula
    predicted_mz = predict_isotopes(ion_formula[:-1], mechanism.charge)[0][0]
    assert predicted_mz == pytest.approx(anion, abs=1e-6)


def test_deprotonated_candidate_is_found_and_scored_at_the_anion_mass():
    target = calculate_mass(formula="C9H14O3") - H + ELECTRON_MASS
    config = CompositionSearchConfig(
        ionizations="-H+",
        mass_range_ppm=1.0,
        element_count_ranges="C1-12 H0-30 O0-6",
        use_unsaturation=True,
        min_unsaturation=-1000.0,
        max_unsaturation=10000.0,
    )
    results = [
        r for r in find_compositions(target, config) if r["formula"] == "C9H14O3"
    ]
    assert results, "the deprotonated neutral must be enumerated"
    result = results[0]
    assert result["ion"] == "C9H13O3-"
    assert abs(result["composition_error_ppm"]) < 0.05
    predicted_mz = predict_isotopes(result["ion"][:-1], -1)[0][0]
    assert (predicted_mz - target) / target * 1e6 == pytest.approx(0.0, abs=0.05)


@pytest.mark.parametrize("notation", ["+^NO3-", "+[15N]O3-"])
def test_labelled_nitrate_keeps_its_label(notation):
    mechanism = parse_ionization(notation)
    assert mechanism.charge == -1
    assert "^N" in mechanism.formula
    assert mechanism.mass == pytest.approx(NO3_15N + ELECTRON_MASS, abs=1e-6)
    assert mechanism.mass - NO3_14N == pytest.approx(0.99703 + ELECTRON_MASS, abs=1e-4)


def test_unknown_bracketed_isotope_is_refused_not_unlabelled():
    with pytest.raises(CompositionFinderException):
        parse_ionization("+[13C]O3-")


def test_protonation_is_unchanged():
    mechanism = parse_ionization("+H+")
    neutral = calculate_mass(formula="C9H14O3")
    ion_formula = combine_formula_and_ionization("C9H14O3", mechanism)
    assert ion_formula == "C9H15O3+"
    predicted_mz = predict_isotopes(ion_formula[:-1], mechanism.charge)[0][0]
    assert predicted_mz == pytest.approx(neutral + H - ELECTRON_MASS, abs=1e-6)
    assert np.isfinite(predicted_mz)
