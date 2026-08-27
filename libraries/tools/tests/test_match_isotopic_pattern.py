"""The intensity errors match_isotopic_pattern reports are SIGNED relative
errors (observed/predicted - 1), the same convention as the targeted matcher's
match_abundance_error. The persisted value is what the UI uses to recover the
predicted relative abundance (observed_rel / (1 + error)), so the sign must
survive; only the tolerance gate and the pattern score work on the magnitude.
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


def _run_with_satellite_factor(factor: float):
    """Match one glucose candidate against a spectrum whose first isotope
    satellite is `factor` times its predicted relative intensity."""
    predicted_mzs, predicted_intensities, _ = predict_isotopes(ION[:-1], 1)
    predicted_rel = predicted_intensities / predicted_intensities[0]
    peaks = pl.DataFrame(
        {
            "mz": [predicted_mzs[0], predicted_mzs[1]],
            "intensity": [BASE_INTENSITY, factor * predicted_rel[1] * BASE_INTENSITY],
        }
    ).sort("mz")
    _, isotope_data = match_isotopic_pattern(CANDIDATES, peaks)
    return isotope_data[0], predicted_rel


def test_intensity_error_is_signed_observed_over_predicted_minus_one():
    # Satellite at 70% of prediction: error must come out negative, not |.|.
    data, _ = _run_with_satellite_factor(0.7)
    assert data["masses"][1] > 0, "satellite should be matched"
    assert data["intensity_errors"][1] == pytest.approx(-0.3)

    data, _ = _run_with_satellite_factor(1.3)
    assert data["intensity_errors"][1] == pytest.approx(0.3)


def test_predicted_abundance_is_recoverable_from_the_signed_error():
    # The inspector recovers theoretical_rel = observed_rel / (1 + error);
    # with the signed error that recovery is exact on both sides of the
    # prediction.
    for factor in (0.7, 1.3):
        data, predicted_rel = _run_with_satellite_factor(factor)
        observed_rel = factor * predicted_rel[1]
        recovered = observed_rel / (1 + data["intensity_errors"][1])
        assert recovered == pytest.approx(predicted_rel[1])


def test_tolerance_gate_still_works_on_the_magnitude():
    # 50% low is a -0.5 error: outside the 0.4 tolerance, so the satellite
    # must be rejected exactly as its +0.5 mirror image would be.
    data, _ = _run_with_satellite_factor(0.5)
    assert data["masses"][1] == 0.0
    assert data["intensity_errors"][1] == 0.0


def test_score_pattern_is_invariant_to_the_error_sign():
    # score_pattern must score on |error| so signed over- and under-shoots
    # cannot cancel into an inflated intensity score.
    masses = np.array([100.0, 101.0, 102.0])
    mass_errors = np.array([0.2, 0.3, 0.1])
    intensities = np.array([1000.0, 110.0, 50.0])
    predicted_rel = np.array([1.0, 0.11, 0.05])
    signed = np.array([0.0, -0.2, 0.2])
    assert score_pattern(
        masses, mass_errors, intensities, signed, predicted_rel
    ) == pytest.approx(
        score_pattern(masses, mass_errors, intensities, np.abs(signed), predicted_rel)
    )
