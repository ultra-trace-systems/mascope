"""Averaging a profile when a scan carries no frequency calibration.

``average_profile`` averages in the frequency domain, which needs each scan's
Conversion Parameters B and C from its trailer. A non-FTMS scan carries none,
and then the m/z-domain fallback runs: a constant-ppm m/z grid, summed with an
integral-conserving interpolation. These tests drive that fallback with a fake
reader whose trailer holds no conversion parameters.
"""

import numpy as np
import pytest

from mascope_thermo.backend import OpenTFRawBackend


MZ = 301.2
FWHM_PPM = 8.0
HEIGHT = 1e5
SCANS = [1, 2, 3]


def _peak(centre: float = MZ, fwhm_ppm: float = FWHM_PPM, points: int = 81) -> tuple:
    """One Gaussian peak sampled evenly in m/z, out to where it has died away."""
    fwhm = centre * fwhm_ppm / 1e6
    sigma = fwhm / (2 * np.sqrt(2 * np.log(2)))
    mz = np.linspace(centre - 4 * fwhm, centre + 4 * fwhm, points)
    return mz, HEIGHT * np.exp(-0.5 * ((mz - centre) / sigma) ** 2)


class _FakeRaw:
    """The slice of ``opentfraw.RawFile`` that ``average_profile`` touches. The
    trailer carries no Conversion Parameters, as a non-FTMS scan's does not, and
    there are no centroid labels, so the axis is left as the fallback built it.
    """

    def __init__(self, profile: tuple):
        self._profile = profile

    def profile(self, scan_number):
        return self._profile

    def scan_parameters(self, scan_number):
        return {"Ion Injection Time (ms):": 10.0}

    def centroid_labels(self, scan_number):
        empty = np.array([])
        return {
            "mz": empty,
            "intensity": empty,
            "resolution": empty,
            "signal_to_noise": empty,
        }


def _backend() -> OpenTFRawBackend:
    backend = OpenTFRawBackend("unused.raw")
    backend._raw = _FakeRaw(_peak())
    return backend


def test_a_scan_without_conversion_parameters_still_averages():
    """The fallback returns the summed profile, with its apex where the peak is
    and the area of every scan it summed."""
    mz, intensity = _peak()
    grid, summed, combined = _backend().average_profile(SCANS)

    assert combined == len(SCANS)
    assert grid.size and summed.size
    assert np.all(np.isfinite(grid)) and np.all(np.isfinite(summed))
    apex = float(grid[np.argmax(summed)])
    assert abs(apex - MZ) / MZ * 1e6 < 1.0
    assert float(np.trapezoid(summed, grid)) == pytest.approx(
        len(SCANS) * float(np.trapezoid(intensity, mz)), rel=1e-3
    )


def test_averaging_divides_by_the_scans_combined():
    mz, intensity = _peak()
    grid, summed, _ = _backend().average_profile(SCANS, average=True)

    assert float(np.trapezoid(summed, grid)) == pytest.approx(
        float(np.trapezoid(intensity, mz)), rel=1e-3
    )
