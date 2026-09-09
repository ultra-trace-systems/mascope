"""One fit of a sample's mass accuracy, for whatever engine scores it.

The fit answers "how well does a mass have to agree on this sample", and the
answer goes into every candidate's mass term. Two engines comparing their
assignments must therefore fit it the same way, or part of what the comparison
reports is that one of them measured the ruler differently. These pin what the
fit is (a median and a scaled MAD over the sample's own anchors), when it
refuses to state one, and how a width - fitted or fallen back to - becomes the
width a score is judged at.
"""

import math

import numpy as np
import pandas as pd
import pytest

from mascope_tools.composition.mass_accuracy import (
    MASS_ACCURACY_MIN_ANCHORS,
    MASS_OFFSET_MIN_ANCHORS,
    MIN_FITTED_SIGMA_PPM,
    PRED_SIGMA_PPM,
    fit_mass_accuracy,
    fit_sample_mass_accuracy,
    mass_accuracy_anchors,
    scoring_sigma_ppm,
)


# Nine errors whose median is 0.4 and whose median absolute deviation is 0.2.
ERRORS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


def _frame(errors, intensities=None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "match_mz_error": errors,
            "sample_peak_intensity": (
                [100.0] * len(errors) if intensities is None else intensities
            ),
        }
    )


class TestTheFit:
    """What the sample's own anchors say about its mass accuracy."""

    def test_the_offset_is_the_median_and_the_width_the_scaled_mad(self):
        mu, sigma = fit_mass_accuracy(ERRORS)

        assert mu == pytest.approx(0.4)
        assert sigma == pytest.approx(1.4826 * 0.2)

    def test_a_wild_anchor_does_not_set_the_width(self):
        # One mis-assigned anchor sits several ppm out. A mean and a standard
        # deviation would let it widen the window every candidate in the run is
        # then scored against; the median and the MAD do not notice it.
        _, sigma = fit_mass_accuracy(ERRORS)
        _, with_outlier = fit_mass_accuracy(ERRORS + [50.0])

        assert with_outlier == pytest.approx(sigma, rel=0.25)

    def test_too_few_anchors_measure_no_width(self):
        # Not a small measurement of a width: no measurement of one, which the
        # caller answers with its instrument class rather than with a guess.
        _, sigma = fit_mass_accuracy(ERRORS[: MASS_ACCURACY_MIN_ANCHORS - 1])

        assert sigma is None

    def test_the_minimum_anchors_are_enough(self):
        _, sigma = fit_mass_accuracy(ERRORS[:MASS_ACCURACY_MIN_ANCHORS])

        assert sigma is not None

    def test_anchors_that_agree_exactly_do_not_claim_infinite_precision(self):
        # A run whose anchors round to one number has not measured to zero ppm,
        # and a sigma of zero would make the mass likelihood a step function:
        # every error inside the rounding perfect, every one outside impossible.
        # The floor is what stops that, so a zero floor must fail this test.
        _, sigma = fit_mass_accuracy([0.3] * 12)

        assert sigma > 0
        assert sigma == MIN_FITTED_SIGMA_PPM

    def test_the_floor_is_wide_enough_to_score_at(self):
        # A width the mass term can divide by: at 0.05 ppm an Orbitrap's own
        # 0.2 ppm error is four sigma, which is why the floor is a floor on the
        # FIT and the score is judged at `scoring_sigma_ppm`, never at this.
        _, sigma = fit_mass_accuracy([0.3] * 12)

        assert scoring_sigma_ppm(sigma, 3.0) > PRED_SIGMA_PPM

    def test_an_unmeasured_offset_is_not_a_zero_one(self):
        # An offset of zero and no offset are opposite claims, and the pair used
        # to answer 0.0 for both: every consumer - the score, the run's record
        # of what it scored at, a comparison against another engine - then read
        # a sample nothing was measured on as one measured to be well centred.
        assert fit_mass_accuracy(ERRORS[: MASS_ACCURACY_MIN_ANCHORS - 1]) == (
            None,
            None,
        )

        mu, sigma = fit_mass_accuracy([0.0] * MASS_ACCURACY_MIN_ANCHORS)
        assert mu == 0.0 and sigma is not None

    def test_the_offset_needs_the_same_anchors_the_width_does(self):
        # It reads like the easier measurement and the gate measured the
        # opposite: an anchor set too small to say how wide it is cannot say
        # where its centre is either. These five scatter over 8 ppm, so their
        # median is not a centre - and the width the fit refuses to report is
        # exactly the evidence for that.
        wide = [-0.4, -10.5, -2.8, -10.4, -0.3]
        assert MASS_OFFSET_MIN_ANCHORS == MASS_ACCURACY_MIN_ANCHORS

        assert fit_mass_accuracy(wide) == (None, None)

    def test_an_unusable_error_is_not_an_anchor(self):
        # NaN reaches the fit from a row that matched nothing; counting it would
        # both mis-state the anchor count and drag the median toward zero.
        errors = ERRORS + [float("nan")] * 5

        assert fit_mass_accuracy(errors) == fit_mass_accuracy(ERRORS)


class TestTheAnchors:
    """Which rows of a match frame the fit is entitled to measure."""

    def test_a_row_needs_an_intensity_and_a_usable_error(self):
        frame = _frame(
            [0.1, 0.2, float("nan"), 0.4],
            intensities=[100.0, 0.0, 100.0, np.nan],
        )

        assert list(mass_accuracy_anchors(frame)) == [0.1]

    def test_a_frame_that_matched_nothing_has_no_anchors(self):
        # Neither column present. The frame has not measured a mass error, which
        # is the same answer as one that measured too few, and not an error.
        assert mass_accuracy_anchors(pd.DataFrame()).empty
        assert fit_sample_mass_accuracy(pd.DataFrame()) == (None, None)

    def test_the_frame_fit_is_the_fit_over_its_anchors(self):
        # The anchor count a run reports and the anchors the fit used are one
        # set; a caller that counted rows itself would drift from the fit.
        frame = _frame(ERRORS + [0.9], intensities=[100.0] * 9 + [0.0])

        assert fit_sample_mass_accuracy(frame) == fit_mass_accuracy(ERRORS)
        assert len(mass_accuracy_anchors(frame)) == len(ERRORS)


class TestTheWidthAScoreIsJudgedAt:
    """The fitted width is not yet the width the mass term uses."""

    def test_a_fitted_width_is_widened_for_prediction_and_centroiding(self):
        # Correct matches spread wider than the calibration anchors do, so the
        # instrument's measured width is not the width a candidate is scored
        # against - scoring at the raw fit would charge a real assignment for
        # the tail of its own peak shape.
        assert scoring_sigma_ppm(0.30, 3.0) == pytest.approx(
            math.hypot(0.30, PRED_SIGMA_PPM)
        )

    def test_a_sample_that_measured_nothing_uses_its_instrument_class(self):
        assert scoring_sigma_ppm(None, 3.0) == pytest.approx(
            math.hypot(3.0, PRED_SIGMA_PPM)
        )

    def test_a_measured_width_beats_the_class_however_wide_it_is(self):
        assert scoring_sigma_ppm(1.4, 0.3) == pytest.approx(
            math.hypot(1.4, PRED_SIGMA_PPM)
        )
