"""The fit score reads an Orbitrap line by how many of its scans hold it.

An Orbitrap scan keeps a dim line only where it clears that scan's noise, so
the averaged peak list reads it diluted by the share of scans it missed. Where
the list counts, per peak, the scans holding it (``n_scans``) and the scans
averaged (``n_scans_total``), a matched line is read over the scans that hold
it against its anchor read over the scans that hold the anchor, and held to
its share; an absent line is charged where the averaged list would have held
it (``mascope_tools.composition.scan_presence``). A list without counts scores
as before.
"""

import numpy as np
import polars as pl
import pytest
from scipy.optimize import brentq

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
    expected_listed_snr,
    presence_corrected,
    presence_fractions,
)
from mascope_tools.composition.scoring import score_candidates


# An anchor and its M+1 line at 0.11 of it, both bright enough that the 5%
# floor sets the line's tolerance
PRED = np.array([1.0, 0.11])
ME = np.array([0.1, 0.1])
SNR = np.array([500.0, 55.0])
FULL = 1000.0 * PRED


def _score(intensities, n_scans=None, n_scans_total=None, snr=SNR, **kwargs):
    return score_pattern_v2(
        ME,
        np.asarray(intensities, float),
        snr,
        PRED,
        n_scans=None if n_scans is None else np.asarray(n_scans, float),
        n_scans_total=(
            None if n_scans_total is None else np.asarray(n_scans_total, float)
        ),
        **kwargs,
    )


def _threshold(scans: float, anchor_scans: float | None = None) -> float:
    """The expected S/N from which an absent line is charged, on a file of
    ``scans`` scans whose anchor is in ``anchor_scans`` of them."""
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

    def test_a_line_is_read_against_its_anchor_over_their_own_scans(self):
        shares, read = presence_corrected(
            np.array([0.055, 0.11, 0.2]),
            np.array([0.5, 0.5, np.nan]),
            np.array([1.0, 0.5, 1.0]),
        )

        assert shares.tolist() == pytest.approx([0.11, 0.11, 0.2])
        assert read.tolist() == [True, True, False]


class TestAMatchedLine:
    def test_a_line_kept_by_half_the_scans_reads_its_share_over_them(self):
        """Missing from two of four scans, the line reads half its share in
        the averaged list; read over the two that hold it, all of it."""
        diluted = [FULL[0], FULL[1] / 2]

        counted = _score(diluted, [4, 2], [4, 4])

        assert counted == pytest.approx(_score(FULL), abs=1e-12)
        assert _score(diluted) < counted - 0.3

    def test_a_compound_in_part_of_the_file_is_read_over_its_own_scans(self):
        """The anchor in two of four scans: a line in the same two is not
        diluted against it, one in one of them is, by half."""
        assert _score(FULL, [2, 2], [4, 4]) == pytest.approx(_score(FULL))
        assert _score([FULL[0], FULL[1] / 2], [2, 1], [4, 4]) == pytest.approx(
            _score(FULL), abs=1e-12
        )

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

    @staticmethod
    def _absent(expected_snr, anchor_scans, scans, **kwargs):
        snr = np.array([expected_snr / PRED[1], np.nan])
        return _score(
            [FULL[0], 0.0], [anchor_scans, np.nan], [scans, np.nan], snr=snr, **kwargs
        )

    @staticmethod
    def _free(expected_snr):
        """The score with the line excluded: below every threshold."""
        snr = np.array([expected_snr / PRED[1], np.nan])
        return _score([FULL[0], 0.0], snr=snr, k_detect=1e9)

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

    def test_a_line_the_list_would_not_have_held_is_free(self):
        """At an expected S/N of 6 a hundred scans would have kept the line in
        too few of them to list it, where today's 3 charges it."""
        assert self._absent(6.0, 100, 100) == pytest.approx(self._free(6.0))
        assert _score(
            [FULL[0], 0.0], snr=np.array([6.0 / PRED[1], np.nan])
        ) < self._free(6.0)

    def test_a_line_the_list_would_have_held_is_charged(self):
        assert self._absent(12.0, 100, 100) < self._free(12.0) - 0.1

    def test_an_anchor_in_fewer_scans_charges_from_lower(self):
        """Half the scans hold the anchor: the line's per-scan S/N is twice
        what it is with every scan holding it, and the list holds it from
        lower."""
        assert _threshold(100, 50) < _threshold(100)
        assert self._absent(8.0, 50, 100) < self._free(8.0) - 0.1
        assert self._absent(8.0, 100, 100) == pytest.approx(self._free(8.0))

    def test_the_count_stands_in_front_of_the_curves_threshold(self):
        curve = EnvelopeTolerance(
            snr_edges=RESPONSE_SNR_EDGES, lower=(0.6,) * 5, detect_snr=20.0
        )

        assert self._absent(12.0, 100, 100, tolerance=curve) < self._free(12.0) - 0.1
        snr = np.array([12.0 / PRED[1], np.nan])
        assert _score([FULL[0], 0.0], snr=snr, tolerance=curve) == pytest.approx(
            self._free(12.0)
        )

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
        scans: each reads its lines and its absences by its own anchor."""
        oi = np.array([FULL[0], FULL[1] / 2, FULL[0], 0.0])
        snr = np.array([500.0, 55.0, 8.0 / PRED[1], np.nan])
        me = np.full(4, 0.1)
        pr = np.concatenate([PRED, PRED])
        present = np.array([4.0, 2.0, 50.0, np.nan])
        total = np.array([4.0, 4.0, 100.0, np.nan])

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
        assert together[0] == pytest.approx(_score(FULL), abs=1e-12)


#: An ion whose M+1 line, diluted to half its share by the scans it missed,
#: falls outside the matcher's 40% window
ION, CHARGE = "C10H15O4", -1


def _diluted_spectrum():
    lines, abundances, _ = predict_isotopes(ION, CHARGE, None, 1e-3)
    lines, abundances = np.asarray(lines[:2]), np.asarray(abundances[:2])
    intensities = abundances / abundances[0] * 1e6
    intensities[1] /= 2
    snr = intensities / 2e3
    return lines, intensities, snr, np.array([10.0, 5.0]), np.array([10.0, 10.0])


class TestTheUntargetedMatcher:
    def test_a_diluted_line_read_over_its_scans_is_kept(self):
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
