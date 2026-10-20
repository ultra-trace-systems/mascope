"""A raw file whose scan events the reader misread fails with the refusal.

``OpenTFRawBackend`` refuses a file whose scans read as an analyzer its
instrument cannot have (``mascope_thermo.scan_events``, GitHub #2394), on the
first scan selection. ``acquisition_params`` and ``scan_streams`` make one
before the property that fails the file, and both degrade any other reader
failure to a warning with a traceback, the level error monitoring subscribes
to. The refusal passes through them, so the file fails once, with the
refusal's text, and nothing before it reports a fault.
"""

from contextlib import contextmanager
from queue import Queue
from threading import Event

import pytest
from test_utils import captured_logs

from mascope_backend.file_converter.base_processor import BaseFileProcessor
from mascope_backend.file_converter.errors import describe_exception
from mascope_thermo.backend import OpenTFRawBackend
from mascope_thermo.processor import RawProcessor
from mascope_thermo.scan_events import MisreadScanEventsError


class _MisreadOpenTFRaw:
    """The ``opentfraw.RawFile`` of a Q Exactive file whose scan events it
    read from the wrong bytes."""

    instrument_model = "Q Exactive HF-X"
    num_scans = 4
    #: Read by OpenTFRawBackend.created(), for the file's stored name: an
    #: Xcalibur audit timestamp.
    created = 1755589320.0

    def __init__(self):
        self.tables_read = 0

    def scan_table(self):
        self.tables_read += 1
        return {
            "scan_number": [1, 2, 3, 4],
            "retention_time": [0.01, 0.02, 0.03, 0.04],
            "polarity": ["+", "-", "", "+"],
            "ms_level": [1, 5, 2, 8],
            "analyzer": ["FTMS", "SQMS", None, "ITMS"],
            "total_ion_current": [1.0] * 4,
            "low_mz": [40.0] * 4,
            "high_mz": [600.0] * 4,
        }


def _processor(raw):
    """A converter whose file context is the OpenTFRaw backend over ``raw``."""
    backend = OpenTFRawBackend("QE-1_misread.raw")
    backend._raw = raw

    @contextmanager
    def _context(_file_path):
        yield backend

    processor = RawProcessor(
        socket_client=None, file_queue=Queue(), shutdown_event=Event()
    )
    processor.file_to_process = "QE-1_misread.raw"
    processor._file_context_manager = _context
    return processor


def test_the_file_fails_once_with_the_refusal():
    raw = _MisreadOpenTFRaw()

    with pytest.raises(MisreadScanEventsError) as refused:
        _processor(raw)._get_sample_file_props()

    assert "2 of its 4 scans read as an analyzer no Orbitrap instrument has" in (
        describe_exception(refused.value)
    )
    # The first property to select scans refuses; none after it reads again.
    assert raw.tables_read == 1


def test_nothing_before_the_refusal_reports_a_fault():
    from mascope_runtime.logging import _SENTRY_LEVELS

    processor = _processor(_MisreadOpenTFRaw())
    with captured_logs() as records:
        # The base walk, not RawProcessor's override, so every property read
        # before the refusal is observed whatever the override names.
        with pytest.raises(MisreadScanEventsError):
            BaseFileProcessor._get_sample_file_props(processor)

    offenders = [
        (r["level"].name, r["message"])
        for r in records
        if r["level"].name in _SENTRY_LEVELS
    ]
    assert offenders == []
