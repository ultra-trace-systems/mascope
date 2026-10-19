"""The calibration identity of a file (``mascope_thermo.calibration``).

What the scan table adds to its per-file statistics: C/B^2 of the conversion
parameters per stream, and the overdue mass calibrations the status log of
the first and last scan names. No committed file carries a status log, or
more than one stream, so scripted readers carry them; the committed files
then pin C/B^2 as the real reader gives it.
"""

import contextlib
import json
import statistics
from types import SimpleNamespace

import pytest
from thermo_test_support import NEG_ORBI_FILE_PATH, POS_ORBI_FILE_PATH

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import OpenTFRawBackend, ThermoBackend
from mascope_thermo.calibration import (
    calibration_status,
    overdue_calibrations,
    stream_calibrations,
)


NEG = "FTMS - p NSI Full ms [40.0000-600.0000]"
POS = "FTMS + p NSI Full ms [40.0000-600.0000]"
NEG_KEY = "FTMS - p NSI Full ms [40.0000-600.0000] R=120000"
POS_KEY = "FTMS + p NSI Full ms [40.0000-600.0000] R=120000"

PERFORMANCE = (
    "Mass Calibration neg: Overdue (2026-10-01 09:14); "
    "Mass Calibration pos: Overdue (2026-09-30 17:02)"
)


class _Reader:
    """A reader backend, as far as the calibration identity reads one.

    ``scans`` are ``(filter, trailer)`` pairs, numbered from 1; ``logs`` the
    status-log record of a scan number, where it has one.
    """

    def __init__(self, scans, logs=None):
        self._scans = scans
        self._logs = logs or {}
        self.status_reads = []

    def scan_filters(self):
        return [
            {"scan": n, "time_s": float(n), "filter": text, "segment": 1, "event": 1}
            for n, (text, _trailer) in enumerate(self._scans, start=1)
        ]

    def scan_trailer(self, scan_number):
        return {"FT Resolution:": 120000, **self._scans[scan_number - 1][1]}

    def status_log(self, scan_number):
        self.status_reads.append(scan_number)
        return self._logs.get(scan_number)

    # What get_scan_table reads beside them.
    def num_scans(self):
        return len(self._scans)

    def scan_rows(self, *_window):
        return {}

    def instrument_details(self):
        return {"Model": "Scripted"}


def _params(b, c):
    return {"Conversion Parameter B:": b, "Conversion Parameter C:": c}


# ---- C/B^2 per stream ----


def test_each_stream_gets_its_medians_its_ratio_range_and_its_count():
    reader = _Reader(
        [
            (NEG, _params(100.0, 4.0)),
            (POS, _params(200.0, 8.0)),
            (NEG, _params(110.0, 5.0)),
            (NEG, _params(120.0, 6.0)),
        ]
    )

    calibrations = stream_calibrations(reader)

    # In the order the streams first appear.
    assert list(calibrations) == [NEG_KEY, POS_KEY]
    ratios = [4.0 / 100.0**2, 5.0 / 110.0**2, 6.0 / 120.0**2]
    assert calibrations[NEG_KEY] == {
        "scans": 3,
        "b": 110.0,
        "c": 5.0,
        "c_over_b2": statistics.median(ratios),
        "c_over_b2_min": min(ratios),
        "c_over_b2_max": max(ratios),
    }
    assert calibrations[POS_KEY] == {
        "scans": 1,
        "b": 200.0,
        "c": 8.0,
        "c_over_b2": 8.0 / 200.0**2,
        "c_over_b2_min": 8.0 / 200.0**2,
        "c_over_b2_max": 8.0 / 200.0**2,
    }


def test_the_medians_are_medians_not_the_first_or_a_mean():
    reader = _Reader(
        [(NEG, _params(b, c)) for b, c in [(300.0, 1.0), (100.0, 9.0), (110.0, 2.0)]]
    )

    calibration = stream_calibrations(reader)[NEG_KEY]

    assert calibration["b"] == 110.0
    assert calibration["c"] == 2.0
    assert calibration["c_over_b2"] == 2.0 / 110.0**2


@pytest.mark.parametrize(
    ("b", "c"),
    [
        pytest.param("100.5", " 4.25 ", id="decimal-point"),
        # Formatted in the machine's culture, as a Finnish or German one does.
        pytest.param("100,5", " 4,25 ", id="decimal-comma"),
    ],
)
def test_trailer_text_is_read_as_numbers(b, c):
    # The Thermo library gives trailer values as text.
    reader = _Reader([(NEG, _params(b, c))])

    calibration = stream_calibrations(reader)[NEG_KEY]

    assert calibration["b"] == 100.5
    assert calibration["c"] == 4.25


@pytest.mark.parametrize(
    "trailer",
    [
        pytest.param({}, id="neither"),
        pytest.param({"Conversion Parameter B:": 100.0}, id="no-c"),
        pytest.param({"Conversion Parameter C:": 4.0}, id="no-b"),
        pytest.param(_params(0.0, 4.0), id="b-zero"),
        pytest.param(_params("n/a", 4.0), id="b-not-a-number"),
        pytest.param(_params(float("nan"), 4.0), id="b-nan"),
        pytest.param(_params(100.0, None), id="c-none"),
        pytest.param(_params(True, 4.0), id="b-a-flag"),
    ],
)
def test_a_scan_without_usable_parameters_is_not_counted(trailer):
    reader = _Reader([(NEG, trailer), (NEG, _params(100.0, 4.0))])

    calibration = stream_calibrations(reader)[NEG_KEY]

    assert calibration["scans"] == 1
    assert calibration["c_over_b2"] == 4.0 / 100.0**2


def test_a_stream_without_parameters_is_left_out():
    reader = _Reader([(NEG, {}), (POS, _params(100.0, 4.0))])

    assert list(stream_calibrations(reader)) == [POS_KEY]
    assert stream_calibrations(_Reader([])) == {}


# ---- the status log ----


def test_overdue_calibrations_are_read_from_the_performance_text():
    record = {"Performance:": PERFORMANCE, "Ambient Temp. (C):": 23.5}

    assert overdue_calibrations(record) == [
        {"polarity": "neg", "calibrated": "2026-10-01 09:14"},
        {"polarity": "pos", "calibrated": "2026-09-30 17:02"},
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "Mass Calibration (neg) Overdue (2026-10-01 09:14)",
            [{"polarity": "neg", "calibrated": "2026-10-01 09:14"}],
            id="polarity-in-brackets",
        ),
        pytest.param(
            "MASS CALIBRATION POSITIVE mode is Overdue (2026-10-01 9:14)",
            [{"polarity": "pos", "calibrated": "2026-10-01 9:14"}],
            id="spelled-out-and-upper-case",
        ),
        pytest.param(
            "Mass Calibration neg OK; Mass Calibration pos Overdue (2026-10-01 09:14)",
            [{"polarity": "pos", "calibrated": "2026-10-01 09:14"}],
            id="one-of-two-overdue",
        ),
        pytest.param("Mass Calibration neg OK", [], id="none-overdue"),
        pytest.param("Overdue (2026-10-01 09:14)", [], id="not-a-calibration"),
    ],
)
def test_overdue_entries_as_they_may_be_written(text, expected):
    assert overdue_calibrations({"Performance:": text}) == expected


def test_only_text_values_are_read():
    assert overdue_calibrations({"Performance:": None, "Count:": 3}) == []


def test_the_status_is_that_of_the_first_and_last_scan():
    logs = {
        1: {"Performance:": PERFORMANCE},
        3: {"Performance:": "Mass Calibration neg OK"},
    }
    reader = _Reader([(NEG, {})] * 3, logs)

    status = calibration_status(reader)

    assert reader.status_reads == [1, 3]
    assert status == {
        "first_scan": {
            "scan": 1,
            "overdue": [
                {"polarity": "neg", "calibrated": "2026-10-01 09:14"},
                {"polarity": "pos", "calibrated": "2026-09-30 17:02"},
            ],
        },
        "last_scan": {"scan": 3, "overdue": []},
    }


def test_a_scan_without_a_record_is_none_beside_one_with():
    reader = _Reader([(NEG, {})] * 3, {3: {"Performance:": PERFORMANCE}})

    status = calibration_status(reader)

    assert status["first_scan"] is None
    assert status["last_scan"]["scan"] == 3


def test_no_record_at_all_is_no_status():
    assert calibration_status(_Reader([(NEG, {})] * 3)) is None
    assert calibration_status(_Reader([])) is None


# ---- the readers' status log ----


def _opentfraw(status_log):
    reader = OpenTFRawBackend("unused.raw")
    reader._raw = SimpleNamespace(status_log=status_log)
    return reader


def test_opentfraw_gives_the_scans_record():
    reader = _opentfraw(lambda n: {"Performance:": f"scan {n}"})

    assert reader.status_log(4) == {"Performance:": "scan 4"}


@pytest.mark.parametrize("record", [None, {}], ids=["none", "empty"])
def test_opentfraw_gives_none_for_a_scan_without_a_record(record):
    assert _opentfraw(lambda _n: record).status_log(4) is None


def test_opentfraw_gives_none_where_the_log_cannot_be_read():
    # OpenTFRaw 3.0.0 raises rather than giving None.
    def unreadable(_n):
        raise ValueError("status log cannot be read")

    assert _opentfraw(unreadable).status_log(4) is None


def _thermo(raw):
    reader = ThermoBackend("unused.raw")
    reader._raw = raw
    return reader


def test_thermo_gives_the_record_nearest_the_scans_start():
    asked = []

    def record_at(time):
        asked.append(time)
        return SimpleNamespace(Labels=["Performance:", "Temp:"], Values=["ok", "23"])

    raw = SimpleNamespace(
        RetentionTimeFromScanNumber=lambda n: n / 60,
        GetStatusLogForRetentionTime=record_at,
    )

    assert _thermo(raw).status_log(6) == {"Performance:": "ok", "Temp:": "23"}
    assert asked == [0.1]


def test_thermo_gives_none_for_an_empty_record_or_an_unreadable_log():
    empty = SimpleNamespace(
        RetentionTimeFromScanNumber=lambda n: n / 60,
        GetStatusLogForRetentionTime=lambda _t: SimpleNamespace(Labels=[], Values=[]),
    )

    assert _thermo(empty).status_log(6) is None
    assert _thermo(SimpleNamespace()).status_log(6) is None


# ---- in the scan table ----


def _table(monkeypatch, reader, **window):
    monkeypatch.setattr(
        m_thermo, "open_backend", lambda _path: contextlib.nullcontext(reader)
    )
    return m_thermo.get_scan_table("scripted.raw", **window)


def test_the_table_carries_both_in_its_file_statistics(monkeypatch):
    reader = _Reader(
        [(NEG, _params(100.0, 4.0)), (NEG, _params(100.0, 4.0))],
        {1: {"Performance:": PERFORMANCE}},
    )

    stats_per_file = _table(monkeypatch, reader)["stats_per_file"]

    assert stats_per_file["Value"] == {"Model": "Scripted"}
    assert stats_per_file["calibrations"] == stream_calibrations(reader)
    assert stats_per_file["calibrations"][NEG_KEY]["scans"] == 2
    assert stats_per_file["calibration_status"] == calibration_status(reader)
    assert stats_per_file["calibration_status"]["first_scan"]["scan"] == 1


def test_the_table_leaves_the_status_out_when_the_reader_gives_none(monkeypatch):
    reader = _Reader([(NEG, _params(100.0, 4.0))])

    stats_per_file = _table(monkeypatch, reader)["stats_per_file"]

    assert set(stats_per_file) == {"Value", "calibrations"}


def test_the_file_statistics_describe_the_file_whatever_was_asked_for(monkeypatch):
    reader = _Reader([(NEG, _params(100.0, 4.0)), (POS, _params(200.0, 8.0))])

    stats_per_file = _table(monkeypatch, reader, polarity="+", first_scan=2)[
        "stats_per_file"
    ]

    assert list(stats_per_file["calibrations"]) == [NEG_KEY, POS_KEY]


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(POS_ORBI_FILE_PATH, id="pos"),
        pytest.param(NEG_ORBI_FILE_PATH, id="neg"),
    ],
)
def test_a_committed_file_has_one_calibration_per_stream(backend, path):  # noqa: ARG001
    table = m_thermo.get_scan_table(path)
    calibrations = table["stats_per_file"]["calibrations"]

    json.dumps(table, allow_nan=False)
    assert calibrations
    ms1 = sum(row["MsType"] == "Ms" for row in table["stats_per_scan"].values())
    assert sum(c["scans"] for c in calibrations.values()) == ms1
    for calibration in calibrations.values():
        low, mid, high = (
            calibration["c_over_b2_min"],
            calibration["c_over_b2"],
            calibration["c_over_b2_max"],
        )
        assert low <= mid <= high
        if backend == "opentfraw":
            # One calibration: constant within the file, far inside the 1e-11
            # that files are grouped by.
            assert high - low < 1e-13
