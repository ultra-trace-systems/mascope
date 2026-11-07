"""The envelope tolerance a run fits from its own committed envelopes, and the
fit score read at it.

Two halves. The score: a matched line inside the band the tolerance admits at
its expected signal-to-noise costs nothing, one outside is charged from the
band's nearer end at the floor's width, an absent line counts from the
tolerance's threshold, and today's tolerance scores exactly as the score always
did. The fit: a curve per bin of expected S/N, non-decreasing and capped at the
natural share, the threshold read off the same lines' presence, a centre and a
width for a TOF, and the level each fell back to.

Inputs to the score follow the matched-array convention of
``test_score_pattern_v2``: the anchor first, an unmatched line carries 0.
"""

import math

import numpy as np
import pytest

from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_DETECT_SNR,
    DEFAULT_TOLERANCE,
    FORM_CENTRE_WIDTH,
    FORM_CURVE,
    FORM_DEFAULT,
    LEVEL_DEFAULT,
    LEVEL_OWN,
    LEVEL_POOLED,
    MIN_BIN_LINES,
    MIN_CENTRE_WIDTH_LINES,
    MIN_RESPONSE_LINES,
    RELATIVE_FLOOR,
    RESPONSE_SNR_EDGES,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_centre_width,
    fit_envelope_tolerance,
    fit_response_curve,
)
from mascope_tools.composition.heuristic_filter import (
    DETECT_SNR_K,
    _predicted_envelope,
    score_ion_envelope,
    score_ion_envelopes,
    score_pattern_v2,
    score_patterns_v2,
)
from mascope_tools.composition.models import PatternScoring


#: A curve that opens 40% down at an expected S/N of 3 to 10 and closes by 100,
#: with absent lines counted from an expected S/N of 10.
CURVE = EnvelopeTolerance(
    snr_edges=RESPONSE_SNR_EDGES,
    lower=(0.6, 0.8, 0.9, 0.95, 1.0),
    detect_snr=10.0,
)

#: A clean anchor (S/N so high its noise term is nothing) and one line whose
#: predicted share puts its expected S/N where the test wants it.
SHARE = 0.11
CLEAN = 1e9


def _two_lines(ratio: float, expected_snr: float, *, tolerance=None) -> float:
    """The fit of an anchor and one line read at ``ratio`` of its share, the
    line's expected S/N ``expected_snr``, every mass error zero."""
    base_snr = expected_snr / SHARE
    return score_pattern_v2(
        np.zeros(2),
        np.array([1000.0, 1000.0 * SHARE * ratio]),
        np.array([base_snr, CLEAN]),
        np.array([1.0, SHARE]),
        tolerance=tolerance,
    )


def _one_line_at(likelihood: float) -> float:
    """The fit an anchor and one line make when the line's intensity term is
    ``likelihood``: the abundance-weighted geometric mean of 1 and it."""
    return math.exp(SHARE * math.log(likelihood) / (1.0 + SHARE))


class TestTodaysToleranceScoresAsBefore:
    """No tolerance and the default tolerance are the score as it was."""

    @pytest.mark.parametrize("seed", range(4))
    def test_random_envelopes(self, seed):
        rng = np.random.default_rng(seed)
        for _ in range(200):
            n = int(rng.integers(1, 12))
            pr = np.concatenate([[1.0], rng.uniform(0.0, 1.5, n - 1)])
            oi = np.where(rng.random(n) < 0.7, rng.uniform(0, 1e6, n), 0.0)
            oi[0] = rng.uniform(1e3, 1e6)
            me = rng.normal(0, 1, n)
            snr = rng.uniform(0.5, 500, n)
            snr[rng.random(n) < 0.2] = np.nan

            assert score_pattern_v2(
                me, oi, snr, pr, tolerance=DEFAULT_TOLERANCE
            ) == score_pattern_v2(me, oi, snr, pr)

    def test_the_default_threshold_is_the_score_s(self):
        # The fit restates the detectability threshold rather than import it,
        # and its first bin starts there: one number, named twice.
        assert DEFAULT_DETECT_SNR == DETECT_SNR_K == RESPONSE_SNR_EDGES[0]
        assert DEFAULT_TOLERANCE.floor == RELATIVE_FLOOR == 0.05


class TestAMatchedLine:
    def test_inside_the_band_it_scores_as_a_match(self):
        # 15% under at an expected S/N of 15, where the curve admits 20% under:
        # as good as a line read at its share.
        assert _two_lines(0.85, 15.0, tolerance=CURVE) == pytest.approx(1.0)
        assert _two_lines(0.85, 15.0) < 0.75

    def test_under_the_band_it_is_charged_from_the_band_s_lower_end(self):
        # 30% under where the band opens 20% down: 10% short of the lower end,
        # two of today's 5% widths.
        assert _two_lines(0.7, 15.0, tolerance=CURVE) == pytest.approx(
            _one_line_at(math.exp(-0.5 * 2.0**2))
        )

    def test_over_its_share_it_is_charged_as_today(self):
        # The band closes at the natural share: a line cannot be censored high.
        assert _two_lines(1.1, 15.0, tolerance=CURVE) == pytest.approx(
            _two_lines(1.1, 15.0)
        )

    def test_the_band_follows_the_line_s_expected_snr(self):
        # The same 15% miss is free at 10 to 20 and charged from 0.95 at 50 to
        # 100: two widths short of it.
        assert _two_lines(0.85, 60.0, tolerance=CURVE) == pytest.approx(
            _one_line_at(math.exp(-0.5 * 2.0**2))
        )

    def test_without_the_anchor_s_snr_the_curve_admits_nothing(self):
        # A curve of S/N cannot place a line no S/N places, so it reads it as
        # today: the abundance floor's 5%.
        args = (np.zeros(2), np.array([1000.0, 1000.0 * SHARE * 0.85]), None)
        assert score_pattern_v2(
            *args, np.array([1.0, SHARE]), tolerance=CURVE
        ) == pytest.approx(score_pattern_v2(*args, np.array([1.0, SHARE])))

    @pytest.mark.parametrize("unusable", [0.0, -1.0, np.inf])
    def test_an_unusable_anchor_snr_places_no_line_either(self, unusable):
        # Zero, negative and infinite are "no S/N" as NaN is, alone and in bulk.
        me, pr = np.zeros(2), np.array([1.0, SHARE])
        oi = np.array([1000.0, 1000.0 * SHARE * 0.85])
        snr = np.array([unusable, 15.0])
        today = score_pattern_v2(me, oi, snr, pr)

        assert score_pattern_v2(me, oi, snr, pr, tolerance=CURVE) == today
        assert score_patterns_v2(me, oi, snr, pr, [0, 2], tolerance=CURVE)[
            0
        ] == pytest.approx(today)

    def test_a_tof_line_is_read_about_the_run_s_centre_at_its_width(self):
        # The TOF form: 15% under against a centre 3% under is 12% off, one
        # fitted 12% width.
        tof = EnvelopeTolerance(centre=-0.03, floor=0.12)
        assert _two_lines(0.85, 15.0, tolerance=tof) == pytest.approx(
            _one_line_at(math.exp(-0.5))
        )

    def test_a_tof_line_15_under_at_a_fitted_12_width_with_no_centre(self):
        # And with the centre at zero the 15% is one and a quarter widths.
        tof = EnvelopeTolerance(centre=0.0, floor=0.12)
        assert _two_lines(0.85, 15.0, tolerance=tof) == pytest.approx(
            _one_line_at(math.exp(-0.5 * 1.25**2))
        )

    def test_the_line_s_own_noise_still_widens_the_band_s_charge(self):
        # The S/N terms only ever widen: a line at S/N 4 is charged at its own
        # noise, wider than the floor, from the band's end.
        noisy = score_pattern_v2(
            np.zeros(2),
            np.array([1000.0, 1000.0 * SHARE * 0.5]),
            np.array([15.0 / SHARE, 4.0]),
            np.array([1.0, SHARE]),
            tolerance=CURVE,
        )
        rel_obs = SHARE * 0.5
        sigma = rel_obs * math.sqrt(1 / 4.0**2 + (SHARE / 15.0) ** 2)
        assert sigma > RELATIVE_FLOOR * SHARE
        miss = (rel_obs - 0.8 * SHARE) / sigma
        assert noisy == pytest.approx(_one_line_at(math.exp(-0.5 * miss**2)))


class TestAnAbsentLine:
    @staticmethod
    def _absent(expected_snr: float, tolerance=None) -> float:
        return score_pattern_v2(
            np.zeros(2),
            np.array([1000.0, 0.0]),
            np.array([expected_snr / SHARE, 0.0]),
            np.array([1.0, SHARE]),
            tolerance=tolerance,
        )

    def test_under_the_fitted_threshold_it_is_not_evidence(self):
        assert self._absent(8.0, CURVE) == 1.0
        # Today's threshold of 3 charges it.
        assert self._absent(8.0) == pytest.approx(_one_line_at(0.3))

    def test_at_the_fitted_threshold_it_is_charged(self):
        assert self._absent(12.0, CURVE) == pytest.approx(_one_line_at(0.3))

    def test_a_tolerance_with_no_threshold_leaves_the_caller_s(self):
        tof = EnvelopeTolerance(centre=-0.03, floor=0.12)
        assert self._absent(4.0, tof) == pytest.approx(_one_line_at(0.3))
        assert self._absent(2.0, tof) == 1.0


class TestManyEnvelopesAtOneTolerance:
    @pytest.mark.parametrize(
        "tolerance",
        [CURVE, EnvelopeTolerance(centre=0.04, floor=0.09)],
        ids=["curve", "centre and width"],
    )
    def test_each_scores_as_it_does_alone(self, tolerance):
        rng = np.random.default_rng(11)
        envelopes = []
        for _ in range(150):
            n = int(rng.integers(1, 10))
            pr = np.concatenate([[1.0], rng.uniform(0.0, 1.0, n - 1)])
            oi = np.where(rng.random(n) < 0.7, pr * 1e5 * rng.uniform(0.4, 1.3, n), 0.0)
            oi[0] = 1e5
            me = rng.normal(0, 0.3, n)
            snr = rng.uniform(1, 400, n)
            snr[rng.random(n) < 0.2] = np.nan
            envelopes.append((me, oi, snr, pr))
        alone = [
            score_pattern_v2(me, oi, snr, pr, tolerance=tolerance)
            for me, oi, snr, pr in envelopes
        ]
        offsets = np.concatenate([[0], np.cumsum([len(e[3]) for e in envelopes])])

        together = score_patterns_v2(
            *(np.concatenate([e[part] for e in envelopes]) for part in range(4)),
            offsets,
            tolerance=tolerance,
        )

        assert together == pytest.approx(alone, abs=1e-12, rel=0)
        # And the tolerance moved them: these are not today's scores.
        assert not np.allclose(
            together,
            score_patterns_v2(
                *(np.concatenate([e[part] for e in envelopes]) for part in range(4)),
                offsets,
            ),
        )


#: A curve whose lower end sits under the matcher's fixed 0.6 at an expected
#: S/N of 3 to 20, as a censoring Orbitrap's does (step 5.3), and above it from
#: 20 up; absent lines charged from the caller's threshold.
CENSORING = EnvelopeTolerance(
    snr_edges=RESPONSE_SNR_EDGES,
    lower=(0.3, 0.45, 0.9, 0.95, 1.0),
)

#: An ion whose envelope at a 1% floor is its own line and one 13C line.
ION = ("C10H15O4", -1)


def _scored(path: str, ratio: float, line_snr, tolerance):
    """The ion scored on a peak list holding its own line and its 13C line read
    at ``ratio`` of its share, the 13C line's expected S/N ``line_snr`` (None
    for a list with no S/N), through one-at-a-time or bulk matching."""
    scoring = PatternScoring(envelope_tolerance=tolerance)
    mzs, shares, _ = _predicted_envelope(*ION, 0.01, scoring)
    assert len(mzs) == 2
    intensities = np.array([1e6, 1e6 * shares[1] * ratio])
    snrs = None if line_snr is None else np.array([line_snr / shares[1], CLEAN])
    if path == "alone":
        score, _, isotopes = score_ion_envelope(
            *ION, 0.01, mzs, intensities, snrs, scoring
        )
        return score, isotopes
    scores, _, isotopes = score_ion_envelopes(
        [ION[0]], [ION[1]], [0.01], mzs, intensities, snrs, scoring
    )
    return float(scores[0]), isotopes[0]


@pytest.mark.parametrize("path", ["alone", "bulk"])
class TestTheMatchersHeightWindow:
    """The matcher's 40% window opens down to a fitted curve's lower end
    (mascope#2502): a dim 13C line censored scan by scan is the ion's own."""

    def test_a_censored_line_on_a_fitted_file_is_matched_not_charged(self, path):
        # 0.5 of its share at an expected S/N of 15, where the curve's lower
        # end is 0.45: inside the band, so it costs nothing.
        score, isotopes = _scored(path, 0.5, 15.0, CENSORING)

        assert isotopes["masses"][1] > 0
        assert isotopes["intensity_errors"][1] == pytest.approx(-0.5)
        assert score == pytest.approx(1.0)

    def test_the_same_line_on_an_unfitted_file_is_charged(self, path):
        score, isotopes = _scored(path, 0.5, 15.0, None)

        assert isotopes["masses"][1] == 0
        assert score < 0.9

    def test_the_window_opens_at_the_line_s_own_expected_snr(self, path):
        # The anchor's S/N is over 100, where the curve opens nowhere; the
        # line's own, the anchor's times its share, is 15.
        _, isotopes = _scored(path, 0.5, 15.0, CENSORING)
        assert isotopes["masses"][1] > 0
        # At an expected S/N of 25 the curve's lower end is 0.9, and the
        # fixed window's 0.6 decides.
        _, isotopes = _scored(path, 0.5, 25.0, CENSORING)
        assert isotopes["masses"][1] == 0

    def test_a_line_below_the_curve_s_lower_end_is_absent(self, path):
        _, isotopes = _scored(path, 0.4, 15.0, CENSORING)

        assert isotopes["masses"][1] == 0

    def test_a_curve_never_narrows_the_fixed_window(self, path):
        # 0.65 of its share where the curve's lower end is 0.9.
        _, isotopes = _scored(path, 0.65, 25.0, CENSORING)

        assert isotopes["masses"][1] > 0

    def test_a_line_read_high_is_still_turned_away(self, path):
        _, isotopes = _scored(path, 1.5, 15.0, CENSORING)

        assert isotopes["masses"][1] == 0

    def test_a_line_with_no_expected_snr_keeps_the_fixed_window(self, path):
        _, isotopes = _scored(path, 0.5, None, CENSORING)

        assert isotopes["masses"][1] == 0


class TestTheAdmittedBand:
    def test_each_bin_admits_its_own_lower_end(self):
        lower, upper = CURVE.admitted(np.array([3.0, 9.9, 10.0, 49.0, 50.0, 1e4]))

        np.testing.assert_allclose(lower, [0.6, 0.6, 0.8, 0.9, 0.95, 1.0])
        np.testing.assert_allclose(upper, 1.0)

    def test_below_the_first_edge_and_without_snr(self):
        # A curve whose brightest bin still opens below the share: a line no
        # S/N places is not read there, nor anywhere on the curve.
        curve = EnvelopeTolerance(
            snr_edges=RESPONSE_SNR_EDGES, lower=(0.6, 0.8, 0.9, 0.95, 0.97)
        )
        lower, upper = curve.admitted(np.array([1.0, np.nan]))

        np.testing.assert_allclose(lower, [0.6, 1.0])
        np.testing.assert_allclose(upper, 1.0)

    def test_a_centre_moves_both_ends(self):
        lower, upper = EnvelopeTolerance(centre=-0.03).admitted(np.array([5.0, 500.0]))

        np.testing.assert_allclose(lower, 0.97)
        np.testing.assert_allclose(upper, 0.97)

    def test_one_lower_end_per_bin(self):
        with pytest.raises(ValueError, match="one lower end per bin"):
            EnvelopeTolerance(snr_edges=(3.0, 10.0), lower=(0.8,))


def _lines(*bins: tuple[float, list[float], int]) -> EnvelopeLines:
    """Lines at an expected S/N per bin: the ratios read there, and how many
    predicted lines there the list did not hold."""
    snr, ratio = [], []
    for at, ratios, absent in bins:
        snr += [at] * (len(ratios) + absent)
        ratio += list(ratios) + [np.nan] * absent
    return EnvelopeLines(np.array(snr), np.array(ratio))


class TestTheResponseCurve:
    def test_per_bin_the_median_and_the_20th_percentile(self):
        low = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        mid = [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 0.8]
        high = [0.92, 0.94, 0.96, 0.98, 1.0, 1.02, 0.97]
        curve = fit_response_curve(
            _lines((5.0, low, 0), (15.0, mid, 0), (30.0, high, 0))
        )

        first, second, third = curve.bins[:3]
        assert first["median"] == pytest.approx(np.median(low))
        assert first["p20"] == pytest.approx(np.quantile(low, 0.2))
        assert second["p20"] == pytest.approx(np.quantile(mid, 0.2))
        assert third["lower"] == pytest.approx(np.quantile(high, 0.2))
        assert [b["lines"] for b in curve.bins] == [6, 7, 7, 0, 0]
        assert curve.bins[0]["snr"] == [3.0, 10.0]
        assert curve.bins[-1]["snr"] == [100.0, None]

    def test_it_is_made_non_decreasing_in_snr(self):
        # 10 to 20 reads higher than 20 to 50: the two are pooled into their
        # weighted mean, and 3 to 10 below them is left alone.
        low = [0.5] * 6
        mid = [0.9] * 10
        high = [0.8] * 5
        curve = fit_response_curve(
            _lines((5.0, low, 0), (15.0, mid, 0), (30.0, high, 0))
        )

        # The record keeps four decimals, and the score reads the record's
        # numbers, so a run scores at exactly what it says it scored at.
        pooled = (0.9 * 10 + 0.8 * 5) / 15
        lowers = [b["lower"] for b in curve.bins]
        assert lowers[:3] == pytest.approx([0.5, pooled, pooled], abs=1e-4)
        assert curve.bins[1]["p20"] == pytest.approx(0.9)
        assert curve.bins[1]["fitted_p20"] == pytest.approx(pooled, abs=1e-4)
        assert lowers == sorted(lowers)

    def test_it_is_capped_at_the_natural_share(self):
        curve = fit_response_curve(_lines((15.0, [1.2] * 10, 0), (60.0, [1.3] * 10, 0)))

        assert [b["lower"] for b in curve.bins[1:4]] == [1.0, 1.0, 1.0]
        assert curve.bins[1]["fitted_median"] == 1.0

    def test_a_bin_with_too_few_lines_takes_the_one_above(self):
        # 10 to 20 holds four lines, one short: it takes 20 to 50's end, and
        # above the last bin read the band is today's.
        curve = fit_response_curve(
            _lines((5.0, [0.6] * 8, 0), (15.0, [0.3] * 4, 0), (30.0, [0.9] * 10, 0))
        )

        assert curve.bins[1]["p20"] is None
        assert [b["lower"] for b in curve.bins] == pytest.approx(
            [0.6, 0.9, 0.9, 1.0, 1.0]
        )

    def test_too_few_lines_fit_no_curve(self):
        assert (
            fit_response_curve(_lines((15.0, [0.9] * (MIN_RESPONSE_LINES - 1), 30)))
            is None
        )
        # Lines under the first edge are in no bin and count for nothing,
        # toward the lines a curve needs either.
        assert fit_response_curve(_lines((2.0, [0.9] * 40, 0))) is None
        assert (
            fit_response_curve(_lines((15.0, [0.9] * 15, 0), (2.0, [0.9] * 10, 0)))
            is None
        )

    def test_absent_lines_count_from_above_the_bins_that_mostly_lack_them(self):
        # 3 to 10: 5 of 20 predicted lines in the list; 10 to 20: 8 of 10.
        curve = fit_response_curve(
            _lines((5.0, [0.7] * 5, 15), (15.0, [0.9] * 8, 2), (30.0, [0.95] * 10, 0))
        )

        assert curve.detect_snr == 10.0
        assert curve.tolerance.detect_snr == 10.0
        assert [b["predicted"] for b in curve.bins[:3]] == [20, 10, 10]

    def test_a_short_bin_above_a_full_one_is_charged_as_today(self):
        # 3 to 10 holds 10 of 12 lines, 10 to 20 only 5 of 15: the lines the
        # file plainly delivers keep today's threshold under them.
        curve = fit_response_curve(
            _lines((5.0, [0.7] * 10, 2), (15.0, [0.9] * 5, 10), (30.0, [0.95] * 10, 0))
        )

        assert curve.detect_snr is None

    def test_it_counts_from_the_lowest_bin_that_holds_its_lines(self):
        # 3 to 10: 6 of 20 in the list; 10 to 20: 20 of 22; 20 to 50 and 50 to
        # 100 hold every line; 100 up: 2 of 6, four bright lines the ratio
        # window turned away or a neighbour swelled.
        curve = fit_response_curve(
            _lines(
                (5.0, [0.8] * 6, 14),
                (15.0, [0.9] * 20, 2),
                (30.0, [0.95] * 30, 0),
                (70.0, [0.97] * 10, 0),
                (200.0, [1.0] * 2, 4),
            )
        )

        assert curve.detect_snr == 10.0
        # So an absent 81Br-like twin at an expected S/N of 58 costs what it
        # costs today, and an absent dim line under 10 nothing.
        twin = (np.zeros(2), np.array([1000.0, 0.0]), np.array([60.0, 0.0]))
        shares = np.array([1.0, 0.97])
        assert score_pattern_v2(
            *twin, shares, tolerance=curve.tolerance
        ) == pytest.approx(score_pattern_v2(*twin, shares))
        assert score_pattern_v2(*twin, shares) < 0.6
        assert (
            score_pattern_v2(
                np.zeros(2),
                np.array([1000.0, 0.0]),
                np.array([8.0 / SHARE, 0.0]),
                np.array([1.0, SHARE]),
                tolerance=curve.tolerance,
            )
            == 1.0
        )

    def test_no_short_bin_under_the_lowest_full_one_leaves_the_caller_s(self):
        # 3 to 10 is short but holds four predicted lines, too few to judge.
        curve = fit_response_curve(_lines((5.0, [0.7], 3), (15.0, [0.9] * 30, 2)))

        assert curve.detect_snr is None

    def test_every_judged_bin_short_counts_from_above_the_highest(self):
        # 3 to 10 and 20 to 50 each hold 10 of 25; 10 to 20 is not judged.
        curve = fit_response_curve(
            _lines((5.0, [0.7] * 10, 15), (15.0, [0.9] * 2, 1), (30.0, [0.9] * 10, 15))
        )

        assert curve.detect_snr == 50.0

    def test_the_open_top_bin_short_still_charges_from_its_lower_edge(self):
        curve = fit_response_curve(
            _lines((5.0, [0.7] * 10, 15), (200.0, [1.0] * 10, 12))
        )

        assert curve.detect_snr == 100.0


class TestTheCentreAndWidth:
    def test_the_median_and_scaled_mad_of_the_relative_error(self):
        ratios = np.array([0.9, 0.95, 1.0, 1.05, 1.1, 1.3, 0.85] * 3)
        centre, width = fit_centre_width(
            EnvelopeLines(np.full(ratios.size, 10.0), ratios)
        )

        errors = ratios - 1.0
        assert centre == pytest.approx(np.median(errors))
        assert width == pytest.approx(
            1.4826 * np.median(np.abs(errors - np.median(errors)))
        )

    def test_absent_lines_are_not_read(self):
        lines = _lines((10.0, [1.04] * MIN_CENTRE_WIDTH_LINES, 50))

        assert fit_centre_width(lines) == pytest.approx((0.04, 0.0))

    def test_too_few_lines_fit_nothing(self):
        assert (
            fit_centre_width(_lines((10.0, [1.0] * (MIN_CENTRE_WIDTH_LINES - 1), 0)))
            is None
        )


class TestTheLevels:
    THIN = _lines((15.0, [0.85] * 10, 2))
    SIBLING = _lines((15.0, [0.8] * 12, 1), (40.0, [0.95] * 6, 0))

    def test_a_file_with_lines_enough_fits_its_own(self):
        own = _lines((15.0, [0.85] * 25, 0))
        fit = fit_envelope_tolerance(FORM_CURVE, own, [self.SIBLING])

        assert fit.level == LEVEL_OWN
        assert fit.tolerance.lower[1] == pytest.approx(0.85)
        record = fit.record()
        assert record["level"] == LEVEL_OWN and "pooled_files" not in record
        # What the fit asks for, beside what it read.
        assert (
            record["min_lines"],
            record["min_bin_lines"],
            record["quantile"],
            record["presence_share"],
        ) == (MIN_RESPONSE_LINES, MIN_BIN_LINES, 0.2, 0.5)
        assert len(record["bins"]) == len(RESPONSE_SNR_EDGES)

    def test_a_thin_file_takes_the_curve_pooled_with_its_siblings(self):
        fit = fit_envelope_tolerance(FORM_CURVE, self.THIN, [self.SIBLING])

        pool = EnvelopeLines.concatenate([self.THIN, self.SIBLING])
        assert fit.level == LEVEL_POOLED
        assert (fit.pooled_files, fit.pooled_lines) == (2, 28)
        assert fit.tolerance == fit_response_curve(pool).tolerance
        record = fit.record()
        assert (record["pooled_files"], record["pooled_lines"]) == (2, 28)
        # The file's own lines are what it records, for the next file to pool.
        assert (record["lines"], record["absent"]) == (10, 2)
        assert len(record["observations"]["ratio"]) == 12

    def test_a_pool_too_thin_too_is_today_s(self):
        fit = fit_envelope_tolerance(
            FORM_CURVE, self.THIN, [_lines((15.0, [0.8] * 3, 0))]
        )

        assert fit.level == LEVEL_DEFAULT
        assert fit.tolerance == DEFAULT_TOLERANCE
        assert fit.record()["detect_snr"] == DEFAULT_DETECT_SNR

    def test_a_tof_fits_its_centre_and_width_and_pools_nothing(self):
        own = _lines((10.0, [0.9, 1.0, 1.1, 1.2] * 6, 0))
        fit = fit_envelope_tolerance(FORM_CENTRE_WIDTH, own, [self.SIBLING])

        assert fit.level == LEVEL_OWN
        centre, width = fit_centre_width(own)
        # Scored at the numbers the record keeps.
        assert fit.tolerance == EnvelopeTolerance(
            centre=round(centre, 4), floor=round(width, 4)
        )
        record = fit.record()
        assert (record["centre"], record["width"], record["min_lines"]) == (
            round(centre, 4),
            round(width, 4),
            MIN_CENTRE_WIDTH_LINES,
        )
        assert (
            fit_envelope_tolerance(
                FORM_CENTRE_WIDTH, self.THIN, [self.SIBLING] * 5
            ).level
            == LEVEL_DEFAULT
        )

    def test_a_tof_width_under_five_percent_floors_at_today_s(self):
        own = _lines((10.0, [1.02, 1.03] * 12, 0))
        fit = fit_envelope_tolerance(FORM_CENTRE_WIDTH, own)

        assert fit.centre_width[1] < RELATIVE_FLOOR
        assert fit.tolerance.floor == RELATIVE_FLOOR
        assert fit.record()["floor"] == RELATIVE_FLOOR

    def test_any_other_class_is_today_s(self):
        fit = fit_envelope_tolerance("unknown", _lines((15.0, [0.8] * 40, 0)))

        assert (fit.form, fit.level) == (FORM_DEFAULT, LEVEL_DEFAULT)
        assert fit.tolerance == DEFAULT_TOLERANCE


class TestTheRecord:
    @pytest.mark.parametrize(
        "form, lines",
        [
            (FORM_CURVE, _lines((5.0, [0.6] * 8, 20), (15.0, [0.85] * 12, 2))),
            (FORM_CENTRE_WIDTH, _lines((10.0, [0.9, 1.0, 1.1, 1.3] * 6, 0))),
            (
                FORM_CENTRE_WIDTH,
                _lines(
                    (10.0, 1.0 + np.random.default_rng(3).normal(0.0312, 0.0912, 40), 0)
                ),
            ),
        ],
        ids=["curve", "centre and width", "centre and width, unrounded"],
    )
    def test_it_rebuilds_the_tolerance_the_run_scored_at(self, form, lines):
        fit = fit_envelope_tolerance(form, lines)

        rebuilt = EnvelopeTolerance.from_record(fit.record())

        assert rebuilt == fit.tolerance

    @pytest.mark.parametrize(
        "record",
        [None, {}, {"form": FORM_CURVE, "level": LEVEL_DEFAULT}, "not a record"],
    )
    def test_no_fitted_record_is_today_s(self, record):
        assert EnvelopeTolerance.from_record(record) == DEFAULT_TOLERANCE

    def test_the_lines_survive_the_record(self):
        lines = _lines((5.0, [0.61234, 0.7], 2))

        again = EnvelopeLines.from_record(lines.record())

        np.testing.assert_allclose(again.expected_snr, lines.expected_snr)
        np.testing.assert_allclose(again.ratio, [0.6123, 0.7, np.nan, np.nan])
        assert (
            EnvelopeLines.from_record({"expected_snr": [1.0], "ratio": []}).lines == 0
        )

    def test_lines_no_snr_places_are_dropped(self):
        lines = EnvelopeLines(
            np.array([5.0, np.nan, 7.0, 8.0]), np.array([0.9, 0.9, np.inf, np.nan])
        )

        assert (lines.lines, lines.absent) == (1, 1)

    def test_min_bin_lines_is_what_a_bin_needs(self):
        curve = fit_response_curve(
            _lines((5.0, [0.6] * MIN_BIN_LINES, 0), (15.0, [0.9] * 20, 0))
        )
        assert curve.bins[0]["p20"] == pytest.approx(0.6)
