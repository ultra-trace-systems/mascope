"""A manual MS2 acquisition: fragment scans after the MS1 block, and unlinked.

An operator who fragments a precursor by hand, in the instrument's tuning
software with no method loaded, leaves a file of a particular shape: MS1 scans
first, then the fragment scans as a block of their own, rather than the two
interleaved as a data-dependent method records them. Two things follow, and
each once cost such a file its MS2 data.

**A window taken from its MS1 scans ends before its fragment scans begin.**
Every MS2 read selects within the window of the sample it is asked through,
so a window that came from the MS1 scans held none of the fragment scans, and
the file read as an MS1 acquisition with nothing fragmented in it. The window
has to span every scan type (``get_acquisition_window``, which this is the
reader-side half of).

**Nothing in the file links a fragment scan to a precursor scan.** A method
records each dependent scan's master scan, and every scan's event; a manual
acquisition records neither. What a fragment scan belongs to is read from its
own filter - the precursor m/z and the activation - and its survey scan is the
MS1 scan before it in scan order.

No committed file records a fragmentation scan, so the committed MS1 file is
read here as such an acquisition, scripted by :data:`SCRIPT`: its first scan
stays the survey scan it is, and the fragment scans follow it, each played by
the file's second scan under the filter and the trailer values the script
gives it. Two shapes are scripted: a stepped-energy run, one precursor at four
activations in consecutive blocks, and a group whose scans isolate at two
widths, one of whose trailers lacks the fields the script would set.

The script is played at the reader object each backend reads everything
from: OpenTFRaw's ``RawFile``, and the Thermo library's raw file, whose
filters, scan statistics and trailers are answered by stand-ins. The scan
records and the summary run on both. The centroids and the scan table's
linkage run on OpenTFRaw alone: the Thermo library averages and reads peaks
inside .NET, which a stand-in cannot reach.
"""

from types import SimpleNamespace
from typing import NamedTuple

import pytest
from thermo_test_support import POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import (
    MS2_HCD_ENERGY,
    MS2_INJECTION_TIME,
    MS2_ISOLATION_WIDTH,
    MS2_SCAN_RECORD_SOURCES,
    OpenTFRawBackend,
    ThermoBackend,
    open_backend,
)
from mascope_thermo.lib import thermo_available


class Fragment(NamedTuple):
    """One scripted fragment scan: its filter's event and range, and what its
    trailer stores. ``None`` in a trailer value: the trailer lacks it."""

    precursor_mz: float
    activation: str
    scan_range: tuple[float, float]
    isolation_width: float | None
    hcd_energy_v: str | None
    injection_time_ms: float | None


STEPPED_MZ = 137.096
TWO_WIDTH_MZ = 200.5
STEPS = ("hcd10.00", "hcd20.00", "hcd40.00", "hcd80.00")
STEP_VOLTS = ("14.3", "28.5", "57.0", "114.0")
TWO_WIDTH_ACTIVATION = "hcd25.00"
# The energy stepped within each scan of the two-width group.
WITHIN_SCAN_VOLTS = "17.8,35.6,53.4"

#: The fragment scans, in scan order, from scan 2.
SCRIPT: tuple[Fragment, ...] = (
    # A stepped-energy run: one precursor at four activations, two scans each.
    *(
        Fragment(STEPPED_MZ, step, (40.0, 160.0), 1.0, volts, 20.0 + 2 * i + k)
        for i, (step, volts) in enumerate(zip(STEPS, STEP_VOLTS, strict=True))
        for k in range(2)
    ),
    # One precursor at one activation, isolated at 1.0 once and at 4.0 twice.
    Fragment(
        TWO_WIDTH_MZ, TWO_WIDTH_ACTIVATION, (50.0, 215.0), 1.0, WITHIN_SCAN_VOLTS, 30.0
    ),
    Fragment(
        TWO_WIDTH_MZ, TWO_WIDTH_ACTIVATION, (50.0, 215.0), 4.0, WITHIN_SCAN_VOLTS, 31.0
    ),
    Fragment(
        TWO_WIDTH_MZ, TWO_WIDTH_ACTIVATION, (50.0, 215.0), 4.0, WITHIN_SCAN_VOLTS, 32.0
    ),
    # ... and once more by a scan whose trailer lacks all three.
    Fragment(TWO_WIDTH_MZ, TWO_WIDTH_ACTIVATION, (50.0, 215.0), None, None, None),
)
LACKING_SCAN = 1 + len(SCRIPT)

#: The scan that plays every fragment scan, and the time between two of them.
_PLAYED_BY = 2
_STEP_MIN = 0.05


def _fragment(scan_number) -> Fragment | None:
    index = int(scan_number) - 2
    return SCRIPT[index] if 0 <= index < len(SCRIPT) else None


def _played_by(scan_number) -> int:
    return _PLAYED_BY if _fragment(scan_number) else int(scan_number)


def _fragment_filter(filter_string: str, fragment: Fragment) -> str:
    """The MS1 filter rewritten as the fragment scan's."""
    head = filter_string.split(" Full ms [", 1)[0]
    low, high = fragment.scan_range
    return (
        f"{head} Full ms2 {fragment.precursor_mz:.4f}@{fragment.activation}"
        f" [{low:.4f}-{high:.4f}]"
    )


def _trailer_values(fragment: Fragment) -> dict:
    return {
        MS2_ISOLATION_WIDTH: fragment.isolation_width,
        MS2_HCD_ENERGY: fragment.hcd_energy_v,
        MS2_INJECTION_TIME: fragment.injection_time_ms,
    }


class _ScriptedRawFile:
    """An ``opentfraw.RawFile`` whose scans after the first are the script's.

    Everything not overridden is the real file's. The scan table already
    records what a manual acquisition does about linkage - no master scan,
    and scan event 0 - which ``TestWhatLinksAFragmentScanToItsPrecursor``
    checks rather than assumes. A trailer value the script leaves ``None`` is
    not in the scan's trailer at all.
    """

    def __init__(self, raw):
        self._raw = raw

    def __getattr__(self, name):
        return getattr(self._raw, name)

    @property
    def num_scans(self):
        return 1 + len(SCRIPT)

    def scan_table(self):
        real = self._raw.scan_table()
        table = {name: [values[0]] for name, values in real.items()}
        played = {name: values[_PLAYED_BY - 1] for name, values in real.items()}
        for index, fragment in enumerate(SCRIPT):
            for name, value in played.items():
                table[name].append(value)
            table["scan_number"][-1] = index + 2
            table["retention_time"][-1] = played["retention_time"] + index * _STEP_MIN
            table["ms_level"][-1] = 2
            table["filter_string"][-1] = _fragment_filter(
                played["filter_string"], fragment
            )
        return table

    def scan_filter(self, scan_number):
        filter_string = self._raw.scan_filter(_played_by(scan_number))
        fragment = _fragment(scan_number)
        if fragment is None or not filter_string:
            return filter_string
        return _fragment_filter(filter_string, fragment)

    def scan_parameters(self, scan_number):
        params = dict(self._raw.scan_parameters(_played_by(scan_number)) or {})
        fragment = _fragment(scan_number)
        if fragment is not None:
            for label, value in _trailer_values(fragment).items():
                if value is None:
                    params.pop(label, None)
                else:
                    params[label] = value
        return params

    def centroid_labels(self, scan_number):
        return self._raw.centroid_labels(_played_by(scan_number))

    def scan(self, scan_number, *args, **kwargs):
        return self._raw.scan(_played_by(scan_number), *args, **kwargs)

    def profile(self, scan_number, *args, **kwargs):
        return self._raw.profile(_played_by(scan_number), *args, **kwargs)


class _Override:
    """A .NET object with some of its attributes answered from Python."""

    def __init__(self, inner, **attributes):
        self._inner = inner
        self.__dict__.update(attributes)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _ScriptedThermoRawFile(_Override):
    """The Thermo library's raw file, with the script's filters, statistics
    and trailers for every scan after the first.

    The trailer's labels are the file's, as for every scan of a real file, so
    a value the script leaves ``None`` is empty text there, and no stored
    number.
    """

    def __init__(self, raw):
        super().__init__(
            raw,
            RunHeaderEx=_Override(raw.RunHeaderEx, SpectraCount=1 + len(SCRIPT)),
        )
        self._labels = [item.Label for item in raw.GetTrailerExtraHeaderInformation()]

    def GetFilterForScanNumber(self, scan_number):
        real = self._inner.GetFilterForScanNumber(_played_by(scan_number))
        fragment = _fragment(scan_number)
        if fragment is None:
            return real
        text = _fragment_filter(real.ToString(), fragment)
        return _Override(
            real,
            MSOrder=SimpleNamespace(ToString=lambda: "Ms2"),
            ToString=lambda: text,
        )

    def GetScanStatsForScanNumber(self, scan_number):
        real = self._inner.GetScanStatsForScanNumber(_played_by(scan_number))
        if _fragment(scan_number) is None:
            return real
        index = int(scan_number) - 2
        return _Override(real, StartTime=real.StartTime + index * _STEP_MIN)

    def GetTrailerExtraInformation(self, scan_number):
        real = self._inner.GetTrailerExtraInformation(_played_by(scan_number))
        fragment = _fragment(scan_number)
        values = list(real.Values)
        if fragment is not None:
            for label, value in _trailer_values(fragment).items():
                values[self._labels.index(label)] = "" if value is None else str(value)
        return SimpleNamespace(Labels=list(real.Labels), Values=values)

    def GetTrailerExtraValue(self, scan_number, index):
        fragment = _fragment(scan_number)
        if fragment is not None:
            scripted = _trailer_values(fragment)
            label = self._labels[index]
            if label in scripted:
                value = scripted[label]
                return None if value is None else float(value)
        return self._inner.GetTrailerExtraValue(_played_by(scan_number), index)


def _play_on_opentfraw(monkeypatch):
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    enter = OpenTFRawBackend.__enter__

    def entered(self):
        backend = enter(self)
        backend._raw = _ScriptedRawFile(backend._raw)
        return backend

    monkeypatch.setattr(OpenTFRawBackend, "__enter__", entered)


def _play_on_thermo(monkeypatch):
    if not thermo_available():
        pytest.skip(
            "Thermo backend unavailable; set MASCOPE_THERMO_DLL_DIR to the "
            "RawFileReader DLLs to run it."
        )
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "thermo")
    enter = ThermoBackend.__enter__

    def entered(self):
        backend = enter(self)
        backend._raw = _ScriptedThermoRawFile(backend._raw)
        return backend

    monkeypatch.setattr(ThermoBackend, "__enter__", entered)


@pytest.fixture(params=["opentfraw", "thermo"])
def on_both(request, monkeypatch):
    """Read the committed MS1 file as the scripted acquisition, once per
    backend."""
    if request.param == "thermo":
        _play_on_thermo(monkeypatch)
    else:
        _play_on_opentfraw(monkeypatch)
    return request.param


@pytest.fixture
def on_opentfraw(monkeypatch):
    """Read it so on OpenTFRaw alone, for what a stand-in cannot reach on
    Thermo."""
    _play_on_opentfraw(monkeypatch)


def _window(scan_type):
    times = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH, scan_type=scan_type)
    return float(times.min()), float(times.max())


def _groups(summary) -> list[tuple]:
    return [
        (group["parent_peak_mz"], group["activation"], group["scan_count"])
        for group in summary["groups"]
    ]


#: Every (precursor, activation) group of the script, in acquisition order.
SCRIPTED_GROUPS = [
    *((STEPPED_MZ, step, 2) for step in STEPS),
    (TWO_WIDTH_MZ, TWO_WIDTH_ACTIVATION, 4),
]


class TestTheFixtureIsSuchAnAcquisition:
    """Guards the stand-in, which every case below takes on trust."""

    def test_its_ms1_scans_come_before_its_fragment_scans(self, on_both):
        ms1 = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH, scan_type="Ms")
        ms2 = m_thermo.get_scan_timestamps(POS_ORBI_FILE_PATH, scan_type="Ms2")

        assert ms1.size == 1
        assert ms2.size == len(SCRIPT)
        assert ms1.max() < ms2.min()

    def test_its_fragment_scans_are_the_scripts(self, on_both):
        with open_backend(POS_ORBI_FILE_PATH) as backend:
            events = backend.ms2_events_by_scan()

        assert events == {
            scan: (fragment.precursor_mz, fragment.activation)
            for scan, fragment in enumerate(SCRIPT, start=2)
        }


class TestTheWindowOfItsSample:
    def test_a_window_from_its_ms1_scans_holds_no_fragment_scan(self, on_both):
        """The defect, kept on record: this is what such a window reads.

        A sample whose window was taken from the TIC - MS1 scans only - asked
        every MS2 route to look where no fragment scan was recorded.
        """
        t0, t1 = _window("Ms")

        summary = m_thermo.get_ms2_summary_metadata(
            POS_ORBI_FILE_PATH, t_min=t0, t_max=t1
        )

        assert summary["ms2_scan_count"] == 0
        assert summary["groups"] == []

    def test_a_window_from_every_scan_holds_them_all(self, on_both):
        whole_file = m_thermo.get_ms2_summary_metadata(POS_ORBI_FILE_PATH)
        t0, t1 = _window(None)

        summary = m_thermo.get_ms2_summary_metadata(
            POS_ORBI_FILE_PATH, t_min=t0, t_max=t1
        )

        assert summary["ms2_scan_count"] == whole_file["ms2_scan_count"] == len(SCRIPT)
        assert summary["ms1_scan_count"] == whole_file["ms1_scan_count"] == 1

    def test_its_fragment_spectra_are_read_through_that_window(self, on_opentfraw):
        t0, t1 = _window(None)

        by_parent = m_thermo.get_ms2_centroids_by_parent(
            POS_ORBI_FILE_PATH, t_min=t0, t_max=t1
        )

        assert list(by_parent) == [
            (mz, activation) for mz, activation, _ in SCRIPTED_GROUPS
        ]
        for masses, intensities, _, _ in by_parent.values():
            assert masses.size == intensities.size > 0


class TestWhatLinksAFragmentScanToItsPrecursor:
    def test_the_file_records_no_master_scan_and_no_scan_event(self, on_opentfraw):
        """The premise: there is nothing else to link them by."""
        with open_backend(POS_ORBI_FILE_PATH) as backend:
            fragment_scans = [
                scan for scan in backend._all_scans() if int(scan["ms_level"]) == 2
            ]

        assert fragment_scans
        assert {scan["master_scan_number"] for scan in fragment_scans} == {None}
        assert {int(scan["scan_event"]) for scan in fragment_scans} == {0}

    def test_the_summary_groups_them_by_their_own_filters_alone(self, on_both):
        summary = m_thermo.get_ms2_summary_metadata(POS_ORBI_FILE_PATH)

        assert summary["parent_peaks"] == [STEPPED_MZ, TWO_WIDTH_MZ]
        assert _groups(summary) == SCRIPTED_GROUPS

    def test_its_survey_scan_is_the_ms1_scan_before_it_in_scan_order(self, on_both):
        """Scan order is the only thing that says which MS1 scan it follows."""
        with open_backend(POS_ORBI_FILE_PATH) as backend:
            ms1_scans = backend.scan_indices(ms_type="Ms")
            fragment_scans = backend.scan_indices(ms_type="Ms2")

        assert max(ms1_scans) < min(fragment_scans)


def _records() -> list[dict]:
    with open_backend(POS_ORBI_FILE_PATH) as backend:
        return backend.ms2_scan_records()


class TestEveryFragmentScanHasItsRecord:
    """``ms2_scan_records``: one record per MS2 scan, each field with the
    place it was read from."""

    def test_one_record_per_fragment_scan_in_scan_order(self, on_both):
        with open_backend(POS_ORBI_FILE_PATH) as backend:
            records = backend.ms2_scan_records()
            fragment_scans = backend.scan_indices(ms_type="Ms2")

        assert [record["scan_number"] for record in records] == fragment_scans

    def test_each_record_reads_its_filter_and_its_trailer(self, on_both):
        records = _records()

        read = [
            Fragment(
                record["precursor_mz"],
                record["activation"],
                (record["low_mass"], record["high_mass"]),
                record["isolation_width"],
                None
                if record["hcd_energy_v"] is None
                else ",".join(str(v) for v in record["hcd_energy_v"]),
                record["injection_time_ms"],
            )
            for record in records
        ]
        assert read == list(SCRIPT)
        assert {record["polarity"] for record in records} == {"+"}

    def test_the_activation_reads_as_its_kind_and_energy(self, on_both):
        records = _records()

        assert [(r["activation_kind"], r["nce"]) for r in records] == [
            ("hcd", float(fragment.activation[3:])) for fragment in SCRIPT
        ]

    def test_a_filter_the_reader_does_not_render_is_the_scan_tables(
        self, on_opentfraw, monkeypatch
    ):
        """OpenTFRaw renders a scan's filter from its scan event, and falls
        back on the one its scan table holds, as ``ms2_events_by_scan``
        does, so the records and the groups read the same filters."""
        monkeypatch.setattr(_ScriptedRawFile, "scan_filter", lambda self, scan: None)

        records = _records()

        assert [(r["precursor_mz"], r["activation"]) for r in records] == [
            (fragment.precursor_mz, fragment.activation) for fragment in SCRIPT
        ]

    def test_the_energy_stepped_within_a_scan_is_every_volt(self, on_both):
        record = _records()[-2]

        assert record["hcd_energy_v"] == [17.8, 35.6, 53.4]

    def test_its_time_and_tic_are_the_scan_indexs(self, on_both):
        records = _records()
        with open_backend(POS_ORBI_FILE_PATH) as backend:
            times, tics = backend.tic_per_scan(ms_type="Ms2")

        assert [r["time_s"] for r in records] == pytest.approx(times.tolist())
        assert [r["tic"] for r in records] == pytest.approx(tics.tolist())
        assert all(
            type(r["time_s"]) is float and type(r["tic"]) is float for r in records
        )

    def test_every_field_names_its_source(self, on_both):
        record = _records()[0]

        assert record["sources"] == MS2_SCAN_RECORD_SOURCES
        assert set(record) == {*MS2_SCAN_RECORD_SOURCES, "sources"}

    def test_a_trailer_lacking_a_field_leaves_it_none_not_a_neighbours(self, on_both):
        records = {record["scan_number"]: record for record in _records()}
        lacking, before = records[LACKING_SCAN], records[LACKING_SCAN - 1]

        trailer_fields = ("hcd_energy_v", "isolation_width", "injection_time_ms")
        assert all(before[field] is not None for field in trailer_fields)
        assert {field: lacking[field] for field in trailer_fields} == dict.fromkeys(
            trailer_fields
        )
        assert {field: lacking["sources"][field] for field in trailer_fields} == (
            dict.fromkeys(trailer_fields)
        )
        # What its filter says is still read, and still named.
        assert lacking["precursor_mz"] == TWO_WIDTH_MZ
        assert lacking["sources"]["precursor_mz"] == "filter"


class TestTheSummaryOfTheScript:
    """``get_ms2_summary_metadata`` over the stepped run and the two-width
    group."""

    @pytest.fixture
    def summary(self, on_both):
        return m_thermo.get_ms2_summary_metadata(POS_ORBI_FILE_PATH)

    def test_a_stepped_run_is_one_group_per_step_each_at_its_one_width(self, summary):
        stepped = [g for g in summary["groups"] if g["parent_peak_mz"] == STEPPED_MZ]

        assert [g["activation"] for g in stepped] == list(STEPS)
        assert [g["hcd_energy"] for g in stepped] == [[float(v)] for v in STEP_VOLTS]
        assert [g["isolation_width"] for g in stepped] == [1.0] * len(STEPS)
        assert [g["isolation_widths"] for g in stepped] == [
            [{"isolation_width": 1.0, "scan_count": 2}]
        ] * len(STEPS)

    def test_a_two_width_group_answers_both_widths(self, summary):
        (group,) = [g for g in summary["groups"] if g["parent_peak_mz"] == TWO_WIDTH_MZ]

        assert group["activation"] == TWO_WIDTH_ACTIVATION
        assert group["scan_count"] == 4
        assert group["isolation_width"] is None
        # The scan whose trailer stores no width is in no entry.
        assert group["isolation_widths"] == [
            {"isolation_width": 1.0, "scan_count": 1},
            {"isolation_width": 4.0, "scan_count": 2},
        ]
        assert group["hcd_energy"] == [17.8, 35.6, 53.4]

    def test_the_files_width_is_none_when_its_scans_use_two(self, summary):
        assert summary["isolation_width"] is None

    def test_the_two_width_group_keeps_its_key(self, on_opentfraw):
        """The keys the centroid and timeseries routes serve are the
        summary's groups, however many widths a group holds."""
        summary = m_thermo.get_ms2_summary_metadata(POS_ORBI_FILE_PATH)
        by_parent = m_thermo.get_ms2_centroids_by_parent(POS_ORBI_FILE_PATH)

        assert list(by_parent) == [
            (g["parent_peak_mz"], g["activation"]) for g in summary["groups"]
        ]
        assert (TWO_WIDTH_MZ, TWO_WIDTH_ACTIVATION) in by_parent
