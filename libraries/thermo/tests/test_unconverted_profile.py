"""A scan whose profile the reader gives no m/z for refuses the file.

OpenTFRaw converts a scan's profile from frequency to m/z with the
calibration its scan event carries, and from 3.0.0 on gives ``NaN`` for each
point of a scan whose calibration layout it does not know (GitHub issue
#2518). Unchecked, the averaged profile of a selection holding such a scan
came back as one ``NaN`` point, the other scans' points lost with it, and
the scan's own profile came back empty, its points dropped by the m/z range
mask. The OpenTFRaw backend now refuses the file, with which scan and how
many of its points, wherever a profile is read.

The reader is a stand-in that answers what a scan selection, a profile and a
centroid read ask of it, and opens nothing: the backend's path names no file.
"""

import importlib.metadata

import numpy as np
import opentfraw
import pytest

import mascope_thermo.thermo as m_thermo
from mascope_thermo.backend import OpenTFRawBackend
from mascope_thermo.scan_events import MisreadScanEventsError, UnconvertedProfileError
from mascope_thermo.thermo import NoScansFoundError


# Realistic Orbitrap conversion parameters, m/z = B/f^2 + C/f^4.
_B = 169405382.13
_C = -71563003.56
_POINTS = 400
_PEAK = 300.25
_READER = f"OpenTFRaw {importlib.metadata.version('opentfraw')}"


def _profile() -> tuple[np.ndarray, np.ndarray]:
    """A peak sampled on a uniform frequency grid, as an FT transient is."""
    f_start = np.sqrt(_B / 301.0)
    df = (np.sqrt(_B / 300.0) - f_start) / _POINTS
    freq = f_start + np.arange(_POINTS) * df
    mz = _B / freq**2 + _C / freq**4
    sigma = _PEAK * 8.0 / 1e6 / (2 * np.sqrt(2 * np.log(2)))
    return mz, 1e5 * np.exp(-0.5 * ((mz - _PEAK) / sigma) ** 2)


def _no_mz(mz: np.ndarray) -> np.ndarray:
    """The m/z OpenTFRaw gives a scan whose calibration layout it does not
    know."""
    return np.full_like(mz, np.nan)


def _every_other_no_mz(mz: np.ndarray) -> np.ndarray:
    unconverted = mz.copy()
    unconverted[::2] = np.nan
    return unconverted


class Reader:
    """An OpenTFRaw ``RawFile`` of three MS1 scans from an Orbitrap-only
    instrument, the second of them given the m/z ``unconvert`` makes."""

    instrument_model = "Orbitrap Exploris 480"
    first_scan = 1
    num_scans = 3

    def __init__(self, unconvert=_no_mz):
        self.unconvert = unconvert

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
        mz, intensity = _profile()
        return (self.unconvert(mz) if scan_number == 2 else mz), intensity

    def scan_parameters(self, scan_number: int) -> dict:
        return {"Conversion Parameter B:": _B, "Conversion Parameter C:": _C}

    def centroid_labels(self, scan_number: int) -> dict:
        return {
            "mz": [_PEAK],
            "intensity": [1e5],
            "resolution": [120000.0],
            "signal_to_noise": [500.0],
        }


@pytest.fixture
def opened(monkeypatch, tmp_path):
    """Open the OpenTFRaw backend, the default reader, on a :class:`Reader`."""
    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)

    def open_(unconvert=_no_mz):
        monkeypatch.setattr(opentfraw, "RawFile", lambda _path: Reader(unconvert))
        return OpenTFRawBackend(str(tmp_path / "absent.raw")).__enter__()

    return open_


def test_a_scan_with_no_mz_refuses_the_average(opened):
    # Unchecked, this came back as a single NaN point, and scans 1 and 3
    # were lost with it.
    with pytest.raises(UnconvertedProfileError) as refused:
        opened().average_profile([1, 2, 3])

    assert str(refused.value) == (
        f"The profile of scan 2 of this file cannot be read: {_READER} gives no "
        f"m/z for {_POINTS} of its {_POINTS} points, as it does for a scan whose "
        "calibration layout it does not know (GitHub issue #2518)."
    )


def test_a_scan_with_some_points_without_mz_is_refused_too(opened):
    backend = opened(_every_other_no_mz)

    with pytest.raises(UnconvertedProfileError, match=f"{_POINTS // 2} of its"):
        backend.average_profile([1, 2, 3])


def test_the_scans_with_mz_still_average(opened):
    # The refusal is of a read that takes in the scan, not of every read of
    # the file: the scans around it average as they did.
    grid, summed, combined = opened().average_profile([1, 3])

    # Two scans of one profile: the sum peaks on its apex sample, at twice it.
    mz, intensity = _profile()
    assert combined == 2
    assert np.all(np.isfinite(grid)) and np.all(np.isfinite(summed))
    assert grid[np.argmax(summed)] == pytest.approx(mz[np.argmax(intensity)], rel=1e-9)
    assert summed.max() == pytest.approx(2 * intensity.max())


@pytest.mark.parametrize("mz_range", [(None, None), (300.0, 301.0)])
def test_the_scan_is_refused_rather_than_read_empty(opened, mz_range):
    # Unchecked, the m/z range mask dropped every NaN point, and the scan
    # came back empty with no error.
    mz_min, mz_max = mz_range

    with pytest.raises(UnconvertedProfileError, match="scan 2 of this file"):
        opened().profile_per_scan(mz_min=mz_min, mz_max=mz_max)


def test_the_averaged_centroids_are_refused(opened):
    # Their heights are read off the averaged profile's apex, which fell
    # back to the binned heights without a word when the average was NaN.
    with pytest.raises(UnconvertedProfileError):
        opened().average_centroids([1, 2, 3])


@pytest.mark.parametrize(
    "read", [m_thermo.compute_sum_signal, m_thermo.get_signal, m_thermo.get_centroids]
)
def test_every_profile_read_of_the_file_is_refused(opened, tmp_path, read):
    opened()  # installs the stand-in reader the public reads open

    with pytest.raises(UnconvertedProfileError):
        read(str(tmp_path / "absent.raw"))


def test_a_refusal_is_a_misread_refusal_and_no_empty_file(opened):
    # A MisreadScanEventsError, so every handler that lets a misread file's
    # refusal through lets this one through; a ValueError, the class whose
    # text a file's status shows; not the empty selection the file is not.
    with pytest.raises(MisreadScanEventsError) as refused:
        opened().average_profile([2])

    assert isinstance(refused.value, ValueError)
    assert not isinstance(refused.value, NoScansFoundError)
