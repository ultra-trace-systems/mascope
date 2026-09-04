"""Averaged centroids across a calibration step between scans.

The OpenTFRaw backend averages centroids by pooling every scan's label peaks
and ppm-binning them. What moves a peak's label m/z between scans is the
scan's own frequency->m/z calibration (Conversion Parameters B/C); when a lock
mass engages part-way through a file that calibration steps by a few ppm at
once, which at low m/z is wider than both the ppm bin and half a FWHM. These
tests drive ``average_centroids`` with a fake reader whose scans carry such a
step and check that a peak's centroids bin by physical frequency, that resolved
neighbours stay apart, and that a reader without B/C falls back to binning the
labels as written.
"""

import numpy as np
import pytest

from mascope_thermo.backend import OpenTFRawBackend


# A real Orbitrap calibration (m/z = B/f^2 + C/f^4) and a resolution at which
# the FWHM at m/z 42 is ~4 ppm, so half a FWHM is below a 2.5 ppm step.
B_REF = 169_413_136.0
C_REF = 25_075_658.0
RESOLUTION = 249_000.0
STEP_PPM = 2.5
SCANS = list(range(1, 8))


class _FakeRaw:
    """The slice of ``opentfraw.RawFile`` that ``average_centroids`` touches:
    centroid labels and trailer parameters per scan, and a profile (empty
    here, which keeps the profile-apex height refinement out of the picture).
    """

    def __init__(self, scans: dict[int, dict]):
        self._scans = scans

    def centroid_labels(self, scan_number):
        return self._scans[scan_number]["labels"]

    def scan_parameters(self, scan_number):
        return self._scans[scan_number]["params"]

    def profile(self, scan_number):
        return np.array([]), np.array([])


def _mz_on(b: float, c: float, f: np.ndarray) -> np.ndarray:
    return b / f**2 + c / f**4


def _labels(mzs, intensity=1e5, sn=4000.0) -> dict:
    mzs = np.asarray(mzs, dtype=float)
    return {
        "mz": mzs,
        "intensity": np.full(mzs.size, intensity),
        "resolution": np.full(mzs.size, RESOLUTION),
        "signal_to_noise": np.full(mzs.size, sn),
    }


def _stepped_scans(peaks_mz, with_params=True) -> dict[int, dict]:
    """Seven scans of the same ions. Scans 1-3 sit on a calibration
    ``STEP_PPM`` above the reference (the lock mass not yet found), scans 4-7
    on the reference; ``peaks_mz`` are the ions' m/z on the reference."""
    freqs = OpenTFRawBackend._mz_to_freq(np.asarray(peaks_mz, float), B_REF, C_REF)
    scans = {}
    for n in SCANS:
        scale = 1 + STEP_PPM / 1e6 if n <= 3 else 1.0
        b, c = B_REF * scale, C_REF * scale
        scans[n] = {
            "labels": _labels(_mz_on(b, c, freqs)),
            "params": (
                {"Conversion Parameter B:": b, "Conversion Parameter C:": c}
                if with_params
                else {}
            ),
        }
    return scans


def _backend(scans: dict[int, dict]) -> OpenTFRawBackend:
    backend = OpenTFRawBackend("unused.raw")
    backend._raw = _FakeRaw(scans)
    return backend


def _all_labels(scans: dict[int, dict]) -> np.ndarray:
    return np.concatenate([scans[n]["labels"]["mz"] for n in SCANS])


def test_a_peak_split_by_a_calibration_step_is_one_centroid():
    scans = _stepped_scans([42.0086])
    labels = _all_labels(scans)
    # The step really is wider than the bin: as written, the labels form two
    # groups 2.5 ppm apart.
    assert (labels.max() - labels.min()) / labels.min() * 1e6 == pytest.approx(
        STEP_PPM, abs=0.01
    )

    masses, intensities, resolutions, sn = _backend(scans).average_centroids(
        SCANS, ppm=1
    )

    assert masses.size == 1
    # The m/z is the intensity-weighted mean of the labels as written, which is
    # what a merged peak has always reported.
    assert masses[0] == pytest.approx(labels.mean(), rel=1e-12)
    assert intensities[0] == pytest.approx(7e5)
    assert resolutions[0] == pytest.approx(RESOLUTION)
    # Present in all seven scans: mean S:N * n / sqrt(N).
    assert sn[0] == pytest.approx(4000.0 * 7 / np.sqrt(7))


def test_resolved_neighbours_stay_apart_across_the_step():
    # Two ions 1.5 FWHM (6 ppm) apart in every scan.
    scans = _stepped_scans([42.0086, 42.0086 * (1 + 6.0 / 1e6)])

    masses, intensities, _, _ = _backend(scans).average_centroids(SCANS, ppm=1)

    assert masses.size == 2
    assert np.all(np.diff(masses) > 0)
    np.testing.assert_allclose(intensities, [7e5, 7e5])


def test_without_conversion_parameters_labels_bin_as_written():
    # A reader without B/C keeps the old behaviour, which is what the
    # frequency key exists to avoid: the same step yields one bin per
    # calibration, and the half-FWHM merge cannot bridge them.
    scans = _stepped_scans([42.0086], with_params=False)

    masses, intensities, _, _ = _backend(scans).average_centroids(SCANS, ppm=1)

    assert masses.size == 2
    np.testing.assert_allclose(np.sort(intensities), [3e5, 4e5])


def test_labels_on_one_calibration_agree_across_scans():
    scans = _stepped_scans([42.0086, 100.0])
    parts = [scans[n]["labels"]["mz"] for n in SCANS]

    keys = _backend(scans)._labels_on_one_calibration(SCANS, parts)

    assert keys is not None and len(keys) == len(parts)
    # The same ion keys to the same value from every scan, whatever the scan's
    # calibration did to its label.
    for key in keys:
        np.testing.assert_allclose(key, keys[0], rtol=1e-12)
    # A scan on the reference calibration is passed through unchanged.
    assert any(key is part for key, part in zip(keys, parts))


def test_labels_on_one_calibration_needs_every_scan_to_carry_parameters():
    scans = _stepped_scans([42.0086])
    scans[5]["params"] = {}
    parts = [scans[n]["labels"]["mz"] for n in SCANS]

    assert _backend(scans)._labels_on_one_calibration(SCANS, parts) is None
