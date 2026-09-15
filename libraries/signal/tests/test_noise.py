"""The shared MAD noise measure, and the two readings taken from it.

``mascope_signal.noise.max_peak_snr`` is the one place a TOF spectrum's
tallest peak is weighed against the spread of the others. Ambient detection
here and blank classification in ``mascope_tofwerk`` both read it, with
different thresholds and opposite readings of a missing noise floor, so the
degenerate spectra are pinned on this side too.

The case that used to raise is a spectrum with no detectable peaks:
``median_abs_deviation`` of an empty array is NaN rather than 0, so the
``noise_threshold <= 0`` guard did not fire and ``np.max`` over the empty
array of peak heights raised ``ValueError: zero-size array to reduction
operation maximum``. A TOF file with a flat signal is ingested as a blank
sample file carrying no instrument function, which is the state a later fit
request starts from.
"""

import warnings

import numpy as np
import pytest
from scipy.signal import find_peaks
from scipy.stats import median_abs_deviation

from mascope_signal.instrument_func.fit import (
    AMBIENT_SNR_THRESHOLD,
    NOISE_THRESHOLD_FACTOR,
    _is_ambient_tof_spectrum,
)
from mascope_signal.noise import max_peak_snr


# Peaks of near-equal height: a noise bed with nothing standing out of it.
NOISE_PEAKS = [1.0, 1.1, 1.0, 1.2, 1.0, 1.1]


def _spectrum_with_peaks(heights):
    """A spectrum whose detectable peaks are exactly ``heights``.

    Each height sits between two zeros, so ``find_peaks`` reports every one of
    them and nothing else.
    """
    spec = np.zeros(3 * len(heights) + 1, dtype=float)
    spec[1::3] = heights
    return spec


def test_the_helper_names_the_peaks_it_builds():
    """Guards the fixture: a spacing slip would change every case below."""
    spec = _spectrum_with_peaks(NOISE_PEAKS)
    assert list(spec[find_peaks(spec)[0]]) == NOISE_PEAKS


class TestMaxPeakSnr:
    def test_no_peaks_scores_zero(self):
        """An empty array of heights has to answer, not reduce over nothing."""
        assert max_peak_snr(np.array([], dtype=float), NOISE_THRESHOLD_FACTOR) == 0.0

    def test_no_peaks_asks_for_no_statistics(self):
        """Nothing is measured over the empty array, so nothing warns about it.

        ``median_abs_deviation`` of an empty array raises scipy's
        ``SmallSampleWarning`` on top of returning NaN; answering before it is
        called is what keeps both out of the caller's way.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            snr = max_peak_snr(np.array([], dtype=float), NOISE_THRESHOLD_FACTOR)

        assert snr == 0.0

    @pytest.mark.parametrize(
        "peak_heights",
        [
            pytest.param(np.array([5.0]), id="single-peak"),
            pytest.param(np.full(8, 500.0), id="saturated"),
        ],
    )
    def test_absent_noise_floor_is_reported_as_undefined(self, peak_heights):
        """A MAD of 0 leaves no ratio; what it means is the caller's to say."""
        assert max_peak_snr(peak_heights, NOISE_THRESHOLD_FACTOR) is None

    def test_ratio_is_the_tallest_peak_over_the_scaled_noise_level(self):
        """The arithmetic both call sites carried, now stated once."""
        peak_heights = np.array(NOISE_PEAKS + [10_000.0])
        noise_mad = median_abs_deviation(peak_heights, scale="normal")
        expected = np.max(peak_heights) / (1.4826 * noise_mad * NOISE_THRESHOLD_FACTOR)

        snr = max_peak_snr(peak_heights, NOISE_THRESHOLD_FACTOR)

        assert snr == pytest.approx(expected)
        # A real float: both callers return a comparison against it directly,
        # and np.float64 would make that comparison a numpy bool.
        assert type(snr) is float


class TestAmbientTofSpectrum:
    @pytest.mark.parametrize(
        "spec",
        [
            pytest.param(np.zeros(64), id="flat-zero"),
            pytest.param(np.full(64, 7.0), id="flat-nonzero"),
            pytest.param(np.arange(64, dtype=float), id="monotonic"),
            pytest.param(np.array([], dtype=float), id="empty"),
        ],
    )
    def test_spectrum_without_peaks_is_ambient(self, spec):
        """The regression: no peaks must be answered, not raised over.

        ``find_peaks`` reports nothing for any of these, so the peak heights
        are empty. This used to reach ``np.max()`` on the empty array and fail
        with ``ValueError: zero-size array to reduction operation maximum``,
        which a fit request then reported in place of "Not enough quality
        peaks to evaluate instrument functions".
        """
        is_ambient, signal_to_noise = _is_ambient_tof_spectrum(spec)

        assert is_ambient is True
        assert signal_to_noise == 0.0

    def test_absent_noise_floor_reads_as_unbounded_here(self):
        """The opposite reading from blank classification, deliberately.

        A saturated spectrum - every peak the same height - has a MAD of 0 and
        so no noise floor. Blank classification calls that blank; ambient
        detection calls the tallest peak unbounded against it, and the file
        keeps the strict R-squared threshold.
        """
        is_ambient, signal_to_noise = _is_ambient_tof_spectrum(
            _spectrum_with_peaks([500.0] * 8)
        )

        assert is_ambient is False
        assert signal_to_noise == float("inf")

    def test_noise_only_spectrum_is_ambient(self):
        """Peaks of near-equal height: nothing rises far above the rest."""
        is_ambient, signal_to_noise = _is_ambient_tof_spectrum(
            _spectrum_with_peaks(NOISE_PEAKS)
        )

        assert is_ambient is True
        assert 0 < signal_to_noise < AMBIENT_SNR_THRESHOLD

    def test_strong_peak_is_not_ambient(self):
        """The measure must still see a real spectrum for what it is."""
        is_ambient, signal_to_noise = _is_ambient_tof_spectrum(
            _spectrum_with_peaks(NOISE_PEAKS + [10_000.0])
        )

        assert is_ambient is False
        assert signal_to_noise > AMBIENT_SNR_THRESHOLD
