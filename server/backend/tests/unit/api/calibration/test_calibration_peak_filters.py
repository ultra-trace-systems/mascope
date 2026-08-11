"""Unit tests for calibration peak filtering methods."""

import numpy as np
from conftest import get_small_peak_data, get_test_calibration_handler

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    BaseCalibrationHandler,
)


class TestFilterMzsBySnrAndPolarity:
    """Default SNR thresholds are 10 for TOF and 50 for Orbitrap

    Since the structure of the data is the same for both, we can just test the Orbitrap handler

    mz:         [50, 100, 250, 300, 350, 400]
    SNR:        [3,  5,   20,  30,  60,  80]
    Polarity:   [-,  +,   -,   +,   +,   +]
    """

    def setup_method(self):
        self.mixed_peak_data = get_small_peak_data("mixed")
        self.pos_peak_data = get_small_peak_data("+")
        self.neg_peak_data = get_small_peak_data("-")

        self.orbi_pos_handler = get_test_calibration_handler("orbitrap", "+")
        self.orbi_neg_handler = get_test_calibration_handler("orbitrap", "-")

    def test_mixed_data_positive_handler(self):
        test_mzs = self.orbi_pos_handler._filter_mzs_by_polarity_and_snr(
            self.mixed_peak_data
        )
        true_mzs = np.array([350, 400], dtype=np.float64)
        np.testing.assert_array_equal(test_mzs, true_mzs)

    def test_mixed_data_negative_handler(self):
        test_mzs = self.orbi_neg_handler._filter_mzs_by_polarity_and_snr(
            self.mixed_peak_data
        )
        true_mzs = np.array([], dtype=np.float64)
        np.testing.assert_array_equal(test_mzs, true_mzs)

    def test_all_positive_data_positive_handler(self):
        test_mzs = self.orbi_pos_handler._filter_mzs_by_polarity_and_snr(
            self.pos_peak_data
        )
        true_mzs = np.array([350, 400], dtype=np.float64)
        np.testing.assert_array_equal(test_mzs, true_mzs)

    def test_all_positive_data_negative_handler(self):
        test_mzs = self.orbi_neg_handler._filter_mzs_by_polarity_and_snr(
            self.pos_peak_data
        )
        true_mzs = np.array([], dtype=np.float64)
        np.testing.assert_array_equal(test_mzs, true_mzs)

    def test_all_negative_data_positive_handler(self):
        test_mzs = self.orbi_pos_handler._filter_mzs_by_polarity_and_snr(
            self.neg_peak_data
        )
        true_mzs = np.array([], dtype=np.float64)
        np.testing.assert_array_equal(test_mzs, true_mzs)

    def test_all_negative_data_negative_handler(self):
        test_mzs = self.orbi_neg_handler._filter_mzs_by_polarity_and_snr(
            self.neg_peak_data
        )
        true_mzs = np.array([350, 400], dtype=np.float64)
        np.testing.assert_array_equal(test_mzs, true_mzs)


class TestFilterMzsByRefineWindow:
    """Tests for _filter_mzs_by_refine_window.
    The conftest handler is built with refine_window=100 ppm.
    Tests use the Orbitrap handler, but the logic is the same for both.

    Expected behaviors:
    - Peaks within the refine window of any target m/z are kept.
    - Peaks outside the refine window of all target m/z are discarded.
    - A peak exactly on the boundary of the refine window is kept.
    - An empty array of peaks returns an empty array.
    """

    def setup_method(self):
        self.orbi_pos_handler = get_test_calibration_handler("orbitrap", "+")

    def test_peaks_within_window_are_kept(self):
        peak_mzs = np.array([100.0005, 200.0, 300.0, 300.002], dtype=np.float64)
        target_mzs = np.array([100.0, 300.0], dtype=np.float64)

        test_mzs = self.orbi_pos_handler._filter_mzs_by_refine_window(
            peak_mzs, target_mzs
        )
        true_mzs = np.array([100.0005, 300.0, 300.002], dtype=np.float64)

        np.testing.assert_array_equal(test_mzs, true_mzs)

    def test_no_peaks_within_window(self):
        peak_mzs = np.array([120.0, 130.0, 140.0], dtype=np.float64)
        target_mzs = np.array([100.0], dtype=np.float64)

        test_mzs = self.orbi_pos_handler._filter_mzs_by_refine_window(
            peak_mzs, target_mzs
        )

        assert test_mzs.size == 0

    def test_peak_exactly_on_boundary_is_kept(self):
        target_mz = 100.0
        mz_shift = target_mz * 10 * 1e-6
        peak_mzs = np.array([target_mz + mz_shift], dtype=np.float64)
        target_mzs = np.array([target_mz], dtype=np.float64)

        test_mzs = self.orbi_pos_handler._filter_mzs_by_refine_window(
            peak_mzs, target_mzs
        )

        assert test_mzs.size == 1, "Expected one peak to be kept"
        np.testing.assert_almost_equal(test_mzs[0], target_mz + mz_shift)

    def test_empty_peak_mzs_returns_empty(self):
        handler = get_test_calibration_handler("orbitrap", "+")
        peak_mzs = np.array([], dtype=np.float64)
        target_mzs = np.array([100.0], dtype=np.float64)

        result = handler._filter_mzs_by_refine_window(peak_mzs, target_mzs)

        assert result.size == 0


def _constant_resolution(value):
    """Return a resolution function that always returns `value`."""
    return lambda mz: np.full_like(mz, value, dtype=np.float64)


class TestRemoveOverlappingMzs:
    """Tests for the static BaseCalibrationHandler._remove_overlapping_mzs.

    FWHM = mz / R.  At R=10 000 and mz=100:  FWHM = 0.01.
    Two peaks overlap when their edge ranges intersect, when
    the gap between them is less than the average of their FWHMs.

        Expected behaviors:
        - All overlapping peaks are removed, leaving only non-overlapping peaks.
        - If no peaks overlap, all peaks are kept.
        - If multiple peaks overlap in a chain, all of them are removed.
    """

    def test_overlapping_pair_both_removed(self):
        peak_mzs = np.array([100.0, 100.004], dtype=np.float64)

        test_mzs = BaseCalibrationHandler._remove_overlapping_mzs(
            peak_mzs, _constant_resolution(10_000)
        )

        assert test_mzs.size == 0, "Expected both overlapping peaks to be removed"

    def test_non_overlapping_peaks_all_kept(self):
        # Gaps are much larger than FWHM=0.01
        peak_mzs = np.array([100.0, 101.0, 102.0], dtype=np.float64)

        test_mzs = BaseCalibrationHandler._remove_overlapping_mzs(
            peak_mzs, _constant_resolution(10_000)
        )

        np.testing.assert_array_equal(test_mzs, peak_mzs)

    def test_chain_of_overlapping_peaks_all_removed(self):
        # 3 peaks each 0.004 apart, all overlap their neighbors
        peak_mzs = np.array([100.0, 100.004, 100.008], dtype=np.float64)

        test_mzs = BaseCalibrationHandler._remove_overlapping_mzs(
            peak_mzs, _constant_resolution(10_000)
        )

        assert test_mzs.size == 0, (
            "Expected all peaks in the overlapping chain to be removed"
        )

    def test_single_peak_returned_as_is(self):
        peak_mzs = np.array([100.0], dtype=np.float64)

        test_mzs = BaseCalibrationHandler._remove_overlapping_mzs(
            peak_mzs, _constant_resolution(10_000)
        )

        np.testing.assert_array_equal(test_mzs, peak_mzs)


def _dominance_peak_data(mzs, heights, polarity="+"):
    """Minimal peak dataset for _filter_dominated_peaks."""
    import xarray as xr

    mzs = np.asarray(mzs, dtype=np.float64)
    return xr.Dataset(
        data_vars={
            "sum_peak_heights": ("mz", np.asarray(heights, dtype=np.float64)),
            "polarity": ("mz", np.array([polarity] * len(mzs))),
            "signal_to_noise": ("mz", np.full(len(mzs), 100.0)),
        },
        coords={"mz": ("mz", mzs)},
    )


class TestFilterDominatedPeaks:
    """Tests for _filter_dominated_peaks (Orbitrap local-dominance guard).

    Orbi handler: dominance window 100 ppm, dominance ratio 10.
    At mz=100, 100 ppm = 0.01.

    Expected behaviors:
    - A weak peak within the window of a >=10x stronger peak is rejected,
      even when the stronger peak is not itself a candidate (out of the
      refine window or below SNR).
    - The dominant peak itself is kept.
    - Isolated peaks and comparable-intensity neighbors are kept.
    - The guard is a no-op for handlers with dominance window 0 (TOF).
    """

    def setup_method(self):
        self.orbi_handler = get_test_calibration_handler("orbitrap", "+")
        self.tof_handler = get_test_calibration_handler("tofwerk", "+")

    def test_sidelobe_of_stronger_neighbor_rejected(self):
        # Candidate at 100.000 is 100x weaker than the peak 50 ppm away.
        peak_data = _dominance_peak_data([100.0, 100.005], [1e5, 1e7])
        candidates = np.array([100.0])

        kept = self.orbi_handler._filter_dominated_peaks(candidates, peak_data)

        assert kept.size == 0, "Expected the dominated sidelobe to be rejected"

    def test_dominant_peak_is_kept(self):
        peak_data = _dominance_peak_data([100.0, 100.005], [1e5, 1e7])
        candidates = np.array([100.005])

        kept = self.orbi_handler._filter_dominated_peaks(candidates, peak_data)

        np.testing.assert_array_equal(kept, candidates)

    def test_comparable_neighbors_both_kept(self):
        # 5x ratio is below the 10x dominance ratio.
        peak_data = _dominance_peak_data([100.0, 100.005], [2e6, 1e7])
        candidates = np.array([100.0, 100.005])

        kept = self.orbi_handler._filter_dominated_peaks(candidates, peak_data)

        np.testing.assert_array_equal(kept, candidates)

    def test_strong_peak_outside_window_does_not_dominate(self):
        # 200 ppm separation is outside the 100 ppm dominance window.
        peak_data = _dominance_peak_data([100.0, 100.02], [1e5, 1e7])
        candidates = np.array([100.0])

        kept = self.orbi_handler._filter_dominated_peaks(candidates, peak_data)

        np.testing.assert_array_equal(kept, candidates)

    def test_opposite_polarity_does_not_dominate(self):
        import xarray as xr

        peak_data = xr.Dataset(
            data_vars={
                "sum_peak_heights": ("mz", np.array([1e5, 1e7])),
                "polarity": ("mz", np.array(["+", "-"])),
                "signal_to_noise": ("mz", np.full(2, 100.0)),
            },
            coords={"mz": ("mz", np.array([100.0, 100.005]))},
        )
        candidates = np.array([100.0])

        kept = self.orbi_handler._filter_dominated_peaks(candidates, peak_data)

        np.testing.assert_array_equal(kept, candidates)

    def test_tof_guard_disabled(self):
        peak_data = _dominance_peak_data([100.0, 100.005], [1e5, 1e7])
        candidates = np.array([100.0])

        kept = self.tof_handler._filter_dominated_peaks(candidates, peak_data)

        np.testing.assert_array_equal(kept, candidates)

    def test_empty_candidates_returns_empty(self):
        peak_data = _dominance_peak_data([100.0], [1e7])
        candidates = np.array([])

        kept = self.orbi_handler._filter_dominated_peaks(candidates, peak_data)

        assert kept.size == 0
