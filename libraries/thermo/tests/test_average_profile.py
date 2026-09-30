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


# -- the FTMS path: the output grid must land on the samples it averages --

# Realistic Orbitrap conversion parameters, m/z = B/f^2 + C/f^4.
_B = 169405382.13
_C = -71563003.56
# Where each sample sits inside its native cell, as a fraction of the cell. Any
# value clear of 0 and 1 will do: it only has to be somewhere the centre of the
# cell is not, so that a grid built at cell centres cannot pass by accident.
_SUB_CELL = 0.2


def _ftms_scan(points: int = 400) -> tuple:
    """A profile sampled on a uniform frequency grid, as an FT transient's is.

    Two Gaussian peaks, so the profile has slope nearly everywhere and reading
    it half a cell off cannot return the stored value by chance.
    """
    f_start = np.sqrt(_B / 301.0)
    df = (np.sqrt(_B / 300.0) - f_start) / points
    # The first sample anchors the cell lattice, so it sits at the cell's edge;
    # the rest sit _SUB_CELL into theirs.
    offsets = np.concatenate(([0.0], np.arange(1, points) + _SUB_CELL))
    freq = f_start + offsets * df
    mz = _B / freq**2 + _C / freq**4
    intensity = np.zeros_like(mz)
    for centre, height in ((300.25, 1e5), (300.75, 4e4)):
        sigma = centre * FWHM_PPM / 1e6 / (2 * np.sqrt(2 * np.log(2)))
        intensity += height * np.exp(-0.5 * ((mz - centre) / sigma) ** 2)
    return mz, intensity


def test_the_frequency_grid_lands_on_the_samples_it_averages():
    """One scan in, and every grid point comes back at a stored sample, carrying
    that sample's intensity.

    With a single scan there is nothing to average, so the averaged profile can
    only be the scan's own samples. That pins where the output grid sits: a grid
    built at the centres of the native cells lands between the samples, and the
    interpolation then returns a chord across the peak rather than the measured
    value. A raw file keeps only about three points per peak width, so that
    costs several percent of the apex -- and it costs a varying amount, because
    the phase between the cells and the samples drifts across the mass range,
    which is how a change in the reader's m/z axis used to move peak heights.
    """
    mz, intensity = _ftms_scan()
    grid, summed = OpenTFRawBackend("unused.raw")._average_profile_in_frequency(
        [(mz, intensity, _B, _C)]
    )

    assert grid.size and np.all(np.diff(grid) > 0)
    order = np.argsort(mz)
    stored_mz, stored_intensity = mz[order], intensity[order]
    nearest = np.abs(grid[:, None] - stored_mz[None, :]).argmin(axis=1)

    assert np.max(np.abs(grid - stored_mz[nearest]) / grid) < 1e-9, (
        "grid points sit between the stored samples, not on them"
    )
    assert summed == pytest.approx(stored_intensity[nearest], rel=1e-9), (
        "the averaged profile does not carry the stored intensities"
    )
    # The apex is the tallest sample itself, not an interpolation short of it.
    assert summed.max() == pytest.approx(stored_intensity.max(), rel=1e-9)
