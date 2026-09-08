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


# A dibromide: IsoSpec ranks its configurations by abundance, so the line it
# returns first is 79Br81Br at 46.8%, two mass units ABOVE the monoisotopic one
# at 24.0%. Every index-0 assumption in the matcher and the scorer means the
# ion's own line, so this is the geometry that tells them apart.
DIBROMIDE = "C6H12Br2"
DIBROMIDE_CANDIDATES = [
    {"formula": "C6H12", "ion": f"{DIBROMIDE}-", "composition_error_ppm": 0.3}
]


def _dibromide_lines():
    mzs, intensities, labels = predict_isotopes(DIBROMIDE, -1)
    return {
        label: (float(mz), float(intensity))
        for mz, intensity, label in zip(mzs, intensities, labels)
    }


def test_the_predictors_first_line_is_not_the_ions_own():
    """The premise of the tests below, pinned so it cannot drift silently."""
    _, intensities, labels = predict_isotopes(DIBROMIDE, -1)

    assert labels[0] == "81Br"
    assert labels.index("M0") == 1
    assert intensities[0] > intensities[1]


def test_the_envelope_is_anchored_on_the_ions_own_line():
    lines = _dibromide_lines()
    peaks = pl.DataFrame(
        {
            "mz": [lines["M0"][0], lines["81Br"][0]],
            "intensity": [1.0e6, 1.0e6 * lines["81Br"][1] / lines["M0"][1]],
        }
    ).sort("mz")

    _, isotope_data = match_isotopic_pattern(DIBROMIDE_CANDIDATES, peaks)
    data = isotope_data[0]

    assert data["labels"][0] == "M0"
    assert data["masses"][0] == pytest.approx(lines["M0"][0], abs=1e-4)
    # ...and the satellite's predicted share is stated relative to the ion,
    # which for this envelope is larger than one.
    assert data["predicted_intensities"][1] == pytest.approx(
        lines["81Br"][1] / lines["M0"][1], rel=1e-6
    )
    assert abs(data["intensity_errors"][1]) < 1e-6


def test_a_bright_peak_is_not_lost_to_a_faint_neighbour_two_mass_units_up():
    """The set D regression: a bright target, a faint peak where the
    most-abundant line would fall.

    Anchoring on the predictor's first line matched that faint peak, normalised
    the envelope to it, and then found the target thousands of percent too
    bright for its own monoisotopic line - so the target went unmatched while
    the pattern still scored well. The peak the candidate was enumerated FOR
    then got no row at all.
    """
    lines = _dibromide_lines()
    peaks = pl.DataFrame(
        {
            "mz": [lines["M0"][0], lines["81Br"][0]],
            "intensity": [7.0e3, 3.6e2],
        }
    ).sort("mz")

    ranked, isotope_data = match_isotopic_pattern(DIBROMIDE_CANDIDATES, peaks)
    data = isotope_data[0]

    # The target is matched, and it is the row the envelope hangs off.
    assert data["masses"][0] == pytest.approx(lines["M0"][0], abs=1e-4)
    assert data["labels"][0] == "M0"
    # The faint neighbour is what it is: far too weak for the 79Br81Br line of
    # this ion, so it fails the intensity gate and is not claimed.
    assert data["masses"][1] == 0.0
    # And the reading is worth nothing rather than merely less: a dibromide
    # whose brightest predicted line is not in the spectrum is not a dibromide,
    # however well its one matched line agrees. Without this the anchoring
    # would trade one phantom for another - the reading that used to swallow
    # the target would win it instead, as an M0 with no envelope at all.
    assert ranked[0]["isotopic_pattern_score"] == 0.0


def test_a_candidate_whose_brightest_line_is_absent_scores_nothing():
    """The requirement the anchoring separated out and had to state again.

    Before, the caller put the brightest line first and this function required
    index 0, so "the brightest line is there" was implicit in "the ion's line is
    there". Once the two are different rows, both have to be asked for.
    """
    lines = _dibromide_lines()
    peaks = pl.DataFrame(
        {
            "mz": [lines["M0"][0], lines["13C"][0]],
            "intensity": [1.0e6, 1.0e6 * lines["13C"][1] / lines["M0"][1]],
        }
    ).sort("mz")

    ranked, isotope_data = match_isotopic_pattern(DIBROMIDE_CANDIDATES, peaks)

    # The ion's own line is matched, and so is a satellite...
    assert isotope_data[0]["masses"][0] > 0
    assert np.count_nonzero(isotope_data[0]["masses"]) == 2
    # ...but not the one the prediction leads with, so the reading is not
    # evidence of this ion.
    assert ranked[0]["isotopic_pattern_score"] == 0.0


def test_a_candidate_whose_own_line_is_absent_scores_nothing():
    """The ion's monoisotopic line is the peak the composition search proposed
    the candidate for, so a spectrum that does not hold it is not evidence for
    the candidate - however well the rest of the envelope lines up."""
    lines = _dibromide_lines()
    peaks = pl.DataFrame(
        {"mz": [lines["81Br"][0], lines["81Br2"][0]], "intensity": [1.0e6, 4.9e5]}
    ).sort("mz")

    ranked, isotope_data = match_isotopic_pattern(DIBROMIDE_CANDIDATES, peaks)

    assert ranked[0]["isotopic_pattern_score"] == 0.0
    assert not np.any(isotope_data[0]["masses"] > 0)
