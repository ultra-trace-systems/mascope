"""One fit of a sample's mass accuracy, for whatever engine scores it.

The fit answers "how well does a mass have to agree on this sample", and the
answer goes into every candidate's mass term. Two engines comparing their
assignments must therefore fit it the same way, or part of what the comparison
reports is that one of them measured the ruler differently. These pin what the
fit is (a median and a scaled MAD over the sample's own anchors), when it
refuses to state one, and how a width - fitted or fallen back to - becomes the
width a score is judged at. And the mass trend beside it: when a sample's error
follows its m/z closely enough to be judged along it, and when it only seems to.
"""

import math

import numpy as np
import pandas as pd
import pytest

from mascope_tools.composition.mass_accuracy import (
    MASS_ACCURACY_MIN_ANCHORS,
    MASS_OFFSET_MIN_ANCHORS,
    MASS_TREND_MIN_POINTS,
    MASS_TREND_MIN_SIDE_POINTS,
    MIN_FITTED_SIGMA_PPM,
    PRED_SIGMA_PPM,
    TREND_NARROW_RANGE,
    TREND_NO_BETTER_THAN_CONSTANT,
    TREND_OFFSET_TOO_LARGE,
    TREND_ONE_SIDED,
    TREND_SLOPE_NOT_SIGNIFICANT,
    TREND_TOO_FEW_POINTS,
    fit_mass_accuracy,
    fit_mass_trend,
    fit_sample_mass_accuracy,
    mass_accuracy_anchors,
    scoring_sigma_ppm,
    trend_width_floor_ppm,
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


def _on_a_trend(count, *, a=-0.25, b=-0.12, noise=0.05, lo=59.0, hi=400.0, seed=3):
    """``count`` errors on ``ppm = a + b * 1000 / mz``, spread evenly in 1000/mz.

    Ordered from the highest m/z to the lowest.
    """
    rng = np.random.default_rng(seed)
    mz = 1000.0 / np.linspace(1000.0 / hi, 1000.0 / lo, count)
    return mz, a + b * 1000.0 / mz + rng.normal(0.0, noise, count)


class TestTheMassTrend:
    """When a sample's mass error follows its m/z, and when it only seems to."""

    def test_it_measures_a_constant_absolute_offset(self):
        # -0.12 mDa is 2 ppm at m/z 60 and 0.3 ppm at m/z 400: the shape an
        # Orbitrap calibration's residual takes toward the low-mass edge.
        trend, refused = fit_mass_trend(*_on_a_trend(40))

        assert refused is None
        assert trend.offset_mda == pytest.approx(-0.12, abs=0.01)
        assert trend.intercept_ppm == pytest.approx(-0.25, abs=0.05)
        assert trend.centre_ppm(100.0) == pytest.approx(-1.45, abs=0.05)
        assert (trend.mz_lo, trend.mz_hi) == pytest.approx((59.0, 400.0))

    def test_a_flat_source_rarely_grows_a_trend(self):
        # A slope clearing two standard errors alone is a 5% two-sided test,
        # and on these samples it grows a trend on 14 of 200 that have none.
        # Three standard errors with the residual ratio hold it to under one
        # sample in fifty.
        grown = 0
        for seed in range(200):
            rng = np.random.default_rng(seed)
            mz = 1000.0 / rng.uniform(1000.0 / 420, 1000.0 / 60, 30)
            grown += fit_mass_trend(mz, -0.3 + rng.normal(0.0, 0.2, 30))[0] is not None

        assert grown <= 4

    def test_one_far_point_is_a_lever_not_a_trend(self):
        # Twenty points at m/z 150-169 and one at m/z 480, a ppm off. The line
        # passes through the far point, so trimming never removes it, the
        # residual ratio improves and the slope is significant because that one
        # point carries the spread along the axis: every statistical test
        # passes, and the line puts +2 ppm on an m/z 61 nothing measured.
        mz = [150.0 + i for i in range(20)] + [480.2]
        ppm = [[-0.10, 0.0, 0.10, 0.05, -0.05][i % 5] for i in range(20)] + [-1.0]

        assert fit_mass_trend(mz, ppm) == (None, TREND_ONE_SIDED)

    @pytest.mark.parametrize(
        ("low_mass_points", "refused"),
        [
            (MASS_TREND_MIN_SIDE_POINTS - 1, TREND_ONE_SIDED),
            (MASS_TREND_MIN_SIDE_POINTS, None),
        ],
    )
    def test_each_half_of_the_range_needs_its_own_points(
        self, low_mass_points, refused
    ):
        # Thirty points above m/z 160 and a few below m/z 80, all on one line:
        # the low half of the 1000/mz range starts near m/z 103, and it is how
        # many points sit there that decides, not how well they agree.
        high_mz, high_ppm = _on_a_trend(30, a=0.35, b=-0.15, lo=160.0, seed=5)
        low_mz = np.array([59.0, 64.0, 70.0, 75.0, 80.0])[:low_mass_points]
        low_ppm = 0.35 - 0.15 * 1000.0 / low_mz + 0.03

        trend, why = fit_mass_trend(
            np.concatenate([high_mz, low_mz]), np.concatenate([high_ppm, low_ppm])
        )

        assert why == refused
        assert (trend is None) == (refused is not None)

    def test_the_centre_is_held_at_the_edges_of_what_was_measured(self):
        trend, _ = fit_mass_trend(
            *_on_a_trend(60, a=-0.2, b=-0.3, noise=0.1, lo=150.0, hi=480.0)
        )

        assert trend.centre_ppm(61.0) == trend.centre_ppm(trend.mz_lo)
        assert trend.centre_ppm(900.0) == trend.centre_ppm(trend.mz_hi)
        # Not the line's extrapolation, which is two ppm further out at m/z 61.
        extrapolated = trend.intercept_ppm + trend.offset_mda * 1000.0 / 61.0
        assert abs(extrapolated - trend.centre_ppm(61.0)) > 2.0

    def test_wild_points_do_not_tilt_the_line(self):
        # Three mis-assigned rows 8 ppm out at the high-mass end. Untrimmed,
        # they flatten the slope into its own scatter.
        mz, ppm = _on_a_trend(40)
        ppm[:3] += 8.0

        trend, refused = fit_mass_trend(mz, ppm)

        assert refused is None
        assert trend.offset_mda == pytest.approx(-0.12, abs=0.01)
        assert trend.points == 36

    def test_an_offset_no_working_calibration_has_is_refused(self):
        assert fit_mass_trend(*_on_a_trend(40, b=-0.8)) == (
            None,
            TREND_OFFSET_TOO_LARGE,
        )

    def test_a_slope_inside_its_own_scatter_is_refused(self):
        assert fit_mass_trend(*_on_a_trend(30, b=-0.01, noise=0.5)) == (
            None,
            TREND_SLOPE_NOT_SIGNIFICANT,
        )

    def test_a_slope_that_explains_little_is_refused(self):
        # Significant over three thousand points, and still leaving the scatter
        # nearly as wide as the constant centre does: no better to judge by.
        mz, ppm = _on_a_trend(3000, b=-0.02, noise=0.3, lo=60.0)

        assert fit_mass_trend(mz, ppm) == (None, TREND_NO_BETTER_THAN_CONSTANT)

    def test_too_few_points_measure_no_trend(self):
        mz, ppm = _on_a_trend(MASS_TREND_MIN_POINTS - 1)

        assert fit_mass_trend(mz, ppm) == (None, TREND_TOO_FEW_POINTS)

    def test_a_short_stretch_of_the_mass_range_measures_no_trend(self):
        # m/z 260-400 spans 1.35 of 1000/mz.
        mz, ppm = _on_a_trend(40, lo=260.0)

        assert fit_mass_trend(mz, ppm) == (None, TREND_NARROW_RANGE)

    def test_an_unusable_point_is_ignored(self):
        mz, ppm = _on_a_trend(40)

        with_unusable = fit_mass_trend(
            np.append(mz, [np.nan, 0.0, -5.0, 100.0]),
            np.append(ppm, [0.1, 0.1, 0.1, np.nan]),
        )

        assert with_unusable == fit_mass_trend(mz, ppm)

    def test_the_width_floor_is_absolute(self):
        # 0.03 mDa: half a ppm at m/z 60, a tenth at m/z 300.
        assert trend_width_floor_ppm(60.0) == pytest.approx(0.5)
        assert trend_width_floor_ppm(300.0) == pytest.approx(0.1)
