"""A raw file with a scan the reader gives no m/z for is refused, not read.

OpenTFRaw gives ``NaN`` m/z for a scan whose calibration layout it does not
know, and the OpenTFRaw backend refuses the file when it reads that scan's
profile (``mascope_thermo.scan_events``, GitHub issue #2518). ``load_signal``
answers a ``ValueError`` from the reader with an empty signal, and the
refusal is one: it is let through, as a misread file's is. The sum signal
is not cached either: before, its one ``NaN`` point was, and every view of
the sample read it from there.

The reader under the backend is a stand-in that answers a scan selection and
a profile read and opens nothing, so what is pinned is the refusal the
backend itself raises on the way down, not one the test raises in its place.
"""

import os

import numpy as np
import opentfraw
import pytest
from signal_test_support import SIGNAL_TEST_FILENAME

import mascope_file.name as m_name
import mascope_signal.compute as m_compute
from mascope_thermo.scan_events import UnconvertedProfileError


# Realistic Orbitrap conversion parameters, m/z = B/f^2 + C/f^4.
_B = 169405382.13
_C = -71563003.56


class _Reader:
    """An OpenTFRaw ``RawFile`` reduced to what a profile read asks of it:
    three survey scans from an Orbitrap-only instrument, the second of which
    has a calibration layout the reader does not know."""

    instrument_model = "Orbitrap Exploris 480"
    first_scan = 1
    num_scans = 3

    def scan_table(self) -> dict:
        return {
            "scan_number": [1, 2, 3],
            "retention_time": [1 / 60, 2 / 60, 3 / 60],
            "polarity": ["+"] * 3,
            "ms_level": [1] * 3,
            "analyzer": ["FTMS"] * 3,
            "total_ion_current": [1.0] * 3,
            "low_mz": [300.0] * 3,
            "high_mz": [301.0] * 3,
        }

    def profile(self, scan_number: int) -> tuple[np.ndarray, np.ndarray]:
        freq = np.linspace(np.sqrt(_B / 301.0), np.sqrt(_B / 300.0), 200)
        mz = _B / freq**2 + _C / freq**4
        if scan_number == 2:
            mz = np.full_like(mz, np.nan)
        return mz, np.full(mz.size, 1e3)

    def scan_parameters(self, scan_number: int) -> dict:
        return {"Conversion Parameter B:": _B, "Conversion Parameter C:": _C}


@pytest.fixture
def unconverted(monkeypatch, sample_file_path):
    """The test sample as a raw Orbitrap file the stand-in reader reads."""
    monkeypatch.setattr(m_compute.m_name, "get_sample_file_type", lambda _: "orbi_raw")
    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)
    monkeypatch.setattr(opentfraw, "RawFile", lambda _path: _Reader())


def test_the_signal_is_refused_rather_than_read_as_empty(unconverted):
    with pytest.raises(UnconvertedProfileError) as refused:
        m_compute.load_signal(SIGNAL_TEST_FILENAME)

    assert "The profile of scan 2 of this file cannot be read" in str(refused.value)


def test_the_sum_signal_is_refused_and_nothing_is_cached(unconverted):
    cached = m_name.filename_to_zarr_path(
        SIGNAL_TEST_FILENAME,
        m_compute._get_sum_signal_hash_name(None, None, None, "orbi_raw"),
    )

    for _ in range(2):
        with pytest.raises(UnconvertedProfileError, match="scan 2 of this file"):
            m_compute.get_sum_signal(SIGNAL_TEST_FILENAME)

    assert not os.path.exists(cached)
