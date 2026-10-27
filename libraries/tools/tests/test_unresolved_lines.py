"""A peak the file cannot split is scored against every line it holds.

On a TOF the one peak at the 13C position also holds the 15N, 2H and 17O lines,
a few mDa away; scored against the 13C line alone its share reads high. Where
the run knows the file's resolving power, the envelope match merges the lines
one peak of that width holds into one line.
"""

import numpy as np
import polars as pl
import pytest

from mascope_tools.composition.heuristic_filter import (
    match_isotopic_pattern,
    merge_unresolved_lines,
    predict_isotopes,
    predict_resolved_isotopes,
)
from mascope_tools.composition.models import PatternScoring


#: C10H15NO3, protonated.
ION = "C10H16NO3+"
TOF = 8000.0
ORBITRAP = 120000.0
BASE_INTENSITY = 1.0e6


def _resolving(power):
    return lambda mz: power


def _fine_structure():
    """Every line of the ion, deep enough to hold the four at M+1."""
    mzs, intensities, labels = predict_isotopes(ION[:-1], 1, threshold=1e-4)
    share = dict(zip(labels, intensities / intensities[labels.index("M0")]))
    mass = dict(zip(labels, mzs))
    return share, mass


M1_MEMBERS = ("13C", "15N", "2H", "17O")


def _match(scoring, m1_share):
    """Match the ion against its M0 and one peak at M+1 holding ``m1_share``."""
    share, mass = _fine_structure()
    peaks = pl.DataFrame(
        {
            "mz": [mass["M0"], mass["13C"]],
            "intensity": [BASE_INTENSITY, m1_share * BASE_INTENSITY],
        }
    ).sort("mz")
    candidates = [{"formula": "C10H15NO3", "ion": ION, "composition_error_ppm": 0.1}]
    _, isotopes = match_isotopic_pattern(candidates, peaks, scoring)
    envelope = isotopes[0]
    at_m1 = list(envelope["labels"]).index("13C")
    return envelope, at_m1


def test_a_tof_peak_at_13c_predicts_the_sum_of_the_lines_it_holds():
    share, _ = _fine_structure()
    summed = sum(share[label] for label in M1_MEMBERS)

    envelope, at_m1 = _match(PatternScoring(resolution=_resolving(TOF)), summed)

    assert envelope["predicted_intensities"][at_m1] == pytest.approx(summed, rel=1e-6)
    # And a peak reading that sum is read on its prediction, not 6% high.
    assert envelope["intensity_errors"][at_m1] == pytest.approx(0.0, abs=1e-9)


def test_the_merged_line_sits_at_its_members_weighted_centre():
    share, mass = _fine_structure()
    centre = sum(share[label] * mass[label] for label in M1_MEMBERS) / sum(
        share[label] for label in M1_MEMBERS
    )

    envelope, at_m1 = _match(PatternScoring(resolution=_resolving(TOF)), 0.11)

    assert envelope["predicted_masses"][at_m1] == pytest.approx(centre, abs=1e-9)


def test_without_a_resolution_the_13c_line_stands_alone():
    share, _ = _fine_structure()

    envelope, at_m1 = _match(PatternScoring(), share["13C"])

    assert envelope["predicted_intensities"][at_m1] == pytest.approx(share["13C"])


def test_an_orbitrap_separates_the_lines_and_keeps_them_apart():
    share, _ = _fine_structure()

    envelope, at_m1 = _match(PatternScoring(resolution=_resolving(ORBITRAP)), 0.11)

    assert envelope["predicted_intensities"][at_m1] == pytest.approx(share["13C"])


def test_the_members_under_the_floor_still_count_toward_the_merged_share():
    # At a 1% floor the 15N, 2H and 17O lines are each under it; the merged
    # line still carries them, because they are predicted deeper first.
    share, _ = _fine_structure()
    assert all(share[label] < 0.01 for label in M1_MEMBERS[1:])

    mzs, intensities, labels = predict_resolved_isotopes(
        ION[:-1], 1, 0.01, _resolving(TOF)
    )
    relative = intensities / intensities[labels.index("M0")]

    assert relative[labels.index("13C")] == pytest.approx(
        sum(share[label] for label in M1_MEMBERS), rel=1e-6
    )


def test_the_floor_applies_to_the_merged_lines():
    # At M+2 the 13C2 and 18O lines are each under a 1% floor and together
    # over it: the TOF shows one peak there, and the envelope predicts it.
    share, _ = _fine_structure()
    assert share["13C2"] < 0.01 and share["18O"] < 0.01
    assert share["13C2"] + share["18O"] > 0.01

    _, _, separate = predict_resolved_isotopes(ION[:-1], 1, 0.01, None)
    _, _, merged = predict_resolved_isotopes(ION[:-1], 1, 0.01, _resolving(TOF))

    assert len(separate) == 2
    assert len(merged) == 3


def test_lines_half_a_width_apart_are_two_peaks():
    # One peak: from the brightest line, every line less than half a FWHM
    # from it. At m/z 100 and a resolving power of 1000 that is 0.05.
    mzs, intensities, labels = merge_unresolved_lines(
        [100.0, 100.0499, 100.0501],
        [1.0, 0.5, 0.25],
        ["M0", "a", "b"],
        _resolving(1000.0),
    )

    assert labels == ["M0", "b"]
    np.testing.assert_allclose(intensities, [1.5, 0.25])
    assert mzs[0] == pytest.approx((100.0 + 0.5 * 100.0499) / 1.5)


def test_a_bin_starts_at_its_brightest_line_not_its_lightest():
    # A dim line 6 mDa under the bright one and two more 1 and 3 mDa over it,
    # half a FWHM 6.6 mDa: started at the dim line, the bin would take the
    # bright line and leave the two over it behind.
    _, intensities, labels = merge_unresolved_lines(
        [198.0, 198.0063, 198.0072, 198.0093],
        [0.004, 0.1, 0.002, 0.0015],
        ["15N", "13C", "17O", "2H"],
        _resolving(198.0063 / 0.0132),
    )

    assert labels == ["13C"]
    np.testing.assert_allclose(intensities, [0.1075])


@pytest.mark.parametrize("power", [TOF, 15000.0])
def test_the_13c_peak_holds_all_four_lines_up_to_a_resolving_power_of_15000(power):
    # At 15000 half a FWHM at m/z 199 is 6.6 mDa: the 15N line is 6.3 mDa under
    # the 13C line, the 17O and 2H lines under 3 mDa over it.
    share, _ = _fine_structure()

    mzs, intensities, labels = predict_resolved_isotopes(
        ION[:-1], 1, 0.01, _resolving(power)
    )
    relative = intensities / intensities[labels.index("M0")]

    assert relative[labels.index("13C")] == pytest.approx(
        sum(share[label] for label in M1_MEMBERS), rel=1e-6
    )


def test_the_ions_own_line_keeps_its_label_beside_a_brighter_member():
    # The anchor looks for the M0 label, and a merged line holding it is the
    # ion's own whatever else it holds.
    _, _, labels = merge_unresolved_lines(
        [200.0, 200.001], [0.2, 1.0], ["M0", "x"], _resolving(TOF)
    )

    assert labels == ["M0"]


def test_otherwise_the_brightest_member_names_the_line():
    _, _, labels = merge_unresolved_lines(
        [200.0, 201.0, 201.003],
        [1.0, 0.01, 0.1],
        ["M0", "15N", "13C"],
        _resolving(TOF),
    )

    assert labels == ["M0", "13C"]


@pytest.mark.parametrize(
    "resolution",
    [_resolving(float("nan")), _resolving(0.0), lambda mz: 1 / 0],
    ids=["nan", "zero", "raises"],
)
def test_a_resolution_that_reads_nothing_merges_nothing(resolution):
    mzs, intensities, labels = merge_unresolved_lines(
        [200.0, 200.001], [1.0, 0.5], ["M0", "x"], resolution
    )

    assert labels == ["M0", "x"]
    np.testing.assert_allclose(intensities, [1.0, 0.5])


def test_a_merged_line_under_the_floor_is_left_out():
    # The members are predicted deeper than the floor; what the floor then
    # judges is the merged line. At 5% the M+2 peak (about 1.2%) is not there.
    _, intensities, labels = predict_resolved_isotopes(
        ION[:-1], 1, 0.05, _resolving(TOF)
    )

    assert labels == ["M0", "13C"]
    assert intensities.min() >= 0.05 * intensities.max()


@pytest.mark.parametrize("power", [TOF, None], ids=["tof", "none"])
def test_a_labelled_reagent_is_predicted_at_its_own_purity(power):
    # A 95% 15N nitrate's unlabelled remainder, one mass unit under the ion, is
    # 5/95 of it; the default purity of 98% would make it 2/98.
    _, intensities, labels = predict_resolved_isotopes(
        "O3^N", -1, 1e-3, None if power is None else _resolving(power), purity=0.95
    )

    share = dict(zip(labels, intensities / intensities[labels.index("M0")]))
    assert share["14N"] == pytest.approx(0.05 / 0.95, rel=1e-3)
