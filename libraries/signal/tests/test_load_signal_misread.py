"""A raw file whose scan events are misread is refused, not read as empty.

The OpenTFRaw backend refuses a raw Orbitrap file whose scans read as an
analyzer no Orbitrap instrument has (``mascope_thermo.scan_events``, GitHub
issue #2394). ``load_signal`` answers a ``ValueError`` from the reader with an
empty signal, and the refusal is one: answered that way, the caller would show
no data and no reason. So it is let through (GitHub issue #2411).

The reader under the backend is a stand-in that answers ``scan_table()`` and
opens nothing, so what is pinned is the refusal the backend itself raises on
the way from ``load_signal`` down, not one the test raises in its place.
"""

import opentfraw
import pytest
from signal_test_support import SIGNAL_TEST_FILENAME

import mascope_signal.compute as m_compute
from mascope_thermo.scan_events import MisreadScanEventsError


class _Reader:
    """An OpenTFRaw ``RawFile`` reduced to what a scan selection reads: a
    Q Exactive's survey scan and two fragment scans, one of them read as a
    single quadrupole."""

    instrument_model = "Q Exactive Plus"
    first_scan = 1
    num_scans = 3

    def scan_table(self) -> dict:
        return {
            "scan_number": [1, 2, 3],
            "retention_time": [1 / 60, 2 / 60, 3 / 60],
            "polarity": ["+"] * 3,
            "ms_level": [1, 2, 2],
            "analyzer": ["FTMS", "SQMS", "FTMS"],
            "total_ion_current": [1.0] * 3,
            "low_mz": [40.0] * 3,
            "high_mz": [600.0] * 3,
        }


def test_a_misread_file_is_refused_rather_than_read_as_empty(
    monkeypatch, sample_file_path
):
    monkeypatch.setattr(m_compute.m_name, "get_sample_file_type", lambda _: "orbi_raw")
    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)
    monkeypatch.setattr(opentfraw, "RawFile", lambda _path: _Reader())

    with pytest.raises(MisreadScanEventsError) as refused:
        m_compute.load_signal(SIGNAL_TEST_FILENAME)

    assert "1 of its 3 scans read as an analyzer no Orbitrap instrument has" in (
        str(refused.value)
    )
