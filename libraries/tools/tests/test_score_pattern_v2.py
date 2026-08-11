"""Unit tests for the v2 match score (detectability-gated, SNR-aware) and its
calibration. Inputs follow the matched-array convention: one entry per predicted
isotopologue (index 0 = the base peak, i.e. the most abundant predicted isotopologue),
unmatched entries carry 0.

SNR is optional evidence, never a requirement: every SNR term only widens a tolerance, so
an unknown SNR (None, or a per-row 0 / negative / NaN / inf) must grant no concession
rather than infinite tolerance. The `no_snr` tests below pin that.
"""

import numpy as np
import pytest

from mascope_tools.composition.heuristic_filter import (
    DEFAULT_CALIBRATION_V2,
    REL_DETECT_NO_SNR,
    SCORE_VERSION,
    calibrate_score,
    score_pattern,
    score_pattern_v2,
)


# A two-isotopologue ion: M0 (rel 1.0) + M+1 (rel 0.11), base SNR 500.
PRED = np.array([1.0, 0.11])
ME = np.array([0.2, 0.3])  # ppm errors
SNR = np.array([500.0, 55.0])

# The same ion with a perfect mass but an M+1 EIGHT TIMES more intense than predicted --
# a badly-fitting envelope whose only defect is the intensity ratio, so it isolates the
# SNR-driven intensity tolerance.
BAD_INT_ME = np.array([0.0, 0.0])
BAD_INT_OI = np.array([1000.0, 880.0])
# The unusable SNR values: all four mean "no SNR for this peak".
UNUSABLE_SNR = (0.0, -1.0, np.nan, np.inf)


def _perfect():
    return score_pattern_v2(ME, np.array([1000.0, 110.0]), SNR, PRED)


def test_version_constant():
    assert SCORE_VERSION == 2


def test_perfect_match_scores_high():
    assert _perfect() > 0.95


def test_detectable_missing_isotopologue_is_penalized():
    # M+1 predicted at 0.11 -> expected SNR 0.11*500 = 55 >= 3 -> should be visible
    missing = score_pattern_v2(
        ME, np.array([1000.0, 0.0]), np.array([500.0, 0.0]), PRED
    )
    assert missing < _perfect() - 0.05


def test_undetectable_missing_isotopologue_not_penalized():
    # tiny M+1 (rel 0.005) -> expected SNR 0.005*500 = 2.5 < 3 -> below noise, excluded
    pred = np.array([1.0, 0.005])
    s = score_pattern_v2(
        np.array([0.2, 0.3]), np.array([1000.0, 0.0]), np.array([500.0, 0.0]), pred
    )
    assert s > 0.95


def test_low_snr_isotopologue_gets_wide_tolerance():
    # a noisy (SNR 4) M+1 off by ~30% is penalized but NOT tanked, because its
    # tolerance scales with its own noise (a hard relative-error term would zero it)
    noisy = score_pattern_v2(ME, np.array([1000.0, 80.0]), np.array([500.0, 4.0]), PRED)
    assert 0.85 < noisy < _perfect()


def test_low_snr_mass_error_is_forgiven():
    # A trace M+1 off by 0.6 ppm scores higher when it is NOISY than when it is clean:
    # a weak peak's centroid is legitimately less precise, so the mass width scales with
    # 1/SNR. Intensities are kept perfect (exact ratio) to isolate the mass term.
    pred = np.array([1.0, 0.3])
    ints = np.array([1000.0, 300.0])  # exact 0.3 ratio -> intensity term ~1
    me = np.array([0.0, 0.6])
    clean = score_pattern_v2(me, ints, np.array([500.0, 200.0]), pred, sigma_ppm=0.3)
    noisy = score_pattern_v2(me, ints, np.array([500.0, 4.0]), pred, sigma_ppm=0.3)
    assert noisy > clean


def test_high_snr_mass_width_is_essentially_the_fixed_sigma():
    # At high SNR the SNR term (MASS_SNR_K/SNR) is negligible, so a 0.6 ppm error on a
    # trace peak is penalised against the tight fixed sigma (Br3--like: no free pass).
    pred = np.array([1.0, 0.3])
    ints = np.array([1000.0, 300.0])
    me = np.array([0.0, 0.6])
    s = score_pattern_v2(me, ints, np.array([5000.0, 5000.0]), pred, sigma_ppm=0.3)
    assert s < 0.75  # ~0.63: the 2-sigma error is still penalised at high SNR


def test_absent_monoisotopic_returns_zero():
    assert score_pattern_v2(ME, np.array([0.0, 0.0]), np.array([0.0, 0.0]), PRED) == 0.0


# --- SNR is optional evidence, never a licence ------------------------------


def test_unusable_snr_does_not_inflate_a_bad_intensity_fit():
    # An M+1 8x more intense than predicted is a bad fit. Degrading only its SNR to an
    # unusable value must not turn that into a perfect one: the SNR terms widen the
    # tolerance, so "no SNR" has to mean "no widening", not "infinite widening".
    real = score_pattern_v2(BAD_INT_ME, BAD_INT_OI, np.array([500.0, 300.0]), PRED)
    assert real == pytest.approx(0.2543, abs=1e-3)
    for bad in UNUSABLE_SNR:
        assert score_pattern_v2(
            BAD_INT_ME, BAD_INT_OI, np.array([500.0, bad]), PRED
        ) == pytest.approx(real, abs=1e-3)


def test_unknown_base_snr_scores_instead_of_zero_or_one():
    # An unknown BASE snr used to drop the base peak out of the weighted mean entirely:
    # 0.0 when the sibling was absent, a free pass on the gate otherwise. Both now land
    # on the real-SNR answer.
    assert score_pattern_v2(
        BAD_INT_ME, BAD_INT_OI, np.array([np.nan, 300.0]), PRED
    ) == pytest.approx(0.2543, abs=1e-3)
    absent = (np.array([0.0, 0.0]), np.array([1000.0, 0.0]))
    assert score_pattern_v2(*absent, np.array([np.nan, 0.0]), PRED) == pytest.approx(
        score_pattern_v2(*absent, np.array([500.0, 0.0]), PRED)
    )


def test_no_snr_argument_is_accepted():
    # `observed_snr=None` is how a caller with no SNR column at all declares it.
    assert score_pattern_v2(BAD_INT_ME, BAD_INT_OI, None, PRED) == pytest.approx(
        score_pattern_v2(BAD_INT_ME, BAD_INT_OI, np.array([500.0, 300.0]), PRED),
        abs=1e-3,
    )


def test_absent_detectable_sibling_penalised_without_snr():
    # Without a base SNR the expected-SNR gate cannot be evaluated, so it falls back to
    # predicted abundance: an M+1 at 30% of base that is entirely absent is still
    # evidence against the assignment, and scores exactly as it does with a real SNR.
    pred = np.array([1.0, 0.30])
    args = (np.array([0.0, 0.0]), np.array([1000.0, 0.0]))
    no_snr = score_pattern_v2(*args, None, pred, sigma_ppm=0.5)
    assert no_snr == pytest.approx(0.7574, abs=1e-3)
    assert no_snr == pytest.approx(
        score_pattern_v2(*args, np.array([500.0, 0.0]), pred, sigma_ppm=0.5)
    )


def test_absent_sibling_below_rel_detect_excluded_without_snr():
    # Below the fallback threshold absence carries no information without a noise
    # estimate, so it is excluded rather than penalised (the honest residual: at a real
    # SNR of 500 this sibling would have been gated).
    pred = np.array([1.0, 0.005])
    assert pred[1] < REL_DETECT_NO_SNR
    s = score_pattern_v2(
        np.array([0.0, 0.0]), np.array([1000.0, 0.0]), None, pred, sigma_ppm=0.5
    )
    assert s == pytest.approx(1.0)


def test_unknown_snr_never_widens_a_matched_peak_tolerance():
    # Property, for MATCHED rows: both SNR terms only widen a tolerance and both
    # likelihood factors decrease monotonically in their width, so replacing a real SNR
    # by an unusable one can never RAISE the score.
    for m1_error in (0.0, 0.4, 1.2):
        for ratio in (0.06, 0.11, 0.2):
            me = np.array([0.0, m1_error])
            oi = np.array([1000.0, 1000.0 * ratio])
            real = score_pattern_v2(
                me, oi, np.array([500.0, 40.0]), PRED, sigma_ppm=0.5
            )
            for bad in UNUSABLE_SNR:
                unknown = score_pattern_v2(
                    me, oi, np.array([500.0, bad]), PRED, sigma_ppm=0.5
                )
                assert unknown <= real + 1e-12


# --- Unusable inputs must not become free passes ----------------------------


def test_non_finite_mass_error_on_matched_peak_is_not_a_free_pass():
    # A "matched" peak with no usable mass error carries no mass evidence. It used to
    # score a perfect 1.0 (its NaN likelihood dropped out of the weighted mean); it is
    # now treated as absent and judged by the detectability gate.
    s = score_pattern_v2(np.array([0.0, np.nan]), np.array([1000.0, 110.0]), SNR, PRED)
    assert s == pytest.approx(
        score_pattern_v2(
            np.array([0.0, 0.0]), np.array([1000.0, 0.0]), np.array([500.0, 0.0]), PRED
        )
    )


def test_non_finite_mass_error_on_base_peak_scores_zero():
    # Same rule as an absent base peak: no usable base evidence, no assignment.
    assert (
        score_pattern_v2(np.array([np.nan, 0.3]), np.array([1000.0, 110.0]), SNR, PRED)
        == 0.0
    )


def test_non_finite_predicted_abundance_does_not_return_nan():
    # A NaN predicted abundance used to propagate all the way out through the weighted
    # mean (`wsum <= 0` is False for NaN). That isotopologue is now simply dropped.
    s = score_pattern_v2(ME, np.array([1000.0, 110.0]), SNR, np.array([1.0, np.nan]))
    assert np.isfinite(s)
    assert s == pytest.approx(
        score_pattern_v2(ME[:1], np.array([1000.0]), SNR[:1], np.array([1.0]))
    )


def test_length_mismatch_names_the_offending_arrays():
    with pytest.raises(ValueError, match="predicted_rel=3"):
        score_pattern_v2(
            ME, np.array([1000.0, 110.0]), SNR, np.array([1.0, 0.11, 0.01])
        )


def test_calibration_is_probability_and_monotone():
    lo, hi = calibrate_score(0.5), calibrate_score(0.99)
    assert 0.0 < lo < hi < 1.0
    assert DEFAULT_CALIBRATION_V2[0] > 0  # positive slope


def test_v1_score_pattern_unchanged_perfect():
    # guard that v1 still behaves (byte-identical formula): a clean match scores high
    s = score_pattern(
        np.array([100.0, 101.0]),  # observed_masses
        np.array([0.2, 0.3]),  # mass errors ppm
        np.array([1000.0, 110.0]),  # intensities
        np.array([0.0, 0.02]),  # intensity errors
        PRED,  # predicted_rel
    )
    assert 0.9 < s <= 1.0
