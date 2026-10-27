"""Tests for the reader-backend selection seam (``mascope_thermo.backend``).

These cover the ``MASCOPE_THERMO_BACKEND`` switch and don't read any file, so
they run anywhere.
"""

import importlib.metadata
import re
from types import SimpleNamespace

import pytest

from mascope_thermo import backend as m_backend
from mascope_thermo.scan_filter import parse_scan_filter
from mascope_thermo.thermo import NoScansFoundError


def test_default_backend_is_opentfraw(monkeypatch):
    monkeypatch.delenv(m_backend.ENV_BACKEND, raising=False)
    be = m_backend.open_backend("dummy.raw")
    assert isinstance(be, m_backend.OpenTFRawBackend)
    # OpenTFRawBackend structurally satisfies the ReaderBackend protocol.
    assert isinstance(be, m_backend.ReaderBackend)


def test_explicit_thermo_backend(monkeypatch):
    monkeypatch.setenv(m_backend.ENV_BACKEND, "thermo")
    assert isinstance(m_backend.open_backend("dummy.raw"), m_backend.ThermoBackend)


def test_backend_name_is_case_insensitive(monkeypatch):
    monkeypatch.setenv(m_backend.ENV_BACKEND, "Thermo")
    assert isinstance(m_backend.open_backend("dummy.raw"), m_backend.ThermoBackend)


def test_opentfraw_backend_selected(monkeypatch):
    monkeypatch.setenv(m_backend.ENV_BACKEND, "opentfraw")
    be = m_backend.open_backend("dummy.raw")
    assert isinstance(be, m_backend.OpenTFRawBackend)
    assert isinstance(be, m_backend.ReaderBackend)


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv(m_backend.ENV_BACKEND, "bogus")
    with pytest.raises(ValueError):
        m_backend.open_backend("dummy.raw")


# What the converter tells the axis fingerprint the file was read with.
@pytest.mark.parametrize(
    ("selected", "name"),
    [(None, "opentfraw"), ("opentfraw", "opentfraw"), ("Thermo", "thermo")],
)
def test_backend_name_is_the_selected_reader(monkeypatch, selected, name):
    if selected is None:
        monkeypatch.delenv(m_backend.ENV_BACKEND, raising=False)
    else:
        monkeypatch.setenv(m_backend.ENV_BACKEND, selected)
    assert m_backend.backend_name() == name


def test_backend_name_rejects_an_unknown_reader(monkeypatch):
    monkeypatch.setenv(m_backend.ENV_BACKEND, "bogus")
    with pytest.raises(ValueError, match="bogus"):
        m_backend.backend_name()


# A cached averaged profile is keyed on the signature of what computed it, so a
# reader upgrade has to change the signature by itself.
def test_profile_signature_names_the_reader_and_its_version(monkeypatch):
    monkeypatch.delenv(m_backend.ENV_BACKEND, raising=False)
    version = importlib.metadata.version("opentfraw")
    generation = m_backend.AVERAGED_PROFILE_GENERATION
    assert m_backend.averaged_profile_signature() == f"otf{version}-g{generation}"


def test_profile_signature_of_the_thermo_library(monkeypatch):
    monkeypatch.setenv(m_backend.ENV_BACKEND, "Thermo")
    assert m_backend.averaged_profile_signature() == "thermo"


def test_profile_signature_rejects_an_unknown_backend(monkeypatch):
    monkeypatch.setenv(m_backend.ENV_BACKEND, "bogus")
    with pytest.raises(ValueError):
        m_backend.averaged_profile_signature()


def test_profile_signature_is_safe_in_a_file_name(monkeypatch):
    monkeypatch.delenv(m_backend.ENV_BACKEND, raising=False)
    assert re.fullmatch(r"[A-Za-z0-9.+-]+", m_backend.averaged_profile_signature())


# The run's start and end times are the run header's, the MS controller's.


def test_opentfraw_run_times_are_the_ms_controllers():
    be = m_backend.OpenTFRawBackend("dummy.raw")
    be._raw = SimpleNamespace(
        controllers=lambda: [
            {"is_ms_controller": False, "start_time": 0.0, "end_time": 0.0},
            {"is_ms_controller": True, "start_time": 0.25, "end_time": 9.5},
        ]
    )
    assert be.run_time_range() == (15.0, 570.0)


def test_opentfraw_file_without_an_ms_controller_has_no_scans():
    be = m_backend.OpenTFRawBackend("dummy.raw")
    be._raw = SimpleNamespace(controllers=lambda: [{"is_ms_controller": False}])
    with pytest.raises(NoScansFoundError):
        be.run_time_range()


def test_thermo_run_times_are_its_run_headers():
    be = m_backend.ThermoBackend("dummy.raw")
    be._raw = SimpleNamespace(RunHeaderEx=SimpleNamespace(StartTime=0.25, EndTime=9.5))
    assert be.run_time_range() == (15.0, 570.0)


# The Thermo library writes a scan range bound as the method's double rounded
# to four decimals, OpenTFRaw as the single-precision float it reads (#2658).
# Measured on a customer MS2 file: the method holds 181.636952, the Thermo
# library writes 181.6370 and OpenTFRaw 181.6369.


class _ThermoFilter:
    """An ``IScanFilter`` as far as the filter text reads it."""

    def __init__(self, text, ranges):
        self._text = text
        self._ranges = ranges
        self.MassRangeCount = len(ranges)

    def ToString(self):
        return self._text

    def GetMassRange(self, index):
        low, high = self._ranges[index]
        return SimpleNamespace(Low=low, High=high)


_MS2_RENDERED = "FTMS + p NSI Full ms2 158.1176@hcd1.00 [40.0000-181.6370]"
_MS2_OPENTFRAW = "FTMS + p NSI Full ms2 158.1176@hcd1.00 [40.0000-181.6369]"


def _thermo_backend_over(scan_filter):
    """A ThermoBackend over one scan of the given filter."""
    be = m_backend.ThermoBackend("dummy.raw")
    stats = SimpleNamespace(StartTime=0.5, TIC=1.0, SegmentNumber=0, ScanEventNumber=0)
    selector = SimpleNamespace(
        all_scan_indices=[1],
        raw_scan_filters=[scan_filter],
        raw_scan_stats=[stats],
        scan_indices_1based=[1],
        scan_filters=[scan_filter],
        scan_stats=[stats],
    )
    be._selector = lambda *args, **kwargs: selector
    be.trailer_numbers = lambda *args, **kwargs: {}
    be.scan_trailer = lambda scan: {}
    return be


def test_thermo_writes_a_scan_range_as_opentfraw_does():
    be = _thermo_backend_over(_ThermoFilter(_MS2_RENDERED, [(40.0, 181.636952)]))
    (row,) = be.scan_filters()
    assert row["filter"] == _MS2_OPENTFRAW
    # So the two backends key the stream alike.
    assert parse_scan_filter(row["filter"]).stream_key(120000) == parse_scan_filter(
        _MS2_OPENTFRAW
    ).stream_key(120000)


def test_thermo_ms2_records_read_the_range_as_opentfraw_does():
    be = _thermo_backend_over(_ThermoFilter(_MS2_RENDERED, [(40.0, 181.636952)]))
    (record,) = be.ms2_scan_records()
    assert record["high_mass"] == 181.6369
    assert record["low_mass"] == 40.0


@pytest.mark.parametrize(
    ("text", "ranges", "written"),
    [
        # A survey range is the same either way.
        (
            "FTMS - p NSI Full ms [40.0000-600.0000]",
            [(40.0, 600.0)],
            "FTMS - p NSI Full ms [40.0000-600.0000]",
        ),
        # Every range of a multiplexed scan, in the filter's order.
        (
            "FTMS + p NSI SIM msx ms [181.6370-190.0000, 100.0000-110.0000]",
            [(181.636952, 190.0), (100.0, 110.0)],
            "FTMS + p NSI SIM msx ms [181.6369-190.0000, 100.0000-110.0000]",
        ),
        # Fewer decimals, as an ion trap's filter has, become four.
        (
            "ITMS + c NSI Full ms [135.00-1000.00]",
            [(135.0, 1000.0)],
            "ITMS + c NSI Full ms [135.0000-1000.0000]",
        ),
        # A filter whose text does not close on the ranges it holds is
        # left as written.
        ("FTMS + p NSI Full ms", [(40.0, 600.0)], "FTMS + p NSI Full ms"),
        (
            "FTMS + p NSI Full ms [40.0000-600.0000]",
            [(40.0, 600.0), (700.0, 800.0)],
            "FTMS + p NSI Full ms [40.0000-600.0000]",
        ),
        (
            "FTMS + p NSI Full ms [40.0000-600.0000]",
            [],
            "FTMS + p NSI Full ms [40.0000-600.0000]",
        ),
    ],
)
def test_thermo_filter_text_rewrites_only_the_ranges_it_holds(text, ranges, written):
    assert m_backend._thermo_filter_text(_ThermoFilter(text, ranges)) == written
