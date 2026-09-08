"""The neutral grid holds what the element box allows, and says so exactly.

The grid replaced a per-peak tree walk, so what these pin is that it answers the
same question: the compositions in a mass window, all of them and only them.
"""

import numpy as np
import pytest

from mascope_tools.composition.grid import (
    NeutralGrid,
    build_neutral_grid,
)
from mascope_tools.composition.models import CompositionSearchConfig


def grid_for(ranges: str, mass_min: float, mass_max: float, **kwargs) -> NeutralGrid:
    config = CompositionSearchConfig(
        ionizations="+H+", element_count_ranges=ranges, **kwargs
    )
    grid = build_neutral_grid(config, mass_min, mass_max)
    assert grid is not None
    return grid


class TestWhatTheGridHolds:
    def test_the_masses_are_sorted_so_a_window_can_be_bisected(self):
        grid = grid_for("C0-6 H0-12 O0-3", 0.0, 200.0)
        assert np.all(np.diff(grid.mass) >= 0)

    def test_every_composition_of_the_box_in_range_is_there(self):
        # Small enough to enumerate independently: every (C, H, O) triple whose
        # mass lands in the range has to appear exactly once.
        grid = grid_for("C0-3 H0-4 O0-2", 10.0, 60.0)
        found = {tuple(row) for row in grid.counts}
        masses = {atom.symbol: atom.mass for atom in grid.atoms}
        order = [atom.symbol for atom in grid.atoms]
        expected = set()
        for carbon in range(4):
            for hydrogen in range(5):
                for oxygen in range(3):
                    counts = {"C": carbon, "H": hydrogen, "O": oxygen}
                    mass = sum(counts[s] * masses[s] for s in order)
                    if 10.0 <= mass <= 60.0:
                        expected.add(tuple(counts[s] for s in order))
        assert found == expected
        assert len(grid) == len(expected)

    def test_a_composition_outside_the_range_is_not_there(self):
        grid = grid_for("C0-6 H0-12 O0-3", 100.0, 120.0)
        assert grid.mass.min() >= 100.0
        assert grid.mass.max() <= 120.0

    def test_the_minimum_count_of_the_box_is_respected(self):
        # 'C1-3' means at least one carbon, so no row may be carbon-free.
        grid = grid_for("C1-3 H0-8", 0.0, 60.0)
        carbon = [atom.symbol for atom in grid.atoms].index("C")
        assert grid.counts[:, carbon].min() >= 1


class TestTheWindow:
    def test_it_is_closed_at_both_ends(self):
        # A mass exactly on either bound is inside the window: the search's own
        # test is `<= tolerance`, and a candidate must not be lost to a tie.
        grid = grid_for("C0-6 H0-12 O0-3", 0.0, 200.0)
        centre = float(grid.mass[5])
        neighbour = float(grid.mass[6])
        rows = grid.window(centre, neighbour - centre)
        assert 5 in rows and 6 in rows

    def test_it_is_empty_where_the_box_reaches_nothing(self):
        grid = grid_for("C0-6 H0-12 O0-3", 0.0, 200.0)
        assert len(grid.window(150.5, 1e-9)) == 0

    def test_it_finds_every_row_in_range_and_no_other(self):
        grid = grid_for("C0-8 H0-16 O0-4", 0.0, 200.0)
        centre, tolerance = 100.0, 2.0
        rows = list(grid.window(centre, tolerance))
        inside = np.flatnonzero(np.abs(grid.mass - centre) <= tolerance)
        assert rows == inside.tolist()


class TestTheRowBound:
    def test_a_box_too_wide_to_hold_is_refused_rather_than_truncated(self):
        # Half an answer is worse than none: the caller falls back to a grid per
        # peak, which is bounded by the peak's own window.
        config = CompositionSearchConfig(
            ionizations="+H+", element_count_ranges="C0-60 H0-120 N0-10 O0-20"
        )
        assert build_neutral_grid(config, 0.0, 1000.0, max_rows=1000) is None

    def test_a_box_that_fits_is_returned_whole(self):
        config = CompositionSearchConfig(
            ionizations="+H+", element_count_ranges="C0-3 H0-4"
        )
        grid = build_neutral_grid(config, 0.0, 60.0, max_rows=1000)
        assert grid is not None and len(grid) <= 1000

    def test_an_empty_range_holds_nothing(self):
        config = CompositionSearchConfig(
            ionizations="+H+", element_count_ranges="C0-3 H0-4"
        )
        assert build_neutral_grid(config, 500.0, 10.0) is None


class TestUnsaturation:
    def test_a_composition_outside_the_window_is_left_out(self):
        config = CompositionSearchConfig(
            ionizations="+H+",
            element_count_ranges="C0-6 H0-14 O0-2",
            use_unsaturation=True,
            min_unsaturation=0.0,
            max_unsaturation=2.0,
        )
        grid = build_neutral_grid(config, 0.0, 200.0)
        assert grid is not None and grid.unsaturation is not None
        assert grid.unsaturation.min() >= 0.0
        assert grid.unsaturation.max() <= 2.0

    def test_the_value_rides_along_on_the_row_that_kept_it(self):
        config = CompositionSearchConfig(
            ionizations="+H+",
            element_count_ranges="C0-6 H0-14 O0-2",
            use_unsaturation=True,
            min_unsaturation=-10.0,
            max_unsaturation=10.0,
        )
        grid = build_neutral_grid(config, 0.0, 200.0)
        assert grid is not None and grid.unsaturation is not None
        symbols = [atom.symbol for atom in grid.atoms]
        coefficients = {"C": 2, "H": -1, "O": 0}
        for row in range(0, len(grid), 37):
            counts = grid.counts[row]
            expected = (
                sum(coefficients[s] * int(c) for s, c in zip(symbols, counts)) + 2
            ) / 2.0
            assert grid.unsaturation[row] == pytest.approx(expected)

    def test_nothing_is_computed_when_the_search_does_not_ask(self):
        grid = grid_for("C0-6 H0-14 O0-2", 0.0, 200.0, use_unsaturation=False)
        assert grid.unsaturation is None


class TestTheCompositionOfARow:
    def test_zero_counts_are_left_out_the_way_a_formula_leaves_them_out(self):
        grid = grid_for("C1-2 H0-4 O0-1", 0.0, 60.0)
        oxygen_free = next(
            row
            for row in range(len(grid))
            if grid.composition(row).get("O", 0) == 0  # noqa: PLR1714
        )
        assert "O" not in grid.composition(oxygen_free)

    def test_pyteomics_gets_the_notation_it_parses(self):
        # The bracket-first isotope this codebase writes is element-first there,
        # and an ion formula is built from the pyteomics side.
        grid = grid_for("C0-2 [15N]0-2", 0.0, 60.0)
        labelled = next(
            row for row in range(len(grid)) if grid.composition(row).get("[15N]")
        )
        assert "N[15]" in grid.pyteomics_composition(labelled)
        assert "[15N]" in grid.composition(labelled)
