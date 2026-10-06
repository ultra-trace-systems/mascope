"""Selecting one scan stream (the ``stream`` argument of the reader).

A stream is selected by its key, the one ``mascope_thermo.streams`` reports,
so the scans selected for a stream are the ones the census counted into it.
No committed file holds more than one stream, so a scripted acquisition is
driven through **both** reader backends - OpenTFRaw over a scripted
``RawFile``, the Thermo library over a scripted adapter, neither needing its
real reader - and every selection is asserted on each. The committed sample
files then pin, through each backend available, that a single-stream file's
only stream selects what no stream selects.
"""

import re
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import (
    _SCAN_INDEX_UNSET,
    OpenTFRawBackend,
    ThermoBackend,
    open_backend,
)
from mascope_thermo.lib import thermo_available
from mascope_thermo.runtime import runtime
from mascope_thermo.streams import scan_stream_keys, scan_streams
from mascope_thermo.thermo import NoScansFoundError, UnknownStreamError


NEG_LOW = "FTMS - p NSI Full ms [40.0000-160.0000]"
NEG_HIGH = "FTMS - p NSI Full ms [128.0000-600.0000]"
POS_LOW = "FTMS + p NSI Full ms [40.0000-160.0000]"
MS2 = "FTMS - p NSI Full ms2 300.0000@hcd30.00 [50.0000-310.0000]"

LOW = f"{NEG_LOW} R=120000"
HIGH = f"{NEG_HIGH} R=120000"


def _scan(text, event=None, tic=1e6, resolution=120000, segment=1):
    """One scripted scan: its filter, its scan event (None: none recorded,
    and then no segment either), its TIC, its FT resolution and its method
    segment. Scans are one second apart."""
    return SimpleNamespace(
        filter=text,
        event=event,
        tic=tic,
        resolution=resolution,
        segment=None if event is None else segment,
    )


class _ScriptedOpenTFRaw:
    """The slice of ``opentfraw.RawFile`` scan selection reads."""

    def __init__(self, scans):
        self._scans = scans
        self.trailer_reads = 0

    def scan_table(self):
        numbers = list(range(1, len(self._scans) + 1))
        return {
            "scan_number": numbers,
            "scan_event": [
                _SCAN_INDEX_UNSET if s.event is None else s.event - 1
                for s in self._scans
            ],
            "scan_segment": [
                _SCAN_INDEX_UNSET if s.segment is None else s.segment - 1
                for s in self._scans
            ],
            "data_size": [0] * len(numbers),
            "ms_level": [2 if " ms2 " in s.filter else 1 for s in self._scans],
            "polarity": ["+" if " + " in s.filter else "-" for s in self._scans],
            "retention_time": [(n - 1) / 60 for n in numbers],
            "filter_string": [s.filter for s in self._scans],
            "total_ion_current": [s.tic for s in self._scans],
            "base_peak_mz": [100.0] * len(numbers),
            "base_peak_intensity": [1.0] * len(numbers),
            "low_mz": [40.0] * len(numbers),
            "high_mz": [600.0] * len(numbers),
        }

    def scan_parameters(self, scan_number):
        self.trailer_reads += 1
        return {"FT Resolution:": self._scans[scan_number - 1].resolution}


class _Text:
    """A .NET enum or filter, as far as ``ToString()`` goes."""

    def __init__(self, text):
        self._text = text

    def ToString(self):  # noqa: N802
        return self._text


class _ScriptedThermoRaw:
    """The slice of the Thermo ``RawFileReaderAdapter`` scan selection reads."""

    def __init__(self, scans):
        self._scans = scans
        self.trailer_reads = 0
        self.RunHeaderEx = SimpleNamespace(
            SpectraCount=len(scans),
            StartTime=0.0,
            EndTime=max(len(scans) - 1, 0) / 60,
        )

    def GetFilterForScanNumber(self, number):  # noqa: N802
        scan = self._scans[number - 1]
        scan_filter = _Text(scan.filter)
        scan_filter.Polarity = _Text("Positive" if " + " in scan.filter else "Negative")
        scan_filter.MSOrder = _Text("Ms2" if " ms2 " in scan.filter else "Ms")
        return scan_filter

    def GetScanStatsForScanNumber(self, number):  # noqa: N802
        scan = self._scans[number - 1]
        return SimpleNamespace(
            StartTime=(number - 1) / 60,
            TIC=scan.tic,
            # The Thermo library reports a segment never set as 0.
            SegmentNumber=0 if scan.segment is None else scan.segment - 1,
            ScanEventNumber=-1 if scan.event is None else scan.event - 1,
        )

    def GetTrailerExtraInformation(self, number):  # noqa: N802
        self.trailer_reads += 1
        return SimpleNamespace(
            Labels=["FT Resolution:"],
            Values=[str(self._scans[number - 1].resolution)],
        )


def _opentfraw(scans):
    reader = OpenTFRawBackend("unused.raw")
    reader._raw = _ScriptedOpenTFRaw(scans)
    return reader


def _thermo(scans):
    reader = ThermoBackend("unused.raw")
    reader._raw = _ScriptedThermoRaw(scans)
    return reader


@pytest.fixture(params=[_opentfraw, _thermo], ids=["opentfraw", "thermo"])
def scripted(request):
    """Build a reader backend over scripted scans, once per backend."""
    return request.param


# Two ranges alternating in one polarity, then a positive scan and a
# fragmentation scan: scans 1, 3, 5 are LOW and 2, 4, 6 are HIGH.
ALTERNATING = [_scan(NEG_LOW, 1), _scan(NEG_HIGH, 2)] * 3 + [
    _scan(POS_LOW, 3),
    _scan(MS2, 4, resolution=15000),
]


def test_a_stream_selects_its_own_scans(scripted):
    reader = scripted(ALTERNATING)

    assert reader.scan_indices(stream=LOW) == [1, 3, 5]
    assert reader.scan_indices(stream=HIGH) == [2, 4, 6]
    assert reader.scan_indices(stream=f"{POS_LOW} R=120000") == [7]


def test_no_stream_selects_as_before(scripted):
    reader = scripted(ALTERNATING)

    assert reader.scan_indices() == [1, 2, 3, 4, 5, 6, 7]
    assert reader.scan_indices(polarity="-") == [1, 2, 3, 4, 5, 6]
    assert reader.scan_indices(ms_type=None) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_the_keys_selection_compares_are_the_census_keys(scripted):
    """One function names the stream of every scan, for the census and for
    selection alike: each stream the census reports selects exactly the scans
    it counted."""
    reader = scripted(ALTERNATING)

    keys = scan_stream_keys(reader)
    for stream in scan_streams(reader):
        ms_type = "Ms" if stream["signature"]["ms_order"] == 1 else "Ms2"
        selected = reader.scan_indices(ms_type=ms_type, stream=stream["key"])
        assert selected == [n for n, key in enumerate(keys, 1) if key == stream["key"]]
        assert len(selected) == stream["scans"]


def test_two_experiments_under_one_filter_are_selected_apart(scripted):
    """What the scan event is for: no filter, polarity or MS order tells
    these two apart."""
    reader = scripted([_scan(NEG_LOW, 1)] * 3 + [_scan(NEG_LOW, 2)] * 4)

    assert reader.scan_indices(stream=f"{LOW} event=1") == [1, 2, 3]
    assert reader.scan_indices(stream=f"{LOW} event=2") == [4, 5, 6, 7]
    # The bare signature names no stream of this file - though it is the
    # key of the first experiment in a run stopped before the second, which
    # is how a key that was right for one file is stale for the next.
    with pytest.raises(UnknownStreamError) as raised:
        reader.scan_indices(stream=LOW)
    assert raised.value.held == [f"{LOW} event=1", f"{LOW} event=2"]


def test_the_same_event_in_two_segments_is_selected_apart(scripted):
    """A method numbers its scan events within each segment, and so does the
    key selection compares."""
    reader = scripted(
        [_scan(NEG_LOW, 1, segment=1)] * 2 + [_scan(NEG_LOW, 1, segment=2)] * 3
    )

    assert reader.scan_indices(stream=f"{LOW} event=1") == [1, 2]
    assert reader.scan_indices(stream=f"{LOW} segment=2 event=1") == [3, 4, 5]


def test_scans_that_record_no_experiment_are_selected_by_their_signature(scripted):
    """A file acquired with no method loaded: the key is the signature."""
    reader = scripted([_scan(NEG_LOW)] * 2 + [_scan(NEG_HIGH)] * 2)

    assert reader.scan_indices(stream=LOW) == [1, 2]
    assert reader.scan_indices(stream=HIGH) == [3, 4]


def test_a_stream_is_selected_within_the_other_filters(scripted):
    reader = scripted(ALTERNATING)

    # Scans are a second apart: 2 s to 4 s holds scans 3, 4 and 5.
    assert reader.scan_indices(t_min=2.0, t_max=4.0, stream=LOW) == [3, 5]
    assert reader.scan_indices(polarity="-", stream=LOW) == [1, 3, 5]
    # A key of the other polarity, or of another MS order, selects nothing.
    with pytest.raises(NoScansFoundError):
        reader.scan_indices(polarity="+", stream=LOW)
    with pytest.raises(NoScansFoundError):
        reader.scan_indices(ms_type="Ms2", stream=LOW)


def test_a_fragmentation_stream_needs_its_scan_type(scripted):
    reader = scripted(ALTERNATING)
    key = f"{MS2} R=15000"

    assert reader.scan_indices(ms_type="Ms2", stream=key) == [8]
    assert reader.scan_indices(ms_type=None, stream=key) == [8]
    with pytest.raises(NoScansFoundError):
        reader.scan_indices(stream=key)


def test_a_key_the_file_does_not_hold_is_not_an_empty_selection(scripted):
    """A stored key can be stale without being mistyped: it is a name, and
    the name of an experiment depends on what else its file holds, on the
    backend that rendered its filter and on the version of the keying. Read
    as "no scans", a stale key would be taken by the code above the reader
    for a polarity the file does not carry, or an empty window."""
    reader = scripted(ALTERNATING)

    with pytest.raises(UnknownStreamError) as raised:
        reader.scan_indices(stream="no such stream")

    assert not isinstance(raised.value, (NoScansFoundError, ValueError))
    assert raised.value.stream == "no such stream"
    # The keys the file does hold, in the order its streams first appear
    assert raised.value.held == [
        LOW,
        HIGH,
        f"{POS_LOW} R=120000",
        f"{MS2} R=15000",
    ]
    message = str(raised.value)
    assert "'no such stream'" in message
    assert all(key in message for key in raised.value.held)


def test_a_key_the_file_does_not_hold_is_refused_whatever_else_is_asked(scripted):
    """Not only where the other filters would have left something to select:
    the key is wrong before the window or the polarity is."""
    reader = scripted(ALTERNATING)

    # The last window lies between two scans and holds none of any stream.
    for filters in (
        {"polarity": "+"},
        {"ms_type": "Ms2"},
        {"t_min": 6.4, "t_max": 6.6},
    ):
        with pytest.raises(UnknownStreamError):
            reader.scan_indices(stream="no such stream", **filters)


def test_a_real_stream_with_no_scan_in_the_window_is_an_empty_selection(scripted):
    """LOW holds scans 1, 3 and 5, at 0, 2 and 4 s. A window after them holds
    none of it, which is an empty selection and says so by the stream's name:
    the stream exists, and has nothing there."""
    reader = scripted(ALTERNATING)

    with pytest.raises(NoScansFoundError, match=re.escape(f"stream='{LOW}'")):
        reader.scan_indices(t_min=4.5, t_max=7.0, stream=LOW)


def test_a_file_with_no_scans_has_no_stream_to_select(scripted):
    """Such a file holds no key at all, so no key is unknown to it: a stream
    asked of it is the empty selection the scanless-file handling is built
    on, and not an index into an empty mask."""
    with pytest.raises(NoScansFoundError):
        scripted([]).scan_indices(stream=LOW)


def test_a_key_selects_under_the_rendering_that_reported_it(scripted):
    """The two real backends render some filter tokens differently, the
    source fragmentation among them, and the token is part of the key. So a
    key belongs to the census it came from. Here a file that records no
    experiment holds two scans acquired with in-source CID and three without:
    read with the token, the plain key is the three; read without it, all
    five pool under that key, and the other key does not exist."""
    with_sid = "FTMS - p NSI sid=20.00 Full ms [40.0000-160.0000]"
    kept = scripted([_scan(with_sid)] * 2 + [_scan(NEG_LOW)] * 3)
    dropped = scripted([_scan(NEG_LOW)] * 5)
    sid_key = scan_stream_keys(kept)[0]
    assert sid_key != LOW

    assert kept.scan_indices(stream=LOW) == [3, 4, 5]
    assert kept.scan_indices(stream=sid_key) == [1, 2]
    assert dropped.scan_indices(stream=LOW) == [1, 2, 3, 4, 5]
    with pytest.raises(UnknownStreamError):
        dropped.scan_indices(stream=sid_key)


def test_a_selection_without_a_stream_names_none(scripted):
    with pytest.raises(NoScansFoundError) as raised:
        scripted(ALTERNATING).scan_indices(polarity="+", ms_type="Ms2")
    assert "stream" not in str(raised.value)


@pytest.mark.parametrize(
    "selected",
    [
        lambda reader, stream: reader.scan_indices(stream=stream),
        lambda reader, stream: [round(t) + 1 for t in reader.scan_times(stream=stream)],
        lambda reader, stream: [
            round(t) + 1 for t in reader.tic_per_scan(stream=stream)[0]
        ],
        lambda reader, stream: list(
            reader.scan_acquisition_settings(stream=stream)["settings"]
        ),
    ],
    ids=["scan_indices", "scan_times", "tic_per_scan", "scan_acquisition_settings"],
)
def test_every_selecting_method_takes_the_stream(scripted, selected):
    reader = scripted(ALTERNATING)

    assert selected(reader, LOW) == [1, 3, 5]
    assert selected(reader, HIGH) == [2, 4, 6]


def test_the_tic_of_a_stream_is_its_own_scans(scripted):
    reader = scripted(
        [_scan(NEG_LOW, 1, tic=10.0), _scan(NEG_HIGH, 2, tic=500.0)] * 2
        + [_scan(NEG_LOW, 1, tic=30.0)]
    )

    times, tic = reader.tic_per_scan(stream=LOW)
    assert tic.tolist() == [10.0, 10.0, 30.0]
    assert times.tolist() == pytest.approx([0.0, 2.0, 4.0])


def test_the_keys_are_read_once_per_open_file(scripted):
    """A stream's key holds the FT resolution, which is a trailer read per
    scan; a caller selecting stream after stream must not pay it each time."""
    reader = scripted(ALTERNATING)

    reader.scan_indices(stream=LOW)
    reader.scan_indices(stream=HIGH)
    reader.scan_times(stream=LOW)

    assert reader._raw.trailer_reads == len(ALTERNATING)


def test_no_trailer_is_read_when_no_stream_is_asked_for(scripted):
    """The default selection is every file's hot path, and reads no key."""
    reader = scripted(ALTERNATING)

    reader.scan_indices()
    reader.scan_times(polarity="-")

    assert reader._raw.trailer_reads == 0


# -- the first scan ------------------------------------------------------------


def test_the_first_scan_is_compared_with_the_rest_of_its_own_stream(scripted):
    """A first scan five times the file's median is dropped today, whatever
    the other scans measured. Asked for a stream, it is judged against the
    scans that measured what it did - and here it is ordinary among them."""
    reader = scripted(
        [_scan(NEG_LOW, 1, tic=1000.0), _scan(NEG_LOW, 1, tic=900.0)]
        + [_scan(NEG_HIGH, 2, tic=100.0)] * 5
    )

    # File-wide: 1000 against a median of 100.
    assert reader.scan_indices() == [2, 3, 4, 5, 6, 7]
    # Within its stream: 1000 against 900.
    assert reader.scan_indices(stream=LOW) == [1, 2]
    assert reader.scan_indices(stream=HIGH) == [3, 4, 5, 6, 7]


def test_a_first_scan_that_is_an_outlier_in_its_stream_is_left_out(scripted):
    reader = scripted(
        [_scan(NEG_LOW, 1, tic=5000.0)]
        + [_scan(NEG_LOW, 1, tic=1000.0)] * 3
        + [_scan(NEG_HIGH, 2, tic=4000.0)] * 5
    )

    # File-wide it passes: 5000 against a median of 4000.
    assert reader.scan_indices() == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    # Within its stream it is five times the others.
    assert reader.scan_indices(stream=LOW) == [2, 3, 4]


def test_a_stream_that_does_not_hold_the_first_scan_loses_nothing(scripted):
    """The rule is about the file's first scan, not each stream's: a later
    stream that opens on a strong scan keeps it."""
    reader = scripted(
        [_scan(NEG_LOW, 1, tic=100.0)] * 3
        + [_scan(NEG_HIGH, 2, tic=9000.0)]
        + [_scan(NEG_HIGH, 2, tic=100.0)] * 3
    )

    assert reader.scan_indices(stream=HIGH) == [4, 5, 6, 7]
    assert reader.scan_indices(stream=LOW) == [1, 2, 3]


@contextmanager
def _logged():
    """The messages the reader logs inside the block, at INFO and above."""
    messages: list[str] = []
    sink = runtime.logger.add(
        lambda message: messages.append(message.record["message"]), level="INFO"
    )
    try:
        yield messages
    finally:
        runtime.logger.remove(sink)


def test_only_a_selection_that_leaves_the_first_scan_out_says_so(scripted):
    """The line names an exclusion. Selecting a stream that never held the
    file's first scan excludes nothing, however its own first scan reads."""
    later = scripted(
        [_scan(NEG_LOW, 1, tic=100.0)] * 3
        + [_scan(NEG_HIGH, 2, tic=9000.0)]
        + [_scan(NEG_HIGH, 2, tic=100.0)] * 3
    )
    with _logged() as messages:
        later.scan_indices(stream=HIGH)
    assert not [message for message in messages if "first scan" in message]

    opening = scripted(
        [_scan(NEG_LOW, 1, tic=5000.0)] + [_scan(NEG_LOW, 1, tic=1000.0)] * 3
    )
    with _logged() as messages:
        opening.scan_indices(stream=f"{LOW}")
    assert [message for message in messages if "first scan" in message]


def test_a_stream_of_one_scan_keeps_it(scripted):
    """Nothing to compare the first scan with."""
    reader = scripted([_scan(NEG_LOW, 1, tic=9e9)] + [_scan(NEG_HIGH, 2)] * 3)

    assert reader.scan_indices(stream=LOW) == [1]


# -- every selecting method hands the stream on ----------------------------------


class _Asked(Exception):
    """Raised by the recording selector once it has what was asked."""


_SELECTING = {
    "scan_times": {},
    "tic_per_scan": {},
    "scan_acquisition_settings": {},
    "scan_statistics": {},
    "scan_indices": {},
    "centroids_per_scan": {},
    "profile_per_scan": {},
    "xic": {"mzs": [100.0]},
}


@pytest.mark.parametrize("method", list(_SELECTING))
@pytest.mark.parametrize(
    ("backend_class", "selector"),
    [(OpenTFRawBackend, "_selected"), (ThermoBackend, "_selector")],
    ids=["opentfraw", "thermo"],
)
def test_each_selecting_method_passes_the_stream_to_the_selection(
    monkeypatch, backend_class, selector, method
):
    """The selection is made in one place per backend; a method that dropped
    the argument on the way there would read every stream and say nothing."""
    if backend_class is ThermoBackend and method == "xic":
        # The one method that names .NET types before it selects.
        if not thermo_available():
            pytest.skip("Thermo backend unavailable (set MASCOPE_THERMO_DLL_DIR)")
        m_thermo._ensure_dotnet()
    asked = {}

    def _record(self, polarity=None, t_min=None, t_max=None, ms_type="Ms", stream=None):
        asked.update(stream=stream)
        raise _Asked

    monkeypatch.setattr(backend_class, selector, _record)
    # The m/z bounds are validated before the selection, from the run header.
    monkeypatch.setattr(
        OpenTFRawBackend, "_validate_mz_range", lambda self, lo, hi: (0.0, 1e6)
    )
    monkeypatch.setattr(m_thermo, "_validate_mz_range", lambda raw, lo, hi: (0.0, 1e6))
    reader = backend_class("unused.raw")

    with pytest.raises(_Asked):
        getattr(reader, method)(**_SELECTING[method], stream="the stream")

    assert asked == {"stream": "the stream"}


class _RecordingBackend:
    """A reader that records what each selecting method was asked for."""

    def __init__(self):
        self.asked = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def _answer(self, name, kwargs, value):
        self.asked[name] = kwargs
        return value

    def profile_per_scan(self, **kwargs):
        return self._answer(
            "profile_per_scan",
            kwargs,
            ([np.array([100.0])], [np.array([1.0])], np.array([0.0])),
        )

    def scan_indices(self, **kwargs):
        return self._answer("scan_indices", kwargs, [1])

    def average_profile(self, indices, ppm=1, average=False):
        return np.array([100.0]), np.array([1.0]), len(indices)

    def average_centroids(self, indices, ppm=1, average=False):
        return (np.array([100.0]),) * 4

    def tic_per_scan(self, **kwargs):
        return self._answer("tic_per_scan", kwargs, (np.array([0.0]), np.array([1.0])))

    def scan_times(self, **kwargs):
        return self._answer("scan_times", kwargs, np.array([0.0]))

    def xic(self, mzs, **kwargs):
        return self._answer("xic", kwargs, (np.zeros((len(mzs), 1)), np.array([0.0])))

    def centroids_per_scan(self, **kwargs):
        return self._answer("centroids_per_scan", kwargs, [])


@pytest.mark.parametrize(
    ("function", "arguments", "asks"),
    [
        (m_thermo.get_signal, {}, "profile_per_scan"),
        (m_thermo.compute_sum_signal, {}, "scan_indices"),
        (m_thermo.get_tic_per_scan, {}, "tic_per_scan"),
        (m_thermo.get_scan_timestamps, {}, "scan_times"),
        (m_thermo.get_peak_timeseries, {"mzs": [100.0]}, "xic"),
        (m_thermo.get_centroids, {}, "scan_indices"),
        (m_thermo.get_centroids_per_scan, {}, "centroids_per_scan"),
    ],
    ids=lambda value: getattr(value, "__name__", None),
)
def test_each_public_read_passes_the_stream_to_the_reader(
    monkeypatch, function, arguments, asks
):
    reader = _RecordingBackend()
    monkeypatch.setattr(m_thermo, "open_backend", lambda path: reader)

    function("unused.raw", **arguments, stream="the stream")

    assert reader.asked[asks]["stream"] == "the stream"


@pytest.mark.parametrize(
    ("function", "arguments", "asks"),
    [
        (m_thermo.get_signal, {}, "profile_per_scan"),
        (m_thermo.compute_sum_signal, {}, "scan_indices"),
        (m_thermo.get_tic_per_scan, {}, "tic_per_scan"),
        (m_thermo.get_scan_timestamps, {}, "scan_times"),
        (m_thermo.get_peak_timeseries, {"mzs": [100.0]}, "xic"),
        (m_thermo.get_centroids, {}, "scan_indices"),
        (m_thermo.get_centroids_per_scan, {}, "centroids_per_scan"),
    ],
    ids=lambda value: getattr(value, "__name__", None),
)
def test_a_public_read_given_no_stream_asks_for_none(
    monkeypatch, function, arguments, asks
):
    reader = _RecordingBackend()
    monkeypatch.setattr(m_thermo, "open_backend", lambda path: reader)

    function("unused.raw", **arguments)

    assert reader.asked[asks]["stream"] is None


# -- the committed sample files, through each backend available ----------------


@pytest.mark.parametrize("path", [POS_ORBI_FILE_PATH, NEG_ORBI_FILE_PATH])
def test_a_single_stream_files_stream_selects_what_no_stream_selects(backend, path):
    """Nearly every file: one stream. Selecting it by its key has to give the
    very scans the default selection gives, the first-scan rule included,
    because on such a file the stream is the file."""
    with open_backend(path) as reader:
        (stream,) = scan_streams(reader)
        key = stream["key"]

        assert reader.scan_indices(stream=key) == reader.scan_indices()
        np.testing.assert_array_equal(
            reader.scan_times(stream=key), reader.scan_times()
        )
        for with_stream, without in zip(
            reader.tic_per_scan(stream=key), reader.tic_per_scan()
        ):
            np.testing.assert_array_equal(with_stream, without)
        assert reader.scan_statistics(stream=key) == reader.scan_statistics()
        with pytest.raises(UnknownStreamError) as raised:
            reader.scan_indices(stream="no such stream")
        assert raised.value.held == [key]


@pytest.mark.parametrize("path", [POS_ORBI_FILE_PATH, NEG_ORBI_FILE_PATH])
def test_a_public_read_does_not_swallow_a_key_the_file_does_not_hold(backend, path):
    """The reads built on the reader hand the refusal on as it is."""
    with pytest.raises(UnknownStreamError):
        m_thermo.get_scan_timestamps(path, stream="no such stream")
    with pytest.raises(UnknownStreamError):
        m_thermo.get_tic_per_scan(path, stream="no such stream")


@pytest.mark.parametrize("path", [POS_ORBI_FILE_PATH, NEG_ORBI_FILE_PATH])
def test_the_public_reads_of_a_single_stream_file_agree_with_and_without_it(
    backend, path
):
    with open_backend(path) as reader:
        (stream,) = scan_streams(reader)
    key = stream["key"]

    for with_stream, without in zip(
        m_thermo.get_centroids(path, stream=key), m_thermo.get_centroids(path)
    ):
        np.testing.assert_array_equal(with_stream, without)

    summed, combined = m_thermo.compute_sum_signal(path, stream=key)
    expected, expected_combined = m_thermo.compute_sum_signal(path)
    assert combined == expected_combined
    np.testing.assert_array_equal(summed.mz.values, expected.mz.values)
    np.testing.assert_array_equal(summed.values, expected.values)

    mzs = m_thermo.get_centroids(path)[0][:5]
    np.testing.assert_array_equal(
        m_thermo.get_peak_timeseries(path, mzs, stream=key).values,
        m_thermo.get_peak_timeseries(path, mzs).values,
    )
    np.testing.assert_array_equal(
        m_thermo.get_scan_timestamps(path, stream=key),
        m_thermo.get_scan_timestamps(path),
    )
