"""
The TIC of each scan of a TofDaq file is in ions/s.

``get_tic_per_scan`` sums each buf of ``FullSpectra/TofData`` and rescales
the scans to add up to ``FullSpectra/SumSpectrum`` in ions/s. TofDaq writes
``SumSpectrum`` as the sum of every buf it recorded (checked on real files:
the two agree to the last digit), so the rescaling only converts units: each
scan's TIC is its own buf converted to ions/s, and a longer acquisition of
the same signal has the same TIC per scan - its mean, which is what a sample
item stores, does not grow with the number of scans.
"""

import h5py
import numpy as np
import pytest

from mascope_tofwerk.tofwerk import get_tic_per_scan


# Acquisition parameters: 1 ns samples, a 20 us extraction period, a single
# ion of 0.5 mV.ns, 1000 extractions summed per buf.
_SAMPLE_INTERVAL_S = 1e-9
_TOF_PERIOD_NS = 20_000.0
_SINGLE_ION_SIGNAL = 0.5
_N_EXTRACTIONS = 1000
_N_SAMPLES = 50
_N_BUFS = 2

# [ions/(mV.s)], as get_conversion_coefficient derives it.
_COEFFICIENT = (
    _SAMPLE_INTERVAL_S
    * (1 / (_TOF_PERIOD_NS * 1e-9))
    / (_SINGLE_ION_SIGNAL * 1e-9)
    / _N_EXTRACTIONS
)


def _write_tof_file(path, buf_sums):
    """A TofDaq file whose bufs carry the given summed signal, in mV.

    :param path: Where to write it.
    :param buf_sums: The summed signal of each buf, in order; its length is
        a whole number of writes of ``_N_BUFS`` bufs.
    :return: The path.
    """
    buf_sums = np.asarray(buf_sums, dtype=float)
    n_writes = buf_sums.size // _N_BUFS
    # Each buf spreads its signal evenly over the spectrum.
    tof_data = np.repeat(buf_sums / _N_SAMPLES, _N_SAMPLES).reshape(
        n_writes, _N_BUFS, 1, _N_SAMPLES
    )
    buf_times = np.arange(buf_sums.size, dtype=float).reshape(n_writes, _N_BUFS)
    with h5py.File(path, "w") as h5_file:
        h5_file.attrs["NbrWaveforms"] = np.array([_N_EXTRACTIONS], dtype=np.int32)
        spectra = h5_file.create_group("FullSpectra")
        spectra.create_dataset("TofData", data=tof_data.astype(np.float32))
        # What TofDaq writes: the sum of every buf, not their average.
        spectra.create_dataset(
            "SumSpectrum", data=tof_data.reshape(-1, _N_SAMPLES).sum(axis=0)
        )
        spectra.attrs["Single Ion Signal"] = np.array([_SINGLE_ION_SIGNAL])
        spectra.attrs["SampleInterval"] = np.array([_SAMPLE_INTERVAL_S])
        timing = h5_file.create_group("TimingData")
        timing.create_dataset("BufTimes", data=buf_times)
        timing.attrs["TofPeriod"] = np.array([_TOF_PERIOD_NS])
    return str(path)


def test_each_scan_is_its_own_buf_in_ions_per_second(tmp_path):
    buf_sums = [10.0, 20.0, 30.0, 40.0]

    _, tic = get_tic_per_scan(_write_tof_file(tmp_path / "data.h5", buf_sums))

    np.testing.assert_allclose(tic, np.array(buf_sums) * _COEFFICIENT, rtol=1e-6)


def test_the_mean_tic_does_not_grow_with_the_number_of_scans(tmp_path):
    """The same signal recorded twice as long: twice the summed TIC, the same
    TIC per scan, and the same mean the sample item stores."""
    signal = [10.0, 30.0]

    _, short = get_tic_per_scan(_write_tof_file(tmp_path / "short.h5", signal * 2))
    _, long = get_tic_per_scan(_write_tof_file(tmp_path / "long.h5", signal * 4))

    assert long.size == 2 * short.size
    assert long.sum() == pytest.approx(2 * short.sum())
    assert long.mean() == pytest.approx(short.mean())
    assert short.mean() == pytest.approx(20.0 * _COEFFICIENT)


def test_a_file_that_recorded_no_signal_has_a_tic_of_zero(tmp_path):
    """Not 0/0: rescaled to a SumSpectrum of zero, every scan read NaN, which
    its sample item stored as its TIC."""
    _, tic = get_tic_per_scan(_write_tof_file(tmp_path / "data.h5", [0.0] * 4))

    np.testing.assert_array_equal(tic, np.zeros(4))
