"""Tests: how the file processors resolve a sample's UTC offset.

The offset turns an instrument-local timestamp into ``datetime_utc``, and it was
previously guessed from the converter host's clock - wrong whenever the
instrument PC and the converter disagree on timezone or DST, and, through a
``timedelta.seconds`` bug, unable to represent a west-of-UTC offset at all.

These tests pin the replacement: precedence (file-embedded offset > agent zone >
host guess) and, specifically, a correct negative offset for a west-of-UTC zone.
They drive ``_resolve_utc_offset`` directly with a stubbed file handle and file
context, so no real .h5 file or converter is needed.
"""

from datetime import datetime, timezone
from queue import Queue
from threading import Event
from types import SimpleNamespace

from mascope_tofwerk.processor import H5Processor


# 2026-07-01 12:00 UTC — summer, so America/Denver is MDT (-6h), and the sign is
# what the old .seconds arithmetic got wrong.
ACQUIRED_UTC = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
# Windows FILETIME ticks: 100-nanosecond intervals since 1601-01-01 UTC.
ACQUIRED_FILETIME = int((ACQUIRED_UTC - _FILETIME_EPOCH).total_seconds() * 1e7)


class _Attrs(dict):
    """h5-attrs stand-in: missing keys raise KeyError, values index with [0]."""


class _FakeHandle:
    """Minimal ``self.file_handle`` stub exposing only TimingData attrs."""

    def __init__(self, attrs: dict):
        self._groups = {"TimingData": SimpleNamespace(attrs=_Attrs(attrs))}

    def __getitem__(self, key):
        return self._groups[key]


def _make_processor(zone_name: str | None):
    """An H5Processor whose file context reports ``zone_name`` (or none)."""
    context = SimpleNamespace(instrument_timezone=zone_name)
    socket_client = SimpleNamespace(
        context_manager=SimpleNamespace(get_context=lambda _name: context)
    )
    processor = H5Processor(
        socket_client=socket_client,
        file_queue=Queue(),
        shutdown_event=Event(),
    )
    processor.file_to_process = "ACQUISITION.h5"
    return processor


def test_offset_prefers_the_files_own_declared_offset():
    processor = _make_processor(zone_name="America/Denver")
    processor.file_handle = _FakeHandle(
        {"AcquisitionTimeZero": [ACQUIRED_FILETIME], "LocalTimeOffsetToUTC": [2.0]}
    )
    offset, source = processor._resolve_utc_offset()
    # The embedded +2h wins over the agent's Denver zone.
    assert offset == 2.0 * 3600
    assert source == "file"


def test_offset_falls_back_to_the_agent_zone():
    processor = _make_processor(zone_name="America/Denver")
    # No LocalTimeOffsetToUTC attribute -> resolve from the reported zone.
    processor.file_handle = _FakeHandle({"AcquisitionTimeZero": [ACQUIRED_FILETIME]})
    offset, source = processor._resolve_utc_offset()
    # Denver in July is MDT: UTC-6. The sign is the regression this guards.
    assert offset == -6 * 3600
    assert source == "agent"


def test_offset_guess_is_used_when_nothing_else_is_available():
    processor = _make_processor(zone_name=None)
    processor.file_handle = _FakeHandle({"AcquisitionTimeZero": [ACQUIRED_FILETIME]})
    offset, source = processor._resolve_utc_offset()
    assert source == "guess"
    # Whatever the host offset is, it must be a sane whole-minute value.
    assert offset % 60 == 0
    assert -12 * 3600 <= offset <= 14 * 3600


def test_unknown_agent_zone_is_ignored_and_falls_through(caplog):
    processor = _make_processor(zone_name="Mars/Olympus_Mons")
    processor.file_handle = _FakeHandle({"AcquisitionTimeZero": [ACQUIRED_FILETIME]})
    offset, source = processor._resolve_utc_offset()
    # An unresolvable zone must not error; it degrades to the host guess.
    assert source == "guess"
    assert offset % 60 == 0
