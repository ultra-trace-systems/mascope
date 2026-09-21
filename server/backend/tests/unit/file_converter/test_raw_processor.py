"""Ingestion (RawProcessor) metadata extraction, exercised on the OpenTFRaw
backend with the Thermo DLLs unavailable.

Regression guard: file processing must work with no proprietary Thermo
dependency. The processor previously read every metadata field straight from
Thermo's .NET RawFile, so ingesting any file required the DLLs even though the
read path defaults to OpenTFRaw -- a DLL-free deploy failed with "Thermo
RawFileReader DLLs not found". If any processor property regresses to the .NET
reader, ``_get_sample_file_props`` raises here instead of returning.

Also pins the acquisition-timestamp behaviour: it must be the file's recorded
wall-clock (from the Xcalibur audit tag), to the second, and independent of this
machine's timezone -- not the file mtime.
"""

import json
from datetime import datetime
from pathlib import Path
from queue import Queue
from threading import Event

import pytest

from mascope_thermo.processor import RawProcessor


# Committed sample (ships in the repo; only the small KORBI MS1 files are tracked).
_REPO_ROOT = Path(__file__).resolve().parents[5]
KORBI_POS = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)

# Golden metadata for that file (decoded by OpenTFRaw; instrument-independent of
# the reading machine). Acquisition wall-clock comes from the audit tag.
EXPECTED_RANGE = [40.0, 500.0]
EXPECTED_POLARITY = "+"
EXPECTED_CREATED_TO_SEC = datetime(2026, 1, 9, 17, 43, 57)
EXPECTED_METHOD_FILE = (
    r"C:\Xcalibur\methods\5.1 Methods\ambient_pos_massrange40-500.meth"
)


@pytest.fixture
def props(monkeypatch):
    """Sample-file props extracted DLL-free via the OpenTFRaw backend."""
    if not KORBI_POS.exists():
        pytest.skip(f"sample file missing: {KORBI_POS}")
    # Force the open-source backend and ensure the Thermo DLLs are not used, so
    # this exercises (and only passes on) the DLL-free ingestion path.
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    monkeypatch.delenv("MASCOPE_THERMO_DLL_DIR", raising=False)

    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = str(KORBI_POS)
    return processor._get_sample_file_props()


def test_ingestion_is_dll_free_and_correct(props):
    # Reaching here means _get_sample_file_props() ran without the Thermo DLLs.
    assert props.range == EXPECTED_RANGE
    assert props.polarity == EXPECTED_POLARITY
    # The reader that opened the file names its class; the name need not.
    assert props.instrument_type == "orbi"
    assert props.length > 0
    assert props.interval > 0
    # Orbitrap files carry no m/z calibration coefficient.
    assert props.mz_calibration is None
    # The instrument method, verbatim from the file's sample information.
    assert props.method_file == EXPECTED_METHOD_FILE


def test_acquisition_timestamp_is_exact_and_tz_independent(props):
    """The timestamp must be the file's recorded acquisition wall-clock to the
    second, not the file mtime, and naive (no machine-timezone applied)."""
    ts = datetime.fromisoformat(props.timestamp)
    assert ts.tzinfo is None  # instrument-local wall-clock, no machine tz applied
    assert ts.replace(microsecond=0) == EXPECTED_CREATED_TO_SEC


def test_canonical_filename_embeds_the_acquisition_time(props):
    assert "2026.01.09-17h43m57s" in props.filename


def test_acquisition_params_are_captured_into_props(props):
    """Acquisition parameters reach .props via the DLL-free path.

    Written verbatim into .props (which is json.dump'd) and deliberately not
    into the DB record, so this is the only place the capture is pinned.
    """
    params = props.acquisition_params
    assert params["source"] == "opentfraw"
    assert params["scans_sampled"] > 0
    # Method-level settings the trailer carries but no typed field exposes.
    assert params["constant"]["Application Mode:"] == "Small Molecule"
    assert params["constant"]["FT Resolution:"] == 120000
    json.dumps(params)  # .props is written with json.dump


def test_acquisition_params_never_fail_ingestion(props, monkeypatch):
    """Metadata capture is best-effort: a reader that cannot supply the trailer
    must degrade to {} rather than cost us the file."""

    def boom(self, *args, **kwargs):
        raise RuntimeError("reader exploded")

    monkeypatch.setattr(
        "mascope_thermo.backend.OpenTFRawBackend.acquisition_parameters", boom
    )
    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = str(KORBI_POS)
    assert processor._get_sample_file_props().acquisition_params == {}


def test_scan_streams_are_captured_into_props(props):
    """The stream census reaches .props via the DLL-free path, and only there."""
    streams = props.scan_streams
    assert [stream["key"] for stream in streams] == [
        "FTMS + p NSI Full ms [40.0000-500.0000] R=120000"
    ]
    stream = streams[0]
    assert stream["signature"]["polarity"] == EXPECTED_POLARITY
    assert stream["blocks"] == 1
    assert stream["acquisition_params"]["source"] == "opentfraw"
    json.dumps(streams)  # .props is written with json.dump


def test_scan_streams_never_fail_ingestion(props, monkeypatch):
    """The census is best-effort, like the acquisition parameters."""

    def boom(self):
        raise RuntimeError("reader exploded")

    monkeypatch.setattr("mascope_thermo.backend.OpenTFRawBackend.scan_filters", boom)
    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = str(KORBI_POS)
    assert processor._get_sample_file_props().scan_streams == []


class _TwoRangesPerPolarity:
    """Reader stand-in for a method that alternates two scan ranges."""

    _FILTERS = (
        "FTMS - p NSI Full ms [40.0000-160.0000]",
        "FTMS - p NSI Full ms [128.0000-600.0000]",
    )

    def scan_filters(self):
        return [
            {"scan": n, "time_s": float(n), "filter": self._FILTERS[n % 2]}
            for n in range(1, 7)
        ]

    def scan_trailer(self, scan_number):  # noqa: ARG002
        return {"FT Resolution:": 120000}

    def acquisition_parameters(self, max_scans=5, scan_numbers=None):  # noqa: ARG002
        return {}


def test_pooled_ms1_streams_are_reported_once_at_info():
    """Peak detection pools the two ranges into one peak list. That is a
    property of the acquisition, reported once, and not a fault."""
    import logging
    from contextlib import contextmanager

    from mascope_backend.runtime import runtime

    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = "ORBI-1_two_ranges.raw"

    @contextmanager
    def _context(_file_path):
        yield _TwoRangesPerPolarity()

    processor._file_context_manager = _context

    # Read through the runtime logger the processor's stdlib logger is bridged
    # to: that is where the log files and the monitoring sink see the record.
    captured = []
    sink_id = runtime.logger.add(
        lambda message: captured.append(message.record), level="TRACE"
    )
    try:
        streams = processor.scan_streams
    finally:
        runtime.logger.remove(sink_id)

    assert len(streams) == 2
    records = [r for r in captured if r["name"] == "mascope_thermo.processor"]
    pooled = [r for r in records if "MS1 scan streams" in r["message"]]
    assert [r["level"].name for r in pooled] == ["INFO"]
    message = pooled[0]["message"]
    assert "2 MS1 scan streams in polarity -" in message
    assert "[40.0000-160.0000]" in message
    assert "[128.0000-600.0000]" in message
    assert not [r for r in records if r["level"].no >= logging.WARNING]


def test_method_file_never_fails_ingestion(props, monkeypatch):
    """The method name is descriptive metadata: a reader that cannot supply it
    must degrade to "" rather than cost us the file."""

    def boom(self):
        raise RuntimeError("reader exploded")

    monkeypatch.setattr("mascope_thermo.backend.OpenTFRawBackend.method_file", boom)
    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = str(KORBI_POS)
    assert processor._get_sample_file_props().method_file == ""
