"""The weak flag of a store written before the local floor, flagged again in
place by ``reflag_orbitrap_weak_peaks``.

Each store is detected under the rule, then aged to what a store written
before the floor holds: the signal-to-noise flag alone, and the stitch
overlaps read among the peaks that flag kept. Flagged again from the store
alone, it holds what a detection writes, which is what the script promises.
"""

import numpy as np
import pytest
import zarr
from composite_acquisition import IN_LOW, KEYS
from composite_acquisition import LOW as LOW_RANGE
from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.peak as m_peak
import mascope_signal.stitch as m_stitch
from mascope_backend.db.scripts import reflag_orbitrap_weak_peaks as script
from mascope_thermo.scan_filter import parse_stream_key


ION = 62.0
FLOOR_HEIGHT = 100.0
OTHER_STREAM = ION * (1 + 1e-6)
NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
POS = "FTMS + p NSI Full ms [40.0000-600.0000]"


def _floor(step_ppm=3.0, half_width_ppm=1500.0):
    """Maxima of one height every ``step_ppm`` around the reagent ion, the
    ion itself left out, as in ``test_local_floor``."""
    steps = np.arange(step_ppm, half_width_ppm + step_ppm / 2, step_ppm)
    return ION * (1 + np.concatenate([-steps[::-1], steps]) * 1e-6)


def _store():
    return m_io.load_array(SAMPLE_FILENAME, var="peak_timeseries").load()


def _age(is_weak=None):
    """Put the flag a store written before the floor holds into the test
    sample's store, or ``is_weak``; return the flag the detection wrote."""
    store = _store()
    detected = store.is_weak.values.astype(bool)
    if is_weak is None:
        is_weak = store.signal_to_noise.values < m_peak.SIGNAL_TO_NOISE_THRESHOLD
    m_io.write_peak_variable(SAMPLE_FILENAME, "is_weak", is_weak)
    return detected


def _streams():
    """The composite's streams as the overlaps read them: polarity and scan
    ranges, which their keys state."""
    return [{"signature": parse_stream_key(k).scan_filter.signature()} for k in KEYS]


def _set_overlaps(overlaps):
    path = m_name.filename_to_zarr_path(SAMPLE_FILENAME, "peak_timeseries")
    group = m_io.open_zarr_store(path, mode="r+")
    group.attrs[m_stitch.STITCH_OVERLAPS_ATTR] = overlaps
    zarr.consolidate_metadata(path)


@pytest.fixture
def pooled(acquire, instrument_functions):
    """A file of two polarities, its negative spectrum's reagent ion in a
    floor; the positive one holds one peak of the floor's height there."""
    negative = {float(mz): FLOOR_HEIGHT for mz in _floor()}
    negative[ION] = 1e6
    acquire(
        [
            (NEG, 1, negative),
            (POS, 1, {ION: FLOOR_HEIGHT}),
        ]
    )
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)


@pytest.fixture
def streamed(acquire, instrument_functions):
    """A file detected per stream: two negative experiments, the first with
    the floor, the second one peak of its height inside the floor."""
    floor = {float(mz): FLOOR_HEIGHT for mz in _floor()}
    floor[ION] = 1e6
    acquire(
        [(NEG, 1, floor), (NEG, 2, {OTHER_STREAM: FLOOR_HEIGHT})],
        microscans={1: 1, 2: 10},
    )
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)


def test_a_pooled_store_is_flagged_as_its_detection_flags_it(pooled):
    detected = _age()
    # The store as it was before the floor: none of the floor is weak
    assert not _store().is_weak.values.astype(bool).any()

    reflag = script.read(SAMPLE_FILENAME)
    script.write(SAMPLE_FILENAME, reflag)

    assert np.array_equal(_store().is_weak.values.astype(bool), detected)
    assert reflag.turned_weak == _floor().size
    assert reflag.turned_kept == 0
    # Each polarity is its own spectrum: the positive peak inside the
    # negative floor stays
    store = _store()
    positive = store.polarity.values == "+"
    assert positive.sum() == 1
    assert not store.is_weak.values.astype(bool)[positive].any()
    # A pooled store records no overlaps
    assert reflag.overlaps is None


def test_a_per_stream_store_is_flagged_by_each_streams_own_peaks(streamed):
    """By its streams, not its polarity: read as one spectrum, the second
    experiment's peak would be under the first one's floor."""
    detected = _age()

    reflag = script.read(SAMPLE_FILENAME)
    script.write(SAMPLE_FILENAME, reflag)

    assert np.array_equal(_store().is_weak.values.astype(bool), detected)
    assert reflag.turned_weak == _floor().size


def test_a_store_flagged_by_the_rule_is_left_as_it_is(pooled):
    reflag = script.read(SAMPLE_FILENAME)

    assert not reflag.changed


def test_a_peak_weak_before_and_kept_by_the_rule_is_kept(pooled):
    """Written back where it differs either way: the store then holds what
    a detection writes, whatever flag it held."""
    store = _store()
    detected = store.is_weak.values.astype(bool)
    aged = detected.copy()
    (reagent,) = np.flatnonzero(
        (store.polarity.values == "-") & np.isclose(store.mz.values, ION, rtol=1e-8)
    )
    assert not detected[reagent]
    aged[reagent] = True
    _age(aged)

    reflag = script.read(SAMPLE_FILENAME)
    script.write(SAMPLE_FILENAME, reflag)

    assert (reflag.turned_weak, reflag.turned_kept) == (0, 1)
    assert np.array_equal(_store().is_weak.values.astype(bool), detected)


def test_the_stitch_overlaps_are_read_again_among_the_peaks_kept(composite):
    """A stitched store's overlaps are read among the peaks a load keeps.
    Aged with the ion both the reagent scan and the low window hold weak in
    the window, its overlap shares no ion; flagged again, the overlaps are
    the detection's, read from the store's stream keys alone."""
    store = _store()
    detected_overlaps = store.attrs[m_stitch.STITCH_OVERLAPS_ATTR]
    stream = store.stream.values
    low = KEYS.index(f"{LOW_RANGE} R=120000")
    in_low = (stream == low) & np.isclose(store.mz.values, IN_LOW, rtol=1e-6)
    assert in_low.sum() == 1
    aged = store.is_weak.values.astype(bool) | in_low
    detected = _age(aged)
    _set_overlaps(
        m_stitch.overlap_readings(
            _streams(),
            store.mz.values,
            stream,
            store.sum_peak_heights.values,
            ~(aged | store.is_satellite.values.astype(bool)),
            np.bincount(store.scan_stream.values, minlength=len(KEYS)),
        )
    )
    shared = {
        tuple(reading["streams"]): reading["shared"]
        for reading in _store().attrs[m_stitch.STITCH_OVERLAPS_ATTR]
    }
    assert shared[(0, low)] == 0

    reflag = script.read(SAMPLE_FILENAME)
    script.write(SAMPLE_FILENAME, reflag)

    store = _store()
    assert np.array_equal(store.is_weak.values.astype(bool), detected)
    assert store.attrs[m_stitch.STITCH_OVERLAPS_ATTR] == detected_overlaps
    assert reflag.turned_kept == 1


def test_a_store_that_changed_since_it_was_read_is_not_written(pooled):
    """A detection or a calibration of the file between the read and the
    write: the flag read from the old store is not written over the new."""
    _age()
    reflag = script.read(SAMPLE_FILENAME)
    store = _store()
    m_io.write_peak_variable(
        SAMPLE_FILENAME, "sum_peak_heights", store.sum_peak_heights.values * 2
    )

    with pytest.raises(script.Moved):
        script.write(SAMPLE_FILENAME, reflag)

    assert not _store().is_weak.values.astype(bool).any()


def test_the_overlaps_place_each_streams_peaks_by_its_own_factor(
    composite, instrument_functions
):
    """The low window calibrated by a factor of its own, far larger than a
    real one, takes its ion at 121 past the overlap's edge as recorded; read
    again, the overlaps go by the factor the file's props record, as the
    detection's do."""
    m_io.update_props(
        SAMPLE_FILENAME,
        {
            "mz_calibration": {
                "par": {"calibration_factor": 1.0},
                "streams": {f"{LOW_RANGE} R=120000": {"calibration_factor": 1.01}},
            }
        },
    )
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    detected_overlaps = _store().attrs[m_stitch.STITCH_OVERLAPS_ATTR]
    _set_overlaps([])

    reflag = script.read(SAMPLE_FILENAME)

    assert not reflag.changed
    assert script._json(reflag.overlaps) == detected_overlaps


def test_a_file_recorded_is_current_under_the_rule(pooled):
    script._record(SAMPLE_FILENAME, script.UNREADABLE)

    props = m_io.read_props(SAMPLE_FILENAME)
    assert script.is_current(props)
    assert props[script.RECORD_FIELD]["result"] == script.UNREADABLE


def test_a_peak_under_the_signal_to_noise_cutoff_stays_weak(pooled):
    """The floor is one half of the rule; the cutoff is the other."""
    store = _store()
    snr = store.signal_to_noise.values.copy()
    (reagent,) = np.flatnonzero(
        (store.polarity.values == "-") & np.isclose(store.mz.values, ION, rtol=1e-8)
    )
    snr[reagent] = m_peak.SIGNAL_TO_NOISE_THRESHOLD - 0.5
    store["signal_to_noise"] = ("mz", snr)

    reflag = script.recompute(store, {})

    assert reflag.is_weak[reagent]
    assert reflag.turned_weak == 1


def test_the_overlaps_leave_out_the_satellites(composite):
    """Among the peaks a load keeps: neither weak nor a satellite."""
    store = _store()
    low = KEYS.index(f"{LOW_RANGE} R=120000")
    in_low = (store.stream.values == low) & np.isclose(
        store.mz.values, IN_LOW, rtol=1e-6
    )
    store["is_satellite"] = ("mz", store.is_satellite.values.astype(bool) | in_low)

    reflag = script.recompute(store, {})

    shared = {tuple(r["streams"]): r["shared"] for r in reflag.overlaps}
    assert shared[(0, low)] == 0


def test_a_store_written_while_its_floor_is_computed_is_not_written(
    pooled, monkeypatch
):
    """The guard compares against the arrays the flag came from, read once
    before the floor: a lazy store read again after it would hold the write
    that landed in between."""
    _age()
    floor = m_peak.below_local_floor

    def landing(mz, heights, spectrum):
        m_io.write_peak_variable(SAMPLE_FILENAME, "sum_peak_heights", heights * 2)
        return floor(mz, heights, spectrum)

    monkeypatch.setattr(m_peak, "below_local_floor", landing)
    reflag = script.read(SAMPLE_FILENAME)

    with pytest.raises(script.Moved):
        script.write(SAMPLE_FILENAME, reflag)

    assert not _store().is_weak.values.astype(bool).any()


@pytest.mark.parametrize("name", ["mz", "polarity", "is_weak", "signal_to_noise"])
def test_each_input_of_the_flag_is_guarded(pooled, name):
    _age()
    reflag = script.read(SAMPLE_FILENAME)
    values = _store()[name].values.copy()
    if name == "polarity":
        values[values == "+"] = "-"
    elif name == "is_weak":
        values = ~values.astype(bool)
    else:
        values = values * 1.5
    m_io.write_peak_variable(SAMPLE_FILENAME, name, values)

    with pytest.raises(script.Moved):
        script.write(SAMPLE_FILENAME, reflag)


def test_a_calibration_applied_since_the_read_is_not_written_over(composite):
    """The overlaps place each stream's peaks by the calibration the props
    record: one applied in between would leave them placed by the old."""
    reflag = script.read(SAMPLE_FILENAME)
    m_io.update_props(
        SAMPLE_FILENAME, {"mz_calibration": {"par": {"calibration_factor": 1.0}}}
    )

    with pytest.raises(script.Moved):
        script.write(SAMPLE_FILENAME, reflag)


def _age_composite():
    """The composite aged as in the overlaps test: its in-low ion weak, the
    overlaps read among the peaks that kept. Returns the detection's flag
    and overlaps."""
    store = _store()
    detected_overlaps = store.attrs[m_stitch.STITCH_OVERLAPS_ATTR]
    stream = store.stream.values
    low = KEYS.index(f"{LOW_RANGE} R=120000")
    in_low = (stream == low) & np.isclose(store.mz.values, IN_LOW, rtol=1e-6)
    aged = store.is_weak.values.astype(bool) | in_low
    detected = _age(aged)
    _set_overlaps(
        m_stitch.overlap_readings(
            _streams(),
            store.mz.values,
            stream,
            store.sum_peak_heights.values,
            ~(aged | store.is_satellite.values.astype(bool)),
            np.bincount(store.scan_stream.values, minlength=len(KEYS)),
        )
    )
    return detected, detected_overlaps


def test_a_write_that_stops_at_the_overlaps_is_finished_by_the_next_run(
    composite, monkeypatch
):
    """The flag is written last, so it is what tells a run the file is done:
    one that stopped after the flag would leave the overlaps stale for good."""
    detected, detected_overlaps = _age_composite()

    def broken(filename, overlaps):
        raise OSError("disk full")

    with monkeypatch.context() as patched:
        patched.setattr(script, "_write_overlaps", broken)
        with pytest.raises(OSError):
            script.write(SAMPLE_FILENAME, script.read(SAMPLE_FILENAME))
    assert not script.is_current(m_io.read_props(SAMPLE_FILENAME))

    again = script.read(SAMPLE_FILENAME)
    assert again.changed
    script.write(SAMPLE_FILENAME, again)

    store = _store()
    assert np.array_equal(store.is_weak.values.astype(bool), detected)
    assert store.attrs[m_stitch.STITCH_OVERLAPS_ATTR] == detected_overlaps
    assert script.is_current(m_io.read_props(SAMPLE_FILENAME))


def test_a_changed_file_is_recorded_by_its_write(pooled):
    _age()
    script.write(SAMPLE_FILENAME, script.read(SAMPLE_FILENAME))

    record = m_io.read_props(SAMPLE_FILENAME)[script.RECORD_FIELD]
    assert record["result"] == script.CHANGED
    assert record["turned_weak"] == _floor().size


def test_a_per_stream_store_without_overlaps_gets_none(streamed):
    """Overlaps are read again only on a store that records them."""
    path = m_name.filename_to_zarr_path(SAMPLE_FILENAME, "peak_timeseries")
    group = m_io.open_zarr_store(path, mode="r+")
    group.attrs.pop(m_stitch.STITCH_OVERLAPS_ATTR, None)
    zarr.consolidate_metadata(path)
    _age()

    reflag = script.read(SAMPLE_FILENAME)
    script.write(SAMPLE_FILENAME, reflag)

    assert reflag.changed
    assert reflag.overlaps is None
    assert m_stitch.STITCH_OVERLAPS_ATTR not in _store().attrs


def test_a_record_waits_for_the_files_detection_lock(pooled):
    """A calibration writes the props under it; an unlocked read-modify-
    write beside it could drop the fit."""
    import threading

    lock = m_io.peak_detection_lock_path(SAMPLE_FILENAME)
    with m_io.zarr_write_lock(lock):
        recording = threading.Thread(
            target=script._record, args=(SAMPLE_FILENAME, script.CURRENT)
        )
        recording.start()
        recording.join(0.5)
        assert recording.is_alive()
        assert script.RECORD_FIELD not in m_io.read_props(SAMPLE_FILENAME)
    recording.join(5)

    assert m_io.read_props(SAMPLE_FILENAME)[script.RECORD_FIELD]["result"] == (
        script.CURRENT
    )


def test_a_file_is_raw_only_where_its_raw_file_is_kept(sample_file_path):
    """An Orbitrap file kept as zarr is detected on another path, which flags
    no peak weak: it is not re-flagged."""
    assert not script._is_raw(SAMPLE_FILENAME)

    with open(f"{sample_file_path}/data.raw", "wb"):
        pass

    assert script._is_raw(SAMPLE_FILENAME)


def test_a_store_holding_a_nan_is_written(pooled):
    """A NaN equals no NaN: compared without that, a store holding one would
    count as moved on every run, and never be flagged."""
    detected = _age()
    snr = _store().signal_to_noise.values.copy()
    snr[0] = np.nan
    m_io.write_peak_variable(SAMPLE_FILENAME, "signal_to_noise", snr)

    reflag = script.read(SAMPLE_FILENAME)
    script.write(SAMPLE_FILENAME, reflag)

    assert np.array_equal(_store().is_weak.values.astype(bool)[1:], detected[1:])


def test_a_file_has_a_store_once_its_peaks_are_detected(
    sample_file_path, acquire, instrument_functions
):
    assert not script.has_store(SAMPLE_FILENAME)

    acquire([(NEG, 1, {ION: FLOOR_HEIGHT})])
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    assert script.has_store(SAMPLE_FILENAME)
