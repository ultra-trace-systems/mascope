"""Both errors match_isotopic_pattern reports are SIGNED: the intensity error as
observed/predicted - 1 and the m/z error as (observed - predicted)/predicted, the
conventions the targeted matcher uses for match_abundance_error and match_mz_error.
The persisted values are what the UI recovers the prediction from - the relative
abundance as observed_rel / (1 + abundance_error) and the theoretical m/z as
observed_mz / (1 + mz_error_ppm/1e6) - so the signs must survive; only the tolerance
gate, the candidate ranking and the pattern score work on the magnitude.
"""

import numpy as np
import polars as pl
import pytest

from mascope_tools.composition.heuristic_filter import (
    match_isotopic_pattern,
    predict_isotopes,
    score_pattern,
)


ION = "C6H13O6+"
CANDIDATES = [{"formula": "C6H12O6", "ion": ION, "composition_error_ppm": 0.5}]
BASE_INTENSITY = 1.0e6


def _run(factor: float = 1.0, isotopologue_shift_ppm: float = 0.0):
    """Match one glucose candidate against a spectrum whose first isotopologue
    is `factor` times its predicted relative intensity and sits `isotopologue_shift_ppm`
    off its predicted m/z."""
    predicted_mzs, predicted_intensities, _ = predict_isotopes(ION[:-1], 1)
    predicted_rel = predicted_intensities / predicted_intensities[0]
    isotopologue_mz = predicted_mzs[1] * (1 + isotopologue_shift_ppm * 1e-6)
    peaks = pl.DataFrame(
        {
            "mz": [predicted_mzs[0], isotopologue_mz],
            "intensity": [BASE_INTENSITY, factor * predicted_rel[1] * BASE_INTENSITY],
        }
    ).sort("mz")
    _, isotope_data = match_isotopic_pattern(CANDIDATES, peaks)
    return isotope_data[0], predicted_mzs, predicted_rel


def test_intensity_error_is_signed_observed_over_predicted_minus_one():
    # Isotopologue at 70% of prediction: error must come out negative, not |.|.
    data, _, _ = _run(factor=0.7)
    assert data["masses"][1] > 0, "isotopologue should be matched"
    assert data["intensity_errors"][1] == pytest.approx(-0.3)

    data, _, _ = _run(factor=1.3)
    assert data["intensity_errors"][1] == pytest.approx(0.3)


def test_predicted_abundance_is_recoverable_from_the_signed_error():
    # The inspector recovers theoretical_rel = observed_rel / (1 + error);
    # with the signed error that recovery is exact on both sides of the
    # prediction.
    for factor in (0.7, 1.3):
        data, _, predicted_rel = _run(factor=factor)
        observed_rel = factor * predicted_rel[1]
        recovered = observed_rel / (1 + data["intensity_errors"][1])
        assert recovered == pytest.approx(predicted_rel[1])


def test_tolerance_gate_still_works_on_the_magnitude():
    # 50% low is a -0.5 error: outside the 0.4 tolerance, so the isotopologue
    # must be rejected exactly as its +0.5 mirror image would be.
    data, _, _ = _run(factor=0.5)
    assert data["masses"][1] == 0.0
    assert data["intensity_errors"][1] == 0.0


def test_mass_error_is_signed_observed_minus_predicted():
    # An isotopologue 2 ppm BELOW its prediction must report -2 ppm. An unsigned m/z
    # error is the same defect the abundance error had: the spectrum chart places
    # the theoretical marker at observed / (1 + ppm/1e6), which mirrors it onto
    # the wrong side of the measured peak, and the inspector shows every
    # untargeted assignment as if its peak were heavy.
    data, _, _ = _run(isotopologue_shift_ppm=-2.0)
    assert data["masses"][1] > 0, "isotopologue should be matched"
    assert data["mass_errors_ppm"][1] == pytest.approx(-2.0, abs=1e-4)

    data, _, _ = _run(isotopologue_shift_ppm=2.0)
    assert data["mass_errors_ppm"][1] == pytest.approx(2.0, abs=1e-4)


def test_predicted_mz_is_recoverable_from_the_signed_mass_error():
    # The chart recovers theoretical_mz = observed_mz / (1 + mz_error_ppm/1e6).
    for shift_ppm in (-2.0, 2.0):
        data, predicted_mzs, _ = _run(isotopologue_shift_ppm=shift_ppm)
        recovered = data["masses"][1] / (1 + data["mass_errors_ppm"][1] / 1e6)
        assert recovered == pytest.approx(predicted_mzs[1], rel=1e-12)


def test_score_pattern_is_invariant_to_the_error_signs():
    # score_pattern must score both errors on their magnitude, so signed over-
    # and under-shoots cannot cancel into an inflated score.
    masses = np.array([100.0, 101.0, 102.0])
    intensities = np.array([1000.0, 110.0, 50.0])
    predicted_rel = np.array([1.0, 0.11, 0.05])
    signed_intensity = np.array([0.0, -0.2, 0.2])
    signed_mass = np.array([0.2, -0.3, 0.1])
    assert score_pattern(
        masses, signed_mass, intensities, signed_intensity, predicted_rel
    ) == pytest.approx(
        score_pattern(
            masses,
            np.abs(signed_mass),
            intensities,
            np.abs(signed_intensity),
            predicted_rel,
        )
    )
