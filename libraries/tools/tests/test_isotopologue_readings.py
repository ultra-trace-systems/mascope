"""Reading a peak as another line of a candidate ion than its monoisotopic one.

`find_compositions` proposes the formulas whose ion's monoisotopic line lands on
a peak. Asked with an ``isotopologue_floor``, it also proposes those one of whose
other lines does - the peak a 13C line one unit up, a dibromide's brightest line
two units up, or a 15N-nitrate adduct's unlabelled remainder one unit down -
each marked with the line it is read as and measured against that line.
"""

from dataclasses import replace
from functools import partial

import numpy as np
import pytest

from mascope_tools.composition import finder
from mascope_tools.composition.finder import find_compositions
from mascope_tools.composition.grid import build_neutral_grid
from mascope_tools.composition.heuristic_filter import predict_isotopes
from mascope_tools.composition.isotopologues import (
    _brightest_on_target,
    _ion_lines,
    isotopologue_windows,
    line_offsets,
)
from mascope_tools.composition.models import CompositionSearchConfig
from mascope_tools.composition.utils import (
    combine_counts_and_ionization,
    parse_composition,
    parse_ionization,
)


NITRATE = CompositionSearchConfig(
    ionizations="[M+[15N]O3]-",
    mass_range_ppm=3.0,
    element_count_ranges="C0-40 H0-80 O0-25",
    use_unsaturation=True,
    min_unsaturation=-1000.0,
    max_unsaturation=10000.0,
)

HALOGENS = CompositionSearchConfig(
    ionizations="[M-H]-,[M+Br]-",
    mass_range_ppm=3.0,
    element_count_ranges="C0-40 H0-80 N0-3 O0-18 S0-2 Cl0-2 Br0-2",
    use_unsaturation=True,
    min_unsaturation=-1000.0,
    max_unsaturation=10000.0,
)

PROTONATION = CompositionSearchConfig(
    ionizations="[M+H]+",
    mass_range_ppm=3.0,
    element_count_ranges="C0-40 H0-80 N0-3 O0-18",
)


def _lines(ion: str, charge: int) -> dict[str, tuple[float, float]]:
    """An ion's predicted lines by label: m/z and share of the brightest."""
    mzs, probs, labels = predict_isotopes(ion, charge, threshold=1e-4)
    top = float(np.max(probs))
    return {
        label: (float(mz), float(prob) / top)
        for mz, prob, label in zip(mzs, probs, labels)
    }


def _readings_of(formula: str, results: list[dict]) -> list[dict]:
    return [result for result in results if result["formula"] == formula]


def _at_a_line(result: dict) -> bool:
    """Whether the search read the peak as another line of the result's ion."""
    return "isotope_mz" in result


class TestAMonoisotopicSearch:
    """Without a floor the search is the one it always was."""

    @pytest.mark.parametrize("target", [182.0740, 311.0750, 250.8536])
    def test_it_reads_no_other_line(self, target):
        for config in (NITRATE, HALOGENS, PROTONATION):
            results = find_compositions(target, config)
            assert not any(_at_a_line(result) for result in results)

    @pytest.mark.parametrize("target", [182.0740, 311.0750, 250.8536])
    @pytest.mark.parametrize("cap", [100, 3])
    def test_a_floor_adds_readings_and_changes_none(self, target, cap):
        # Whether or not the cap binds - at 311 it does for the halogen box
        # even at 100: a line reading takes only the room the monoisotopic
        # readings leave, never the place of a compound read at its own mass.
        for config in (NITRATE, HALOGENS, PROTONATION):
            config = replace(config, max_result_rows=cap)
            plain = find_compositions(target, config)
            wider = find_compositions(target, config, isotopologue_floor=0.01)
            assert [r for r in wider if not _at_a_line(r)] == plain


class TestALabelledReagent:
    """The 15N-nitrate adduct is read at its labelled line, the monoisotopic one
    the search anchors it on, and its 2% unlabelled remainder sits one unit
    below."""

    LINES = _lines("C10H16O10^N", -1)

    def test_the_labelled_line_is_the_monoisotopic_reading(self):
        target, share = self.LINES["M0"]
        assert share == pytest.approx(1.0)
        readings = _readings_of(
            "C10H16O7", find_compositions(target, NITRATE, isotopologue_floor=0.01)
        )
        assert len(readings) == 1
        assert not _at_a_line(readings[0])

    def test_the_unlabelled_remainder_is_read_one_unit_below(self):
        target, _ = self.LINES["14N"]
        assert not _readings_of("C10H16O7", find_compositions(target, NITRATE))

        readings = _readings_of(
            "C10H16O7", find_compositions(target, NITRATE, isotopologue_floor=0.01)
        )
        assert len(readings) == 1
        reading = readings[0]
        assert reading["isotope_mz"] == pytest.approx(target, abs=1e-6)
        assert reading["ion"] == "C10H16O10^N-"
        assert reading["composition_error_ppm"] == pytest.approx(0.0, abs=1e-3)

    def test_a_floor_above_the_remainder_does_not_read_it(self):
        target, share = self.LINES["14N"]
        assert share < 0.05
        readings = find_compositions(target, NITRATE, isotopologue_floor=0.05)
        assert not _readings_of("C10H16O7", readings)


class TestAHeavierLine:
    GLUCOSE = _lines("C6H13O6", 1)

    def test_a_13c_line_is_read_as_its_ions_isotopologue(self):
        target, _ = self.GLUCOSE["13C"]
        assert not _readings_of("C6H12O6", find_compositions(target, PROTONATION))

        readings = _readings_of(
            "C6H12O6", find_compositions(target, PROTONATION, isotopologue_floor=0.05)
        )
        assert len(readings) == 1
        assert readings[0]["isotope_mz"] == pytest.approx(target, abs=1e-9)

    def test_a_line_under_the_floor_is_not_read(self):
        target, share = self.GLUCOSE["13C"]
        assert share < 0.1
        readings = find_compositions(target, PROTONATION, isotopologue_floor=0.1)
        assert not _readings_of("C6H12O6", readings)

    def test_the_mass_error_is_against_the_line_read(self):
        # Signed and relative to the prediction, as for a monoisotopic reading,
        # so the line the peak was read as is recoverable from the error.
        line, _ = self.GLUCOSE["13C"]
        target = line * (1 + 2e-6)
        (reading,) = _readings_of(
            "C6H12O6", find_compositions(target, PROTONATION, isotopologue_floor=0.05)
        )
        assert reading["composition_error_ppm"] == pytest.approx(2.0, abs=1e-6)
        recovered = target / (1 + reading["composition_error_ppm"] / 1e6)
        assert recovered == pytest.approx(reading["isotope_mz"], rel=1e-12)


class TestTheBrightestLine:
    """A floor of one reads the peak as each ion's brightest line alone - the
    line a dibromide shows most of, two units above its monoisotopic one."""

    DIBROMOPHENOL = _lines("C6H3Br2O", -1)

    def test_the_brightest_line_is_read(self):
        target, share = self.DIBROMOPHENOL["81Br"]
        assert share == pytest.approx(1.0)
        assert not _readings_of("C6H4Br2O", find_compositions(target, HALOGENS))

        (reading,) = _readings_of(
            "C6H4Br2O", find_compositions(target, HALOGENS, isotopologue_floor=1.0)
        )
        assert reading["ionization_mechanism"] == "[M-H]-"
        assert reading["isotope_mz"] == pytest.approx(target, abs=1e-6)

    def test_a_dimmer_line_is_not_read_at_a_floor_of_one(self):
        target, share = self.DIBROMOPHENOL["81Br2"]
        assert share < 1.0
        readings = find_compositions(target, HALOGENS, isotopologue_floor=1.0)
        assert not _readings_of("C6H4Br2O", readings)

        (reading,) = _readings_of(
            "C6H4Br2O", find_compositions(target, HALOGENS, isotopologue_floor=0.3)
        )
        assert reading["isotope_mz"] == pytest.approx(target, abs=1e-6)


def test_the_reagents_own_line_is_the_empty_formula():
    # The bare adduct is read as "()" at its monoisotopic line; its 81Br line is
    # the same ion, read once however many windows reach it.
    target, _ = _lines("Br", -1)["81Br"]
    readings = find_compositions(target, HALOGENS, isotopologue_floor=0.01)
    empty = _readings_of("()", readings)
    assert len(empty) == 1
    assert empty[0]["ionization_mechanism"] == "[M+Br]-"
    assert empty[0]["isotope_mz"] == pytest.approx(target, abs=1e-6)
    # At its monoisotopic line it is the search's own reading, and no other.
    monoisotopic, _ = _lines("Br", -1)["M0"]
    (own,) = _readings_of(
        "()", find_compositions(monoisotopic, HALOGENS, isotopologue_floor=0.01)
    )
    assert not _at_a_line(own)


def test_the_row_cap_fills_with_the_monoisotopic_readings_first():
    # max_result_rows bounds a mechanism's readings, as it always has. The
    # monoisotopic readings fill it first and the closest line readings take
    # the room they leave: a line reading is the weaker hypothesis, since its
    # ion's monoisotopic line need not be in the spectrum at all.
    target, _ = _lines("C6H3Br2O", -1)["81Br"]
    wide = replace(
        HALOGENS, ionizations="[M-H]-", mass_range_ppm=20.0, max_result_rows=10**6
    )
    every = find_compositions(target, wide, isotopologue_floor=0.01)
    monoisotopic = [r for r in every if not _at_a_line(r)]
    lines = sorted(
        (r for r in every if _at_a_line(r)),
        key=lambda r: abs(r["composition_error_ppm"]),
    )
    assert len(monoisotopic) > 1 and len(lines) > 3
    # A line closer than a monoisotopic reading does not take its place.
    assert abs(lines[0]["composition_error_ppm"]) < max(
        abs(r["composition_error_ppm"]) for r in monoisotopic
    )

    def kept(cap):
        return find_compositions(
            target, replace(wide, max_result_rows=cap), isotopologue_floor=0.01
        )

    room_for_three = kept(len(monoisotopic) + 3)
    assert [r for r in room_for_three if not _at_a_line(r)] == monoisotopic
    assert [r for r in room_for_three if _at_a_line(r)] == sorted(
        lines[:3], key=lambda r: abs(r["composition_error_ppm"])
    )
    no_room = kept(len(monoisotopic) - 1)
    assert no_room == monoisotopic[:-1]


def test_a_grid_atom_with_a_fixed_label_is_not_given_an_envelope():
    # "[13C]" in the element box is an atom of fixed mass; an ion holding one
    # has no envelope the predictor could honestly give it, and reading it as
    # plain carbon would invent one - the labelled glucose's lines would be the
    # unlabelled glucose's, one of which is this 13C2 line.
    config = replace(PROTONATION, element_count_ranges="C0-12 H0-24 O0-8 [13C]0-1")
    target, _ = _lines("C6H13O6", 1)["13C2"]
    readings = find_compositions(target, config, isotopologue_floor=0.001)
    assert _readings_of("C6H12O6", readings), "the unlabelled glucose is read"
    assert all("[13C]" not in r["formula"] for r in readings if "isotope_mz" in r)
    # ... while the labelled formula is still read at its own monoisotopic mass.
    at_label, _ = _lines("C6H13O6", 1)["13C"]
    labelled = find_compositions(at_label, config, isotopologue_floor=0.001)
    assert any(r["formula"] == "[13C]C5H12O6" for r in labelled)


def test_a_floor_of_one_searches_the_brightest_offsets_alone():
    # The brightest line of a CHNO ion this light is its monoisotopic one, so
    # there is nothing else to search; a dibromide's is two units up.
    mechanism = parse_ionization("[M+H]+")
    assert line_offsets(PROTONATION, mechanism, 182.0740, 1.0) == {}

    deprotonation = parse_ionization("[M-H]-")
    assert set(line_offsets(HALOGENS, deprotonation, 250.8536, 1.0)) == {2}


def test_an_offset_only_combinations_under_the_floor_reach_is_not_searched():
    # At m/z 182 the box's 13C2, 18O and 15N lines each clear 1% on their own,
    # but no two of them together do: 13C with 18O is a third of a percent. So
    # the offsets they could only reach together, three units and up, hold no
    # line worth a window.
    offsets = line_offsets(PROTONATION, parse_ionization("[M+H]+"), 182.0740, 0.01)
    assert set(offsets) == {1, 2}
    # One unit spans 15N's line to 13C's, two units 18O's to 13C2's.
    assert offsets[1] == pytest.approx((0.997035, 1.003355), abs=1e-6)
    assert offsets[2] == pytest.approx((2.004245, 2.006710), abs=1e-6)


@pytest.mark.parametrize("order", [[0, 1, 2, 3], [3, 2, 1, 0]])
def test_the_brightest_line_inside_the_window_is_the_reading(order):
    # Fine structure a wide window cannot separate - a 13C2 and an 18O line
    # 2.5 mDa apart - puts two lines of one ion on the target. The brighter
    # takes the peak, as it would in the targeted matcher, whatever order the
    # lines come in.
    lines = [(0.0, 1.0), (1.0, 0.06), (2.0, 0.002), (2.0025, 0.012)]
    reading = _brightest_on_target([lines[i] for i in order], 100.0, 1, 102.0010, 0.004)
    assert reading == pytest.approx(102.0025)
    assert _brightest_on_target(lines, 100.0, 1, 103.5, 0.004) is None


@pytest.mark.parametrize(
    ("config", "target", "floor"),
    [
        (NITRATE, 311.0750, 0.01),
        (HALOGENS, 250.8536, 0.01),
        (HALOGENS, 430.7000, 0.05),
        (PROTONATION, 182.0740, 0.01),
    ],
)
def test_the_offsets_hold_every_line_the_box_can_build(config, target, floor):
    """Every line a formula of the box puts at or above the floor sits inside
    the span `line_offsets` gives its nominal offset - checked against the
    predictor itself, over every formula of a grid around the target."""
    tolerance = target * config.mass_range_ppm * 1e-6
    for notation in config.ionizations.split(","):
        mechanism = parse_ionization(notation)
        offsets = line_offsets(config, mechanism, target, floor)
        shift = mechanism.mass if mechanism.addition else -mechanism.mass
        windows = isotopologue_windows(target, mechanism, offsets, tolerance)
        grid = build_neutral_grid(
            config,
            max(0.0, min(low for low, _ in windows)),
            max(high for _, high in windows),
        )
        assert grid is not None and len(grid) > 0
        for row in range(0, len(grid), max(1, len(grid) // 400)):
            counts = grid.pyteomics_composition(row)
            if not mechanism.addition and counts.get("H", 0) < 1:
                continue
            ion = combine_counts_and_ionization(counts, mechanism)
            anchor = float(grid.mass[row]) + shift
            mzs, probs, _ = predict_isotopes(
                ion[:-1], mechanism.charge, threshold=floor
            )
            if not len(probs):
                continue
            shares = np.asarray(probs) / np.max(probs)
            for mz, share in zip(mzs, shares):
                delta = float(mz) - anchor
                nominal = round(delta)
                if share < floor or abs(delta) < 1e-4:
                    continue  # under the floor, or the monoisotopic line
                assert nominal in offsets, (ion, nominal, share)
                lowest, highest = offsets[nominal]
                assert lowest - 1e-9 <= delta <= highest + 1e-9, (ion, delta)


@pytest.mark.parametrize(
    "ion",
    ["C10H16O10^N", "O3^N", "C6H3Br2O", "C20H31O16^N2", "C12H9Cl2S2O4"],
)
def test_the_lines_agree_with_the_predictor(ion):
    """The lines the search combines from its elements' configurations are the
    lines the finder's predictor gives the whole ion, at the same shares - and
    at the same masses, to the micro-dalton an atom by which the grid's element
    masses and the isotope tables' differ."""
    charge = -1
    floor = 0.01
    expected_mzs, probs, labels = predict_isotopes(ion, charge, threshold=floor / 100)
    monoisotopic_mz = float(np.asarray(expected_mzs)[list(labels).index("M0")])
    expected_shares = np.asarray(probs) / np.max(probs)
    keep = expected_shares >= floor
    expected = sorted(zip(np.asarray(expected_mzs)[keep], expected_shares[keep]))
    got = sorted(
        (monoisotopic_mz + offset, share)
        for offset, share in _ion_lines(parse_composition(ion), floor)
    )
    assert len(got) == len(expected)
    for (mz, share), (want_mz, want_share) in zip(got, expected):
        assert mz == pytest.approx(want_mz, abs=1e-5)
        assert share == pytest.approx(want_share, rel=1e-6)


class TestALineAtTheMonoisotopicNominalMass:
    """A labelled reagent's unlabelled remainder with a 13C sits at the ion's
    monoisotopic nominal mass, 6.3 mDa above its monoisotopic line: outside a
    window a few ppm wide around that line, so the monoisotopic reading never
    reaches it."""

    LINES = _lines("C10H16O10^N", -1)

    def test_it_is_read_as_a_line_of_its_own(self):
        target, _ = self.LINES["13C+14N"]
        monoisotopic_mz, _ = self.LINES["M0"]
        assert target - monoisotopic_mz > 3 * target * NITRATE.mass_range_ppm * 1e-6
        assert not _readings_of("C10H16O7", find_compositions(target, NITRATE))

        (reading,) = _readings_of(
            "C10H16O7", find_compositions(target, NITRATE, isotopologue_floor=0.002)
        )
        assert reading["isotope_mz"] == pytest.approx(target, abs=1e-6)

    def test_its_offset_is_searched_only_where_such_a_line_exists(self):
        mechanism = parse_ionization("[M+[15N]O3]-")
        lowest, highest = line_offsets(NITRATE, mechanism, 311.0750, 0.002)[0]
        assert lowest == pytest.approx(0.0, abs=1e-6)
        assert highest == pytest.approx(0.009242, abs=1e-6)  # 2H with the 14N
        # Without a labelled reagent, nothing but the monoisotopic line is there.
        assert 0 not in line_offsets(
            PROTONATION, parse_ionization("[M+H]+"), 311.0750, 0.002
        )


def test_a_box_too_wide_to_hold_across_the_lines_is_still_searched(monkeypatch):
    # The windows one search reads - one per mechanism, one per line - spread
    # over daltons, and a box can hold more compositions across that span than
    # a grid may. The windows themselves hold far fewer, and they are all the
    # grid is built of: a bound the span would overflow still answers.
    target = 311.0750
    tolerance = target * HALOGENS.mass_range_ppm * 1e-6
    windows = []
    for notation in HALOGENS.ionizations.split(","):
        mechanism = parse_ionization(notation)
        shift = mechanism.mass if mechanism.addition else -mechanism.mass
        windows.append((target - shift - tolerance, target - shift + tolerance))
        windows += isotopologue_windows(
            target,
            mechanism,
            line_offsets(HALOGENS, mechanism, target, 0.01),
            tolerance,
        )
    low = max(0.0, min(low for low, _ in windows))
    high = max(high for _, high in windows)
    span = len(build_neutral_grid(HALOGENS, low, high))
    held = len(build_neutral_grid(HALOGENS, low, high, windows=windows))
    bound = 2 * held
    assert bound < span, (held, span)
    assert build_neutral_grid(HALOGENS, low, high, max_rows=bound) is None

    expected = find_compositions(target, HALOGENS, isotopologue_floor=0.01)
    assert any(_at_a_line(r) for r in expected)
    monkeypatch.setattr(
        finder, "build_neutral_grid", partial(build_neutral_grid, max_rows=bound)
    )
    assert find_compositions(target, HALOGENS, isotopologue_floor=0.01) == expected
