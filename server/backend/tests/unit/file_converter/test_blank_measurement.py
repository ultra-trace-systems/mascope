"""Blank classification answers for every sum signal, including degenerate ones.

``H5Processor._is_blank_measurement`` decides whether a TOF file is worth
running peak detection over, and it is the first thing ``_process_file`` asks.
It has to answer rather than raise: the caller catches only ``FileExistsError``,
so anything else propagates to the processing loop, which logs it with a
traceback (an error-monitoring event) and fails the file instead of ingesting
it as a blank.

The case that broke was a sum signal with no detectable peaks at all. The
``noise_mad == 0`` guard was meant to cover it, but ``median_abs_deviation`` of
an empty array is NaN, not 0 - so the guard did not fire and ``np.max()`` raised
``ValueError: zero-size array to reduction operation maximum`` on a real file
whose signal is flat. A file with no peaks is a blank measurement by
definition, which is what it must now report.

The measurement itself now lives in ``mascope_signal.noise.max_peak_snr``,
shared with the ambient-spectrum detection that carried a second copy of it
and the same hazard. These cases pin the classification this side of it.
"""

from queue import Queue
from threading import Event

import numpy as np
import pytest
import xarray as xr
from scipy.signal import find_peaks

import mascope_tofwerk.processor as tof_processor
from mascope_tofwerk.processor import H5Processor


def _signal_with_peaks(heights):
    """A sum signal whose detectable peaks are exactly ``heights``.

    Each height sits between two zeros, so ``find_peaks`` reports every one of
    them and nothing else - the test states the peak heights it means rather
    than a spectrum they have to be read out of.
    """
    signal = np.zeros(3 * len(heights) + 1, dtype=float)
    signal[1::3] = heights
    return signal


def test_the_helper_names_the_peaks_it_builds():
    """Guards the fixture itself.

    Every case below states its meaning as a list of peak heights, so a
    spacing slip that merged or added one would quietly change what all of
    them exercise - a "single peak" case carrying two peaks would still pass.
    """
    heights = [1.0, 5.0, 2.0]
    signal = _signal_with_peaks(heights)
    assert list(signal[find_peaks(signal)[0]]) == heights


@pytest.fixture
def blank_of(monkeypatch):
    """Classify a given sum signal, with the filestore read stubbed out."""

    def _classify(sum_signal):
        def _get_sum_signal(_base_filename, *args, **kwargs):
            values = np.asarray(sum_signal, dtype=float)
            return xr.DataArray(values, dims="mz")

        monkeypatch.setattr(tof_processor.m_compute, "get_sum_signal", _get_sum_signal)

        processor = H5Processor(
            socket_client=None, file_queue=Queue(), shutdown_event=Event()
        )
        processor.file_to_process = "TOF-1_blank.h5"
        return processor._is_blank_measurement

    return _classify


@pytest.mark.parametrize(
    "sum_signal",
    [
        pytest.param(np.zeros(64), id="flat-zero"),
        pytest.param(np.full(64, 7.0), id="flat-nonzero"),
        pytest.param(np.arange(64, dtype=float), id="monotonic"),
        pytest.param(np.array([], dtype=float), id="empty"),
    ],
)
def test_signal_without_peaks_is_blank(blank_of, sum_signal):
    """The regression: no peaks found must classify, not raise.

    ``find_peaks`` returns nothing for any of these, so the peak heights are
    empty and the noise level is undefined. Before the guard each of these
    reached ``np.max()`` on an empty array and aborted the file.
    """
    assert blank_of(sum_signal) is True


def test_single_peak_is_blank(blank_of):
    """One peak has no spread to measure a noise level against."""
    assert blank_of(_signal_with_peaks([1000.0])) is True


def test_identical_peaks_are_blank(blank_of):
    """A saturated signal: every peak the same height, so the MAD is zero."""
    assert blank_of(_signal_with_peaks([500.0] * 8)) is True


def test_signal_carrying_nans_still_classifies(blank_of):
    """NaNs in the sum signal must not stop a real peak from being seen.

    ``find_peaks`` never reports a NaN as a local maximum - every comparison
    against it is False - so a NaN drops the peak it sits on and leaves the
    rest of the spectrum to be judged on its own. What must not happen is the
    file failing over it.
    """
    heights = [1.0, 1.1, 1.0, np.nan, 1.2, 1.0, 10_000.0]
    assert blank_of(_signal_with_peaks(heights)) is False


def test_noise_only_peaks_are_blank(blank_of):
    """Many peaks of near-equal height: nothing rises above the noise."""
    assert blank_of(_signal_with_peaks([1.0, 1.1, 1.0, 1.2, 1.0, 1.1])) is True


def test_peak_far_above_the_noise_is_not_blank(blank_of):
    """The guards must not swallow a real measurement.

    A single large peak over a bed of near-equal noise peaks is the signal
    every non-blank file has; it has to keep reporting False so the file goes
    on to peak detection.
    """
    heights = [1.0, 1.1, 1.0, 1.2, 1.0, 1.1, 10_000.0]
    assert blank_of(_signal_with_peaks(heights)) is False


def test_blank_classification_reads_the_shared_noise_measure(blank_of, monkeypatch):
    """Blank and ambient classification must not grow two answers again.

    The signal-to-noise measure is ``mascope_signal.noise.max_peak_snr``,
    shared with the ambient-spectrum detection in
    ``mascope_signal.instrument_func.fit`` - which carried a second copy of it,
    with the same empty-array hazard, until it was extracted.

    This is a design guard rather than a regression test: it pins that the
    classifier delegates to the shared measure and honours its answer, not any
    behaviour that was ever wrong. The cases above cover the behaviour, and
    ``libraries/signal/tests/test_noise.py`` covers the measure itself.
    """
    calls = []

    def _spy(peak_heights, noise_threshold_factor):
        calls.append(noise_threshold_factor)
        return float(tof_processor.BLANK_SNR_THRESHOLD)

    monkeypatch.setattr(tof_processor, "max_peak_snr", _spy)

    # The measure's answer is what decides: exactly at the threshold is not
    # below it, so this file is not blank.
    assert blank_of(_signal_with_peaks([1.0, 5.0, 2.0])) is False
    assert calls == [tof_processor.NOISE_THRESHOLD_FACTOR]
