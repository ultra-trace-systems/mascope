"""The scan table (``ReaderBackend.scan_rows``, ``thermo.get_scan_table``).

The table describes a file: one row per scan, its statistics and its trailer,
for the scans of every MS order and with none left out. That is what sets it
apart from every scan *selection*, which drops an outlier first scan and
raises when nothing matches, so the cases that tell the two apart are the
ones pinned here. No committed file has an outlier first scan, a second
polarity or a fragmentation scan, so a scripted acquisition is driven through
**both** reader backends - OpenTFRaw over a scripted ``RawFile``, the Thermo
library over a scripted adapter, neither needing its real reader. The
scripted readers have no peak accessor at all, so a table that read a scan's
peaks would fail on them. The committed sample files then pin the table
against what the metadata document holds for the same scans, through each
backend available.
"""

import contextlib
import json
import math
from types import SimpleNamespace

import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import (
    MS_SCAN_DETECTOR_STATS,
    SCAN_STAT_FIELDS,
    OpenTFRawBackend,
    ThermoBackend,
    open_backend,
)
from mascope_thermo.thermo import InvalidRangeError, PolarityError


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
POS = "FTMS + p NSI Full ms [40.0000-600.0000]"
MS2 = "FTMS - p NSI Full ms2 300.0000@hcd30.00 [50.0000-310.0000]"


def _scan(text, tic=1e6, resolution=120000):
    """One scripted scan: its filter, its TIC and its FT resolution. Scans
    are one second apart, the first at 0 s."""
    return SimpleNamespace(filter=text, tic=tic, resolution=resolution)


class _ScriptedOpenTFRaw:
    """The slice of ``opentfraw.RawFile`` the scan table reads."""

    instrument_model = "Scripted"

    def __init__(self, scans):
        self._scans = scans
        self.trailer_reads = []

    @property
    def num_scans(self):
        return len(self._scans)

    def scan_table(self):
        numbers = list(range(1, len(self._scans) + 1))
        return {
            "scan_number": numbers,
            "scan_event": [0] * len(numbers),
            "scan_segment": [0] * len(numbers),
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
        self.trailer_reads.append(scan_number)
        return {"FT Resolution:": self._scans[scan_number - 1].resolution}


class _Text:
    """A .NET enum or filter, as far as ``ToString()`` goes."""

    def __init__(self, text):
        self._text = text

    def ToString(self):  # noqa: N802
        return self._text


class _ScriptedThermoRaw:
    """The slice of the Thermo ``RawFileReaderAdapter`` the scan table reads."""

    def __init__(self, scans):
        self._scans = scans
        self.trailer_reads = []
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
            **{
                **dict.fromkeys(SCAN_STAT_FIELDS),
                **MS_SCAN_DETECTOR_STATS,
                "StartTime": (number - 1) / 60,
                "TIC": scan.tic,
                "ScanNumber": number,
                "ScanEventNumber": 0,
                "SegmentNumber": 0,
                "ScanType": scan.filter,
            }
        )

    def GetTrailerExtraInformation(self, number):  # noqa: N802
        self.trailer_reads.append(number)
        return SimpleNamespace(
            Labels=["FT Resolution:"],
            Values=[self._scans[number - 1].resolution],
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


# Scan 1 is an outlier (its TIC is far above the rest), scans 2-5 are plain
# negative MS1 scans, scan 6 is positive and scan 7 a fragmentation scan.
# Their start times are 0, 1, ... 6 s.
ACQUISITION = [
    _scan(NEG, tic=1e9),
    *[_scan(NEG)] * 4,
    _scan(POS),
    _scan(MS2, resolution=15000),
]


def test_no_scan_is_left_out_not_even_an_outlier_first_scan(scripted):
    reader = scripted(ACQUISITION)

    # A selection of the same file drops scan 1, which is what the table
    # must not do.
    assert reader.scan_indices(ms_type=None) == [2, 3, 4, 5, 6, 7]
    assert list(reader.scan_rows()) == [1, 2, 3, 4, 5, 6, 7]


def test_scans_of_every_ms_order_are_described_and_say_which(scripted):
    rows = scripted(ACQUISITION).scan_rows()

    assert [row["MsType"] for row in rows.values()] == ["Ms"] * 6 + ["Ms2"]


def test_a_row_is_the_scans_statistics_then_its_trailer(scripted):
    reader = scripted(ACQUISITION)

    rows = reader.scan_rows()

    statistics = reader.scan_statistics(ms_type=None)
    for scan, row in rows.items():
        assert list(row) == [*SCAN_STAT_FIELDS, "MsType", "FT Resolution:"]
        assert row["ScanNumber"] == scan
        assert row["FT Resolution:"] == ACQUISITION[scan - 1].resolution
        # The statistics are the ones a selection reports for the scan.
        if scan in statistics:
            assert {name: row[name] for name in statistics[scan]} == statistics[scan]
    assert 1 not in statistics


@pytest.mark.parametrize(
    ("window", "described"),
    [
        pytest.param({"polarity": "-"}, [1, 2, 3, 4, 5, 7], id="polarity"),
        pytest.param({"polarity": "+"}, [6], id="other-polarity"),
        pytest.param({"first_scan": 3}, [3, 4, 5, 6, 7], id="from-a-scan"),
        pytest.param({"last_scan": 3}, [1, 2, 3], id="up-to-a-scan"),
        pytest.param({"first_scan": 2, "last_scan": 4}, [2, 3, 4], id="scan-range"),
        pytest.param({"first_scan": 4, "last_scan": 4}, [4], id="one-scan"),
        # Scan n starts at n - 1 seconds, and a bound includes the scan
        # that starts exactly on it.
        pytest.param({"t_min": 2.0}, [3, 4, 5, 6, 7], id="from-a-time"),
        pytest.param({"t_max": 2.0}, [1, 2, 3], id="up-to-a-time"),
        pytest.param({"t_min": 0.5, "t_max": 3.5}, [2, 3, 4], id="time-range"),
        pytest.param({"t_min": 0.0, "t_max": 0.0}, [1], id="time-zero"),
        pytest.param(
            {"polarity": "-", "first_scan": 4, "t_max": 6.0},
            [4, 5, 7],
            id="every-bound-at-once",
        ),
    ],
)
def test_a_window_narrows_which_scans_are_described(scripted, window, described):
    assert list(scripted(ACQUISITION).scan_rows(**window)) == described


def test_scans_that_match_nothing_are_no_rows_rather_than_an_error(scripted):
    reader = scripted(ACQUISITION)

    assert reader.scan_rows(first_scan=8) == {}
    assert reader.scan_rows(polarity="+", last_scan=5) == {}
    assert scripted([]).scan_rows() == {}


def test_only_the_described_scans_trailers_are_read(scripted):
    reader = scripted(ACQUISITION)

    reader.scan_rows(first_scan=3, last_scan=4)

    assert reader._raw.trailer_reads == [3, 4]


def test_a_polarity_that_is_none_is_refused(scripted):
    with pytest.raises(PolarityError):
        scripted(ACQUISITION).scan_rows(polarity="positive")


@pytest.mark.parametrize(
    "window",
    [{"first_scan": 5, "last_scan": 4}, {"t_min": 3.0, "t_max": 2.0}],
    ids=["scans", "times"],
)
def test_a_range_that_ends_before_it_starts_is_refused(scripted, window):
    with pytest.raises(InvalidRangeError):
        scripted(ACQUISITION).scan_rows(**window)


def test_a_float_json_cannot_carry_is_none_in_the_table(monkeypatch):
    # No committed file holds one, so a scripted scan does: a NaN statistic
    # and an infinite trailer value, beside a scan that has neither.
    reader = _opentfraw(
        [_scan(NEG, tic=math.nan, resolution=math.inf), _scan(NEG, tic=2e6)]
    )
    monkeypatch.setattr(
        m_thermo, "open_backend", lambda _path: contextlib.nullcontext(reader)
    )

    table = m_thermo.get_scan_table("scripted.raw")

    json.dumps(table, allow_nan=False)
    assert table["stats_per_scan"][1]["TIC"] is None
    assert table["stats_per_scan"][1]["FT Resolution:"] is None
    assert table["stats_per_scan"][2]["TIC"] == 2e6
    assert table["stats_per_scan"][2]["FT Resolution:"] == 120000


# ---- the committed sample files, through each backend available ----

SAMPLE_FILES = [
    pytest.param(POS_ORBI_FILE_PATH, id="pos"),
    pytest.param(NEG_ORBI_FILE_PATH, id="neg"),
]


@pytest.mark.parametrize("path", SAMPLE_FILES)
def test_the_table_holds_the_metadata_documents_rows(backend, path):  # noqa: ARG001
    # What /scans serves is what /metadata serves without its centroids, for
    # every scan the two both hold: same rows, same file statistics.
    table = m_thermo.get_scan_table(path)
    document = m_thermo.RawFileMetadataLegacy(path).to_dict(include_centroids=False)

    assert set(table) == {"num_of_scans", "stats_per_scan", "stats_per_file"}
    assert table["num_of_scans"] == document["num_of_scans"]
    assert list(table["stats_per_scan"]) == list(range(1, table["num_of_scans"] + 1))
    assert document["stats_per_scan"]
    for scan, row in document["stats_per_scan"].items():
        assert table["stats_per_scan"][scan] == row
        assert list(table["stats_per_scan"][scan]) == list(row)
    assert table["stats_per_file"] == document["stats_per_file"]


@pytest.mark.parametrize("path", SAMPLE_FILES)
def test_the_table_is_strict_json(backend, path):  # noqa: ARG001
    json.dumps(m_thermo.get_scan_table(path), allow_nan=False)


def test_fields_keep_the_named_entries_and_the_scan_number(backend):  # noqa: ARG001
    table = m_thermo.get_scan_table(
        POS_ORBI_FILE_PATH,
        fields=["FT Resolution:", "ScanType", "TIC"],
    )

    assert table["stats_per_scan"]
    for scan, row in table["stats_per_scan"].items():
        # In the order asked for, behind the scan number.
        assert list(row) == ["ScanNumber", "FT Resolution:", "ScanType", "TIC"]
        assert row["ScanNumber"] == scan
    full = m_thermo.get_scan_table(POS_ORBI_FILE_PATH)["stats_per_scan"]
    assert table["stats_per_scan"] == {
        scan: {name: full[scan][name] for name in row}
        for scan, row in table["stats_per_scan"].items()
    }
    # The file's own statistics are not per-scan entries, and stay whole.
    assert table["stats_per_file"]["Value"]["Model"]


def test_a_field_no_scan_carries_is_left_out(backend):  # noqa: ARG001
    # Trailer labels differ between instrument models, so a name this file
    # does not use is not an error: its rows simply do not carry it.
    table = m_thermo.get_scan_table(
        POS_ORBI_FILE_PATH, fields=["FT Resolution", "No Such Label:", "ScanNumber"]
    )

    for row in table["stats_per_scan"].values():
        assert list(row) == ["ScanNumber"]


def test_no_fields_at_all_leaves_the_scan_numbers(backend):  # noqa: ARG001
    table = m_thermo.get_scan_table(POS_ORBI_FILE_PATH, fields=[])

    assert table["stats_per_scan"] == {
        scan: {"ScanNumber": scan} for scan in range(1, table["num_of_scans"] + 1)
    }


def test_a_window_reaches_the_reader_and_the_count_stays_the_files(backend):  # noqa: ARG001
    with open_backend(POS_ORBI_FILE_PATH) as reader:
        num_of_scans = reader.num_scans()
    assert num_of_scans >= 2

    table = m_thermo.get_scan_table(POS_ORBI_FILE_PATH, first_scan=2, last_scan=2)

    assert list(table["stats_per_scan"]) == [2]
    assert table["num_of_scans"] == num_of_scans
    assert m_thermo.get_scan_table(POS_ORBI_FILE_PATH, polarity="-") == {
        "num_of_scans": num_of_scans,
        "stats_per_scan": {},
        "stats_per_file": table["stats_per_file"],
    }
