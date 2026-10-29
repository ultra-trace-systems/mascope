"""Unit tests for flag_exception_peaks.

An Orbitrap flags each centroid it reads as a side lobe of a brighter peak
(Exception). A peak most of whose per-scan centroids carry the flag is a lobe,
unless it sits on an isotopologue line of a peak that is not one: the
instrument flags by position, and an ion's 2H line lies 2.92 mDa above its 13C
line, where it expects a lobe of that line. The spectra below are built around
the demo dataset's case, an ion of m/z 158 and its lines.
"""

import numpy as np
import pandas as pd
import pytest
from pyteomics.mass import nist_mass

from mascope_tools.alignment.utils import (
    EXCEPTION_MAJORITY,
    ISOTOPOLOGUE_LINES,
    ISOTOPOLOGUE_TOLERANCE_FWHM,
    ISOTOPOLOGUE_TOLERANCE_PPM,
    flag_exception_peaks,
)


ION_MZ = 158.153
ION_HEIGHT = 1e7
RESOLUTION = 120000.0
FWHM = ION_MZ / RESOLUTION

C13 = nist_mass["C"][13][0] - nist_mass["C"][12][0]
H2 = nist_mass["H"][2][0] - nist_mass["H"][1][0]


def _spacing(element, heavy, light):
    return nist_mass[element][heavy][0] - nist_mass[element][light][0]


def _peaks(rows, resolution=RESOLUTION):
    """A peaks frame from (mz, intensity, exception share) tuples."""
    mz, intensity, share = zip(*rows)
    return pd.DataFrame(
        {
            "mz": np.asarray(mz, dtype=float),
            "intensity": np.asarray(intensity, dtype=float),
            "exception_share": np.asarray(share, dtype=float),
            "resolution": np.full(len(mz), resolution),
        }
    )


def _flags(rows, **kwargs):
    return flag_exception_peaks(_peaks(rows), **kwargs)["is_exception_peak"].tolist()


def test_a_peak_most_of_whose_centroids_are_flagged_is_a_lobe():
    # A lobe 33 ppm above the ion at 0.6 % of it, flagged in every scan
    lobe = ION_MZ * (1 + 33e-6)

    assert _flags([(ION_MZ, ION_HEIGHT, 0.0), (lobe, 6e4, 1.0)]) == [False, True]


def test_more_than_half_of_the_centroids_make_a_lobe():
    lobe = ION_MZ * (1 + 33e-6)
    rows = [(ION_MZ, ION_HEIGHT, 0.0)]

    assert _flags([*rows, (lobe, 6e4, EXCEPTION_MAJORITY)]) == [False, False]
    assert _flags([*rows, (lobe, 6e4, 0.51)]) == [False, True]


def test_a_peak_no_centroid_was_read_under_is_not_flagged():
    lobe = ION_MZ * (1 + 33e-6)

    assert _flags([(ION_MZ, ION_HEIGHT, 0.0), (lobe, 6e4, np.nan)]) == [False, False]


def test_a_2h_line_flagged_beside_its_13c_line_is_kept():
    # C9H18: the 13C line at 9.7 % of the ion, the 2H line at 0.2 %; the
    # instrument flags the 2H line, 2.92 mDa above the 13C line, as its lobe
    rows = [
        (ION_MZ, ION_HEIGHT, 0.0),
        (ION_MZ + C13, 0.097 * ION_HEIGHT, 0.0),
        (ION_MZ + H2, 0.002 * ION_HEIGHT, 1.0),
    ]

    assert _flags(rows) == [False, False, False]


def test_a_line_of_a_lobe_is_a_lobe():
    # A lobe of the ion, and the same lobe of its 13C line one spacing up at
    # the 13C line's share: both flagged, so neither spares the other
    lobe = ION_MZ * (1 + 33e-6)
    rows = [
        (ION_MZ, ION_HEIGHT, 0.0),
        (lobe, 6e4, 1.0),
        (lobe + C13, 6e4 * 0.097, 1.0),
    ]

    assert _flags(rows) == [False, True, True]


@pytest.mark.parametrize(
    ("offset_fwhm", "flagged"),
    [
        (0.45, False),
        (-0.45, False),
        (0.55, True),
        (-0.55, True),
    ],
)
def test_a_line_is_spared_within_half_its_width_of_its_place(offset_fwhm, flagged):
    # A real line averaged with a lobe it sits on is centroided between them
    assert ISOTOPOLOGUE_TOLERANCE_FWHM == 0.5
    line = ION_MZ + C13 + offset_fwhm * (ION_MZ + C13) / RESOLUTION
    rows = [(ION_MZ, ION_HEIGHT, 0.0), (line, 0.097 * ION_HEIGHT, 1.0)]

    assert _flags(rows) == [False, flagged]


def test_the_tolerance_is_never_under_a_ppm():
    # At a resolution of 10 million half a width is under a tenth of a ppm
    line = (ION_MZ + C13) * (1 + 0.9 * ISOTOPOLOGUE_TOLERANCE_PPM * 1e-6)
    rows = [(ION_MZ, ION_HEIGHT, 0.0), (line, 0.097 * ION_HEIGHT, 1.0)]

    assert flag_exception_peaks(_peaks(rows, resolution=1e7))[
        "is_exception_peak"
    ].tolist() == [False, False]


def test_a_peak_without_a_width_is_tested_at_a_ppm():
    near = (ION_MZ + C13) * (1 + 0.9e-6)
    far = (ION_MZ + C13) * (1 + 1.1e-6)
    for line, flagged in ((near, False), (far, True)):
        peaks = _peaks([(ION_MZ, ION_HEIGHT, 0.0), (line, 0.097 * ION_HEIGHT, 1.0)])
        peaks["resolution"] = np.nan

        assert flag_exception_peaks(peaks)["is_exception_peak"].tolist() == [
            False,
            flagged,
        ]


@pytest.mark.parametrize(
    ("share", "flagged"),
    [
        # Under half a carbon
        (0.003, True),
        (0.005, False),
        # Over 1.5 x the share of the carbons an m/z 158 ion can hold:
        # 1.5 x 158 / 12 x 1.08 % = 0.214
        (0.20, False),
        (0.22, True),
    ],
)
def test_a_line_has_to_stand_at_a_height_its_ion_can_make(share, flagged):
    rows = [(ION_MZ, ION_HEIGHT, 0.0), (ION_MZ + C13, share * ION_HEIGHT, 1.0)]

    assert _flags(rows) == [False, flagged]


def test_an_81br_line_may_stand_above_its_partner():
    # A monobromide's 81Br line is 0.97 of its 79Br line, and with the noise
    # often the brighter of the two; a 13C line never is
    br = _spacing("Br", 81, 79)
    bromide = 262.0135
    rows = [(bromide, 1e5, 0.0), (bromide + br, 1.1e5, 1.0)]

    assert _flags(rows) == [False, False]
    assert _flags([(ION_MZ, 1e5, 0.0), (ION_MZ + C13, 1.1e5, 1.0)]) == [False, True]


@pytest.mark.parametrize(
    ("element", "heavy", "light", "share"),
    [
        ("C", 13, 12, 0.097),
        ("H", 2, 1, 0.002),
        ("N", 15, 14, 0.0037),
        ("O", 17, 16, 0.0008),
        ("O", 18, 16, 0.004),
        ("S", 34, 32, 0.045),
        ("Cl", 37, 35, 0.32),
        ("Br", 81, 79, 0.97),
    ],
)
def test_each_line_spares_a_peak(element, heavy, light, share):
    assert (element, heavy, light) in ISOTOPOLOGUE_LINES
    partner = 200.0
    line = partner + _spacing(element, heavy, light)
    rows = [(partner, 1e6, 0.0), (line, share * 1e6, 1.0)]

    assert _flags(rows) == [False, False]


def test_a_29si_line_spares_nothing():
    # It sits where the negative lobe of an ion's 13C line falls
    line = 200.0 + _spacing("Si", 29, 28)

    assert _flags([(200.0, 1e6, 0.0), (line, 0.05e6, 1.0)]) == [False, True]


def test_the_partner_is_one_spacing_below():
    # 2H above the ion, not below it
    rows = [(ION_MZ, ION_HEIGHT, 0.0), (ION_MZ - H2, 0.002 * ION_HEIGHT, 1.0)]

    assert _flags(rows) == [False, True]


def test_the_order_of_the_peaks_does_not_matter():
    lobe = ION_MZ * (1 + 33e-6)
    rows = [
        (ION_MZ + H2, 0.002 * ION_HEIGHT, 1.0),
        (lobe, 6e4, 1.0),
        (ION_MZ + C13, 0.097 * ION_HEIGHT, 0.0),
        (ION_MZ, ION_HEIGHT, 0.0),
    ]

    assert _flags(rows) == [False, True, False, False]


def test_the_peaks_are_copied_with_the_flag():
    peaks = _peaks([(ION_MZ, ION_HEIGHT, 0.0), (ION_MZ * (1 + 33e-6), 6e4, 1.0)])

    out = flag_exception_peaks(peaks)

    assert "is_exception_peak" not in peaks.columns
    assert out["is_exception_peak"].dtype == bool
    assert out.drop(columns="is_exception_peak").equals(peaks)


def test_an_empty_list_flags_nothing():
    out = flag_exception_peaks(_peaks([(ION_MZ, ION_HEIGHT, 0.0)]).iloc[:0])

    assert out["is_exception_peak"].tolist() == []


@pytest.mark.parametrize("column", ["mz", "intensity", "exception_share", "resolution"])
def test_a_missing_column_is_refused(column):
    peaks = _peaks([(ION_MZ, ION_HEIGHT, 0.0)]).drop(columns=column)

    with pytest.raises(ValueError, match=column):
        flag_exception_peaks(peaks)
