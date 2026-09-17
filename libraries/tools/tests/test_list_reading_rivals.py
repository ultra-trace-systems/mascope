"""A list hit meets the formula search's grid.

A list reading wins its peak before the search runs, so the formulas the element
box holds for that mass were never enumerated there. These pin how the grid is
asked about such a peak: which candidates count as rivals, on what scale, and
what is reported.
"""

import pandas as pd
import pytest

from mascope_tools.composition.arbitration import candidate_density, unseparated
from mascope_tools.composition.finder import (
    ListReading,
    rivals_of_readings,
)
from mascope_tools.composition.heuristic_filter import neutral_is_closed_shell
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
    PatternScoring,
)
from mascope_tools.composition.utils import calculate_mass, parse_ionization


def _box(ionizations="+H+", ppm=30.0):
    return CompositionSearchConfig(
        ionizations=ionizations,
        element_count_ranges="C1-30 H0-60 N0-4 O0-12 S0-1",
        mass_range_ppm=ppm,
        use_unsaturation=True,
        min_unsaturation=-1000.0,
        max_unsaturation=10000.0,
    )


HEURISTICS = HeuristicFilterConfig(use_senior=True)

#: C11H16O5 through a proton. Its box holds four other formulas within 12 ppm:
#: C12H12N4O and C15H16S, closed-shell, and C9H14N3O4 and C14H14NO2, radicals.
MZ = calculate_mass(formula="C11H16O5") + parse_ionization("+H+").mass

#: A faint lone peak judged at a width as wide as the window: nothing the
#: spectrum shows separates the formulas the box holds for its mass.
UNRESOLVED = PatternScoring(sigma_ppm=30.0, mz_tolerance_ppm=30.0)

#: The same peak at an Orbitrap-like width, where the mass separates them.
RESOLVED = PatternScoring(sigma_ppm=5.0, mz_tolerance_ppm=30.0)


def _peak(snr=2.0):
    return pd.DataFrame({"mz": [MZ], "intensity": [100.0], "signal_to_noise": [snr]})


def _measure(scoring, *, readings=None, closed_shell_only=False, config=None):
    return rivals_of_readings(
        _peak(),
        config or _box(),
        readings or [ListReading(MZ, "C11H16O5", "+H+", 0.0)],
        HEURISTICS,
        scoring,
        closed_shell_only=closed_shell_only,
    )


class TestTheCount:
    def test_the_rivals_the_evidence_cannot_separate_are_counted(self):
        (found,) = _measure(UNRESOLVED)
        assert found.density == 5
        assert {rival["formula"] for rival in found.rivals} == {
            "C12H12N4O",
            "C9H14N3O4",
            "C15H16S",
            "C14H14NO2",
        }
        assert found.candidates == 8

    def test_a_peak_the_evidence_resolves_has_none(self):
        (found,) = _measure(RESOLVED)
        assert (found.density, found.rivals) == (1, ())

    def test_the_rivals_are_named_best_first_with_their_readings(self):
        (found,) = _measure(UNRESOLVED)
        fits = [rival["fit_score"] for rival in found.rivals]
        assert fits == sorted(fits, reverse=True)
        first = found.rivals[0]
        assert first["ionization_mechanism"] == "+H+"
        assert first["ion"].endswith("+")
        assert first["mz_error_ppm"] is not None

    def test_the_reading_is_scored_on_the_same_scale(self):
        (found,) = _measure(UNRESOLVED)
        assert found.fit_score == pytest.approx(1.0)
        assert all(rival["fit_score"] <= found.fit_score for rival in found.rivals)


class TestWhatIsNoRival:
    def test_a_radical_is_none_when_the_caller_asks_for_closed_shells(self):
        (found,) = _measure(UNRESOLVED, closed_shell_only=True)
        assert {rival["formula"] for rival in found.rivals} == {
            "C12H12N4O",
            "C15H16S",
        }
        assert found.density == 3
        assert all(neutral_is_closed_shell(r["formula"]) for r in found.rivals)

    def test_the_same_ion_split_another_way_is_none(self):
        # C11H19NO5 through a proton is the ion C11H16O5 makes with ammonium:
        # one hypothesis split two ways, which an election collapses before
        # anything is ranked, and never a rival.
        mz = calculate_mass(formula="C11H19NO5") + parse_ionization("+H+").mass
        (found,) = rivals_of_readings(
            pd.DataFrame({"mz": [mz], "intensity": [100.0], "signal_to_noise": [2.0]}),
            _box(ionizations="+H+,+NH4+"),
            [ListReading(mz, "C11H19NO5", "+H+", 0.0)],
            HEURISTICS,
            UNRESOLVED,
        )
        assert found.rivals
        assert all(rival["ion"] != "C11H20NO5+" for rival in found.rivals)
        assert "C11H16O5" not in {rival["formula"] for rival in found.rivals}

    def test_the_reading_itself_is_never_its_own_rival(self):
        (found,) = _measure(UNRESOLVED)
        assert "C11H16O5" not in {rival["formula"] for rival in found.rivals}


class TestTheReadings:
    def test_a_list_spelling_is_read_as_its_composition(self):
        # A library writes what a person typed; the reading is the same neutral.
        (spelled,) = _measure(
            UNRESOLVED, readings=[ListReading(MZ, "C11H16O5", "+H+", 0.0)]
        )
        (hill,) = _measure(
            UNRESOLVED, readings=[ListReading(MZ, "O5C11H16", "+H+", 0.0)]
        )
        assert spelled == hill

    def test_a_formula_outside_the_box_says_so(self):
        silicon = "C4H14Si2O"
        mz = calculate_mass(formula=silicon) + parse_ionization("+H+").mass
        (found,) = rivals_of_readings(
            pd.DataFrame({"mz": [mz], "intensity": [100.0]}),
            _box(),
            [ListReading(mz, silicon, "+H+", 0.0)],
            HEURISTICS,
            UNRESOLVED,
        )
        assert found.in_grid is False

    def test_a_formula_inside_the_box_says_so(self):
        (found,) = _measure(RESOLVED)
        assert found.in_grid is True

    @pytest.mark.parametrize(
        "reading",
        [
            ListReading(MZ, "Xx3", "+H+", 0.0),
            ListReading(MZ, "C11H16O5", "+Xx-", 0.0),
        ],
    )
    def test_a_reading_nobody_can_parse_is_not_measured(self, reading):
        assert _measure(UNRESOLVED, readings=[reading]) == [None]

    def test_the_results_keep_the_readings_order(self):
        # Given in neither ascending nor descending m/z: the grid is walked in
        # ascending m/z whatever order the readings arrive in.
        low = calculate_mass(formula="C10H16O4") + parse_ionization("+H+").mass
        high = calculate_mass(formula="C12H18O6") + parse_ionization("+H+").mass
        peaks = pd.DataFrame(
            {
                "mz": [low, MZ, high],
                "intensity": [100.0, 100.0, 100.0],
                "signal_to_noise": [2.0, 2.0, 2.0],
            }
        )
        readings = [
            ListReading(MZ, "C11H16O5", "+H+", 0.0),
            ListReading(low, "C10H16O4", "+H+", 0.0),
            ListReading(high, "C12H18O6", "+H+", 0.0),
        ]
        together = rivals_of_readings(peaks, _box(), readings, HEURISTICS, UNRESOLVED)
        alone = [
            rivals_of_readings(peaks, _box(), [reading], HEURISTICS, UNRESOLVED)[0]
            for reading in readings
        ]
        assert together == alone
        assert together[0] != together[1]
        assert together[1] != together[2]

    def test_nothing_to_measure_measures_nothing(self):
        assert rivals_of_readings(_peak(), _box(), [], HEURISTICS, UNRESOLVED) == []


class TestTheNamedCount:
    @pytest.mark.parametrize(
        "candidates, around",
        [
            ([("A", 0.9), ("B", 0.85), ("C", 0.2)], "A"),
            ([("A", 0.9), ("B", 0.85), ("C", 0.2)], "B"),
            ([("A", 0.9), ("B", 0.85), ("C", 0.2)], "C"),
            ([("A", 0.0), ("B", 0.0)], "A"),
            ([("A", 0.9), ("A", 0.5), ("B", 0.89)], "A"),
            # A formula arriving twice is counted at its best evidence.
            ([("A", 0.9), ("B", 0.5), ("A", 0.3)], "B"),
            # The gap is a tenth of the best evidence, not of the anchor's.
            ([("A", 1.0), ("B", 0.5), ("C", 0.42)], "B"),
            # Near zero evidence the gap is its floor.
            ([("A", 0.01), ("B", 0.006)], "A"),
        ],
    )
    def test_it_names_what_the_density_counts(self, candidates, around):
        named = unseparated(candidates, around=around)
        assert len(named) == candidate_density(candidates, around=around)
        assert around in named

    def test_it_names_the_best_first(self):
        assert unseparated([("B", 0.85), ("A", 0.9)], around="B") == ["A", "B"]

    def test_an_anchor_among_no_candidates_stands_alone(self):
        assert unseparated([("A", 0.9)], around="Z") == ["Z"]

    def test_no_candidates_name_nothing(self):
        assert unseparated([], around="A") == []
