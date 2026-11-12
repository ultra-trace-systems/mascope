"""A peak the file cannot split is scored against every line it holds.

On a TOF the one peak at the 13C position also holds the 15N, 2H and 17O lines,
a few mDa away; scored against the 13C line alone its share reads high. Where
the run knows the file's resolving power, the envelope match merges the lines
one peak of that width holds into one line.
"""

import numpy as np
import polars as pl
import pytest

from mascope_tools.composition import heuristic_filter
from mascope_tools.composition.heuristic_filter import (
    _fold_under_floor,
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
    # At 120000 a FWHM at m/z 199 is 1.7 mDa: the 15N line (6.3 mDa under the
    # 13C line) and the 2H line (2.9 mDa over it) are peaks of their own, under
    # the floor. The 17O line, 0.9 mDa over it, is not: one peak holds both.
    share, _ = _fine_structure()

    envelope, at_m1 = _match(PatternScoring(resolution=_resolving(ORBITRAP)), 0.11)

    assert envelope["predicted_intensities"][at_m1] == pytest.approx(
        share["13C"] + share["17O"]
    )


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


def _m1_share(ion, floor, power):
    """The predicted M+1 share of the ion's envelope, and every line at M+1.

    The second value is every fine-structure line one mass unit up, each
    labelled, predicted far deeper than any M+1 line of these ions, so it does
    not move with the members' depth.
    """
    mzs, intensities, labels = predict_resolved_isotopes(
        ion, 1, floor, _resolving(power)
    )
    anchor = labels.index("M0")
    at_m1 = np.round(mzs - mzs[anchor]) == 1
    predicted = float(intensities[at_m1].sum() / intensities[anchor])

    fine_mz, fine, fine_labels = predict_isotopes(ion, 1, threshold=1e-7)
    fine_anchor = fine_labels.index("M0")
    reached = {
        label: float(share / fine[fine_anchor])
        for mz, share, label in zip(fine_mz, fine, fine_labels)
        if round(float(mz - fine_mz[fine_anchor])) == 1
    }
    return predicted, reached


@pytest.mark.parametrize(
    ("ion", "power", "members"),
    [
        # The 15N line is 6.3 mDa under the 13C line, half a FWHM 5.4 mDa:
        # a bin of its own at 0.37% of M0, under the 1% floor.
        ("C4H8NO", 8000.0, {"13C", "15N", "2H", "17O"}),
        # The 13C bin takes the 33S line (4 mDa) but not the 15N line (6.3
        # mDa, half a FWHM 5.9 mDa), which alone is 0.73% of M0.
        ("C6H12N2O2S", 15000.0, {"13C", "33S", "15N", "2H", "17O"}),
    ],
    ids=["C4H8NO+ at 8000", "C6H12N2O2S+ at 15000"],
)
def test_a_line_under_the_floor_within_one_fwhm_of_a_kept_line_is_added_to_it(
    ion, power, members
):
    # mascope#2591: one peak of that width does not separate the 15N line
    # from the 13C line, so the peak there is predicted with both.
    predicted, reached = _m1_share(ion, 0.01, power)

    assert set(reached) == members
    assert predicted == pytest.approx(sum(reached.values()), rel=1e-6)


def test_a_line_under_the_floor_more_than_one_fwhm_away_is_still_dropped():
    # At 30000 a FWHM at m/z 87 is 2.902 mDa: the 15N line, 6.3 mDa from the
    # 13C line, is a peak of its own, and under the floor it is not predicted.
    # The 17O line, 0.9 mDa over it, is in the 13C peak. The 2H line sits
    # 2.914 mDa over that merged 13C+17O line, only 0.4% more than the FWHM,
    # so it is dropped too: a merged centre 0.012 mDa higher would fold it in.
    predicted, reached = _m1_share("C4H8NO", 0.01, 30000.0)

    assert predicted == pytest.approx(reached["13C"] + reached["17O"], rel=1e-6)


@pytest.mark.parametrize("power", [4000.0, 8000.0])
def test_a_small_ions_m1_peak_holds_its_lines_under_a_tenth_of_the_floor(power):
    # mascope#3024: the 2H and 17O lines of C4H8NO+ are 0.093% and 0.038% of
    # M0, both under a tenth of the 1% floor. Predicted only that deep, the
    # TOF's M+1 peak left them out and read 2.6% low.
    predicted, reached = _m1_share("C4H8NO", 0.01, power)

    assert reached["2H"] < 1e-3
    assert reached["17O"] < 1e-3
    assert predicted == pytest.approx(
        reached["13C"] + reached["15N"] + reached["2H"] + reached["17O"], rel=1e-6
    )


def test_a_fold_into_the_brightest_line_keeps_a_line_on_the_floor(monkeypatch):
    # The floor is judged once, on the merged lines. At m/z 100 and a
    # resolving power of 1000 a FWHM is 0.1: the 100.07 line is a bin of its
    # own under the floor and folds into M0, which then reads 1.005. The line
    # at exactly 1% of M0 as merged stays kept, though it is under 1% of 1.005.
    monkeypatch.setattr(
        heuristic_filter,
        "predict_isotopes",
        lambda *args, **kwargs: (
            np.array([100.0, 100.07, 101.0]),
            np.array([1.0, 0.005, 0.01]),
            ["M0", "15N", "13C"],
        ),
    )

    _, intensities, labels = predict_resolved_isotopes("X", 1, 0.01, _resolving(1000.0))

    assert labels == ["M0", "13C"]
    np.testing.assert_allclose(intensities, [1.005, 0.01])


@pytest.mark.parametrize("power", [40000.0, 50000.0])
def test_two_lines_under_the_floor_one_peak_holds_over_it_are_one_line(power):
    # mascope#3027: the 33S and 15N lines of C6H12N2O2S+ are 0.79% and 0.73%
    # of M0, 2.35 mDa apart; at 40000 a FWHM at m/z 177 is 4.4 mDa, over half
    # a FWHM apart, so each was a bin of its own under the floor. The 33S bin
    # folded into the 13C line, the 15N bin, 6.3 mDa from it, was dropped and
    # M+1 read 9% low. Together they clear the floor: one peak, one line.
    mzs, intensities, labels = predict_resolved_isotopes(
        "C6H12N2O2S", 1, 0.01, _resolving(power)
    )
    predicted, reached = _m1_share("C6H12N2O2S", 0.01, power)

    assert predicted == pytest.approx(sum(reached.values()), rel=1e-6)
    _, mass = _fine_structure_of("C6H12N2O2S")
    line = labels.index("33S")
    share = intensities / intensities[labels.index("M0")]
    assert share[line] == pytest.approx(reached["33S"] + reached["15N"], rel=1e-6)
    assert mzs[line] == pytest.approx(
        (mass["33S"] * reached["33S"] + mass["15N"] * reached["15N"])
        / (reached["33S"] + reached["15N"]),
        abs=1e-7,
    )


def test_lines_under_the_floor_over_one_fwhm_apart_stay_apart():
    # At 80000 a FWHM at m/z 177 is 2.2 mDa: the 33S and 15N lines, 2.35 mDa
    # apart, are two peaks, each under the floor, and neither is predicted.
    predicted, reached = _m1_share("C6H12N2O2S", 0.01, 80000.0)

    assert predicted == pytest.approx(reached["13C"] + reached["17O"], rel=1e-6)


def _fine_structure_of(ion):
    mzs, intensities, labels = predict_isotopes(ion, 1, threshold=1e-7)
    share = dict(zip(labels, intensities / intensities[labels.index("M0")]))
    return share, dict(zip(labels, mzs))


def _predicted_as(monkeypatch, mzs, intensities, labels, power=1000.0):
    """Predict ``X+`` with the predictor's lines replaced by these."""
    monkeypatch.setattr(
        heuristic_filter,
        "predict_isotopes",
        lambda *args, **kwargs: (
            np.array(mzs, dtype=float),
            np.array(intensities, dtype=float),
            list(labels),
        ),
    )
    return predict_resolved_isotopes("X", 1, 0.01, _resolving(power))


def test_the_merged_line_is_kept_in_its_brightest_members_place(monkeypatch):
    # At m/z 101 and a resolving power of 1000 a FWHM is 0.101. The A and B
    # lines are 0.06 from each other and A from the 13C line, over half a FWHM:
    # three bins, A and B under the 1% floor. Together they are 1.1%: one line,
    # named and placed as A, at their weighted centre. It is merged before the
    # fold, so A is not added to the 13C line and B left to be dropped.
    mzs, intensities, labels = _predicted_as(
        monkeypatch,
        [100.0, 100.88, 101.0, 100.94],
        [1.0, 0.005, 0.1, 0.006],
        ["M0", "B", "13C", "A"],
    )

    assert labels == ["M0", "13C", "A"]
    np.testing.assert_allclose(intensities, [1.0, 0.1, 0.011])
    np.testing.assert_allclose(mzs[:2], [100.0, 101.0])
    assert mzs[2] == pytest.approx((100.94 * 0.006 + 100.88 * 0.005) / 0.011)


def test_lines_under_the_floor_that_reach_it_exactly_are_kept(monkeypatch):
    # The floor is the same one a merged line is kept by: at it, not over it.
    _, intensities, labels = _predicted_as(
        monkeypatch, [100.0, 102.0, 102.06], [1.0, 0.005, 0.005], ["M0", "A", "B"]
    )

    assert labels == ["M0", "A"]
    np.testing.assert_allclose(intensities, [1.0, 0.01])


def test_lines_under_the_floor_that_do_not_reach_it_are_left_to_the_fold(
    monkeypatch,
):
    # 0.9% together: A is still added to the 13C line it sits 0.06 from, and B,
    # 0.12 from it, still dropped.
    _, intensities, labels = _predicted_as(
        monkeypatch,
        [100.0, 100.88, 101.0, 100.94],
        [1.0, 0.004, 0.1, 0.005],
        ["M0", "B", "13C", "A"],
    )

    assert labels == ["M0", "13C"]
    np.testing.assert_allclose(intensities, [1.0, 0.105])


def test_lines_under_the_floor_one_fwhm_apart_are_not_merged(monkeypatch):
    # At m/z 102 and 1000 a FWHM is 0.102: B, 0.1025 over A, is a peak of its
    # own. Neither line reaches the floor, and no kept line is near to fold in.
    _, _, labels = _predicted_as(
        monkeypatch, [100.0, 102.0, 102.1025], [1.0, 0.006, 0.006], ["M0", "A", "B"]
    )

    assert labels == ["M0"]


def test_lines_under_the_floor_just_inside_one_fwhm_are_merged(monkeypatch):
    # The other side of the edge: B, 0.1 over A at a FWHM of 0.102 (0.98 FWHM),
    # is held by A's peak, and together they clear the floor.
    mzs, intensities, labels = _predicted_as(
        monkeypatch, [100.0, 102.0, 102.1], [1.0, 0.006, 0.006], ["M0", "A", "B"]
    )

    assert labels == ["M0", "A"]
    np.testing.assert_allclose(intensities, [1.0, 0.012])
    assert mzs[1] == pytest.approx(102.05)


def test_a_merged_line_under_the_floor_takes_a_further_one_in_its_fwhm(
    monkeypatch,
):
    # A group reaches one FWHM either side of its brightest line: B and C,
    # 0.08 under and over A at a FWHM of 0.102, are both A's, though 0.16
    # apart.
    mzs, intensities, labels = _predicted_as(
        monkeypatch,
        [100.0, 101.92, 102.0, 102.08],
        [1.0, 0.004, 0.005, 0.004],
        ["M0", "B", "A", "C"],
    )

    assert labels == ["M0", "A"]
    np.testing.assert_allclose(intensities, [1.0, 0.013])
    assert mzs[1] == pytest.approx(102.0)


def _fold(mzs, intensities, keep, power):
    return _fold_under_floor(
        np.asarray(mzs, dtype=float),
        np.asarray(intensities, dtype=float),
        np.asarray(keep, dtype=bool),
        _resolving(power),
    )


def test_a_folded_line_moves_the_kept_line_to_their_weighted_centre():
    # At m/z 100 and a resolving power of 1000 a FWHM is 0.1.
    mzs, intensities = _fold(
        [100.0, 101.0, 101.08], [1.0, 0.1, 0.005], [True, True, False], 1000.0
    )

    np.testing.assert_allclose(intensities[:2], [1.0, 0.105])
    assert mzs[0] == 100.0
    assert mzs[1] == pytest.approx((101.0 * 0.1 + 101.08 * 0.005) / 0.105)


def test_a_line_one_fwhm_away_is_not_folded():
    mzs, intensities = _fold(
        [100.0, 101.0, 101.1015], [1.0, 0.1, 0.005], [True, True, False], 1000.0
    )

    np.testing.assert_allclose(intensities[:2], [1.0, 0.1])
    np.testing.assert_allclose(mzs[:2], [100.0, 101.0])


def test_a_line_between_two_kept_lines_goes_to_the_nearer():
    _, intensities = _fold(
        [100.0, 100.07, 100.09], [1.0, 0.005, 0.5], [True, False, True], 1000.0
    )

    np.testing.assert_allclose(intensities[[0, 2]], [1.0, 0.505])


def test_distances_are_read_against_the_kept_lines_as_merged():
    # Two dim lines either side of the kept line each fold on their own
    # distance to it. Had the first fold moved the line to its new centre, the
    # second, 0.0998 over it at a FWHM of 0.1, would then be over a FWHM away.
    mzs, intensities = _fold(
        [99.905, 100.0, 100.0998], [0.004, 1.0, 0.004], [False, True, False], 1000.0
    )

    assert intensities[1] == pytest.approx(1.008)
    assert mzs[1] == pytest.approx((100.0 + 0.004 * (99.905 + 100.0998)) / 1.008)


@pytest.mark.parametrize("power", [float("nan"), 0.0], ids=["nan", "zero"])
def test_a_resolution_that_reads_nothing_folds_nothing(power):
    mzs, intensities = _fold([100.0, 100.01], [1.0, 0.005], [True, False], power)

    np.testing.assert_allclose(intensities, [1.0, 0.005])
    np.testing.assert_allclose(mzs, [100.0, 100.01])


def test_a_resolution_that_raises_folds_nothing():
    _, intensities = _fold_under_floor(
        np.array([100.0, 100.01]),
        np.array([1.0, 0.005]),
        np.array([True, False]),
        lambda mz: 1 / 0,
    )

    np.testing.assert_allclose(intensities, [1.0, 0.005])


def test_a_line_at_or_under_zero_mz_is_a_group_of_its_own():
    # Its width is not positive and takes no line, not even its own; the
    # targeted library's degenerate atomless ion reaches the grouping so.
    groups = heuristic_filter.group_unresolved_lines(
        [-0.000549, 0.0, 18.0106, 18.0107], [0.5, 0.2, 0.3, 0.1], _resolving(1e4)
    )

    assert [group.tolist() for group in groups] == [[0], [2, 3], [1]]


def test_fold_targets_names_the_kept_line_a_dim_line_goes_to():
    # At m/z 100 and a resolving power of 1000 a FWHM is 0.1: the dim line
    # 0.08 over a kept line goes to it, the one 0.2 away and the kept lines
    # go nowhere.
    targets = heuristic_filter.fold_targets(
        [100.0, 101.0, 101.08, 101.3], [True, True, False, False], _resolving(1000.0)
    )

    assert targets.tolist() == [-1, -1, 1, -1]
