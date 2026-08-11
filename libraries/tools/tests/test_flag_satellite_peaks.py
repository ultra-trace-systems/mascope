"""Unit tests for flag_satellite_peaks.

The synthetic spectra mirror the ORBIONSTAR short-transient pattern that
motivated the current defaults: sidelobes appear as intensity-asymmetric
mirror pairs around an intense parent (similarity well below 0.5) and as
weak unpaired shoulders out to tens of ppm.
"""

import numpy as np
import pandas as pd
import pytest

from mascope_tools.alignment.utils import flag_satellite_peaks


def _spectrum(rows):
    """Build a peaks frame from (mz, intensity) tuples."""
    mz, intensity = zip(*rows)
    return pd.DataFrame({"mz": np.asarray(mz), "intensity": np.asarray(intensity)})


PARENT_MZ = 62.985401
PARENT_INTENSITY = 1e8


def _ppm(offset_ppm):
    return PARENT_MZ * (1 + offset_ppm * 1e-6)


class TestFlagSatellitePeaks:
    def test_asymmetric_mirror_pair_is_flagged(self):
        # Mirror offsets match within tolerance; intensities differ 2.2x
        # (similarity ~0.45, which the old 0.5 gate rejected).
        peaks = _spectrum(
            [
                (PARENT_MZ, PARENT_INTENSITY),
                (_ppm(-13.4), 5.05e5),
                (_ppm(+13.5), 2.29e5),
            ]
        )
        flags = flag_satellite_peaks(peaks)["is_satellite_peak"]

        assert not flags[0]
        assert flags[1] and flags[2]

    def test_far_mirror_pair_with_low_similarity_is_flagged(self):
        # Offsets +-51 ppm, similarity ~0.36: outside any single-sided window,
        # caught only through symmetric pairing.
        peaks = _spectrum(
            [
                (PARENT_MZ, PARENT_INTENSITY),
                (_ppm(-51.1), 1.2e4),
                (_ppm(+51.1), 3.3e4),
            ]
        )
        flags = flag_satellite_peaks(peaks)["is_satellite_peak"]

        assert not flags[0]
        assert flags[1] and flags[2]

    def test_extremely_dissimilar_pair_is_not_flagged(self):
        # Similarity 0.05 is below even the loose gate; unpaired evidence
        # outside the tight window is not enough.
        peaks = _spectrum(
            [
                (PARENT_MZ, PARENT_INTENSITY),
                (_ppm(-51.1), 1.0e6),
                (_ppm(+51.1), 5.0e4),
            ]
        )
        flags = flag_satellite_peaks(peaks)["is_satellite_peak"]

        assert not flags.any()

    def test_single_sided_shoulder_within_tight_window_is_flagged(self):
        peaks = _spectrum(
            [
                (PARENT_MZ, PARENT_INTENSITY),
                (_ppm(-6.1), 4.0e5),
            ]
        )
        flags = flag_satellite_peaks(peaks)["is_satellite_peak"]

        assert not flags[0]
        assert flags[1]

    def test_single_sided_peak_outside_tight_window_is_not_flagged(self):
        peaks = _spectrum(
            [
                (PARENT_MZ, PARENT_INTENSITY),
                (_ppm(+40.0), 4.0e5),
            ]
        )
        flags = flag_satellite_peaks(peaks)["is_satellite_peak"]

        assert not flags.any()

    def test_comparable_intensity_neighbor_is_not_flagged(self):
        # Ratio 0.2 is above ratio_max: a real isobar, not a sidelobe.
        peaks = _spectrum(
            [
                (PARENT_MZ, PARENT_INTENSITY),
                (_ppm(-10.0), 0.2 * PARENT_INTENSITY),
            ]
        )
        flags = flag_satellite_peaks(peaks)["is_satellite_peak"]

        assert not flags.any()

    def test_isotope_peak_is_not_flagged(self):
        # +1 isotope at +1.00336/z Da with a typical minor-isotope ratio.
        peaks = _spectrum(
            [
                (PARENT_MZ, PARENT_INTENSITY),
                (PARENT_MZ + 1.0033548, 0.01 * PARENT_INTENSITY),
            ]
        )
        flags = flag_satellite_peaks(peaks)["is_satellite_peak"]

        assert not flags.any()

    def test_weak_parent_produces_no_flags(self):
        # Both peaks comparable: neither dominates, nothing is a satellite.
        peaks = _spectrum(
            [
                (PARENT_MZ, 1.0e4),
                (_ppm(-6.1), 0.9e4),
            ]
        )
        flags = flag_satellite_peaks(peaks)["is_satellite_peak"]

        assert not flags.any()

    def test_empty_frame_gets_column(self):
        out = flag_satellite_peaks(pd.DataFrame({"mz": [], "intensity": []}))

        assert "is_satellite_peak" in out.columns
        assert out.empty

    def test_missing_columns_raise(self):
        with pytest.raises(ValueError, match="must contain"):
            flag_satellite_peaks(pd.DataFrame({"mz": [1.0]}))
