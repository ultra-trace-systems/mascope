"""The fit score reads an Orbitrap line by how many of its scans hold it.

An Orbitrap scan keeps a dim line only where it clears that scan's noise, so
the averaged peak list reads it diluted by the share of scans it missed. Where
the list counts, per peak, the scans holding it (``n_scans``) and the scans
averaged (``n_scans_total``), a matched line is read at its mean per scan, the
scans that missed it under their floor counted by the censored normal's
estimate, against its anchor read the same way, and held to its share
(``mascope_tools.composition.scan_presence``). An absent line is gated as on a
list without counts: the threshold the anchor's count would set
(``absent_line_detectable``) waits for a soft gate (mascope#3123). A list
without counts scores as before.

A line is built here as the floor model has it (:func:`_censored_line`): kept
by a scan where its per-scan S/N, normal about its mean with the scan's noise,
clears the floor, and listed at the censored normal's moments, where the
estimate's equation holds exactly.
"""

import numpy as np
import polars as pl
import pytest
from scipy.optimize import brentq
from scipy.stats import norm

from mascope_tools.composition.envelope_tolerance import (
    RESPONSE_SNR_EDGES,
    EnvelopeTolerance,
)
from mascope_tools.composition.heuristic_filter import (
    match_isotopic_pattern,
    predict_isotopes,
    score_ion_envelope,
    score_ion_envelopes,
    score_pattern_v2,
    score_patterns_v2,
)
from mascope_tools.composition.models import PatternScoring
from mascope_tools.composition.scan_presence import (
    SCAN_FLOOR_SNR,
    absent_line_detectable,
    censored_scan_mean,
    expected_listed_snr,
    presence_corrected,
    presence_fractions,
    scan_levels,
)
from mascope_tools.composition.scoring import score_candidates


# An anchor and its M+1 line at 0.11 of it, both bright enough that the 5%
# floor sets the line's tolerance
PRED = np.array([1.0, 0.11])
ME = np.array([0.1, 0.1])
SNR = np.array([500.0, 55.0])
FULL = 1000.0 * PRED


def _censored_line(present: float, total: float, floor: float = SCAN_FLOOR_SNR):
    """A line kept by ``present`` of ``total`` scans under the floor model, at
    the censored normal's moments, in units of a scan's noise.

    :return: Its per-scan mean ``mu``, its mean over the scans that keep it,
        its height in the averaged list (the kept scans' sum over every scan)
        and its S/N there (the averaging's law, ``kept mean * n / sqrt(N)``).
    """
    fraction = present / total
    mean = floor + norm.ppf(fraction)
    kept_mean = mean + norm.pdf(floor - mean) / fraction
    return mean, kept_mean, fraction * kept_mean, present * kept_mean / np.sqrt(total)


def _score(intensities, n_scans=None, n_scans_total=None, snr=SNR, pred=PRED, **kwargs):
    return score_pattern_v2(
        ME,
        np.asarray(intensities, float),
        snr,
        pred,
        n_scans=None if n_scans is None else np.asarray(n_scans, float),
        n_scans_total=(
            None if n_scans_total is None else np.asarray(n_scans_total, float)
        ),
        **kwargs,
    )


def _threshold(scans: float, anchor_scans: float | None = None) -> float:
    """The expected S/N from which the anchor's count would charge an absent
    line, on a file of ``scans`` scans whose anchor is in ``anchor_scans`` of
    them."""
    anchor_scans = scans if anchor_scans is None else anchor_scans
    return brentq(
        lambda e: (
            expected_listed_snr(
                np.array([e]), np.array([anchor_scans]), np.array([scans])
            )[0]
            - 3.0
        ),
        0.01,
        1000.0,
    )


class TestPresenceFractions:
    def test_a_fraction_is_the_scans_holding_over_the_scans_averaged(self):
        assert presence_fractions([2, 4], [4, 4]).tolist() == [0.5, 1.0]

    @pytest.mark.parametrize(
        "present, total",
        [
            (0, 4),
            (-1, 4),
            (5, 4),
            (np.nan, 4),
            (2, 0),
            (2, np.nan),
            (np.inf, 4),
            (2, np.inf),
        ],
    )
    def test_a_count_that_is_not_one_says_nothing(self, present, total):
        assert np.isnan(presence_fractions([present], [total])).all()

    def test_no_counts_are_no_fractions(self):
        assert np.isnan(presence_fractions(None, None, 3)).all()
        assert presence_fractions([1, 2], None).size == 0

    def test_a_line_is_read_against_its_anchor_at_their_own_levels(self):
        shares, read = presence_corrected(
            np.array([0.055, 0.11, 0.2, 0.3]),
            np.array([2.0, 1.5, np.nan, 2.0]),
            np.array([1.0, 1.5, 1.0, np.nan]),
        )

        assert shares.tolist() == pytest.approx([0.11, 0.11, 0.2, 0.3])
        assert read.tolist() == [True, True, False, False]


class TestTheEstimate:
    """The censored-normal estimate of a line's per-scan mean."""

    @pytest.mark.parametrize("total", [4, 20, 100])
    @pytest.mark.parametrize("present", [0.05, 0.3, 0.5, 0.69, 0.84, 0.97])
    def test_it_finds_the_mean_the_censored_moments_came_from(self, total, present):
        mean, kept_mean, _, _ = _censored_line(present * total, total)

        assert censored_scan_mean(
            np.array([kept_mean]), np.array([present * total]), np.array([total])
        )[0] == pytest.approx(mean, abs=1e-9)

    def test_a_line_in_every_scan_is_its_kept_mean(self):
        assert censored_scan_mean(
            np.array([2.5, 40.0]), np.array([4.0, 100.0]), np.array([4.0, 100.0])
        ).tolist() == [2.5, 40.0]

    def test_it_solves_its_equation_across_the_range(self):
        """From one scan in two to 9,999 in 10,000, and from a kept mean at
        the floor to a thousand times the noise: the score is zero at the
        root the steps reach."""
        total = np.array([2, 4, 20, 100, 1000, 10000], float)
        kept_mean = np.array([2.0, 2.4, 3.0, 10.0, 100.0, 1000.0])
        total, kept_mean, share = (
            a.ravel()
            for a in np.meshgrid(total, kept_mean, [0.0001, 0.3, 0.5, 0.9, 0.9999])
        )
        present = np.clip(np.round(share * total), 1, total - 1)

        mean = censored_scan_mean(kept_mean, present, total)

        below = SCAN_FLOOR_SNR - mean
        mills = np.exp(norm.logpdf(below) - norm.logcdf(below))
        residual = present * (kept_mean - mean) - (total - present) * mills
        assert np.all(mean < kept_mean)
        assert np.abs(residual) == pytest.approx(0.0, abs=1e-7 * total.max())

    def test_simulated_lines_read_their_share(self):
        """Lines drawn scan by scan, each kept where it clears the floor: the
        estimate's median over the truth is one within the draws' spread,
        where the mean over the kept scans overshoots by 0.40 at half
        presence."""
        rng = np.random.default_rng(3)
        for total, mean in ((20, 2.0), (100, 1.5), (100, 2.0), (100, 3.0)):
            draws = rng.normal(mean, 1.0, (4000, total))
            kept = draws >= SCAN_FLOOR_SNR
            present = kept.sum(axis=1).astype(float)
            seen = present > 0
            kept_mean = np.where(kept, draws, 0.0).sum(axis=1)[seen] / present[seen]

            estimate = censored_scan_mean(
                kept_mean, present[seen], np.full(seen.sum(), float(total))
            )

            assert np.median(estimate / mean) == pytest.approx(1.0, abs=0.02)
            if mean == SCAN_FLOOR_SNR:
                assert np.median(kept_mean / mean) == pytest.approx(1.40, abs=0.02)

    def test_the_estimate_reads_its_floor(self):
        mean, kept_mean, _, _ = _censored_line(30, 100, floor=3.0)
        args = np.array([kept_mean]), np.array([30.0]), np.array([100.0])

        assert censored_scan_mean(*args, floor=3.0)[0] == pytest.approx(mean)
        assert censored_scan_mean(*args)[0] < mean - 0.3


class TestScanLevels:
    def test_the_level_reads_the_listed_height_at_its_mean_per_scan(self):
        mean, _, height, snr = _censored_line(30, 100)

        level = scan_levels(np.array([snr]), np.array([30.0]), np.array([100.0]))

        assert height * level[0] == pytest.approx(mean, rel=1e-9)
        assert height < mean

    def test_a_line_in_every_scan_reads_as_listed(self):
        """Whatever its S/N, the averaged list has its mean per scan."""
        levels = scan_levels(
            np.array([np.nan, 0.0, 55.0]), np.array([4, 4, 4]), np.array([4, 4, 4])
        )

        assert levels.tolist() == [1.0, 1.0, 1.0]
        assert scan_levels(None, np.array([4]), np.array([4])).tolist() == [1.0]

    @pytest.mark.parametrize("snr", [np.nan, 0.0, -3.0, np.inf])
    def test_a_line_some_scans_missed_needs_its_snr(self, snr):
        assert np.isnan(scan_levels(np.array([snr]), np.array([2]), np.array([4])))
        assert np.isnan(scan_levels(None, np.array([2]), np.array([4])))

    def test_a_line_at_the_noise_keeps_the_lists_reading(self):
        """Kept by 2 of 100 scans at the noise's own mean above the floor: the
        estimate puts the line at or below the noise, which no level reads."""
        kept_mean = norm.pdf(SCAN_FLOOR_SNR) / norm.sf(SCAN_FLOOR_SNR)
        snr = 2 * kept_mean / np.sqrt(100)

        assert (
            censored_scan_mean(
                np.array([kept_mean]), np.array([2.0]), np.array([100.0])
            )[0]
            <= 0
        )
        assert np.isnan(scan_levels(np.array([snr]), np.array([2]), np.array([100])))

    def test_a_count_that_is_not_one_reads_no_level(self):
        assert np.isnan(
            scan_levels(np.array([10.0, 10.0]), np.array([5, np.nan]), [4, 4])
        ).all()
        assert np.isnan(scan_levels(np.array([10.0]), None, None, 3)).all()

    def test_the_floor_is_passed_on(self):
        mean, _, height, snr = _censored_line(30, 100, floor=3.0)

        level = scan_levels(np.array([snr]), np.array([30]), np.array([100]), floor=3.0)

        assert height * level[0] == pytest.approx(mean)


def _dim_envelope(present: float, total: float, anchor_present: float | None = None):
    """An anchor and its M+1 line at 0.11 of it, the line kept by ``present``
    of ``total`` scans at the floor model's moments, and the anchor in every
    scan or in ``anchor_present`` of them.

    :return: The listed heights, their S/N, the counts, and the heights at
        the predicted share over every scan.
    """
    mean, _, height, snr = _censored_line(present, total)
    if anchor_present is None:
        anchor_mean = anchor_height = mean / PRED[1]
        anchor_snr = anchor_mean * np.sqrt(total)
        anchor_present = total
    else:
        anchor_mean, _, anchor_height, anchor_snr = _censored_line(
            anchor_present, total
        )
    return (
        np.array([anchor_height, height]),
        np.array([anchor_snr, snr]),
        (np.array([anchor_present, present]), np.array([total, total])),
        np.array([anchor_mean, mean]),
    )


class TestAMatchedLine:
    @pytest.mark.parametrize("present, total", [(2, 4), (30, 100), (50, 100)])
    def test_a_line_some_scans_missed_reads_its_share_at_its_mean(self, present, total):
        """The averaged list reads the line low by the scans it missed, and
        the mean over the scans that kept it high: read at its mean per scan,
        the line is at its share."""
        heights, snr, counts, exact = _dim_envelope(present, total)

        counted = _score(heights, *counts, snr=snr)

        assert counted == pytest.approx(_score(exact, snr=snr), abs=1e-9)
        assert _score(heights, snr=snr) < counted - 0.05

    def test_the_mean_over_the_kept_scans_is_not_the_reading(self):
        """At half the scans the kept scans read the line 1.40 times its
        share: a score held to the share would charge that."""
        heights, snr, counts, _ = _dim_envelope(50, 100)
        over_kept = heights * np.array([1.0, 2.0])

        assert _score(over_kept, snr=snr) < _score(heights, *counts, snr=snr) - 0.3

    def test_an_anchor_some_scans_missed_is_read_at_its_mean_too(self):
        """A dim compound: its anchor kept by 84 of 100 scans and its line by
        30, both read at their mean per scan."""
        heights, snr, counts, exact = _dim_envelope(30, 100, anchor_present=84)
        line_alone = (counts[0] * np.array([np.nan, 1.0]), counts[1])

        counted = _score(heights, *counts, snr=snr, pred=exact / exact[0])

        assert counted == pytest.approx(
            _score(exact, snr=snr, pred=exact / exact[0]), abs=1e-9
        )
        assert _score(heights, *line_alone, snr=snr, pred=exact / exact[0]) == (
            pytest.approx(_score(heights, snr=snr, pred=exact / exact[0]))
        )

    def test_a_line_some_scans_missed_without_an_snr_keeps_the_curve(self):
        curve = EnvelopeTolerance(snr_edges=RESPONSE_SNR_EDGES, lower=(0.6,) * 5)
        heights, snr, counts, _ = _dim_envelope(50, 100)
        no_line_snr = snr * np.array([1.0, np.nan])

        assert _score(
            heights, *counts, snr=no_line_snr, tolerance=curve
        ) == pytest.approx(_score(heights, snr=no_line_snr, tolerance=curve))

    def test_a_line_read_by_its_counts_is_held_to_its_share(self):
        """The curve's band allows for the dilution the counts take out: a
        counted line reading 0.7 of its share in every scan is a miss, where
        the curve would have taken 0.7 as the dilution and let it pass."""
        curve = EnvelopeTolerance(snr_edges=RESPONSE_SNR_EDGES, lower=(0.6,) * 5)
        low = [FULL[0], 0.7 * FULL[1]]

        assert _score(low, tolerance=curve) == pytest.approx(1.0, abs=0.01)
        assert _score(low, [4, 4], [4, 4], tolerance=curve) < 0.5

    def test_a_line_without_a_count_keeps_the_curve(self):
        curve = EnvelopeTolerance(snr_edges=RESPONSE_SNR_EDGES, lower=(0.6,) * 5)
        low = [FULL[0], 0.7 * FULL[1]]

        assert _score(low, [4, np.nan], [4, 4], tolerance=curve) == pytest.approx(
            _score(low, tolerance=curve)
        )

    def test_an_anchor_without_a_count_reads_no_line_by_it(self):
        diluted = [FULL[0], FULL[1] / 2]

        assert _score(diluted, [np.nan, 2], [4, 4]) == pytest.approx(_score(diluted))

    def test_counts_that_disagree_with_the_lines_are_refused(self):
        with pytest.raises(ValueError, match="n_scans"):
            _score(FULL, [4, 2, 1], [4, 4, 4])


class TestAnAbsentLine:
    """The M+1 line absent, its expected S/N its share times the anchor's."""

    #: A response curve whose threshold is above the count rule's at 12
    CURVE = EnvelopeTolerance(
        snr_edges=RESPONSE_SNR_EDGES, lower=(0.6,) * 5, detect_snr=20.0
    )

    @staticmethod
    def _absent(expected_snr, counts, path="alone", **kwargs):
        """The score with the M+1 line absent, its anchor counted with
        ``counts`` (scans holding it, scans averaged) or not at all."""
        snr = np.array([expected_snr / PRED[1], np.nan])
        intensities = np.array([FULL[0], 0.0])
        n_scans, n_scans_total = (
            (np.array([counts[0], np.nan]), np.array([counts[1], np.nan]))
            if counts
            else (None, None)
        )
        if path == "alone":
            return _score(intensities, n_scans, n_scans_total, snr=snr, **kwargs)
        return score_patterns_v2(
            ME,
            intensities,
            snr,
            PRED,
            [0, 2],
            n_scans=n_scans,
            n_scans_total=n_scans_total,
            **kwargs,
        )[0]

    def _free(self, expected_snr):
        """The score with the line excluded: below every threshold."""
        return self._absent(expected_snr, None, k_detect=1e9)

    @pytest.mark.parametrize("path", ["alone", "bulk"])
    @pytest.mark.parametrize(
        "expected_snr, counts, curve, charged",
        [
            # Over today's 3, under the count rule's 9.2 on a hundred scans
            (6.0, (100, 100), False, True),
            # Under both
            (2.0, (100, 100), False, False),
            # Over the count rule's, under the curve's 20
            (12.0, (100, 100), True, False),
            # The anchor in half the scans, over both
            (25.0, (50, 100), True, True),
        ],
        ids=["over-today", "under-both", "under-the-curve", "over-both"],
    )
    def test_a_counted_anchor_gates_an_absent_line_as_without_counts(
        self, path, expected_snr, counts, curve, charged
    ):
        """The anchor's count would move the threshold (mascope#3123); the
        score gates the line at the curve's threshold, or today's 3 without
        one, exactly as without counts."""
        kwargs = {"tolerance": self.CURVE} if curve else {}

        counted = self._absent(expected_snr, counts, path, **kwargs)

        assert counted == pytest.approx(
            self._absent(expected_snr, None, path, **kwargs), abs=1e-12
        )
        if charged:
            assert counted < self._free(expected_snr) - 0.1
        else:
            assert counted == pytest.approx(self._free(expected_snr), abs=1e-12)

    def test_the_count_rule_would_gate_those_lines_otherwise(self):
        """What the cases above stand for: the count rule frees the line at 6
        on a hundred scans and charges it at 12, where 3 and a curve at 20
        do the opposite."""
        assert absent_line_detectable(
            np.array([6.0, 12.0]), np.array([100, 100]), np.array([100, 100]), 3.0
        ).tolist() == [False, True]

    def test_the_threshold_rises_with_the_scans_averaged(self):
        thresholds = [_threshold(n) for n in (1, 2, 4, 10, 20, 100, 1000, 10000)]

        assert thresholds == sorted(thresholds)
        assert thresholds[0] == pytest.approx(3.22, abs=0.01)
        assert _threshold(4) == pytest.approx(4.24, abs=0.01)
        assert _threshold(100) == pytest.approx(9.24, abs=0.01)

    def test_with_no_floor_the_threshold_is_todays(self):
        assert expected_listed_snr(
            np.array([3.0, 7.5]), np.array([100, 4]), np.array([100, 4]), floor=-50
        ) == pytest.approx([3.0, 7.5])

    def test_an_anchor_in_fewer_scans_lowers_the_threshold(self):
        """Half the scans hold the anchor: the line's per-scan S/N is twice
        what it is with every scan holding it, and the list holds it from
        lower."""
        assert _threshold(100, 50) < _threshold(100)
        assert absent_line_detectable(
            np.array([8.0, 8.0]), np.array([50, 100]), np.array([100, 100]), 3.0
        ).tolist() == [True, False]

    def test_without_the_anchors_signal_to_noise_the_abundance_decides(self):
        """No S/N to read the count against: the no-SNR rule, as before."""
        counted = _score([FULL[0], 0.0], [100, np.nan], [100, np.nan], snr=None)

        assert counted == pytest.approx(_score([FULL[0], 0.0], snr=None))

    def test_the_rule_is_the_floors(self):
        assert SCAN_FLOOR_SNR == 2.0
        assert absent_line_detectable(
            np.array([5.0]), np.array([10]), np.array([10]), 3.0
        ).tolist() == [False]
        assert absent_line_detectable(
            np.array([5.0]), np.array([10]), np.array([10]), 3.0, floor=1.0
        ).tolist() == [True]


class TestManyEnvelopes:
    def test_each_envelope_reads_its_own_anchors_count(self):
        """Two envelopes laid end to end, the second's anchor in half the
        scans: each reads its lines by its own anchor's count."""
        heights, line_snr, (line_present, line_total), exact = _dim_envelope(2, 4)
        oi = np.array([*heights, FULL[0], 0.0])
        snr = np.array([*line_snr, 8.0 / PRED[1], np.nan])
        me = np.full(4, 0.1)
        pr = np.concatenate([PRED, PRED])
        present = np.array([*line_present, 50.0, np.nan])
        total = np.array([*line_total, 100.0, np.nan])

        together = score_patterns_v2(
            me, oi, snr, pr, [0, 2, 4], n_scans=present, n_scans_total=total
        )

        for position, lines in enumerate((slice(0, 2), slice(2, 4))):
            assert together[position] == pytest.approx(
                score_pattern_v2(
                    me[lines],
                    oi[lines],
                    snr[lines],
                    pr[lines],
                    n_scans=present[lines],
                    n_scans_total=total[lines],
                ),
                abs=1e-12,
            )
        assert together[0] == pytest.approx(_score(exact, snr=line_snr), abs=1e-9)


#: An ion whose M+1 line, kept by 30 of 100 scans and so listed at 0.54 of its
#: share, falls outside the matcher's 40% window
ION, CHARGE = "C10H15O4", -1


def _diluted_spectrum(present: float = 30.0, total: float = 100.0, every=False):
    """The ion's first two lines, the anchor in every scan, the M+1 line kept
    by ``present`` of ``total`` scans at the floor model's moments, or in
    every scan at its share with ``every``; heights in a noise of 1,000."""
    lines, abundances, _ = predict_isotopes(ION, CHARGE, None, 1e-3)
    lines, abundances = np.asarray(lines[:2]), np.asarray(abundances[:2])
    mean, _, height, snr = _censored_line(present, total)
    anchor = mean * abundances[0] / abundances[1]
    if every:
        height, snr, present = mean, mean * np.sqrt(total), total
    return (
        lines,
        np.array([anchor, height]) * 1e3,
        np.array([anchor, snr / np.sqrt(total)]) * np.sqrt(total),
        np.array([total, present]),
        np.array([total, total]),
    )


class TestTheUntargetedMatcher:
    def test_a_diluted_line_read_at_its_mean_is_kept(self):
        mzs, intensities, snr, present, total = _diluted_spectrum()
        scoring = PatternScoring(sigma_ppm=0.6)

        counted = score_ion_envelope(
            ION, CHARGE, 1e-3, mzs, intensities, snr, scoring, present, total
        )
        uncounted = score_ion_envelope(
            ION, CHARGE, 1e-3, mzs, intensities, snr, scoring
        )

        assert counted[2]["masses"][1] == mzs[1]
        assert counted[2]["intensity_errors"][1] == pytest.approx(0.0, abs=1e-9)
        assert uncounted[2]["masses"][1] == 0.0
        assert counted[0] > uncounted[0]

    @pytest.mark.parametrize("present", [50, 69, 84])
    @pytest.mark.parametrize("path", ["alone", "bulk"])
    def test_a_true_dim_line_on_a_long_fitted_file_scores_as_an_exact_one(
        self, present, path
    ):
        """A hundred scans and a fitted curve (lower ends 0.45 to 0.94 from an
        expected S/N of 3 to 50): a line kept by half to 84% of them, at an
        expected S/N of 20 to 30, scores as the line at its share in every
        scan does, and no lower than the curve reads it."""
        curve = EnvelopeTolerance(
            snr_edges=(3.0, 10.0, 20.0, 50.0),
            lower=(0.45, 0.6, 0.8, 0.94),
            detect_snr=12.0,
        )
        scoring = PatternScoring(sigma_ppm=0.6, envelope_tolerance=curve)

        def score(*spectrum, counted=True):
            mzs, intensities, snr, n_scans, n_scans_total = spectrum
            counts = (n_scans, n_scans_total) if counted else ()
            if path == "alone":
                return score_ion_envelope(
                    ION, CHARGE, 1e-3, mzs, intensities, snr, scoring, *counts
                )[0]
            return score_ion_envelopes(
                [ION], [CHARGE], 1e-3, mzs, intensities, snr, scoring, *counts
            )[0][0]

        dim = _diluted_spectrum(present, 100.0)
        exact = _diluted_spectrum(present, 100.0, every=True)

        assert score(*dim) == pytest.approx(score(*exact), abs=1e-9)
        assert score(*dim) >= score(*dim, counted=False) - 1e-9

    def test_the_bulk_matcher_reads_the_counts_alike(self):
        mzs, intensities, snr, present, total = _diluted_spectrum()
        scoring = PatternScoring(sigma_ppm=0.6)

        scores, _, isotopes = score_ion_envelopes(
            [ION], [CHARGE], 1e-3, mzs, intensities, snr, scoring, present, total
        )
        alone = score_ion_envelope(
            ION, CHARGE, 1e-3, mzs, intensities, snr, scoring, present, total
        )

        assert scores[0] == pytest.approx(alone[0], abs=1e-12)
        assert isotopes[0]["masses"][1] == mzs[1]

    @pytest.mark.parametrize("path", ["alone", "bulk"])
    def test_a_counted_line_keeps_the_fixed_window_on_a_fitted_file(self, path):
        """A fitted curve opens the window down for a line read without counts
        (mascope#2502). A line read by its counts has had that dilution taken
        out, so at half its share over every scan it is not the ion's line."""
        lines, abundances, _ = predict_isotopes(ION, CHARGE, None, 1e-3)
        mzs = np.asarray(lines[:2])
        share = abundances[1] / abundances[0]
        intensities = np.array([1e6, 1e6 * share * 0.5])
        # The line's expected S/N is 15, where the curve's lower end is 0.45
        snr = np.array([15.0 / share, 7.5])
        every = np.array([10.0, 10.0])
        curve = EnvelopeTolerance(
            snr_edges=RESPONSE_SNR_EDGES, lower=(0.3, 0.45, 0.9, 0.95, 1.0)
        )
        scoring = PatternScoring(sigma_ppm=0.6, envelope_tolerance=curve)

        def masses(*counts):
            if path == "alone":
                return score_ion_envelope(
                    ION, CHARGE, 1e-3, mzs, intensities, snr, scoring, *counts
                )[2]["masses"]
            return score_ion_envelopes(
                [ION], [CHARGE], 1e-3, mzs, intensities, snr, scoring, *counts
            )[2][0]["masses"]

        assert masses()[1] == mzs[1]
        assert masses(every, every)[1] == 0.0

    def test_a_peak_list_carries_its_counts_to_the_score(self):
        mzs, intensities, snr, present, total = _diluted_spectrum()
        peaks = pl.DataFrame(
            {"mz": mzs, "intensity": intensities, "signal_to_noise": snr}
        )
        counted = peaks.with_columns(
            pl.Series("n_scans", present), pl.Series("n_scans_total", total)
        )
        candidate = {
            "ion": f"{ION}-",
            "formula": ION,
            "observed_mass": float(mzs[0]),
            "composition_error_ppm": 0.0,
        }
        scoring = PatternScoring(sigma_ppm=0.6)

        with_counts, _ = match_isotopic_pattern([dict(candidate)], counted, scoring)
        without, _ = match_isotopic_pattern([dict(candidate)], peaks, scoring)

        assert (
            with_counts[0]["isotopic_pattern_score"]
            > without[0]["isotopic_pattern_score"]
        )

    def test_the_bulk_candidate_scorer_reads_a_frames_counts(self):
        mzs, intensities, snr, present, total = _diluted_spectrum()
        peaks = pl.DataFrame(
            {
                "mz": mzs,
                "intensity": intensities,
                "signal_to_noise": snr,
                "n_scans": present,
                "n_scans_total": total,
            }
        ).to_pandas()
        candidates = [{"ion": f"{ION}-", "observed_mass": float(mzs[0])}]
        scoring = PatternScoring(sigma_ppm=0.6)

        counted = score_candidates(candidates, peaks, scoring)
        uncounted = score_candidates(
            candidates, peaks.drop(columns=["n_scans", "n_scans_total"]), scoring
        )

        assert counted["fit_score"].iloc[0] > uncounted["fit_score"].iloc[0]
