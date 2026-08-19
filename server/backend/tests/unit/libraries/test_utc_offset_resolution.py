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

import time
from datetime import datetime, timezone
from queue import Queue
from threading import Event
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from mascope_backend.file_converter.base_processor import (
    WALL_TIME_AMBIGUOUS,
    WALL_TIME_NONEXISTENT,
    resolve_wall_time_offset,
)
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


def test_agent_reported_zone_reaches_the_processor():
    """The last hop: socket payload -> FileContext -> processor lookup.

    The other tests stub the context object directly, which would keep passing
    if the socket payload stopped carrying the zone. This one walks the real
    path the converter uses, so the wiring itself is covered.
    """
    from mascope_backend.file_converter.socket.events import _build_file_context
    from mascope_backend.file_converter.socket.session import FileContextManager

    manager = FileContextManager()
    manager.register_file(
        _build_file_context(
            {
                "filename": "ACQUISITION.h5",
                "user_id": 1,
                "username": "agent",
                "role_id": 200,
                "access_token": "t",
                "device_id": 7,
                "instrument_timezone": "America/Denver",
            }
        )
    )

    processor = H5Processor(
        socket_client=SimpleNamespace(context_manager=manager),
        file_queue=Queue(),
        shutdown_event=Event(),
    )
    processor.file_to_process = "ACQUISITION.h5"

    zone = processor._context_timezone()
    assert zone is not None and zone.key == "America/Denver"
    assert processor.acquisition_timezone == "America/Denver"


def test_missing_agent_zone_leaves_the_processor_without_one():
    """A web upload carries no zone; the processor must not invent one."""
    from mascope_backend.file_converter.socket.events import _build_file_context
    from mascope_backend.file_converter.socket.session import FileContextManager

    manager = FileContextManager()
    manager.register_file(
        _build_file_context(
            {
                "filename": "ACQUISITION.h5",
                "user_id": 1,
                "username": "person",
                "role_id": 200,
                "access_token": "t",
            }
        )
    )

    processor = H5Processor(
        socket_client=SimpleNamespace(context_manager=manager),
        file_queue=Queue(),
        shutdown_event=Event(),
    )
    processor.file_to_process = "ACQUISITION.h5"

    assert processor._context_timezone() is None
    assert processor.acquisition_timezone is None


# --- Wall-clock resolution (Thermo-style files, which carry no offset) --------

_DENVER = ZoneInfo("America/Denver")
_SYDNEY = ZoneInfo("Australia/Sydney")


@pytest.mark.parametrize(
    "zone,wall,expected_hours",
    [
        (_DENVER, datetime(2026, 7, 1, 12, 0), -6),  # MDT
        (_DENVER, datetime(2026, 1, 15, 9, 30), -7),  # MST
        (_SYDNEY, datetime(2026, 7, 1, 12, 0), 10),  # AEST
    ],
)
def test_unambiguous_wall_times_resolve_cleanly(zone, wall, expected_hours):
    resolved = resolve_wall_time_offset(wall, zone)

    assert resolved.seconds == expected_hours * 3600
    assert resolved.anomaly is None
    assert resolved.alternative_seconds is None


@pytest.mark.parametrize(
    "zone,wall,chosen_hours,alternative_hours",
    [
        # Clocks go back: 01:30 happens twice, first still on summer time.
        (_DENVER, datetime(2026, 11, 1, 1, 30), -6, -7),
        # Southern hemisphere repeats in April, not November.
        (_SYDNEY, datetime(2026, 4, 5, 2, 30), 11, 10),
    ],
)
def test_repeated_hour_is_flagged_and_resolved_to_the_first_pass(
    zone, wall, chosen_hours, alternative_hours
):
    """The hour cannot be pinned down, so it must at least be reported."""
    resolved = resolve_wall_time_offset(wall, zone)

    assert resolved.seconds == chosen_hours * 3600
    assert resolved.anomaly == WALL_TIME_AMBIGUOUS
    assert resolved.alternative_seconds == alternative_hours * 3600


@pytest.mark.parametrize(
    "zone,wall,chosen_hours,alternative_hours",
    [
        # Clocks jump forward: 02:30 never happens.
        (_DENVER, datetime(2026, 3, 8, 2, 30), -7, -6),
        (_SYDNEY, datetime(2026, 10, 4, 2, 30), 10, 11),
    ],
)
def test_skipped_hour_is_flagged_as_nonexistent(
    zone, wall, chosen_hours, alternative_hours
):
    """A skipped hour is a different fault from a repeated one, and says so."""
    resolved = resolve_wall_time_offset(wall, zone)

    assert resolved.seconds == chosen_hours * 3600
    assert resolved.anomaly == WALL_TIME_NONEXISTENT
    assert resolved.alternative_seconds == alternative_hours * 3600


def test_host_zone_branch_matches_the_explicit_zone_branch(monkeypatch):
    """The two branches must answer the same question.

    They reach the offset differently - one maps the wall clock through a
    zone, the other resolves it against the host's - and reading utcoffset()
    off each gives *different quantities* inside a skipped hour, which had the
    host branch calling a gap "ambiguous" with the offsets the wrong way round.
    Pinned by comparing them on the same zone.
    """
    monkeypatch.setenv("TZ", "America/Denver")
    if hasattr(time, "tzset"):
        time.tzset()
    else:  # Windows has no tzset, so the host zone cannot be steered here.
        pytest.skip("TZ cannot be applied without time.tzset()")

    denver = ZoneInfo("America/Denver")
    for wall in (
        datetime(2026, 7, 1, 12, 0),  # ordinary
        datetime(2026, 11, 1, 1, 30),  # repeated
        datetime(2026, 3, 8, 2, 30),  # skipped
    ):
        assert resolve_wall_time_offset(wall, denver) == resolve_wall_time_offset(
            wall, None
        ), f"branches disagree on {wall}"


def test_host_zone_branch_flags_a_skipped_hour(monkeypatch):
    """The fallback branch is what runs whenever no zone is reported."""
    monkeypatch.setenv("TZ", "America/Denver")
    if hasattr(time, "tzset"):
        time.tzset()
    else:
        pytest.skip("TZ cannot be applied without time.tzset()")

    resolved = resolve_wall_time_offset(datetime(2026, 3, 8, 2, 30), None)

    assert resolved.anomaly == WALL_TIME_NONEXISTENT
    assert resolved.seconds == -7 * 3600
    assert resolved.alternative_seconds == -6 * 3600


def test_processor_warns_when_the_wall_clock_is_not_a_single_instant(monkeypatch):
    """A timestamp on the wrong side of a transition must be explainable.

    The offset is unrecoverable from the file, so the warning naming the file
    and both candidates is the whole remedy; without it the hour of error is
    invisible.
    """
    from mascope_backend.file_converter import base_processor

    warnings = []
    monkeypatch.setattr(
        base_processor.runtime.logger, "warning", lambda msg: warnings.append(msg)
    )

    processor = _make_processor(zone_name=None)
    offset = processor._wall_time_offset(datetime(2026, 11, 1, 1, 30), _DENVER)

    assert offset == -6 * 3600
    assert len(warnings) == 1
    message = warnings[0]
    assert "ambiguous" in message
    assert "America/Denver" in message
    assert "ACQUISITION.h5" in message
    # Both candidates, so the size of the possible error is on the record.
    assert "-21600" in message and "-25200" in message


def test_processor_stays_quiet_for_an_ordinary_wall_clock(monkeypatch):
    from mascope_backend.file_converter import base_processor

    warnings = []
    monkeypatch.setattr(
        base_processor.runtime.logger, "warning", lambda msg: warnings.append(msg)
    )

    processor = _make_processor(zone_name=None)
    offset = processor._wall_time_offset(datetime(2026, 7, 1, 12, 0), _DENVER)

    assert offset == -6 * 3600
    assert warnings == []


def _thermo(zone_name, wall, filename="ORBI_sample.raw"):
    """A RawProcessor wired to a stubbed context and a fixed wall clock."""
    from mascope_thermo.processor import RawProcessor

    context = SimpleNamespace(instrument_timezone=zone_name)
    processor = RawProcessor.__new__(RawProcessor)
    processor.file_to_process = filename
    processor.socket_client = SimpleNamespace(
        context_manager=SimpleNamespace(get_context=lambda _name: context)
    )
    processor._per_file_cache = {}
    processor._timestamp_value = wall
    return processor


@pytest.fixture
def thermo_timestamp(monkeypatch):
    """Serve RawProcessor.timestamp from the stub, restored after the test."""
    from mascope_thermo.processor import RawProcessor

    monkeypatch.setattr(
        RawProcessor, "timestamp", property(lambda self: self._timestamp_value)
    )


def test_thermo_agent_branch_flags_a_repeated_hour(thermo_timestamp, monkeypatch):
    """The defect site: a reported zone, a wall clock that happens twice.

    Before this change the branch resolved with Python's default fold and said
    nothing, so the hour of error was stamped "agent" as though authoritative.
    """
    from mascope_backend.file_converter import base_processor

    warnings = []
    monkeypatch.setattr(
        base_processor.runtime.logger, "warning", lambda msg: warnings.append(msg)
    )

    processor = _thermo("America/Denver", "2026-11-01T01:30:00")
    offset, source = processor._resolve_utc_offset()

    assert (offset, source) == (-6 * 3600, "agent")
    assert len(warnings) == 1
    assert WALL_TIME_AMBIGUOUS in warnings[0]
    assert "America/Denver" in warnings[0]


def test_thermo_agent_branch_is_quiet_for_an_ordinary_time(
    thermo_timestamp, monkeypatch
):
    from mascope_backend.file_converter import base_processor

    warnings = []
    monkeypatch.setattr(
        base_processor.runtime.logger, "warning", lambda msg: warnings.append(msg)
    )

    processor = _thermo("America/Denver", "2026-07-01T12:00:00")

    assert processor._resolve_utc_offset() == (-6 * 3600, "agent")
    assert warnings == []


def test_thermo_resolves_once_per_file(thermo_timestamp, monkeypatch):
    """Both schema fields come from one resolution.

    They are separate fields, so the props collector asks for each; resolving
    twice would reopen the raw file and report a DST anomaly twice for a single
    acquisition.
    """
    from mascope_thermo.processor import RawProcessor

    resolutions = []

    def counting_offset(self, local_dt, zone):
        resolutions.append((local_dt, zone))
        return -6 * 3600

    monkeypatch.setattr(RawProcessor, "_wall_time_offset", counting_offset)

    processor = _thermo("America/Denver", "2026-11-01T01:30:00")

    assert processor.utc_offset == -6 * 3600
    assert processor.utc_offset_source == "agent"
    assert len(resolutions) == 1

    # The cache belongs to one file. The next file clears it (the run loop does
    # so as it dequeues), and the same path may well come round again.
    processor._per_file_cache = {}
    processor._timestamp_value = "2026-01-15T09:30:00"
    processor.utc_offset
    assert len(resolutions) == 2
    assert resolutions[1][0] == datetime(2026, 1, 15, 9, 30)
