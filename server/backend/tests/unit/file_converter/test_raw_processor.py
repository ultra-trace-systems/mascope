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
    assert props.length > 0
    assert props.interval > 0
    # Orbitrap files carry neither of these; the processor must report them empty.
    assert props.mz_calibration is None
    assert props.method_file == ""


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
