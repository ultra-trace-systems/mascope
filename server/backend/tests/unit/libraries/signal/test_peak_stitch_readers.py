"""What reads a stitched store sees its composite.

A per-stream store keeps every stream's reading of an ion - the reagent
scan's and the low window's reading of the ion at 80, half a ppm apart
(``composite_acquisition``) - and says which one its composite takes. The
readers of "the file's peaks" must see that one only: handed both, the m/z
calibration takes two readings a fraction of a ppm apart for two peaks too
close to tell apart and drops both, which on a method that measures one
range twice in a cycle is every calibrant the file has.
"""

import asyncio

import numpy as np
import pytest
import xarray as xr
from composite_acquisition import COMPOSITE, IN_LOW, KEYS, MICROSCANS
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_params_factory,
    get_calibration_handler,
)
from mascope_backend.api.controllers.sample.batches.lib.util import _sync_load_peak_data
from mascope_backend.api.controllers.samples import samples_controller
from mascope_backend.api.controllers.samples.lib.samples_peaks import extract_peaks
from mascope_backend.api.controllers.samples.samples_controller import (
    _nearest_listed_mz,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    MzCalibrationParams,
)


def _readings(store) -> set:
    """``{(stream index, m/z to two decimals)}`` of a store's peaks."""
    return {
        (int(stream), round(float(mz), 2))
        for stream, mz in zip(store.stream.values, store.mz.values)
    }


def test_the_loader_answers_the_composite(composite):
    """By default a per-stream store answers the peaks its composites are
    made of: one reading of each m/z, the stream that owns it. The whole
    store, the other readings included, is answered on request, and both
    carry the streams and the map."""
    whole = m_io.load_peak_data(
        SAMPLE_FILENAME, drop_bad_peaks=False, composite=False
    ).compute()
    loaded = m_io.load_peak_data(SAMPLE_FILENAME, drop_bad_peaks=False).compute()

    assert loaded.composite.values.all()
    assert loaded.mz.size == int(whole.composite.values.sum()) < whole.mz.size
    assert _readings(loaded) == _readings(
        whole.isel(mz=np.flatnonzero(whole.composite.values))
    )
    assert m_compute.peak_store_streams(loaded) == KEYS
    assert m_compute.peak_store_stitch_map(loaded) == whole.attrs["stitch_map"]


def test_an_ion_two_streams_read_comes_back_once(composite):
    """The ion at 80, read by the reagent scan and by the low window that
    owns it: the store holds both readings, the loader answers the window's."""
    whole = m_io.load_peak_data(SAMPLE_FILENAME, composite=False).compute()
    loaded = m_io.load_peak_data(SAMPLE_FILENAME).compute()
    near = lambda store: store.mz.values[np.abs(store.mz.values / 80.0 - 1) < 2e-6]  # noqa: E731

    assert near(whole).size == 2
    assert near(loaded).tolist() == pytest.approx([IN_LOW])


def _calibration_handler():
    """An Orbitrap calibration handler on the scripted file, every peak a
    candidate: no signal-to-noise gate, a wide refine window."""
    params = MzCalibrationParams(refine_window=100, snr_threshold=0.0).with_defaults(
        calibration_params_factory(filename=SAMPLE_FILENAME)
    )
    fit_params = CalibrationFitParams(
        calibration_collection_id="calib_coll_123",
        ionization_mechanism_ids=["im_1"],
        polarity="-",
        **params.model_dump(),
    )
    return get_calibration_handler(SAMPLE_FILENAME, fit_params, notification=None)


def test_the_calibrations_candidates_hold_one_reading_of_the_ion(composite):
    """What the calibration loads to match its calibrants against holds the
    ion at 80 once. Handed both readings, its resolution filter would take
    them for two peaks too close to tell apart and drop both."""
    candidates = _calibration_handler()._sync_load_and_filter_peaks(np.array([80.0]))

    near = candidates[np.abs(candidates / 80.0 - 1) < 2e-6]
    assert near.tolist() == pytest.approx([IN_LOW])


# The ions of the scripted composite as each stream read them per scan: the
# reagent scan (5 scans) holds 62 at 1000 and 125 at 400, the low window (3
# scans) 100 at 50, the mid window (4 scans) 300 at 40, the high window (2
# scans) 700 at 30. Pooled, the listing divided every sum by all 14 scans.
PER_SCAN = {62.0: 1000.0, 100.0: 50.0, 125.0: 400.0, 300.0: 40.0, 700.0: 30.0}
STREAM_OF = {62.0: 0, 100.0: 1, 125.0: 0, 300.0: 2, 700.0: 3}
SCANS_OF_STREAM = {0: 5, 1: 3, 2: 4, 3: 2}
SCANS = sum(SCANS_OF_STREAM.values())


def _by_mz(mzs, values) -> dict:
    return {round(float(mz), 2): float(v) for mz, v in zip(mzs, values)}


def _fill(mzs):
    return asyncio.run(
        m_compute.load_peak_timeseries(SAMPLE_FILENAME, list(mzs))
    ).compute()


@pytest.fixture
def pooled(acquire, instrument_functions):
    """The same file, its peaks detected pooled: one list over every scan."""
    acquisition = acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    return acquisition


@pytest.fixture
def first_scan_left_out(monkeypatch):
    """A file-wide read of a polarity's scans one short of the store's axis,
    as the TIC rule leaves out a first scan that is an outlier among the
    file's scans and not among its own stream's."""
    read = m_compute.get_scan_timestamps

    def _short(base_filename, *args, **kwargs):
        timestamps = read(base_filename, *args, **kwargs)
        if kwargs.get("polarity") is not None and kwargs.get("stream") is None:
            return timestamps[1:]
        return timestamps

    monkeypatch.setattr(m_compute, "get_scan_timestamps", _short)


def test_scans_per_peak_counts_each_peaks_own_stream_among_the_scans_given(composite):
    whole = m_io.load_peak_data(SAMPLE_FILENAME, composite=False).compute()
    timestamps = m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-")
    assert timestamps.size == SCANS

    scans = m_compute.scans_per_peak(whole, timestamps)
    assert scans.tolist() == [SCANS_OF_STREAM[int(s)] for s in whole.stream.values]

    # Among the scans of a range: the reagent scan's first scan left out
    short = m_compute.scans_per_peak(whole, timestamps[1:])
    assert short.tolist() == [
        SCANS_OF_STREAM[int(s)] - (1 if int(s) == 0 else 0) for s in whole.stream.values
    ]
    # No scan of a stream in the range: a floor of one, so its sum of
    # nothing averages to nothing rather than to a division by zero
    only_low = timestamps[5:8]
    assert m_compute.scans_per_peak(whole, only_low).tolist() == [
        3 if int(s) == 1 else 1 for s in whole.stream.values
    ]
    # No scan given at all: the floor for every row
    assert (
        m_compute.scans_per_peak(whole, timestamps[:0]).tolist() == [1] * whole.mz.size
    )


def test_a_stored_sum_is_counted_on_the_stores_own_axis(composite):
    """A file-wide read of the polarity's scans can be one short of the
    axis (the reagent scan's first scan, a TIC outlier file-wide); the
    stored sums were taken over the axis, so that is what counts them."""
    whole = m_io.load_peak_data(SAMPLE_FILENAME, composite=False).compute()
    timestamps = m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-")

    expected = [SCANS_OF_STREAM[int(s)] for s in whole.stream.values]
    assert m_compute.stored_scans_per_peak(whole, timestamps).tolist() == expected
    assert m_compute.stored_scans_per_peak(whole, timestamps[1:]).tolist() == expected


def test_a_pooled_store_is_counted_by_the_polaritys_scans_as_read(pooled):
    """A pooled store's axis holds every scan of the file with no polarity
    on it: the count is the polarity's scans the caller read, every peak
    alike, as an array like a per-stream store's."""
    store = m_io.load_peak_data(SAMPLE_FILENAME).compute()
    timestamps = m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-")

    assert m_compute.peak_store_streams(store) == []
    assert (
        m_compute.scans_per_peak(store, timestamps).tolist() == [SCANS] * store.mz.size
    )
    assert (
        m_compute.scans_per_peak(store, timestamps[:4]).tolist() == [4] * store.mz.size
    )
    assert (
        m_compute.stored_scans_per_peak(store, timestamps).tolist()
        == [SCANS] * store.mz.size
    )
    assert (
        m_compute.scans_per_peak(store, timestamps[:0]).tolist() == [1] * store.mz.size
    )


def test_the_listing_averages_a_peak_over_its_own_streams_scans(composite):
    """An ion a short stream measured reads at its height per scan, not at
    that height diluted by the scans of every other stream of the polarity."""
    timestamps = m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-")
    listed = extract_peaks(
        SAMPLE_FILENAME, "-", float(timestamps.min()), float(timestamps.max())
    )

    heights = _by_mz(listed.mz_values, listed.heights)
    for mz, per_scan in PER_SCAN.items():
        assert heights[mz] == pytest.approx(per_scan), mz


def test_the_batch_alignment_averages_the_same_way(composite):
    mz, intensity, _ids = _sync_load_peak_data(SAMPLE_FILENAME, "-", "sum_peak_heights")

    heights = _by_mz(mz, intensity)
    for mz_value, per_scan in PER_SCAN.items():
        assert heights[mz_value] == pytest.approx(per_scan), mz_value


def test_the_listing_and_the_alignment_are_not_pulled_by_a_read_one_scan_short(
    composite, first_scan_left_out
):
    """With the file-wide read one short, the reagent scan's ions would list
    and align a quarter high (1250 for 1000) if its scans were counted from
    that read; the stored sums are counted on the axis instead."""
    timestamps = m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-")
    assert timestamps.size == SCANS - 1
    listed = extract_peaks(
        SAMPLE_FILENAME, "-", float(timestamps.min()), float(timestamps.max())
    )
    mz, intensity, _ids = _sync_load_peak_data(SAMPLE_FILENAME, "-", "sum_peak_heights")

    for heights in (_by_mz(listed.mz_values, listed.heights), _by_mz(mz, intensity)):
        for mz_value, per_scan in PER_SCAN.items():
            assert heights[mz_value] == pytest.approx(per_scan), mz_value


def test_the_listing_over_a_time_range_divides_by_the_streams_scans_in_range(
    composite,
):
    """Over the low window's three scans and two of the mid window's four,
    their ions read at their height per scan; a stream with no scan in the
    range sums to nothing and reads as nothing, not as a division by zero."""
    axis = m_io.load_peak_data(SAMPLE_FILENAME).compute()
    _fill(axis.mz.values)  # the per-scan values the range aggregation reads
    scans = np.sort(m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-"))
    listed = extract_peaks(
        SAMPLE_FILENAME,
        "-",
        float(scans.min()),
        float(scans.max()),
        t_min=float(scans[5]),
        t_max=float(scans[9]),
    )

    heights = _by_mz(listed.mz_values, listed.heights)
    assert heights[100.0] == pytest.approx(50.0)
    assert heights[300.0] == pytest.approx(40.0)
    assert heights[62.0] == 0.0
    assert heights[700.0] == 0.0
    assert listed.warnings == []


def test_a_pooled_store_lists_and_aligns_as_it_always_did(pooled):
    """Pooled, every ion is divided by the polarity's scans: the reagent
    ion at 1000 per scan over 5 of 14 reads 357.1, as before."""
    timestamps = m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-")
    listed = extract_peaks(
        SAMPLE_FILENAME, "-", float(timestamps.min()), float(timestamps.max())
    )
    mz, intensity, _ids = _sync_load_peak_data(SAMPLE_FILENAME, "-", "sum_peak_heights")

    for heights in (_by_mz(listed.mz_values, listed.heights), _by_mz(mz, intensity)):
        for mz_value, per_scan in PER_SCAN.items():
            pooled_average = per_scan * SCANS_OF_STREAM[STREAM_OF[mz_value]] / SCANS
            assert heights[mz_value] == pytest.approx(pooled_average), mz_value


def test_an_asked_mz_is_resolved_on_the_listing(composite):
    """A client's m/z a tenth of a ppm above 80 is nearer the reagent scan's
    reading, which the composite leaves out; what it gets is the low
    window's, the one the sample lists."""
    assert _nearest_listed_mz(SAMPLE_FILENAME, 80.00001) == pytest.approx(IN_LOW)
    assert _nearest_listed_mz(SAMPLE_FILENAME, 80.00001) != 80.0


def test_an_asked_mz_on_an_empty_listing_is_itself(monkeypatch):
    """A file that lists no peak: the ask is handed on as it is, and the
    route's tolerance refuses it as before."""
    monkeypatch.setattr(
        samples_controller,
        "load_peak_data",
        lambda _name: xr.Dataset(coords={"mz": np.array([], dtype=float)}),
    )
    assert _nearest_listed_mz(SAMPLE_FILENAME, 80.00001) == 80.00001
