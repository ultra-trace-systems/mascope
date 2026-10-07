"""Peak detection per scan stream, and the peak store it writes.

A raw Orbitrap file whose method runs more than one experiment in a polarity
can have its peaks detected per experiment: each stream averaged over its own
scans, its peaks labelled with it in the store, its timeseries filled over
its own scans. Pooled, an ion only one experiment measures is divided by the
scans of all of them.

No committed file holds more than one stream, so these run on a scripted
acquisition (``ScriptedAcquisition``) read through the real selection keys.
What they pin first is the other direction: a file that is not detected per
stream is detected, stored and filled exactly as it was.
"""

import asyncio
import json
import os

import numpy as np
import pytest
from scripted_acquisition import SAMPLE_FILENAME, ScriptedAcquisition

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.peak as m_peak
import mascope_thermo.streams as m_streams
import mascope_thermo.thermo as m_thermo
from mascope_signal.compute import StalePeakStoreError


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
NEG_HIGH = "FTMS - p NSI Full ms [128.0000-600.0000]"
POS = "FTMS + p NSI Full ms [40.0000-600.0000]"

SETTLING = f"{NEG} R=120000 event=1"
MEASURING = f"{NEG} R=120000 event=2"

# A method of two experiments no filter tells apart. The first runs two scans
# while the source settles; the second is the measurement, four scans, and the
# only one that sees the ion at 188. Both see the reagent ion at 62, at the
# same m/z to the last bit. One measuring scan misses the ion at 188.
TWO_EXPERIMENTS = [
    (NEG, 1, {62.0: 100.0, 125.0: 10.0}),
    (NEG, 1, {62.0: 100.0, 125.0: 10.0}),
    (NEG, 2, {62.0: 1000.0, 188.0: 50.0}),
    (NEG, 2, {62.0: 1000.0, 188.0: 50.0}),
    (NEG, 2, {62.0: 1000.0}),
    (NEG, 2, {62.0: 1000.0, 188.0: 50.0}),
]


@pytest.fixture
def instrument_functions():
    """A Gaussian peak shape. Peak areas follow it; nothing here reads them."""
    x = np.linspace(-5.0, 5.0, 101)
    return {"x": x, "y": np.exp(-(x**2) / 2)}, lambda mz: np.full_like(mz, 1e5)


@pytest.fixture
def acquire(monkeypatch, sample_file_path):
    """Make the test sample a raw Orbitrap file read from scripted scans."""

    def _acquire(scans):
        acquisition = ScriptedAcquisition(scans)
        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _: "orbi_raw"
        )
        monkeypatch.setattr(m_thermo, "open_backend", lambda path: acquisition)
        monkeypatch.setattr(m_streams, "open_backend", lambda path: acquisition)
        return acquisition

    return _acquire


def _store():
    """The whole peak store, weak and satellite peaks included."""
    return m_io.load_peak_data(SAMPLE_FILENAME, drop_bad_peaks=False).compute()


def _props(sample_file_path):
    with open(os.path.join(sample_file_path, ".props")) as f:
        return json.load(f)


def _peaks_of(store, key):
    """``{m/z: summed height}`` of the peaks labelled with one stream."""
    index = m_compute.peak_store_streams(store).index(key)
    labelled = store.isel(mz=np.flatnonzero(store.stream.values == index))
    return {
        round(float(mz), 6): float(height)
        for mz, height in zip(labelled.mz.values, labelled.sum_peak_heights.values)
    }


# -- a file not detected per stream is detected as it always was -----------------


LEGACY_VARIABLES = {
    "peak_areas",
    "peak_heights",
    "is_timeseries_computed",
    "sparsity",
    "sum_peak_areas",
    "sum_peak_heights",
    "signal_to_noise",
    "polarity",
    "is_weak",
    "is_satellite",
}


@pytest.mark.parametrize("per_stream", [False, None])
def test_a_file_nobody_asks_for_per_stream_is_pooled(
    acquire, instrument_functions, sample_file_path, per_stream
):
    """Two experiments in one polarity, averaged into one peak list: what
    every release so far has done, and what the default still does."""
    acquisition = acquire(TWO_EXPERIMENTS)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=per_stream)

    store = _store()
    assert set(store.data_vars) == LEGACY_VARIABLES
    assert "streams" not in store.attrs
    assert m_compute.peak_store_streams(store) == []
    assert store.mz.values.tolist() == [62.0, 125.0, 188.0]
    # Summed over the scans of both experiments
    assert store.sum_peak_heights.values.tolist() == [4200.0, 20.0, 150.0]
    assert store.time.values.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    # Nothing about streams was asked of the file, or written beside it
    assert acquisition.trailer_reads == 0
    assert _props(sample_file_path) == {"mz_calibration": None}


@pytest.mark.parametrize(
    "scans",
    [
        [(NEG, 1, {62.0: 100.0})] * 3,
        [(NEG, 1, {62.0: 100.0}), (POS, 2, {59.0: 70.0})] * 2,
        [(NEG, None, {62.0: 100.0})] * 3,
    ],
    ids=["one-experiment", "one-per-polarity", "no-experiment-recorded"],
)
def test_a_file_with_one_stream_in_each_polarity_is_always_detected_whole(
    acquire, instrument_functions, sample_file_path, scans
):
    """Asked for per stream, such a file has nothing to take apart: its
    polarity is its stream. It is stored exactly as when nobody asks."""
    acquire(scans)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    asked = _store()
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    not_asked = _store()

    assert set(asked.data_vars) == LEGACY_VARIABLES
    assert "streams" not in asked.attrs
    for name in ("sum_peak_heights", "sum_peak_areas", "polarity", "signal_to_noise"):
        assert asked[name].values.tolist() == not_asked[name].values.tolist()
    assert asked.mz.values.tolist() == not_asked.mz.values.tolist()
    assert asked.time.values.tolist() == not_asked.time.values.tolist()
    assert _props(sample_file_path) == {"mz_calibration": None}


# -- a file detected per stream --------------------------------------------------


def test_each_stream_gets_the_peaks_of_its_own_scans(
    acquire, instrument_functions, sample_file_path
):
    acquire(TWO_EXPERIMENTS)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    assert m_compute.peak_store_streams(store) == [SETTLING, MEASURING]
    assert _peaks_of(store, SETTLING) == {62.0: 200.0, 125.0: 20.0}
    # 62.0 again, set just above the settling stream's: see the tie tests below
    assert _peaks_of(store, MEASURING) == {62.0: 4000.0, 188.0: 150.0}
    assert store.polarity.values.tolist() == ["-"] * 4
    assert store.stream.dtype == np.int16


def test_an_ion_one_stream_alone_measures_is_no_longer_diluted(
    acquire, instrument_functions
):
    """The point of detecting per stream. The ion at 188 is measured in
    three of the measuring experiment's four scans, at 50. Pooled, its sum is
    divided by the six scans of both experiments; per stream, by its own
    stream's four."""
    acquire(TWO_EXPERIMENTS)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    pooled = _store()
    pooled_scans = m_compute.get_scan_timestamps(SAMPLE_FILENAME).size
    pooled_average = float(pooled.sum_peak_heights.sel(mz=188.0)) / pooled_scans

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    apart = _store()
    stream_scans = m_compute.get_scan_timestamps(SAMPLE_FILENAME, stream=MEASURING).size
    stream_average = _peaks_of(apart, MEASURING)[188.0] / stream_scans

    assert (pooled_scans, stream_scans) == (6, 4)
    assert pooled_average == pytest.approx(25.0)
    assert stream_average == pytest.approx(37.5)


def test_the_scan_axis_names_the_stream_of_every_scan(acquire, instrument_functions):
    """Experiments that alternate scan by scan: the axis holds every scan in
    time order, each labelled with its own stream."""
    low, high = f"{NEG} R=120000", f"{NEG_HIGH} R=120000"
    acquire([(NEG, 1, {62.0: 10.0}), (NEG_HIGH, 2, {188.0: 5.0})] * 3)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    assert m_compute.peak_store_streams(store) == [low, high]
    assert store.time.values.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert store.scan_stream.values.tolist() == [0, 1, 0, 1, 0, 1]
    assert store.scan_stream.dtype == np.int16


def test_every_stream_of_such_a_file_is_labelled_the_lone_one_too(
    acquire, instrument_functions
):
    """Two experiments in one polarity, and the file is detected per stream.
    The single experiment of the other polarity is then a stream like them:
    no peak of such a file is left without one."""
    acquire(
        [(NEG, 1, {62.0: 10.0})] * 2
        + [(POS, 2, {59.0: 7.0})] * 2
        + [(NEG, 3, {62.0: 30.0})] * 2
    )

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    keys = m_compute.peak_store_streams(store)
    assert keys == [
        f"{NEG} R=120000 event=1",
        f"{POS} R=120000",
        f"{NEG} R=120000 event=3",
    ]
    assert _peaks_of(store, keys[1]) == {59.0: 14.0}
    assert sorted(store.stream.values.tolist()) == [0, 1, 2]
    assert store.scan_stream.values.tolist() == [0, 0, 1, 1, 2, 2]
    by_stream = dict(zip(store.stream.values.tolist(), store.polarity.values.tolist()))
    assert by_stream == {0: "-", 1: "+", 2: "-"}


def test_fragmentation_scans_are_no_stream_of_their_own(acquire, instrument_functions):
    """The peaks of a file detected per stream are its survey streams'. A
    fragmentation stream is reached through its parent, later, and gets no
    peak list here."""
    fragments = "FTMS - p NSI Full ms2 188.0000@hcd30.00 [50.0000-200.0000]"
    acquire(
        [(NEG, 1, {62.0: 10.0})] * 2
        + [(fragments, 2, {96.0: 3.0})] * 2
        + [(NEG, 3, {62.0: 30.0})] * 2
    )

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    assert m_compute.peak_store_streams(store) == [
        f"{NEG} R=120000 event=1",
        f"{NEG} R=120000 event=3",
    ]
    assert 96.0 not in store.mz.values.round(6).tolist()
    # The axis is the survey scans, as a pooled store's is
    assert store.time.values.tolist() == [0.0, 1.0, 4.0, 5.0]
    assert store.scan_stream.values.tolist() == [0, 0, 1, 1]


def test_two_streams_peaks_at_one_mz_are_two_rows(acquire, instrument_functions):
    """Both experiments record the reagent ion at the same double. The store
    finds a row by its m/z, so the two may not share one: the second is set
    a part in a trillion above the first, and each keeps its own intensity."""
    acquire(TWO_EXPERIMENTS)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    store = _store()
    mz = store.mz.values
    assert np.all(np.diff(mz) > 0)
    assert mz[0] == 62.0
    assert mz[1] == 62.0 * (1 + m_peak.MZ_ROW_SEPARATION)
    # Far below a part per million, the scale anything here is compared on
    assert (mz[1] - mz[0]) / mz[0] < 1e-11
    assert sorted(store.sum_peak_heights.values[:2].tolist()) == [200.0, 4000.0]
    assert sorted(store.stream.values[:2].tolist()) == [0, 1]
    # And the store reads back whole: a shared m/z would not select at all
    assert m_io.load_peak_data(SAMPLE_FILENAME).mz.size == 4


APART = 1 + m_peak.MZ_ROW_SEPARATION

# A recalibration by five parts per million, which folds 62.0 and the next
# float above it onto one double: the product is rounded.
FOLDING_FACTOR = 0.9999950000000002


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]),
        ([1.0, 1.0, 2.0], [1.0, APART, 2.0]),
        ([1.0, 1.0, 1.0], [1.0, APART, APART * APART]),
        ([], []),
    ],
    ids=["nothing-tied", "a-pair", "three-of-a-kind", "empty"],
)
def test_tied_rows_are_set_apart(given, expected):
    result = m_peak._strictly_increasing(np.array(given, dtype=np.float64))

    assert result.tolist() == expected
    assert np.all(np.diff(result) > 0)


def test_a_row_too_close_to_a_moved_one_moves_as_well():
    """A tie moves up from the row before it, so it can land on, or pass, a
    peak that was not tied. Every row ends up the separation above the last,
    and the first stays where it was."""
    just_above = np.nextafter(1.0, np.inf)
    result = m_peak._strictly_increasing(np.array([1.0, 1.0, just_above]))

    assert result.tolist() == [1.0, APART, APART * APART]


def test_rows_stay_apart_however_often_the_axis_is_rescaled():
    """An m/z calibration multiplies the stored axis by a factor near one,
    again at every recalibration. Rows one float apart do not survive that,
    which is why a tie is moved further than it needs to be told apart."""
    reagent = 62.0
    assert np.nextafter(reagent, np.inf) * FOLDING_FACTOR == reagent * FOLDING_FACTOR

    axis = m_peak._strictly_increasing(np.array([reagent, reagent, reagent]))
    for factor in [FOLDING_FACTOR, 1 / FOLDING_FACTOR, 1 + 3e-6, 1 - 7e-6] * 250:
        axis = axis * factor
        assert np.all(np.diff(axis) > 0)
    # A thousand rescales on, and each could have cost a pair one float: the
    # rows are still thousands of floats apart.
    assert np.all(np.diff(axis) > 4000 * np.spacing(axis[:-1]))


def test_a_calibrated_per_stream_store_still_tells_its_rows_apart(
    acquire, instrument_functions
):
    """What an Orbitrap m/z calibration does to a store: the axis rescaled in
    place, the factor recorded beside the file. The two rows at the reagent
    ion come through as two, each still filled over its own stream."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    m_io.update_zarr_array_coord(
        SAMPLE_FILENAME,
        "peak_timeseries",
        "mz",
        _store().mz.values * FOLDING_FACTOR,
    )
    m_io.update_props(
        SAMPLE_FILENAME,
        {"mz_calibration": {"par": {"calibration_factor": FOLDING_FACTOR}}},
    )

    calibrated = _store()
    assert np.all(np.diff(calibrated.mz.values) > 0)
    assert m_compute.peak_store_streams(calibrated) == [SETTLING, MEASURING]
    filled = _fill(calibrated.mz.values)
    reagent = filled.isel(mz=[0, 1])
    assert reagent.stream.values.tolist() == [0, 1]
    nan = pytest.approx(np.nan, nan_ok=True)
    assert reagent.peak_heights.values.tolist() == [
        [100.0, 100.0, nan, nan, nan, nan],
        [nan, nan, 1000.0, 1000.0, 1000.0, 1000.0],
    ]


# -- a file detected whole whose polarities share an m/z ---------------------------

# A file that switches polarity, one experiment in each, detected whole. Both
# polarities record an ion at the same m/z to the last bit, and neither peak
# is weak or a satellite: both are kept.
SHARED_ACROSS_POLARITIES = [
    (POS, 1, {59.0: 70.0, 62.0: 40.0}),
    (NEG, 2, {62.0: 100.0, 125.0: 10.0}),
] * 2


def test_two_polarities_peaks_at_one_mz_are_two_rows(acquire, instrument_functions):
    """A pooled store holds both polarities' peak lists on one axis, as a
    per-stream store holds its streams', and is found by m/z the same way.
    The negative peak is set a part in a trillion above the positive one, and
    nothing else on the axis moves."""
    acquire(SHARED_ACROSS_POLARITIES)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    store = _store()
    assert store.mz.values.tolist() == [59.0, 62.0, 62.0 * APART, 125.0]
    assert store.polarity.values.tolist() == ["+", "+", "-", "-"]
    assert store.sum_peak_heights.values.tolist() == [140.0, 80.0, 200.0, 20.0]
    assert not (store.is_weak | store.is_satellite).values.any()
    # Pooled all the same: nothing about streams is written
    assert set(store.data_vars) == LEGACY_VARIABLES
    assert m_compute.peak_store_streams(store) == []


def test_two_kept_peaks_at_one_mz_are_read_and_filled_each_on_its_own_row(
    acquire, instrument_functions
):
    """With both peaks kept, the axis a load hands out held the m/z twice,
    and nothing could be selected from it by m/z, whatever was asked for:
    not these two peaks, and not any other peak of the file."""
    acquire(SHARED_ACROSS_POLARITIES)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    assert m_io.load_peak_data(SAMPLE_FILENAME).mz.size == 4
    positive, negative = _store().mz.values[[1, 2]]

    assert _fill([125.0]).polarity.values.tolist() == ["-"]
    filled = _fill([positive, negative])

    assert filled.polarity.values.tolist() == ["+", "-"]
    assert filled.is_timeseries_computed.values.all()
    # Each scaled to its own summed intensity: the fill found its own row
    np.testing.assert_allclose(
        np.nansum(filled.peak_heights.values, axis=1), [80.0, 200.0]
    )
    assert _store().is_timeseries_computed.values.tolist() == [False, True, True, True]


def test_satellites_are_judged_within_a_stream(
    acquire, instrument_functions, monkeypatch
):
    """A satellite is a sidelobe of a strong peak of its own spectrum. Judged
    over the whole axis, a real peak of one experiment would be read as the
    sidelobe of another experiment's strong one."""
    acquire(TWO_EXPERIMENTS)
    judged = []

    def flag(peaks):
        judged.append(peaks["mz"].round(6).tolist())
        return peaks.assign(is_satellite_peak=peaks["mz"].round(6) == 125.0)

    monkeypatch.setattr(m_peak, "flag_satellite_peaks", flag)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    apart = _store()

    assert judged == [[62.0, 125.0], [62.0, 188.0]]
    assert dict(
        zip(apart.mz.round(6).values.tolist(), apart.is_satellite.values.tolist())
    ) == {
        62.0: False,
        125.0: True,
        188.0: False,
    }

    judged.clear()
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    assert judged == [[62.0, 125.0, 188.0]]


# -- what a rebuild of the store goes by -------------------------------------------

DECIDED_PER_STREAM = {"mz_calibration": None, "peaks_per_stream": True}

# The scans of TWO_EXPERIMENTS read back as one experiment, as another reader
# or another keying might: nothing in them is left to detect apart.
AS_ONE_EXPERIMENT = [(text, 1, peaks) for text, _event, peaks in TWO_EXPERIMENTS]


def test_the_decision_is_recorded_beside_the_file(
    acquire, instrument_functions, sample_file_path
):
    """One bit of it, and no stream: the keys a store's labels index are the
    store's own, and a second list beside the file could disagree with them
    exactly where it matters, between a record written and a store whose
    write was cut short."""
    acquire(TWO_EXPERIMENTS)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    assert _props(sample_file_path) == DECIDED_PER_STREAM


def test_a_store_whose_write_fails_is_rebuilt_as_it_was_being_built(
    acquire, instrument_functions, sample_file_path, monkeypatch
):
    """The record is the decision and the store follows it, so the decision
    is written first. Recorded after the store, a conversion cut short
    between the two would leave a per-stream store that the next rebuild pools."""
    acquire(TWO_EXPERIMENTS)

    async def fails(*args, **kwargs):
        raise OSError("No space left on device")

    with monkeypatch.context() as patched:
        patched.setattr(m_io, "write_peaks", fails)
        with pytest.raises(OSError):
            m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    assert _props(sample_file_path) == DECIDED_PER_STREAM
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    assert m_compute.peak_store_streams(_store()) == [SETTLING, MEASURING]


def test_a_rebuild_keeps_a_per_stream_store_per_stream(acquire, instrument_functions):
    """Re-detecting a file's peaks must not change what its samples mean, so
    a rebuild goes by how the store was built, whatever anyone would decide
    for a new file."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert m_compute.peak_store_streams(_store()) == [SETTLING, MEASURING]


def test_a_rebuild_keeps_a_pooled_store_pooled(
    acquire, instrument_functions, sample_file_path
):
    """The other way round, which is every file converted before streams
    could be read apart: nothing recorded means pooled."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert m_compute.peak_store_streams(_store()) == []
    assert _props(sample_file_path) == {"mz_calibration": None}


def test_only_an_explicit_decision_changes_what_a_store_is(
    acquire, instrument_functions, sample_file_path
):
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    assert m_compute.peak_store_streams(_store()) == []
    # Recorded as decided against, so the next rebuild leaves it pooled
    assert _props(sample_file_path) == {
        "mz_calibration": None,
        "peaks_per_stream": False,
    }
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)
    assert m_compute.peak_store_streams(_store()) == []


def test_the_decision_holds_through_a_rebuild_that_finds_one_stream(
    acquire, instrument_functions, sample_file_path
):
    """A rebuild is handed no decision, so it never writes the record. Where
    the file reads back one stream in each polarity, the store is rebuilt
    pooled, because there is nothing to detect apart, and the decision
    stands: once the file reads back its streams, the store is per stream
    again. Written by the rebuild, the record would say pooled for good, with
    no one having decided it."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    acquire(AS_ONE_EXPERIMENT)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert m_compute.peak_store_streams(_store()) == []
    assert _props(sample_file_path) == DECIDED_PER_STREAM

    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert m_compute.peak_store_streams(_store()) == [SETTLING, MEASURING]
    assert _props(sample_file_path) == DECIDED_PER_STREAM


def test_a_rebuild_writes_nothing_beside_the_file(
    acquire, instrument_functions, monkeypatch
):
    """Whatever the store was built by, and whatever the file reads back."""
    written = []
    update_props = m_io.update_props

    def recording(filename, props):
        written.append(props)
        return update_props(filename, props)

    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    monkeypatch.setattr(m_io, "update_props", recording)

    for scans in (TWO_EXPERIMENTS, AS_ONE_EXPERIMENT, TWO_EXPERIMENTS):
        acquire(scans)
        m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert written == []


def test_an_explicit_decision_replaces_the_one_recorded(
    acquire, instrument_functions, sample_file_path
):
    """Once a file carries a decision, an explicit one replaces it whatever
    the file reads back at the time: asked for per stream while it reads back
    one stream, it is pooled and decided per stream."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)

    acquire(AS_ONE_EXPERIMENT)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    assert m_compute.peak_store_streams(_store()) == []
    assert _props(sample_file_path) == DECIDED_PER_STREAM

    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    assert m_compute.peak_store_streams(_store()) == [SETTLING, MEASURING]


@pytest.mark.parametrize(
    ("leave_out", "missing"),
    [
        ("streams", "missing the stream keys\\."),
        ("stream", "missing the stream of each peak\\."),
        ("scan_stream", "missing the stream of each scan\\."),
        (
            ("stream", "scan_stream"),
            "missing the stream of each peak and the stream of each scan\\.",
        ),
    ],
    ids=["no-keys", "no-peak-labels", "no-scan-labels", "keys-alone"],
)
def test_a_store_that_is_only_partly_labelled_is_refused(
    acquire, instrument_functions, leave_out, missing
):
    """The keys and the two labels only mean something together. Read as
    pooled instead, a per-stream store's peak would be filled over the scans
    of every stream; so a dataset that lost part of them on the way is
    refused."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    store = _store()

    if leave_out == "streams":
        partly = store.copy()
        del partly.attrs["streams"]
    else:
        partly = store.drop_vars(leave_out)

    with pytest.raises(ValueError, match=missing):
        m_compute.peak_store_streams(partly)


def test_only_a_raw_orbitrap_file_has_peak_streams(monkeypatch, sample_file_path):
    for sample_type in ("tof_h5", "tof_zarr", "orbi_zarr"):
        monkeypatch.setattr(
            m_compute.m_name, "get_sample_file_type", lambda _, t=sample_type: t
        )
        assert m_compute.get_peak_streams(SAMPLE_FILENAME) == []


# -- a file whose streams cannot be read -------------------------------------------


def _unreadable(scan_number):
    raise OSError("the scan trailer could not be read")


def test_a_file_whose_streams_cannot_be_read_is_not_detected_whole(
    acquire, instrument_functions, sample_file_path, monkeypatch
):
    """Asked for per stream, a file's streams have to be read, and where they
    cannot be the detection fails. Detected whole instead, a file of several
    experiments would be pooled with nothing to show for it, and this file,
    which has one, cannot be told from such a file while its streams are
    unread. Nobody asking, the streams are never read and it is detected."""
    acquisition = acquire([(NEG, 1, {62.0: 100.0, 125.0: 10.0})] * 3)
    monkeypatch.setattr(acquisition, "scan_trailer", _unreadable)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=False)
    assert _store().mz.values.tolist() == [62.0, 125.0]

    with pytest.raises(m_peak.PeakDetectionError, match="scan streams") as failed:
        m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    assert isinstance(failed.value.__cause__, OSError)
    assert SAMPLE_FILENAME in str(failed.value)
    # The attempt wrote nothing: no decision, and the store is the one before
    assert _props(sample_file_path) == {"mz_calibration": None}
    assert _store().mz.values.tolist() == [62.0, 125.0]


def test_a_rebuild_that_cannot_read_the_streams_leaves_the_store_as_it_was(
    acquire, instrument_functions, sample_file_path, monkeypatch
):
    """A rebuild of a per-stream store goes by its record, so it asks for the
    file's streams too. Pooling it because they could not be read would
    change what the store is with no decision made."""
    acquisition = acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    before, recorded = _store(), _props(sample_file_path)
    monkeypatch.setattr(acquisition, "scan_trailer", _unreadable)

    with pytest.raises(m_peak.PeakDetectionError, match="scan streams"):
        m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    after = _store()
    assert m_compute.peak_store_streams(after) == [SETTLING, MEASURING]
    assert after.mz.values.tolist() == before.mz.values.tolist()
    assert after.stream.values.tolist() == before.stream.values.tolist()
    assert _props(sample_file_path) == recorded


# -- timeseries of a per-stream store ------------------------------------------------


def _fill(mzs):
    return asyncio.run(
        m_compute.load_peak_timeseries(SAMPLE_FILENAME, list(mzs))
    ).compute()


def test_a_peaks_timeseries_covers_the_scans_of_its_own_stream(
    acquire, instrument_functions
):
    """On the other experiment's scans a peak holds no value: the instrument
    was measuring something else, which is not the same as measuring this ion
    and finding none."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    stored = _store()

    filled = _fill(stored.mz.values)

    assert filled.is_timeseries_computed.values.all()
    heights = {
        (int(stream), round(float(mz), 6)): row
        for stream, mz, row in zip(
            filled.stream.values, filled.mz.values, filled.peak_heights.values.tolist()
        )
    }
    nan = pytest.approx(np.nan, nan_ok=True)
    assert heights[(0, 62.0)] == [100.0, 100.0, nan, nan, nan, nan]
    assert heights[(0, 125.0)] == [10.0, 10.0, nan, nan, nan, nan]
    assert heights[(1, 62.0)] == [nan, nan, 1000.0, 1000.0, 1000.0, 1000.0]
    assert heights[(1, 188.0)] == [nan, nan, 50.0, 50.0, 0.0, 50.0]


def test_a_filled_peak_sums_to_what_peak_detection_measured(
    acquire, instrument_functions
):
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    filled = _fill(_store().mz.values)

    np.testing.assert_allclose(
        np.nansum(filled.peak_heights.values, axis=1), filled.sum_peak_heights.values
    )
    np.testing.assert_allclose(
        np.nansum(filled.peak_areas.values, axis=1), filled.sum_peak_areas.values
    )


def test_sparsity_counts_the_scans_of_the_peaks_own_stream(
    acquire, instrument_functions
):
    """The ion at 188 is missing from one of its stream's four scans. Counted
    over the file's six, the two settling scans would read as misses too."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    filled = _fill(_store().mz.values)

    sparsity = dict(
        zip(filled.mz.round(6).values.tolist(), filled.sparsity.values.tolist())
    )
    assert sparsity[188.0] == pytest.approx(0.25)
    assert sparsity[125.0] == 0.0


def test_only_the_peaks_asked_for_are_filled(acquire, instrument_functions):
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    _fill([188.0])

    computed = dict(
        zip(
            _store().mz.round(6).values.tolist(),
            _store().is_timeseries_computed.values.tolist(),
        )
    )
    assert computed == {62.0: False, 125.0: False, 188.0: True}


def test_an_mz_two_streams_hold_is_answered_by_the_row_that_kept_it(
    acquire, instrument_functions
):
    """``load_peak_timeseries`` takes no stream: it resolves an asked m/z to
    the nearest kept peak, of whichever stream. Both experiments hold the
    reagent ion at 62.0. The row that kept that m/z answers, filled over its
    own stream, and the other stream's row, set just above it, is found only
    by its own m/z. It is what a consumer that asks by stream has to change,
    and it is pinned so that the change is made on purpose."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    set_above = float(_store().mz.values[1])
    assert set_above == 62.0 * (1 + m_peak.MZ_ROW_SEPARATION)

    answered = _fill([62.0])

    assert answered.mz.values.tolist() == [62.0]
    assert answered.stream.values.tolist() == [0]
    nan = pytest.approx(np.nan, nan_ok=True)
    assert answered.peak_heights.values.tolist() == [[100.0, 100.0, nan, nan, nan, nan]]
    assert _fill([set_above]).stream.values.tolist() == [1]


def test_a_fill_a_calibration_overtakes_is_not_written_to_another_streams_row(
    acquire, instrument_functions, monkeypatch
):
    """An m/z calibration applied while a fill reads the file back: the axis
    rescaled in place, the factor recorded beside the file. The fill carries
    the m/z values of the axis as it was. Of the two rows at the reagent ion,
    the first at or above the upper one's old m/z is now the lower one, and
    within a tolerance the measuring stream's timeseries was written there,
    over the settling stream's. The store refuses the fill instead, and the
    same peaks are filled again on the axis as it has become. They are found
    there by their ids: the m/z values asked for are labels of the old axis,
    and to the nearest row both of the reagent ion's now name the lower one."""
    acquisition = acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    before = _store().mz.values
    rescaling = 1 + 3e-6
    read = acquisition.xic
    reads = []

    def read_while_calibrated(*args, **kwargs):
        reads.append(args)
        if len(reads) == 1:
            m_io.update_zarr_array_coord(
                SAMPLE_FILENAME, "peak_timeseries", "mz", before * rescaling
            )
            m_io.update_props(
                SAMPLE_FILENAME,
                {"mz_calibration": {"par": {"calibration_factor": rescaling}}},
            )
        return read(*args, **kwargs)

    monkeypatch.setattr(acquisition, "xic", read_while_calibrated)

    _fill(before)

    stored = _store()
    assert stored.mz.values.tolist() == (before * rescaling).tolist()
    assert stored.is_timeseries_computed.values.all()
    reagent = stored.isel(mz=[0, 1])
    assert reagent.stream.values.tolist() == [0, 1]
    nan = pytest.approx(np.nan, nan_ok=True)
    assert reagent.peak_heights.values.tolist() == [
        [100.0, 100.0, nan, nan, nan, nan],
        [nan, nan, 1000.0, 1000.0, 1000.0, 1000.0],
    ]


def test_a_per_stream_store_just_built_reads_back(acquire, instrument_functions):
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)

    asyncio.run(m_compute.check_peak_store(SAMPLE_FILENAME))


def test_a_stream_the_file_no_longer_reads_back_is_stale(acquire, instrument_functions):
    """The store was built with four measuring scans. A file that now reads
    back three for that stream is refused for the peaks of that stream, and
    the other stream, whose scans still agree, fills as before."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    stored = _store()
    settling = stored.mz.values[stored.stream.values == 0]
    measuring = stored.mz.values[stored.stream.values == 1]

    acquire(TWO_EXPERIMENTS[:-1])

    with pytest.raises(StalePeakStoreError):
        asyncio.run(m_compute.check_peak_store(SAMPLE_FILENAME))
    with pytest.raises(StalePeakStoreError):
        _fill(measuring)
    assert _fill(settling).is_timeseries_computed.values.all()


def test_a_scan_that_changed_streams_makes_both_stale(acquire, instrument_functions):
    """The file still reads back the same six scans at the same times, so a
    check of the axis as a whole would pass. But the third now belongs to
    the settling experiment, and each stream's peaks were summed over the
    scans it had: the store is read back stream by stream."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    stored = _store()

    moved = list(TWO_EXPERIMENTS)
    moved[2] = (NEG, 1, moved[2][2])
    acquire(moved)

    assert m_compute.get_scan_timestamps(SAMPLE_FILENAME).tolist() == (
        stored.time.values.tolist()
    )
    with pytest.raises(StalePeakStoreError):
        asyncio.run(m_compute.check_peak_store(SAMPLE_FILENAME))
    with pytest.raises(StalePeakStoreError):
        _fill(stored.mz.values[stored.stream.values == 0])


def test_a_stream_key_the_file_no_longer_holds_is_stale(
    acquire, instrument_functions, sample_file_path
):
    """A store names its streams by the keys they had when it was built, and
    a key is a name the reader computes, which can change under a store with
    the code that keys. The reader refuses a key the file holds no stream
    under. For a store that is the stale case and not a fault, so that
    whoever meets it asks for the rebuild, which keys the streams afresh."""
    acquire(TWO_EXPERIMENTS)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    stored = _store()

    # The same scans, their experiments numbered otherwise: neither key is left
    acquire([(text, event + 2, peaks) for text, event, peaks in TWO_EXPERIMENTS])

    with pytest.raises(StalePeakStoreError, match="no stream under that key") as stale:
        asyncio.run(m_compute.check_peak_store(SAMPLE_FILENAME))
    assert isinstance(stale.value.__cause__, m_thermo.UnknownStreamError)
    assert SETTLING in str(stale.value)
    with pytest.raises(StalePeakStoreError, match="no stream under that key"):
        _fill(stored.mz.values)

    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions)

    rekeyed = [f"{NEG} R=120000 event=3", f"{NEG} R=120000 event=4"]
    assert m_compute.peak_store_streams(_store()) == rekeyed
    # The record holds no key to go stale with them
    assert _props(sample_file_path) == DECIDED_PER_STREAM
    asyncio.run(m_compute.check_peak_store(SAMPLE_FILENAME))


def test_a_stream_that_kept_no_peak_is_not_read_back(
    acquire, instrument_functions, monkeypatch
):
    """The check reads the file back for the peaks matching could fill, one
    of each stream, and a stream all of whose peaks are flagged has none. A
    key that names nothing any more is then not this check's to report for
    it: there is no m/z to read the stream back for, and nothing of it that
    a fill would touch."""
    acquire(TWO_EXPERIMENTS)

    def flag(peaks):
        """Every peak of the settling stream a satellite, none of the other."""
        settling = bool((peaks["mz"].round(6) == 125.0).any())
        return peaks.assign(is_satellite_peak=settling)

    monkeypatch.setattr(m_peak, "flag_satellite_peaks", flag)
    m_peak.compute_peaks(SAMPLE_FILENAME, instrument_functions, per_stream=True)
    kept = m_io.load_peak_data(SAMPLE_FILENAME).compute()
    assert set(kept.stream.values.tolist()) == {1}
    assert m_compute.peak_store_streams(kept) == [SETTLING, MEASURING]

    # The settling experiment numbered otherwise: its key is gone, and the
    # measuring one's is the key it was.
    acquire(
        [
            (text, 3 if event == 1 else event, peaks)
            for text, event, peaks in TWO_EXPERIMENTS
        ]
    )
    keys = m_streams.scan_stream_keys(m_streams.open_backend(""))
    assert SETTLING not in keys
    assert MEASURING in keys

    asyncio.run(m_compute.check_peak_store(SAMPLE_FILENAME))


def test_the_scripted_reader_refuses_a_key_as_the_real_one_does(acquire):
    """What the stale-key test rests on: asked for a stream it does not hold,
    the scripted acquisition answers as both reader backends do, and an
    empty selection of a stream it does hold stays an empty selection."""
    acquisition = acquire(TWO_EXPERIMENTS)

    with pytest.raises(m_thermo.UnknownStreamError) as refused:
        acquisition.scan_times(stream="no such stream")
    assert refused.value.held == [SETTLING, MEASURING]

    with pytest.raises(m_thermo.NoScansFoundError):
        acquisition.scan_times(stream=MEASURING, t_max=0.5)
