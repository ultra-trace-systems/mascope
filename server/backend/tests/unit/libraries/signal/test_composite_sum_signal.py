"""The stitched sum signal of a composite acquisition.

A composite's spectrum is its streams' sum signals, each within the m/z the
stream owns under the stitch map its peak store carries
(``mascope_signal.compute.get_composite_sum_signal``; the map and the mask
are in ``test_peak_stitch``). What is pinned here is where the signal is
cut and how its samples are labelled, what an average divides by, how it is
cached and when the cache is not to be trusted, and what a time range and a
calibration do to it.

These run on the scripted composite of ``composite_acquisition``. A scan's
profile there is flat over its own range at the sum of its centroids, so a
stream's summed profile is that sum times its scans, and a level says which
stream a sample came from.
"""

import os

import numpy as np
import pytest
import zarr
from composite_acquisition import (
    COMPOSITE,
    KEYS,
    LOW,
    MICROSCANS,
    POSITIVE,
    RUNS,
    record_calibration,
)
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
import mascope_signal.stitch as m_stitch
import mascope_thermo.thermo as m_thermo
from mascope_signal.compute import StalePeakStoreError


# What a scan of each stream sums to, and how many scans each stream holds.
PER_SCAN = [1440.0, 70.0, 55.0, 40.0]
SCANS = [5, 3, 4, 2]


def _stretches(signal):
    """A stitched signal as its stretches of one stream at one level:
    ``(first m/z, last m/z, segment, level)``."""
    signal = signal.compute()
    mz, values, segment = signal.mz.values, signal.values, signal.segment.values
    assert np.all(np.diff(mz) > 0)
    starts = [0, *(np.flatnonzero(np.diff(segment) != 0) + 1).tolist(), mz.size]
    stretches = []
    for first, stop in zip(starts, starts[1:]):
        assert np.all(values[first:stop] == values[first])
        stretches.append(
            (
                float(mz[first]),
                float(mz[stop - 1]),
                int(segment[first]),
                float(values[first]),
            )
        )
    return stretches


def _summed(stream):
    return PER_SCAN[stream] * SCANS[stream]


def test_a_composites_sum_signal_is_its_streams_cut_at_the_maps_boundaries(composite):
    """Each stream's own summed signal within the m/z it owns, on one axis,
    every sample labelled with the stream it came from. A sample on a
    boundary belongs to the run above it, as a peak there does."""
    signal = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")

    assert _stretches(signal) == [
        (40.0, 66.75, 0, _summed(0)),
        (67.0, 121.75, 1, _summed(1)),
        (122.0, 132.75, 0, _summed(0)),
        (133.0, 443.75, 2, _summed(2)),
        (444.0, 899.75, 3, _summed(3)),
    ]
    assert signal.name == "sum_signal"
    assert signal.segment.dtype == np.int16


def test_averaged_each_sample_is_divided_by_the_scans_of_its_own_stream(composite):
    """The streams of a composite hold different numbers of scans, five,
    three, four and two here. Divided by one count, the signal would step at
    every boundary by nothing the instrument measured."""
    signal = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-", average=True)

    assert [(segment, level) for _a, _b, segment, level in _stretches(signal)] == [
        (0, PER_SCAN[0]),
        (1, PER_SCAN[1]),
        (0, PER_SCAN[0]),
        (2, PER_SCAN[2]),
        (3, PER_SCAN[3]),
    ]


def test_the_stitched_signal_is_cached_beside_its_streams(composite, monkeypatch):
    """Stitched once: each stream's profile is averaged for its own cached
    signal, and a second read averages nothing and stitches nothing. The
    cache is a sum signal of the file like the others, by name, so whatever
    moves or removes a file's cached sum signals takes it along."""
    averaged = composite.profiles_averaged
    first = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-").compute()
    assert composite.profiles_averaged == averaged + 4

    def stitched_again(*args, **kwargs):
        raise AssertionError("the cached signal was not served")

    monkeypatch.setattr(m_compute, "_stitch_sum_signals", stitched_again)
    second = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-").compute()

    assert composite.profiles_averaged == averaged + 4
    np.testing.assert_array_equal(second.values, first.values)
    np.testing.assert_array_equal(second.mz.values, first.mz.values)
    np.testing.assert_array_equal(second.segment.values, first.segment.values)
    name = m_compute._composite_sum_signal_name(None, None, "-", "orbi_raw", RUNS, KEYS)
    assert name.startswith("sum_signal_")
    assert name.endswith(m_compute.sum_signal_suffix("orbi_raw"))
    assert os.path.exists(m_name.filename_to_zarr_path(SAMPLE_FILENAME, name))
    sample_path = m_name.parse_path_from_item_filename(SAMPLE_FILENAME)
    assert name in m_io.get_file_data_vars(sample_path)


def test_the_stitched_signals_name_is_of_its_map_and_its_streams():
    """A store rebuilt to another map, or under other keys, is not handed
    the signal stitched for the last; nor is another time range or the other
    polarity. And it is no stream's own name."""
    name = m_compute._composite_sum_signal_name

    names = {
        name(None, None, "-", "orbi_raw", RUNS, KEYS),
        name(None, None, "-", "orbi_raw", [[40, 68, 0], *RUNS[1:]], KEYS),
        name(None, None, "-", "orbi_raw", RUNS, [KEYS[0] + " event=1", *KEYS[1:]]),
        name(0.0, 10.0, "-", "orbi_raw", RUNS, KEYS),
        name(None, None, "+", "orbi_raw", RUNS, KEYS),
        *(
            m_compute._get_sum_signal_hash_name(None, None, None, "orbi_raw", key)
            for key in KEYS
        ),
    }

    assert len(names) == 9
    # A stream the runs do not index is not in the name
    assert name(None, None, "-", "orbi_raw", RUNS, [*KEYS, "another"]) in names


def test_a_cached_signal_is_not_served_under_a_key_the_file_no_longer_holds(
    composite, acquire
):
    """A stream's cached sum signal answers under its key without the file
    being read, and so would the stitched one. The reader is asked for every
    stream first: where the file no longer holds one under a key the store
    lists, the store is stale and is said to be, cached signal or no."""
    m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-").compute()

    # The low window read back under another range: its key names nothing
    moved = "FTMS - p NSI Full ms [66.0000-118.0000]"
    acquire(
        [
            (moved if text == LOW else text, event, peaks)
            for text, event, peaks in COMPOSITE
        ],
        MICROSCANS,
    )

    with pytest.raises(StalePeakStoreError, match="no stream under that key") as stale:
        m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")

    assert KEYS[1] in str(stale.value)
    assert isinstance(stale.value.__cause__, m_thermo.UnknownStreamError)
    # The stream's own cached signal still answers, which is why
    cached = m_compute.get_sum_signal(SAMPLE_FILENAME, stream=KEYS[1]).compute()
    assert float(cached.values[0]) == _summed(1)


def test_a_file_detected_whole_has_no_composite_signal(acquire, instrument_functions):
    """Its polarity's signal is ``get_sum_signal``'s, as it always was."""
    acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    with pytest.raises(ValueError, match="holds no composite of polarity '-'"):
        m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")


def test_a_polarity_with_one_stream_has_no_composite_signal(
    acquire, instrument_functions
):
    """Its signal is that stream's own."""
    acquire(COMPOSITE + [(POSITIVE, 5, {59.0: 70.0})] * 2, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    with pytest.raises(ValueError, match="holds no composite of polarity '\\+'"):
        m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "+")
    assert m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-").mz.size


def test_a_file_with_no_peak_store_has_no_composite_signal(acquire):
    acquire(COMPOSITE, MICROSCANS)

    with pytest.raises(FileNotFoundError):
        m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")


def test_a_per_stream_store_with_no_map_gives_no_composite_signal(composite):
    path = m_name.filename_to_zarr_path(SAMPLE_FILENAME, "peak_timeseries")
    del m_io.open_zarr_store(path, mode="r+").attrs[m_stitch.STITCH_MAP_ATTR]
    zarr.consolidate_metadata(path)

    with pytest.raises(StalePeakStoreError, match="no stitch map"):
        m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-")


def test_a_time_range_takes_each_streams_scans_inside_it(composite):
    """The scans are a second apart: five reagent scans, then three of the
    low window. Up to 6.5 s the range holds two of those three, and neither
    of the other windows. The signal is of the streams that have scans in
    it, each summed over its own, and the m/z the others own is a gap."""
    window = dict(t_min=0.0, t_max=6.5)

    summed = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-", **window)
    averaged = m_compute.get_composite_sum_signal(
        SAMPLE_FILENAME, "-", **window, average=True
    )

    assert _stretches(summed) == [
        (40.0, 66.75, 0, _summed(0)),
        (67.0, 121.75, 1, PER_SCAN[1] * 2),
        (122.0, 132.75, 0, _summed(0)),
    ]
    assert [level for *_rest, level in _stretches(averaged)] == [
        PER_SCAN[0],
        PER_SCAN[1],
        PER_SCAN[0],
    ]


def test_a_time_range_that_holds_no_scan_of_the_composite_is_refused(composite):
    with pytest.raises(m_thermo.NoScansFoundError, match="composite of polarity '-'"):
        m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-", t_min=100.0)


def test_a_calibrated_files_signal_is_cut_where_the_instrument_recorded_it(
    acquire, instrument_functions
):
    """A stream's signal is on the file's calibrated axis. Cut there by the
    whole m/z of the map, the sample the reagent scan recorded at 122 would
    fall below 122 and be left out, and the low window's sample there taken
    in its place. It is cut by the calibration the file carries: the same
    samples as before, from the same streams."""
    down = 1 - 5e-6
    acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    recorded = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-").compute()

    # A file calibrated before any of its sum signals was cached
    for name in m_io.get_file_data_vars(
        m_name.parse_path_from_item_filename(SAMPLE_FILENAME)
    ):
        if name.startswith("sum_signal"):
            m_io.remove_path(m_name.filename_to_zarr_path(SAMPLE_FILENAME, name))
    record_calibration(down)
    calibrated = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-").compute()

    np.testing.assert_array_equal(calibrated.mz.values, recorded.mz.values * down)
    np.testing.assert_array_equal(calibrated.segment.values, recorded.segment.values)
    np.testing.assert_array_equal(calibrated.values, recorded.values)
    # 122 itself is the reagent scan's, wherever the calibration put it
    on_the_boundary = np.flatnonzero(recorded.mz.values == 122.0)
    assert calibrated.segment.values[on_the_boundary].tolist() == [0]
    assert calibrated.mz.values[on_the_boundary] < 122.0
