"""On a file read by heights, a merged line is its dominant member.

A TOF file's intensity is a peak's area, which holds every line under the
peak: a merged line there is its members' summed share at their weighted
centre. An Orbitrap file's intensity is a peak's height, which follows its
dominant member: a member half a FWHM away sits on that member's flank and
adds little to the apex (mascope#3338, MAS-1040). There a merged line is its
dominant member, at that member's m/z, with the shares of the members within
``sum_span`` of it added; the further members stay named on the line and add
nothing.
"""

import numpy as np
import polars as pl
import pytest

from mascope_tools.composition import line_model
from mascope_tools.composition.config import HEIGHT_SUM_SPAN, UNRESOLVED_MEMBER_DEPTH
from mascope_tools.composition.heuristic_filter import match_isotopic_pattern
from mascope_tools.composition.line_model import (
    HeightResolution,
    height_sum_span,
    merge_unresolved_lines,
    predict_isotopes,
    predict_resolved_isotopes,
    resolve_lines,
)
from mascope_tools.composition.models import PatternScoring


def _resolving(power):
    return lambda _mz: power


#: At m/z 100 and a resolving power of 1000 a FWHM is 0.1: a group reaches
#: 0.05 either side of its brightest line, and the default sum span 0.02.
POWER = 1000.0


# --- The siloxane of the issue ------------------------------------------------

#: The D5 urea adduct of mascope#3338, protonated, at the demo's resolving power.
SILOXANE = "C11H35N2O6Si5"
SILOXANE_POWER = 83000.0
FLOOR = 0.01


def _siloxane_fine():
    return predict_isotopes(SILOXANE, 1, None, FLOOR * UNRESOLVED_MEMBER_DEPTH)


def _line(lines, label):
    (line,) = [line for line in lines if line.label == label]
    return line


def test_the_siloxanes_m2_line_is_its_30si_member_at_that_members_mz():
    mzs, shares, labels = _siloxane_fine()
    lines = resolve_lines(
        mzs, shares, labels, HeightResolution(_resolving(SILOXANE_POWER)), FLOOR
    )

    m2 = _line(lines, "30Si")
    m0 = _line(lines, "M0")
    silicon = labels.index("30Si")
    # The [29Si]2 line, 2.3 mDa above, is a member of the peak ...
    twin = labels.index("29Si2")
    assert {silicon, twin} <= set(m2.members)
    # ... and the line sits on the [30Si] member, with that member's share:
    # the members within the sum span (none over a hundredth of it) add
    # under 2 %, and the [29Si]2 line, 0.44 FWHM away, nothing.
    assert m2.mz == mzs[silicon]
    assert m2.abundance / m0.abundance == pytest.approx(
        shares[silicon] / shares[labels.index("M0")], rel=0.02
    )
    assert m2.abundance < shares[silicon] + shares[twin] * 0.5


def test_the_siloxanes_m2_line_on_a_tof_is_still_the_members_sum():
    # The same ion read by areas: every member adds, at their weighted centre.
    mzs, shares, labels = _siloxane_fine()
    for power in (SILOXANE_POWER, 5000.0):
        for line in resolve_lines(mzs, shares, labels, _resolving(power), FLOOR):
            members = list(line.members)
            assert line.abundance == pytest.approx(shares[members].sum(), rel=1e-12)
            assert line.mz == pytest.approx(
                np.sum(mzs[members] * shares[members]) / shares[members].sum(),
                abs=1e-9,
            )


def test_the_envelope_reads_the_siloxane_as_its_file_does():
    # The envelope search and Stage A predict through this one function.
    height = HeightResolution(_resolving(SILOXANE_POWER))
    area = _resolving(SILOXANE_POWER)
    h_mzs, h_shares, h_labels = predict_resolved_isotopes(SILOXANE, 1, FLOOR, height)
    a_mzs, a_shares, a_labels = predict_resolved_isotopes(SILOXANE, 1, FLOOR, area)
    mzs, shares, labels = _siloxane_fine()

    assert h_labels == a_labels
    at = h_labels.index("30Si")
    assert h_mzs[at] == mzs[labels.index("30Si")]
    assert a_mzs[at] > h_mzs[at]
    assert h_shares[at] < 0.9 * a_shares[at]


# --- The rule on a few lines ---------------------------------------------------


def test_a_member_past_the_sum_span_is_named_on_the_line_and_adds_nothing():
    # B sits 0.3 FWHM above A: one peak, but on a height its share is A's.
    fine = ([100.0, 101.0, 101.03], [1.0, 0.10, 0.04], ["M0", "A", "B"])

    height = resolve_lines(*fine, HeightResolution(_resolving(POWER)), 0.01)
    area = resolve_lines(*fine, _resolving(POWER), 0.01)

    assert [line.members for line in height] == [(0,), (1, 2)]
    assert height[1].mz == 101.0
    assert height[1].abundance == pytest.approx(0.10)
    assert area[1].abundance == pytest.approx(0.14)
    assert area[1].mz == pytest.approx((101.0 * 0.10 + 101.03 * 0.04) / 0.14)


def test_a_pair_under_the_sum_span_is_still_summed():
    # B sits 0.1 FWHM above A: on a height it adds, at A's m/z.
    fine = ([100.0, 101.0, 101.01], [1.0, 0.10, 0.04], ["M0", "A", "B"])

    (_, line) = resolve_lines(*fine, HeightResolution(_resolving(POWER)), 0.01)

    assert line.members == (1, 2)
    assert line.mz == 101.0
    assert line.abundance == pytest.approx(0.14)


def test_the_sum_span_is_read_in_fwhm_at_the_dominant_member():
    fine = ([100.0, 101.0, 101.03], [1.0, 0.10, 0.04], ["M0", "A", "B"])

    narrow = resolve_lines(*fine, HeightResolution(_resolving(POWER), 0.2), 0.01)
    wide = resolve_lines(*fine, HeightResolution(_resolving(POWER), 0.31), 0.01)

    assert narrow[1].abundance == pytest.approx(0.10)
    assert wide[1].abundance == pytest.approx(0.14)
    assert wide[1].mz == 101.0


def test_a_member_exactly_at_the_sum_span_adds_nothing():
    # At m/z 128 and 512 a FWHM is 0.25: a quarter of it is 0.0625, exactly.
    fine = ([127.0, 128.0, 128.0625], [1.0, 0.10, 0.04], ["M0", "A", "B"])

    (_, line) = resolve_lines(*fine, HeightResolution(_resolving(512.0), 0.25), 0.01)

    assert line.members == (1, 2)
    assert line.abundance == pytest.approx(0.10)


def test_the_dominant_member_names_where_the_line_sits():
    # B is the brighter of the two: the line sits on it, whatever the order.
    fine = ([100.0, 101.0, 101.03], [1.0, 0.04, 0.10], ["M0", "A", "B"])

    (_, line) = resolve_lines(*fine, HeightResolution(_resolving(POWER)), 0.01)

    assert line.mz == 101.03
    assert line.abundance == pytest.approx(0.10)
    assert line.label == "B"


def test_two_equal_members_sit_on_the_first():
    centre, total = line_model._line_of(
        [1, 2],
        np.array([100.0, 101.0, 101.03]),
        np.array([1.0, 0.05, 0.05]),
        HeightResolution(_resolving(POWER)),
    )

    assert (centre, total) == (101.0, pytest.approx(0.05))


@pytest.mark.parametrize("power", [float("nan"), float("inf"), 0.0, -1.0])
def test_a_resolving_power_that_reads_nothing_leaves_the_dominant_member(power):
    centre, total = line_model._line_of(
        [1, 2],
        np.array([100.0, 101.0, 101.001]),
        np.array([1.0, 0.10, 0.04]),
        HeightResolution(_resolving(power)),
    )

    assert (centre, total) == (101.0, pytest.approx(0.10))


def test_a_sum_span_of_nothing_leaves_the_dominant_member():
    centre, total = line_model._line_of(
        [1, 2],
        np.array([100.0, 101.0, 101.001]),
        np.array([1.0, 0.10, 0.04]),
        HeightResolution(_resolving(POWER), 0.0),
    )

    assert (centre, total) == (101.0, pytest.approx(0.10))


def test_a_resolving_power_that_raises_leaves_the_dominant_member():
    def raises(_mz):
        raise ZeroDivisionError

    centre, total = line_model._line_of(
        [1, 2],
        np.array([100.0, 101.0, 101.001]),
        np.array([1.0, 0.10, 0.04]),
        HeightResolution(raises),
    )

    assert (centre, total) == (101.0, pytest.approx(0.10))


def test_a_line_of_one_member_is_read_as_before():
    # One member reads no width: it is its own line, as on any file.
    assert line_model._line_of(
        [1], np.array([100.0, 101.0]), np.array([1.0, 0.1]), HeightResolution(raise_)
    ) == (pytest.approx(101.0, abs=1e-12), pytest.approx(0.1))


def raise_(_mz):
    raise AssertionError("a line of one member reads no width")


# --- The floor's two steps on a file read by heights --------------------------


def test_lines_under_the_floor_a_height_cannot_add_stay_under_it():
    # X and Y, 0.7 FWHM apart, are two groups under a 5 % floor that together
    # clear it. A peak's area holds both: one line. A height holds the one it
    # sits on: under the floor, and nothing within one FWHM to fold into.
    fine = ([100.0, 101.0, 101.07], [1.0, 0.03, 0.03], ["M0", "X", "Y"])

    area = resolve_lines(*fine, _resolving(POWER), 0.05)
    height = resolve_lines(*fine, HeightResolution(_resolving(POWER)), 0.05)

    assert [line.members for line in area] == [(0,), (1, 2)]
    assert area[1].abundance == pytest.approx(0.06)
    assert [line.members for line in height] == [(0,)]


def test_lines_under_the_floor_a_height_adds_over_it_are_one_line():
    # With a sum span reaching Y, the height holds both and clears the floor,
    # at the dominant member's m/z (X, the first of the two equal ones).
    fine = ([100.0, 101.0, 101.07], [1.0, 0.03, 0.03], ["M0", "X", "Y"])

    height = resolve_lines(*fine, HeightResolution(_resolving(POWER), 0.8), 0.05)

    assert [line.members for line in height] == [(0,), (1, 2)]
    assert height[1].mz == 101.0
    assert height[1].abundance == pytest.approx(0.06)


def test_a_line_folded_under_the_floor_is_named_and_adds_nothing():
    # Z, 0.7 FWHM above M0 and under the floor, folds into M0's line: the
    # line holds it, and its height stays M0's, at M0's m/z.
    fine = ([100.0, 100.07], [1.0, 0.004], ["M0", "Z"])

    area = resolve_lines(*fine, _resolving(POWER), 0.01)
    height = resolve_lines(*fine, HeightResolution(_resolving(POWER)), 0.01)

    assert [line.members for line in area] == [(0, 1)]
    assert area[0].abundance == pytest.approx(1.004)
    assert [line.members for line in height] == [(0, 1)]
    assert (height[0].mz, height[0].abundance) == (100.0, pytest.approx(1.0))


def test_the_floor_is_read_off_the_heights():
    # A's line is 0.14 by area and 0.10 by height: over a 12 % floor by area,
    # under it by height, so only the area keeps it.
    fine = ([100.0, 101.0, 101.03], [1.0, 0.10, 0.04], ["M0", "A", "B"])

    area = resolve_lines(*fine, _resolving(POWER), 0.12)
    height = resolve_lines(*fine, HeightResolution(_resolving(POWER)), 0.12)

    assert [line.label for line in area] == ["M0", "A"]
    assert [line.label for line in height] == ["M0"]


# --- The other readers and the wrapper ----------------------------------------


def test_merge_unresolved_lines_reads_heights_too():
    fine = ([100.0, 101.0, 101.03], [1.0, 0.10, 0.04], ["M0", "A", "B"])

    mzs, shares, labels = merge_unresolved_lines(
        *fine, HeightResolution(_resolving(POWER))
    )
    a_mzs, a_shares, _ = merge_unresolved_lines(*fine, _resolving(POWER))

    assert labels == ["M0", "A"]
    np.testing.assert_allclose(mzs, [100.0, 101.0])
    np.testing.assert_allclose(shares, [1.0, 0.10])
    np.testing.assert_allclose(a_shares, [1.0, 0.14])


def test_the_envelope_match_scores_an_orbitrap_line_against_its_height():
    # Through the finder's scoring: a peak at A's height reads on its share.
    fine = predict_isotopes(SILOXANE, 1, None, FLOOR * UNRESOLVED_MEMBER_DEPTH)
    share = dict(zip(fine[2], fine[1] / fine[1][fine[2].index("M0")]))
    mass = dict(zip(fine[2], fine[0]))
    peaks = pl.DataFrame(
        {"mz": [mass["M0"], mass["30Si"]], "intensity": [1.0e6, share["30Si"] * 1e6]}
    ).sort("mz")
    candidates = [{"formula": SILOXANE, "ion": SILOXANE + "+"}]
    scoring = PatternScoring(
        resolution=HeightResolution(_resolving(SILOXANE_POWER)),
        abundance_floor=FLOOR,
    )

    _, isotopes = match_isotopic_pattern(candidates, peaks, scoring)

    envelope = isotopes[0]
    at = list(envelope["labels"]).index("30Si")
    assert envelope["predicted_masses"][at] == mass["30Si"]
    assert envelope["intensity_errors"][at] == pytest.approx(0.0, abs=0.02)


def test_the_wrapper_reads_the_resolving_power_it_wraps():
    height = HeightResolution(lambda mz: mz * 10.0)

    assert height(200.0) == 2000.0
    assert height.sum_span == HEIGHT_SUM_SPAN
    assert height_sum_span(height) == HEIGHT_SUM_SPAN
    assert height_sum_span(HeightResolution(height, 0.3)) == 0.3
    assert height_sum_span(lambda mz: 1.0) is None
    assert height_sum_span(None) is None


def test_the_line_groups_are_the_same_on_either_file():
    mzs, shares, labels = _siloxane_fine()

    assert [
        group.tolist()
        for group in line_model.group_unresolved_lines(
            mzs, shares, HeightResolution(_resolving(SILOXANE_POWER))
        )
    ] == [
        group.tolist()
        for group in line_model.group_unresolved_lines(
            mzs, shares, _resolving(SILOXANE_POWER)
        )
    ]
