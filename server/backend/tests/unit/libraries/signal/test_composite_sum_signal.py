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
    # Nor is the sample's signal the pooled one: the metadata refuses it too
    with pytest.raises(StalePeakStoreError, match="no stitch map"):
        m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-")


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


def test_a_samples_signal_is_the_stitched_one_where_the_store_stitches(composite):
    """What a sample's spectrum shows is what its peak list was detected in:
    the stitched signal, each stream over its own scans, so a listed peak
    sits on the profile. Over the low window's m/z the level is the window's
    per-scan sum, 70, and its listed peaks - the stored sums divided by what
    the listing divides them by - sum to the same 70. The pooled signal is
    something else there: the reagent scan's five scans and the window's
    three over all fourteen."""
    sample = m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-", average=True)
    stitched = m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-", average=True)

    assert _stretches(sample) == _stretches(stitched)
    assert [level for _a, _b, segment, level in _stretches(sample) if segment == 1] == [
        PER_SCAN[1]
    ]

    listed = m_io.load_peak_data(SAMPLE_FILENAME).compute()
    scans = m_compute.stored_scans_per_peak(
        listed, m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-")
    )
    low = listed.stream.values == 1
    assert float((listed.sum_peak_heights.values / scans)[low].sum()) == pytest.approx(
        PER_SCAN[1]
    )

    pooled = m_compute.get_sum_signal(SAMPLE_FILENAME, polarity="-", average=True)
    assert float(pooled.sel(mz=100.0, method="nearest").compute()) == pytest.approx(
        (PER_SCAN[0] * SCANS[0] + PER_SCAN[1] * SCANS[1]) / sum(SCANS)
    )


def test_a_samples_signal_is_the_pooled_one_where_nothing_is_stitched(
    acquire, instrument_functions
):
    """A file with no peak store yet, and a store detected whole: the
    polarity's pooled signal, as its peaks are averaged."""
    acquire(COMPOSITE, MICROSCANS)
    pooled = m_compute.get_sum_signal(SAMPLE_FILENAME, polarity="-", average=True)
    pooled = pooled.compute()

    no_store = m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-", average=True)
    np.testing.assert_array_equal(no_store.compute().values, pooled.values)
    assert "segment" not in no_store.coords

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    whole = m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-", average=True)
    np.testing.assert_array_equal(whole.compute().values, pooled.values)


def test_a_samples_signal_of_a_stale_store_is_refused_not_pooled(composite, acquire):
    """The pooled profile would sit under peaks detected elsewhere."""
    m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-").compute()
    moved = "FTMS - p NSI Full ms [66.0000-118.0000]"
    acquire(
        [
            (moved if text == LOW else text, event, peaks)
            for text, event, peaks in COMPOSITE
        ],
        MICROSCANS,
    )

    with pytest.raises(StalePeakStoreError):
        m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-")


def test_a_samples_signal_passes_the_time_range_on_both_paths(
    composite, acquire, instrument_functions
):
    """Stitched: 0 to 6.5 s is the reagent scan's five scans and two of the
    low window's, the other windows a gap. Pooled: the polarity's signal of
    those scans, not of the whole file."""
    window = dict(t_min=0.0, t_max=6.5)
    stitched = m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-", **window)
    assert _stretches(stitched) == _stretches(
        m_compute.get_composite_sum_signal(SAMPLE_FILENAME, "-", **window)
    )
    assert len(_stretches(stitched)) == 3

    acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    ranged = m_compute.get_sample_sum_signal(
        SAMPLE_FILENAME, "-", **window, average=True
    )
    pooled = m_compute.get_sum_signal(
        SAMPLE_FILENAME, polarity="-", average=True, **window
    )
    np.testing.assert_array_equal(ranged.compute().values, pooled.compute().values)
    whole = m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-", average=True)
    assert not np.array_equal(ranged.compute().values, whole.compute().values)


def test_a_samples_signal_of_a_polarity_with_one_stream_is_that_streams_own(
    acquire, instrument_functions
):
    """Two positive scans beside the negative composite: the positive
    sample's signal is pooled over its own polarity's scans, at the level
    of its one peak, and carries no segment. Every polarity's scans would
    put the reagent scan's level under it."""
    acquire(COMPOSITE + [(POSITIVE, 5, {59.0: 70.0})] * 2, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    plus = m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "+", average=True)
    plus = plus.compute()
    assert "segment" not in plus.coords
    assert float(plus.sel(mz=59.0, method="nearest")) == pytest.approx(70.0)
    every = m_compute.get_sum_signal(SAMPLE_FILENAME, average=True).compute()
    assert float(every.sel(mz=59.0, method="nearest")) != pytest.approx(70.0)


def test_a_failure_inside_the_stitched_read_is_raised_not_answered_pooled(
    composite, monkeypatch
):
    """The helper does not learn "nothing is stitched" from the class of
    what the stitched read raised."""
    monkeypatch.setattr(
        m_compute,
        "_stitch_sum_signals",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("cut short")),
    )

    with pytest.raises(ValueError, match="cut short"):
        m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-")


def test_a_store_carrying_part_of_a_per_stream_store_is_refused_as_the_listing_refuses_it(
    composite,
):
    """A store with the stream of each peak and each scan and no stream
    keys is what ``peak_store_streams`` refuses rather than reads as
    pooled; the sample's signal refuses it with the same sentence, as the
    listing's count does."""
    path = m_name.filename_to_zarr_path(SAMPLE_FILENAME, "peak_timeseries")
    del m_io.open_zarr_store(path, mode="r+").attrs["streams"]
    zarr.consolidate_metadata(path)

    with pytest.raises(ValueError, match="missing the stream keys") as signal:
        m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-")
    with pytest.raises(ValueError, match="missing the stream keys") as listing:
        m_compute.stored_scans_per_peak(
            m_io.load_peak_data(SAMPLE_FILENAME, composite=False),
            m_compute.get_scan_timestamps(SAMPLE_FILENAME, polarity="-"),
        )
    assert str(signal.value) == str(listing.value)


def test_a_file_detected_whole_is_decided_without_opening_the_store(
    acquire, instrument_functions, monkeypatch
):
    """The decision reads the store's metadata; the dataset is opened only
    for a stitched read."""
    acquire(COMPOSITE, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    load_array = m_compute.m_io.load_array

    def _not_the_peak_store(base_filename, var, *args, **kwargs):
        # The pooled signal's cache is read through the same function
        if var == "peak_timeseries":
            raise AssertionError("the peak store was opened")
        return load_array(base_filename, var, *args, **kwargs)

    monkeypatch.setattr(m_compute.m_io, "load_array", _not_the_peak_store)

    assert m_compute.peak_store_stitches(SAMPLE_FILENAME, "-") is False
    signal = m_compute.get_sample_sum_signal(SAMPLE_FILENAME, "-", average=True)
    assert "segment" not in signal.coords


def test_the_metadata_decision_is_the_datasets(composite):
    """What the store's metadata says agrees with what the dataset says: the
    composite is stitched for its polarity and for no other."""
    stored = m_io.load_array(SAMPLE_FILENAME, var="peak_timeseries")
    assert m_compute.peak_store_stitches(SAMPLE_FILENAME, "-") is True
    assert m_compute.peak_store_stitches(SAMPLE_FILENAME, "+") is False
    assert set(m_compute.peak_store_stitch_map(stored)["runs"]) == {"-"}


# What a per-stream store can have lost: an attribute or an array of it
DAMAGE = {
    "no map": ("attr", m_stitch.STITCH_MAP_ATTR),
    "no mask": ("array", "composite"),
    "no stream keys": ("attr", "streams"),
    "no scan label": ("array", "scan_stream"),
    "no peak label": ("array", "stream"),
}


@pytest.mark.parametrize("damage", list(DAMAGE))
def test_the_metadata_decision_refuses_what_the_datasets_refuses(composite, damage):
    """A per-stream store that lost an attribute or an array is refused off
    its metadata as it is off the dataset, with one sentence, whichever
    polarity is asked about."""
    path = m_name.filename_to_zarr_path(SAMPLE_FILENAME, "peak_timeseries")
    kind, name = DAMAGE[damage]
    group = m_io.open_zarr_store(path, mode="r+")
    if kind == "attr":
        del group.attrs[name]
    else:
        del group[name]
    zarr.consolidate_metadata(path)

    with pytest.raises(ValueError) as dataset:
        m_compute.peak_store_stitch_map(
            m_io.load_array(SAMPLE_FILENAME, var="peak_timeseries")
        )
    for polarity in ("-", "+"):
        with pytest.raises(ValueError) as metadata:
            m_compute.peak_store_stitches(SAMPLE_FILENAME, polarity)
        assert type(metadata.value) is type(dataset.value)
        assert str(metadata.value) == str(dataset.value)
