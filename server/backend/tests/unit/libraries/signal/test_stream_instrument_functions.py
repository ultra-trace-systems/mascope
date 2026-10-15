"""The instrument function of each scan stream of a file detected per stream.

A stitched file's ranges are acquired at their own settings, so each stream
is fitted an instrument function of its own, on its own summed signal; its
peaks are read under it, and the peak store records them all beside the
stitch map (``mascope_signal.instrument_func.streams``). A file detected
whole is fitted once, as it always was.

The fits themselves are scripted: the scripted reader's spectra are a few
centroids, nothing a peak shape can be fitted on. What is pinned is which
fit each stream gets, and what is read by it.
"""

from functools import partial

import numpy as np
import pytest
from composite_acquisition import COMPOSITE, KEYS, MICROSCANS, RUNS
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.instrument_func.streams as m_stream_functions
import mascope_signal.peak as m_peak
from mascope_signal.instrument_func.fit import InsufficientPeaksError, r_orbi


REAGENT, LOW, MID, HIGH = KEYS

#: The file's own fit, and each stream's: a coefficient per stream, so that
#: which function a stream got reads off its record.
FILE_A = 1.6e6
STREAM_A = {REAGENT: 2.0e6, LOW: 1.9e6, MID: 1.8e6, HIGH: 1.7e6}


def _shape(width=1.0):
    """A Gaussian peak shape, ``width`` times as wide as the plain one."""
    x = np.linspace(-5.0, 5.0, 101) * width
    return {"x": x, "y": np.exp(-((x / width) ** 2) / 2)}


@pytest.fixture
def file_functions():
    return _shape(), partial(r_orbi, a=FILE_A)


@pytest.fixture
def fits(monkeypatch):
    """Script each stream's fit: the ones named in ``thin`` find too few
    peaks, the rest their ``STREAM_A`` coefficient and the peak shape
    ``shapes`` names, else the plain one. Records the streams asked for."""
    asked = []

    def _script(thin=(), shapes=None, broken=()):
        def fit(filename, key):
            asked.append(key)
            if key in broken:
                raise RuntimeError("the optimiser gave up")
            if key in thin:
                raise InsufficientPeaksError(
                    "Not enough quality peaks to evaluate instrument functions"
                )
            return (
                (shapes or {}).get(key, _shape()),
                partial(r_orbi, a=STREAM_A[key]),
                {"peakshape": {"num_of_peaks": 12}},
            )

        monkeypatch.setattr(m_stream_functions, "_fit_stream", fit)
        return asked

    return _script


def _detect(acquire, file_functions, per_stream=True):
    acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, file_functions, per_stream=per_stream)
    # Read now: the store is lazy, and the next detection writes over it
    return m_io.load_array(SAMPLE_FILENAME, "peak_timeseries").compute()


def _record():
    store = m_io.load_array(SAMPLE_FILENAME, "peak_timeseries")
    return store.attrs[m_stream_functions.INSTRUMENT_FUNCTIONS_ATTR]


def test_each_stream_is_fitted_on_its_own_and_the_store_keeps_the_fits(
    acquire, file_functions, fits
):
    asked = fits()

    _detect(acquire, file_functions)

    assert asked == KEYS
    record = _record()
    assert record["file"] == {"resolution_function": [FILE_A]}
    assert list(record["streams"]) == KEYS
    for key in KEYS:
        entry = record["streams"][key]
        assert entry["resolution_function"] == [STREAM_A[key]]
        assert entry["source"] == m_stream_functions.FITTED
        assert entry["origin"] is None and entry["note"] is None
        assert entry["peaks"] == 12
        assert len(entry["peakshape"]["x"]) == len(entry["peakshape"]["y"]) == 101


def test_a_file_detected_whole_is_fitted_once_as_it_always_was(
    acquire, file_functions, fits
):
    asked = fits()

    store = _detect(acquire, file_functions, per_stream=False)

    assert asked == []
    assert m_stream_functions.INSTRUMENT_FUNCTIONS_ATTR not in store.attrs


def test_a_stream_too_thin_to_fit_takes_its_nearest_siblings_fit(
    acquire, file_functions, fits
):
    fits(thin={HIGH})

    _detect(acquire, file_functions)

    entry = _record()["streams"][HIGH]
    # The mid window is the nearest in m/z of the same analyzer and resolution
    assert entry["source"] == m_stream_functions.SIBLING
    assert entry["origin"] == MID
    assert entry["resolution_function"] == [STREAM_A[MID]]
    assert entry["note"].startswith("Not enough quality peaks")
    assert entry["peaks"] is None


def test_a_stream_whose_fit_fails_takes_a_siblings_and_the_file_is_detected(
    acquire, file_functions, fits
):
    fits(broken={REAGENT})

    _detect(acquire, file_functions)

    entry = _record()["streams"][REAGENT]
    assert entry["source"] == m_stream_functions.SIBLING
    assert entry["origin"] == LOW
    assert entry["note"] == "Its fit failed: the optimiser gave up"


def test_with_no_stream_fitted_every_stream_takes_the_files(
    acquire, file_functions, fits
):
    fits(thin=set(KEYS))

    _detect(acquire, file_functions)

    for entry in _record()["streams"].values():
        assert entry["source"] == m_stream_functions.FILE
        assert entry["origin"] is None
        assert entry["resolution_function"] == [FILE_A]


def test_each_streams_peaks_are_integrated_under_its_own_peak_shape(
    acquire, file_functions, fits
):
    fits()
    before = _detect(acquire, file_functions)

    # The high window's peak shape is twice as wide: its areas grow, and no
    # other stream's move
    fits(shapes={HIGH: _shape(2.0)})
    after = _detect(acquire, file_functions)

    np.testing.assert_array_equal(before.stream.values, after.stream.values)
    high = after.stream.values == KEYS.index(HIGH)
    assert high.any() and (~high).any()
    areas_before = before.sum_peak_areas.values
    areas_after = after.sum_peak_areas.values
    assert np.all(areas_after[high] > 1.5 * areas_before[high])
    np.testing.assert_array_equal(areas_after[~high], areas_before[~high])


# -- reading a peak's width from the stream it came from ---------------------------


def _fallback(mz):
    return r_orbi(mz, a=FILE_A)


def test_a_stitched_polaritys_widths_follow_the_segment_that_owns_each_mz(
    acquire, file_functions, fits
):
    fits()
    _detect(acquire, file_functions)

    resolution = m_stream_functions.peak_store_resolution(
        SAMPLE_FILENAME, _fallback, polarity="-"
    )

    # One m/z inside each run of the map, read by the run's owner
    for lower, upper, owner in RUNS:
        mz = (lower + upper) / 2
        assert resolution(mz) == pytest.approx(r_orbi(mz, a=STREAM_A[KEYS[owner]]))
    # Past the map's last edge no stream owns an m/z: the file's function
    assert resolution(950.0) == pytest.approx(_fallback(950.0))
    mz = np.array([50.0, 100.0, 300.0, 700.0])
    np.testing.assert_allclose(
        resolution(mz),
        [r_orbi(m, a=STREAM_A[key]) for m, key in zip(mz, [REAGENT, LOW, MID, HIGH])],
    )


def test_one_streams_widths_are_read_by_its_own_function(acquire, file_functions, fits):
    fits()
    _detect(acquire, file_functions)

    resolution = m_stream_functions.peak_store_resolution(
        SAMPLE_FILENAME, _fallback, polarity="-", stream=REAGENT
    )

    # Over the whole of its range, the part the composite takes from
    # another stream included
    assert resolution(100.0) == pytest.approx(r_orbi(100.0, a=STREAM_A[REAGENT]))


def test_a_run_is_placed_by_its_owners_calibration(acquire, file_functions, fits):
    fits()
    _detect(acquire, file_functions)
    # The low window calibrated 1e-4 up: its run's upper edge at 122 is
    # placed at 122 * 1.0001 on the file's axis, so an m/z just past 122 is
    # still the low window's, where on the recorded axis the reagent scan's
    # next run would hold it
    m_io.update_props(
        SAMPLE_FILENAME,
        {
            "mz_calibration": {
                "par": {"calibration_factor": 1.0},
                "streams": {LOW: {"calibration_factor": 1.0001}},
            }
        },
    )

    resolution = m_stream_functions.peak_store_resolution(
        SAMPLE_FILENAME, _fallback, polarity="-"
    )

    mz = 122.0 * 1.00005
    assert resolution(mz) == pytest.approx(r_orbi(mz, a=STREAM_A[LOW]))


@pytest.mark.parametrize("asked", [{"polarity": "-"}, {"stream": REAGENT}, {}])
def test_a_store_detected_whole_is_read_by_the_files_function(
    acquire, file_functions, fits, asked
):
    fits()
    _detect(acquire, file_functions, per_stream=False)

    assert (
        m_stream_functions.peak_store_resolution(SAMPLE_FILENAME, _fallback, **asked)
        is _fallback
    )


def test_a_store_detected_before_the_streams_were_fitted_apart_reads_the_files(
    acquire, file_functions, fits
):
    fits()
    _detect(acquire, file_functions)
    # What a store written before this change holds: the stitch, no fits
    store = m_io.open_zarr_store(
        m_name.filename_to_zarr_path(SAMPLE_FILENAME, "peak_timeseries"), mode="a"
    )
    del store.attrs[m_stream_functions.INSTRUMENT_FUNCTIONS_ATTR]

    assert (
        m_stream_functions.peak_store_resolution(
            SAMPLE_FILENAME, _fallback, polarity="-"
        )
        is _fallback
    )
