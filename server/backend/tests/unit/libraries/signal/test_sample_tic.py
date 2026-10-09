"""The TIC of a file's scans, as a sample and as a peak store hold them.

A sample of a polarity whose scan streams the peak store stitches covers the
scans of those streams, each as its own stream selects them: that is the
store's own time axis for the polarity. A polarity-wide read of the file is
not always the same scans - its first-scan rule judges the file's first scan
against every other scan, a stream's own rule against the stream's - so the
item's TIC and the per-scan export's TIC are read the way the store was
built (``mascope_signal.compute.get_sample_tic_per_scan`` and
``get_stored_tic_per_scan``). A file detected whole is read as it always
was.
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
)
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
from mascope_signal.compute import StalePeakStoreError
from mascope_thermo.thermo import NoScansFoundError


# What each scan type of the composite sums to
REAGENT_TIC, LOW_TIC, MID_TIC, HIGH_TIC = 1440.0, 70.0, 55.0, 40.0
COMPOSITE_TICS = [REAGENT_TIC] * 5 + [LOW_TIC] * 3 + [MID_TIC] * 4 + [HIGH_TIC] * 2

# The composite and, after it, two scans of the other polarity
WITH_POSITIVE = COMPOSITE + [(POSITIVE, 5, {59.0: 70.0})] * 2


def _first_scan_dropped_file_wide(acquisition, monkeypatch):
    """The reader's first-scan rule, as it falls on a composite file that
    opens with a reagent scan: read with no stream named, the first scan is
    left out; read as its own stream, it stays."""
    selected = acquisition._selected

    def _selected(polarity=None, t_min=None, t_max=None, stream=None):
        rows = selected(polarity, t_min, t_max, stream)
        return rows if stream is not None else rows[1:]

    monkeypatch.setattr(acquisition, "_selected", _selected)


def _store_time():
    return m_io.load_peak_data(SAMPLE_FILENAME).time.values


# -- a stitched polarity ----------------------------------------------------------


def test_a_stitched_polaritys_tic_is_its_streams_scans(composite):
    times, tics = m_compute.get_sample_tic_per_scan(SAMPLE_FILENAME, "-")

    assert times.tolist() == [float(second) for second in range(14)]
    assert tics.tolist() == COMPOSITE_TICS


def test_the_first_scan_its_own_stream_keeps_is_in_the_samples_tic(
    composite, monkeypatch
):
    """The case the read exists for: polarity-wide, the file's opening
    reagent scan is judged against the analyte windows and dropped, and the
    sample's TIC is short of a scan its peaks were detected over."""
    _first_scan_dropped_file_wide(composite, monkeypatch)

    _times, pooled = m_compute.get_tic_per_scan(SAMPLE_FILENAME, polarity="-")
    times, tics = m_compute.get_sample_tic_per_scan(SAMPLE_FILENAME, "-")

    assert pooled.sum() == sum(COMPOSITE_TICS) - REAGENT_TIC
    assert tics.sum() == sum(COMPOSITE_TICS)
    assert times.tolist() == _store_time().tolist()


def test_a_stream_of_the_other_polarity_adds_nothing(acquire, instrument_functions):
    acquire(WITH_POSITIVE, {**MICROSCANS, 5: 10})
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    times, tics = m_compute.get_sample_tic_per_scan(SAMPLE_FILENAME, "-")

    assert times.tolist() == [float(second) for second in range(14)]
    assert tics.tolist() == COMPOSITE_TICS


def test_a_polarity_with_one_stream_is_read_as_its_polarity(
    acquire, instrument_functions, monkeypatch
):
    """Nothing is stitched for it, so it is read as it is in a file detected
    whole: no stream is named to the reader."""
    acquisition = acquire(WITH_POSITIVE, {**MICROSCANS, 5: 10})
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    asked = []
    tic_per_scan = acquisition.tic_per_scan

    def _tic_per_scan(polarity=None, stream=None):
        asked.append((polarity, stream))
        return tic_per_scan(polarity=polarity, stream=stream)

    monkeypatch.setattr(acquisition, "tic_per_scan", _tic_per_scan)

    times, tics = m_compute.get_sample_tic_per_scan(SAMPLE_FILENAME, "+")

    assert asked == [("+", None)]
    assert times.tolist() == [14.0, 15.0]
    assert tics.tolist() == [70.0, 70.0]


def test_a_stream_key_the_file_no_longer_holds_is_a_stale_store(composite, acquire):
    acquire([("FTMS - p NSI Full ms [40.0000-600.0000]", 1, {62.0: 100.0})] * 3)

    with pytest.raises(StalePeakStoreError, match="reads back no stream"):
        m_compute.get_sample_tic_per_scan(SAMPLE_FILENAME, "-")
    with pytest.raises(StalePeakStoreError, match="reads back no stream"):
        m_compute.get_stored_tic_per_scan(SAMPLE_FILENAME)


def test_a_polarity_the_file_does_not_hold_has_no_scans(composite):
    with pytest.raises(NoScansFoundError):
        m_compute.get_sample_tic_per_scan(SAMPLE_FILENAME, "+")


# -- a file detected whole, and one with no store ---------------------------------


@pytest.mark.parametrize("detected", [True, False], ids=["pooled-store", "no-store"])
def test_a_file_detected_whole_is_read_as_it_always_was(
    acquire, instrument_functions, detected
):
    acquisition = acquire(COMPOSITE, MICROSCANS)
    if detected:
        m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    reads = acquisition.trailer_reads

    for polarity_wide, read in (
        (
            m_compute.get_tic_per_scan(SAMPLE_FILENAME, polarity="-"),
            m_compute.get_sample_tic_per_scan(SAMPLE_FILENAME, "-"),
        ),
        (
            m_compute.get_tic_per_scan(SAMPLE_FILENAME),
            m_compute.get_stored_tic_per_scan(SAMPLE_FILENAME),
        ),
    ):
        assert np.array_equal(read[0], polarity_wide[0])
        assert np.array_equal(read[1], polarity_wide[1])
    # No stream was named, so none was keyed
    assert acquisition.trailer_reads == reads


# -- the scans a store holds ---------------------------------------------------------


def test_a_per_stream_stores_tic_is_on_its_own_axis(composite, monkeypatch):
    """What the per-scan export pairs with the store's rows by position. A
    file-wide read is a scan short of a sound store here, which read as a
    stale one that no rebuild repairs."""
    _first_scan_dropped_file_wide(composite, monkeypatch)
    stored = _store_time()

    file_wide, _tics = m_compute.get_tic_per_scan(SAMPLE_FILENAME)
    with pytest.raises(StalePeakStoreError):
        m_compute.check_stored_scan_axis(file_wide, stored)

    times, tics = m_compute.get_stored_tic_per_scan(SAMPLE_FILENAME)

    assert m_compute.check_stored_scan_axis(times, stored) is None
    assert tics.tolist() == COMPOSITE_TICS


def test_the_scans_of_streams_that_alternate_come_in_time_order(
    acquire, instrument_functions
):
    """Each stream is read on its own and the scans joined as the store's
    axis was: by time, whichever stream a scan is of."""
    scans = [
        (REAGENT, 1, {62.0: 1000.0, 80.0: 10.0}),
        (LOW, 2, {80.0: 14.0, 100.0: 50.0}),
    ] * 3
    acquire(scans, MICROSCANS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    for times, tics in (
        m_compute.get_stored_tic_per_scan(SAMPLE_FILENAME),
        m_compute.get_sample_tic_per_scan(SAMPLE_FILENAME, "-"),
    ):
        assert (
            times.tolist() == _store_time().tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        )
        assert tics.tolist() == [1010.0, 64.0] * 3


def test_a_per_stream_stores_tic_holds_every_polarity(acquire, instrument_functions):
    acquire(WITH_POSITIVE, {**MICROSCANS, 5: 10})
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    times, tics = m_compute.get_stored_tic_per_scan(SAMPLE_FILENAME)

    assert times.tolist() == _store_time().tolist()
    assert tics.tolist() == COMPOSITE_TICS + [70.0, 70.0]


def test_the_stitch_decision_reads_the_same_metadata(composite):
    """One read of the store's metadata serves the decision and the keys."""
    keys, stitch = m_compute._peak_store_metadata(SAMPLE_FILENAME)

    assert keys == KEYS
    assert sorted(stitch["runs"]) == ["-"]
    assert m_compute.peak_store_stitches(SAMPLE_FILENAME, "-") is True
    assert m_compute.peak_store_stitches(SAMPLE_FILENAME, "+") is False
