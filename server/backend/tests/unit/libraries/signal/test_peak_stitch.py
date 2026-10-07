"""The stitch of a composite acquisition, as peak detection writes it.

A composite method measures one chemistry as several scan ranges, each an
experiment of its own. Detected per stream, its file holds a peak list per
range, and the stitch makes one spectrum of them: each m/z from the one
stream that owns it (``mascope_signal.stitch``, where the rule is tested on
its own). What is pinned here is what peak detection writes of it into the
store, and that a file not detected per stream gets none of it.

No committed file holds a composite, so these run on a scripted acquisition
(``ScriptedAcquisition``), read through the real selection keys.
"""

import numpy as np
import pytest
from composite_acquisition import (
    COMPOSITE,
    KEYS,
    LOW,
    MICROSCANS,
    POSITIVE,
    REAGENT,
    RUNS,
    record_calibration,
)
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
import mascope_signal.stitch as m_stitch
from mascope_signal.compute import StalePeakStoreError


def _store():
    """The whole peak store, weak and satellite peaks included."""
    return m_io.load_peak_data(SAMPLE_FILENAME, drop_bad_peaks=False).compute()


def _in_composite(store):
    """``{(stream index, m/z to two decimals): in its composite}`` per peak."""
    return {
        (int(stream), round(float(mz), 2)): bool(flag)
        for stream, mz, flag in zip(
            store.stream.values, store.mz.values, store.composite.values
        )
    }


# -- what peak detection writes of the stitch ----------------------------------------


def test_a_composite_files_store_says_which_peaks_make_up_its_spectrum(composite):
    """Each m/z from the stream that owns it. The low window owns the ion at
    80 and the reagent scan's reading of it stays out; the reagent scan owns
    its dimer at 125, between the low and the mid window; the high window
    owns the ion at 450, and the mid window's reading stays out."""
    store = _store()

    assert m_compute.peak_store_streams(store) == KEYS
    assert _in_composite(store) == {
        (0, 62.0): True,
        (0, 80.0): False,
        (1, 80.0): True,
        (1, 100.0): True,
        (1, 121.0): True,
        (0, 125.0): True,
        (0, 134.0): False,
        (2, 134.5): True,
        (2, 300.0): True,
        (2, 450.0): False,
        (3, 450.0): True,
        (3, 700.0): True,
    }
    assert store.composite.dtype == bool
    assert store.composite.dims == ("mz",)


def test_the_peaks_left_out_are_still_in_the_store(composite):
    """Nothing is dropped for the composite. The other stream's reading of
    an ion keeps its row, its label and its intensity, which is what the
    overlap is read from."""
    store = _store()

    left_out = store.isel(mz=np.flatnonzero(~store.composite.values))

    assert left_out.mz.values.round(2).tolist() == [80.0, 134.0, 450.0]
    assert left_out.stream.values.tolist() == [0, 0, 2]
    assert left_out.sum_peak_heights.values.tolist() == [50.0, 150.0, 32.0]


def test_the_map_is_recorded_beside_the_peaks(composite):
    """So that nobody draws it again to know where the boundaries are, and a
    store stitched under another rule is told from this one."""
    store = _store()

    assert store.attrs[m_stitch.STITCH_MAP_ATTR] == {
        "rule": m_stitch.STITCH_RULE,
        "runs": {"-": RUNS},
        "sources": {"-": "default"},
        "notes": [],
    }
    assert m_compute.peak_store_stitch_map(store) == store.attrs["stitch_map"]


def test_what_two_streams_read_of_one_ion_is_recorded_too(composite):
    """The reading the composite leaves unused. The low window reads the ion
    at 80 at 14 a scan where the reagent scan reads 10, half a ppm higher;
    the high window reads the one at 450 at 10 where the mid window reads
    8. The reagent scan reaches the mid window over two m/z, and they share
    nothing there."""
    low, mid, high = _store().attrs[m_stitch.STITCH_OVERLAPS_ATTR]

    assert (low["streams"], low["overlap"], low["shared"]) == ([0, 1], [[67, 122]], 1)
    assert low["ratio"] == pytest.approx([1.4, 1.4, 1.4])
    assert low["ppm"] == pytest.approx([0.5, 0.5, 0.5], abs=1e-6)
    assert (mid["streams"], mid["overlap"], mid["shared"]) == ([0, 2], [[133, 135]], 0)
    assert mid["ratio"] is None
    assert (high["streams"], high["overlap"]) == ([2, 3], [[444, 451]])
    assert high["ratio"] == pytest.approx([1.25, 1.25, 1.25])
    assert high["ppm"] == pytest.approx([0.4, 0.4, 0.4], abs=1e-6)


def test_the_stitch_reads_the_peaks_a_load_keeps(
    acquire, instrument_functions, monkeypatch
):
    """A satellite is not what a stream measured of an ion, and takes no
    part in an overlap. It is placed on the map like any peak."""
    acquire(COMPOSITE, MICROSCANS)

    def flag(peaks):
        return peaks.assign(is_satellite_peak=peaks["mz"].round(2) == 450.0)

    monkeypatch.setattr(m_peak, "flag_satellite_peaks", flag)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    store = _store()

    assert store.attrs[m_stitch.STITCH_OVERLAPS_ATTR][2]["shared"] == 0
    assert _in_composite(store)[(3, 450.0)] is True
    assert _in_composite(store)[(2, 450.0)] is False


def test_every_peak_of_a_polarity_with_one_stream_is_its_composites(
    acquire, instrument_functions
):
    """A file that also runs one experiment in the other polarity: that
    polarity has nothing to stitch and no map, and its peaks are all in its
    composite, so the mask of a per-stream store reads one way for every
    polarity."""
    acquire(COMPOSITE + [(POSITIVE, 5, {59.0: 70.0, 80.0: 5.0})] * 2, MICROSCANS)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    assert m_compute.peak_store_streams(store) == [*KEYS, f"{POSITIVE} R=120000"]
    assert store.attrs[m_stitch.STITCH_MAP_ATTR]["runs"] == {"-": RUNS}
    positive = store.isel(mz=np.flatnonzero(store.polarity.values == "+"))
    assert positive.mz.size == 2
    assert positive.composite.values.all()
    # And the ion at 80 of the other polarity is none of the negative map's
    assert _in_composite(store)[(0, 80.0)] is False
    assert _in_composite(store)[(4, 80.0)] is True


def test_streams_the_census_cannot_tell_apart_are_stitched_by_their_order(
    acquire, instrument_functions
):
    """Two experiments on one range whose trailers say nothing of their
    microscans: the first owns the range, and the second's peaks stay out of
    the composite, whole."""
    settle = (REAGENT, 1, {62.0: 100.0, 125.0: 10.0})
    measure = (REAGENT, 2, {62.0: 1000.0, 100.0: 50.0})
    acquire([settle] * 2 + [measure] * 4)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    assert store.attrs[m_stitch.STITCH_MAP_ATTR]["runs"] == {"-": [[40, 138, 0]]}
    assert store.composite.values.tolist() == (store.stream.values == 0).tolist()


def test_the_measurement_owns_a_range_it_shares_with_a_settling_scan(
    acquire, instrument_functions
):
    """The same two experiments, their microscans read: one against ten. The
    measurement owns the whole range and the settling scans own nothing."""
    settle = (REAGENT, 1, {62.0: 100.0, 125.0: 10.0})
    measure = (REAGENT, 2, {62.0: 1000.0, 100.0: 50.0})
    acquire([settle] * 2 + [measure] * 4, {1: 1, 2: 10})

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    assert store.attrs[m_stitch.STITCH_MAP_ATTR]["runs"] == {"-": [[40, 138, 1]]}
    assert store.composite.values.tolist() == (store.stream.values == 1).tolist()


@pytest.mark.parametrize("per_stream", [False, None])
def test_a_file_detected_whole_carries_nothing_of_the_stitch(
    acquire, instrument_functions, per_stream
):
    """A composite file nobody asks for per stream is pooled, as before, and
    its store is a pooled store: no mask, no map, no reading."""
    acquire(COMPOSITE, MICROSCANS)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=per_stream)

    store = _store()
    assert "composite" not in store.variables
    assert m_stitch.STITCH_MAP_ATTR not in store.attrs
    assert m_stitch.STITCH_OVERLAPS_ATTR not in store.attrs
    assert m_compute.peak_store_stitch_map(store) is None


def test_a_file_with_one_stream_carries_nothing_of_it_either(
    acquire, instrument_functions
):
    """Asked for per stream, it has nothing to detect apart and nothing to
    stitch: its store is what it was."""
    acquire([COMPOSITE[0]] * 3, MICROSCANS)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    assert "composite" not in store.variables
    assert m_stitch.STITCH_MAP_ATTR not in store.attrs
    assert m_stitch.STITCH_OVERLAPS_ATTR not in store.attrs


def test_a_rebuild_stitches_the_file_again(composite, instrument_functions):
    """A rebuild is handed no decision and goes by the file's record. The
    store it writes is a per-stream store like the first, mask and map
    included."""
    first = _store()

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    rebuilt = _store()
    assert _in_composite(rebuilt) == _in_composite(first)
    for attribute in (m_stitch.STITCH_MAP_ATTR, m_stitch.STITCH_OVERLAPS_ATTR):
        assert rebuilt.attrs[attribute] == first.attrs[attribute]


# A calibration by five parts per million, which puts the low window's ion at
# 121.9999 above 122 on the stored axis.
CALIBRATION = 1 + 5e-6
NEAR_THE_BOUNDARY = [
    (text, event, {**peaks, 121.9999: 9.0} if text in (REAGENT, LOW) else peaks)
    for text, event, peaks in COMPOSITE
]


def test_a_files_mask_does_not_move_with_its_calibration(acquire, instrument_functions):
    """The map is in m/z as the instrument recorded them. Detected again
    after its calibration, a file's peaks are on the calibrated axis, and
    each is placed where it was recorded: the low window's ion just below
    122 stays its composite's, and the reagent scan's reading of it stays
    out."""
    acquire(NEAR_THE_BOUNDARY, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    before = _store()

    record_calibration(CALIBRATION)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    after = _store()

    np.testing.assert_allclose(after.mz.values, before.mz.values * CALIBRATION)
    moved = after.mz.values[np.flatnonzero(before.mz.values.round(4) == 121.9999)]
    assert moved.size == 2
    assert (moved > 122.0).all()
    assert after.composite.values.tolist() == before.composite.values.tolist()
    assert _in_composite(before)[(1, 122.0)] is True
    assert _in_composite(before)[(0, 122.0)] is False
    # The overlap is read where both streams claim, on the same axis
    assert after.attrs[m_stitch.STITCH_OVERLAPS_ATTR][0]["shared"] == 2
    assert before.attrs[m_stitch.STITCH_OVERLAPS_ATTR][0]["shared"] == 2


def test_a_per_stream_store_with_no_map_is_stale(composite):
    """A store that labels its streams and says nothing of their composites
    was not written by the detection that stitches, or lost part of what it
    wrote. Detecting the file's peaks again writes all of it, so it is the
    stale-store error, which is what asks for that."""
    store = _store()
    unmapped = store.copy()
    del unmapped.attrs[m_stitch.STITCH_MAP_ATTR]

    with pytest.raises(StalePeakStoreError, match="no stitch map"):
        m_compute.peak_store_stitch_map(unmapped)
    with pytest.raises(StalePeakStoreError, match="no stitch map"):
        m_compute.peak_store_stitch_map(store.drop_vars("composite"))
