"""
Unit tests for the Orbitrap ``method_file`` backfill script.

The plan decides every row the script writes, so it is tested directly with a
stand-in reader: which sample files are filled, and which instrument configs
may follow them. The header read is tested against the committed KORBI file.
Everything here stays hermetic - no server, no database.
"""

from pathlib import Path

import pytest

from mascope_backend.db.scripts import populate_orbitrap_method_file as script
from mascope_backend.runtime import runtime


_REPO_ROOT = Path(__file__).resolve().parents[5]
KORBI_POS = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)

POS = "C:\\Xcalibur\\methods\\pos.meth"
NEG = "C:\\Xcalibur\\methods\\neg.meth"


def _candidate(n: int, config: str | None = None) -> dict:
    return {
        "sample_file_id": f"sf{n}",
        "filename": f"ORBI-1_file{n}.raw",
        "instrument_function_id": config,
    }


def _reader(methods: dict[str, str | None]):
    """A ``_read_method`` stand-in answering from a filename -> method map."""
    return lambda filename: methods[filename]


def test_fills_each_file_with_the_method_it_records():
    plan = script._plan(
        [_candidate(1), _candidate(2)],
        {},
        _reader({"ORBI-1_file1.raw": POS, "ORBI-1_file2.raw": NEG}),
    )
    assert plan["sample_files"] == [
        {"id": "sf1", "method_file": POS},
        {"id": "sf2", "method_file": NEG},
    ]


def test_unreadable_and_unrecorded_files_are_counted_not_written():
    plan = script._plan(
        [_candidate(1), _candidate(2)],
        {},
        _reader({"ORBI-1_file1.raw": None, "ORBI-1_file2.raw": ""}),
    )
    assert plan["sample_files"] == []
    assert plan["unreadable"] == 1
    assert plan["unrecorded"] == 1


def test_a_config_follows_its_only_file():
    plan = script._plan(
        [_candidate(1, "if1")],
        {"if1": {"files": [{"filename": "ORBI-1_file1.raw", "method_file": ""}]}},
        _reader({"ORBI-1_file1.raw": POS}),
    )
    assert plan["instrument_functions"] == [{"id": "if1", "method_file": POS}]


def test_a_config_follows_files_that_agree_including_one_already_filled():
    plan = script._plan(
        [_candidate(1, "if1")],
        {
            "if1": {
                "files": [
                    {"filename": "ORBI-1_file1.raw", "method_file": None},
                    {"filename": "ORBI-1_file0.raw", "method_file": POS},
                ]
            }
        },
        _reader({"ORBI-1_file1.raw": POS}),
    )
    assert plan["instrument_functions"] == [{"id": "if1", "method_file": POS}]


def test_a_config_its_files_disagree_about_is_left_empty():
    plan = script._plan(
        [_candidate(1, "if1"), _candidate(2, "if1")],
        {
            "if1": {
                "files": [
                    {"filename": "ORBI-1_file1.raw", "method_file": ""},
                    {"filename": "ORBI-1_file2.raw", "method_file": ""},
                ]
            }
        },
        _reader({"ORBI-1_file1.raw": POS, "ORBI-1_file2.raw": NEG}),
    )
    assert plan["instrument_functions"] == []
    assert plan["ambiguous_configs"] == 1
    # The files themselves are still filled; only the shared config is not.
    assert len(plan["sample_files"]) == 2


def test_a_config_with_an_unresolved_file_is_left_empty():
    # One of its files could not be read: the config cannot be known to match.
    plan = script._plan(
        [_candidate(1, "if1"), _candidate(2, "if1")],
        {
            "if1": {
                "files": [
                    {"filename": "ORBI-1_file1.raw", "method_file": ""},
                    {"filename": "ORBI-1_file2.raw", "method_file": ""},
                ]
            }
        },
        _reader({"ORBI-1_file1.raw": POS, "ORBI-1_file2.raw": None}),
    )
    assert plan["instrument_functions"] == []
    assert plan["ambiguous_configs"] == 0


def test_reads_the_method_from_the_raw_file(monkeypatch):
    if not KORBI_POS.exists():
        pytest.skip(f"sample file missing: {KORBI_POS}")
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    monkeypatch.setattr(
        script.m_name, "filename_to_datafile_path", lambda _filename: str(KORBI_POS)
    )
    assert script._read_method("ORBI-1_file1.raw") == (
        "C:\\Xcalibur\\methods\\5.1 Methods\\ambient_pos_massrange40-500.meth"
    )


def test_a_missing_file_reads_as_unreadable(monkeypatch, tmp_path):
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    monkeypatch.setattr(
        script.m_name,
        "filename_to_datafile_path",
        lambda _filename: str(tmp_path / "gone" / "data.raw"),
    )
    assert script._read_method("ORBI-1_file1.raw") is None


def _warnings(work) -> list:
    """Run ``work`` and return the WARNING-or-above records it logged."""
    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        work()
    finally:
        runtime.logger.remove(sink_id)
    return [r for r in records if r["level"].no >= runtime.logger.level("WARNING").no]


def test_per_file_problems_stay_below_the_monitoring_threshold(monkeypatch, tmp_path):
    # Error monitoring subscribes at WARNING, and scripts run where it is wired
    # up: a server missing many raw files must not report one event per file.
    # run() raises a single summary WARNING instead.
    monkeypatch.setattr(
        script.m_name,
        "filename_to_datafile_path",
        lambda _filename: str(tmp_path / "gone" / "data.raw"),
    )
    shared = {
        "if1": {
            "files": [
                {"filename": "ORBI-1_file1.raw", "method_file": ""},
                {"filename": "ORBI-1_file2.raw", "method_file": ""},
            ]
        }
    }
    assert _warnings(lambda: script._read_method("ORBI-1_file1.raw")) == []
    assert (
        _warnings(
            lambda: script._plan(
                [_candidate(1, "if1"), _candidate(2, "if1")],
                shared,
                _reader({"ORBI-1_file1.raw": POS, "ORBI-1_file2.raw": NEG}),
            )
        )
        == []
    )
