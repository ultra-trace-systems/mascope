"""A list hit meets the formula search's grid.

A list reading wins its peak before the search runs, so the formulas the element
box holds for that mass were never enumerated there. These pin how the grid is
asked about such a peak: which candidates count as rivals, on what scale, what
is reported, and when a rival takes the peak from the reading.
"""

import numpy as np
import pandas as pd
import pytest

from mascope_tools.composition.arbitration import candidate_density, unseparated
from mascope_tools.composition.finder import (
    HELD_BY_LIBRARY,
    HELD_BY_LINES,
    KNOWN_DISPLACED,
    KNOWN_KEPT,
    KNOWN_RIVALS,
    ListReading,
    ReadingRivals,
    _held_against,
    _ScoredReading,
    _weigh,
    _Weighing,
    assign_compositions,
    rivals_of_readings,
)
from mascope_tools.composition.heuristic_filter import (
    PATTERN_BASE_SNR,
    PATTERN_REQUIRED_LINES,
    neutral_is_closed_shell,
)
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
    PatternScoring,
)
from mascope_tools.composition.utils import calculate_mass, parse_ionization


def _box(ionizations="[M+H]+", ppm=30.0):
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
MZ = calculate_mass(formula="C11H16O5") + parse_ionization("[M+H]+").mass

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
        readings or [ListReading(MZ, "C11H16O5", "[M+H]+", 0.0)],
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
        assert first["ionization_mechanism"] == "[M+H]+"
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
        mz = calculate_mass(formula="C11H19NO5") + parse_ionization("[M+H]+").mass
        (found,) = rivals_of_readings(
            pd.DataFrame({"mz": [mz], "intensity": [100.0], "signal_to_noise": [2.0]}),
            _box(ionizations="[M+H]+,[M+NH4]+"),
            [ListReading(mz, "C11H19NO5", "[M+H]+", 0.0)],
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
            UNRESOLVED, readings=[ListReading(MZ, "C11H16O5", "[M+H]+", 0.0)]
        )
        (hill,) = _measure(
            UNRESOLVED, readings=[ListReading(MZ, "O5C11H16", "[M+H]+", 0.0)]
        )
        assert spelled == hill

    def test_a_formula_outside_the_box_says_so(self):
        silicon = "C4H14Si2O"
        mz = calculate_mass(formula=silicon) + parse_ionization("[M+H]+").mass
        (found,) = rivals_of_readings(
            pd.DataFrame({"mz": [mz], "intensity": [100.0]}),
            _box(),
            [ListReading(mz, silicon, "[M+H]+", 0.0)],
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
            ListReading(MZ, "Xx3", "[M+H]+", 0.0),
            ListReading(MZ, "C11H16O5", "[M+Xx]-", 0.0),
        ],
    )
    def test_a_reading_nobody_can_parse_is_not_measured(self, reading):
        assert _measure(UNRESOLVED, readings=[reading]) == [None]

    def test_the_results_keep_the_readings_order(self):
        # Given in neither ascending nor descending m/z: the grid is walked in
        # ascending m/z whatever order the readings arrive in.
        low = calculate_mass(formula="C10H16O4") + parse_ionization("[M+H]+").mass
        high = calculate_mass(formula="C12H18O6") + parse_ionization("[M+H]+").mass
        peaks = pd.DataFrame(
            {
                "mz": [low, MZ, high],
                "intensity": [100.0, 100.0, 100.0],
                "signal_to_noise": [2.0, 2.0, 2.0],
            }
        )
        readings = [
            ListReading(MZ, "C11H16O5", "[M+H]+", 0.0),
            ListReading(low, "C10H16O4", "[M+H]+", 0.0),
            ListReading(high, "C12H18O6", "[M+H]+", 0.0),
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


def _kept(row) -> bool:
    """Whether a result row is a kept reading's marker. A frame holds a missing
    value on the rows that are not, and numpy's bool where every row is one."""
    value = row.get(KNOWN_KEPT)
    return bool(value) if pd.notna(value) else False


PROTON = parse_ionization("[M+H]+").mass

#: C12H12N4O through a proton, where a list reads C11H16O5 5.8 ppm off.
TAKEN_MZ = calculate_mass(formula="C12H12N4O") + PROTON
LINE_MZ = TAKEN_MZ + 1.003355

#: C11H17NO2S through a proton, whose 13C line lands 1 ppm from TAKEN_MZ.
LIGHT_MZ = calculate_mass(formula="C11H17NO2S") + PROTON

#: C9H15N3O4 through a proton, 13.6 ppm from C11H16O5's 13C line.
LISTED_MZ = calculate_mass(formula="C9H15N3O4") + PROTON


def _elect(scoring, *, prior=2.0, formula="C11H16O5", keeps_peak=False):
    peaks = pd.DataFrame(
        {
            "mz": [TAKEN_MZ, LINE_MZ],
            "intensity": [1e5, 1.4e4],
            "signal_to_noise": [1000.0, 140.0],
        }
    )
    matches, _ = assign_compositions(
        peaks,
        _box(),
        HEURISTICS,
        targets=[TAKEN_MZ],
        scoring=scoring,
        known={TAKEN_MZ: ListReading(TAKEN_MZ, formula, "[M+H]+", 5.8, keeps_peak)},
        known_prior=prior,
        closed_shell_rivals=True,
    )
    return {round(float(row["mz"]), 4): row for _, row in matches.iterrows()}


class TestTheElection:
    def test_a_rival_well_ahead_takes_the_peak_and_its_lines(self):
        rows = _elect(RESOLVED)
        taken = rows[round(TAKEN_MZ, 4)]
        assert (taken["formula"], taken["isotope_label"]) == ("C12H12N4O", "M0")
        weighing = taken[KNOWN_DISPLACED]
        assert weighing["peak_mz"] == TAKEN_MZ
        assert (weighing["formula"], weighing["ion"]) == ("C11H16O5", "C11H17O5+")
        assert weighing["ionization_mechanism"] == "[M+H]+"
        assert weighing["prior"] == 2.0
        assert weighing["rival_evidence"] > 2.0 * weighing["evidence"]
        assert weighing["fit_score"] == taken[KNOWN_RIVALS].fit_score
        assert isinstance(taken[KNOWN_RIVALS], ReadingRivals)
        assert not _kept(taken)
        line = rows[round(LINE_MZ, 4)]
        assert (line["formula"], line["isotope_label"]) == ("C12H12N4O", "13C")
        # The weighing is about the peak the ion took, not its lines.
        assert pd.isna(line[KNOWN_DISPLACED])
        assert pd.isna(line[KNOWN_RIVALS])

    def test_the_rival_counts_what_competed_for_the_peak(self):
        taken = _elect(RESOLVED)[round(TAKEN_MZ, 4)]
        assert taken["candidate_density"] >= 1
        assert "C11H16O5" not in str(taken["other_candidates"]).split(",")

    def test_the_reading_keeps_a_peak_the_prior_holds(self):
        rows = _elect(UNRESOLVED)
        kept = rows[round(TAKEN_MZ, 4)]
        assert _kept(kept)
        assert (kept["formula"], kept["isotope_label"]) == ("C11H16O5", "M0")
        assert isinstance(kept[KNOWN_RIVALS], ReadingRivals)
        assert "C12H12N4O" in {rival["formula"] for rival in kept[KNOWN_RIVALS].rivals}
        assert pd.isna(kept.get(KNOWN_DISPLACED))
        # The list's lines are the list's: nothing claims them.
        assert rows[round(LINE_MZ, 4)]["formula"] == "---"

    def test_a_list_spelling_is_named_as_its_composition(self):
        # What a list typed, read the way the grid names its formulas.
        kept = _elect(UNRESOLVED, formula="O5C11H16")[round(TAKEN_MZ, 4)]
        assert kept["formula"] == "C11H16O5"
        taken = _elect(RESOLVED, formula="O5C11H16")[round(TAKEN_MZ, 4)]
        assert taken[KNOWN_DISPLACED]["formula"] == "C11H16O5"

    def test_without_the_prior_the_same_rival_takes_it(self):
        rows = _elect(UNRESOLVED, prior=1.0)
        taken = rows[round(TAKEN_MZ, 4)]
        assert taken["formula"] == "C12H12N4O"
        assert taken[KNOWN_DISPLACED]["prior"] == 1.0

    def test_a_radical_never_takes_a_list_peak(self):
        radical = calculate_mass(formula="C9H14N3O4") + PROTON
        peaks = pd.DataFrame(
            {"mz": [radical], "intensity": [1e5], "signal_to_noise": [1000.0]}
        )

        def elect(closed):
            matches, _ = assign_compositions(
                peaks,
                _box(),
                HEURISTICS,
                targets=[radical],
                scoring=PatternScoring(sigma_ppm=0.5, mz_tolerance_ppm=30.0),
                known={radical: ListReading(radical, "C11H16O5", "[M+H]+", -5.9)},
                known_prior=2.0,
                closed_shell_rivals=closed,
            )
            return matches.iloc[0]

        kept = elect(True)
        assert _kept(kept)
        assert kept["formula"] == "C11H16O5"
        assert elect(False)["formula"] == "C9H14N3O4"

    def test_an_unreadable_reading_keeps_its_peak_unmeasured(self):
        rows = _elect(RESOLVED, formula="Xx3")
        kept = rows[round(TAKEN_MZ, 4)]
        assert _kept(kept)
        assert (kept["formula"], kept["ion"]) == ("Xx3", "---")
        assert pd.isna(kept[KNOWN_RIVALS])

    def test_a_search_without_list_peaks_is_the_search(self):
        peaks = pd.DataFrame(
            {
                "mz": [TAKEN_MZ, LINE_MZ],
                "intensity": [1e5, 1.4e4],
                "signal_to_noise": [1000.0, 140.0],
            }
        )
        plain, _ = assign_compositions(
            peaks, _box(), HEURISTICS, targets=[TAKEN_MZ], scoring=RESOLVED
        )
        asked, _ = assign_compositions(
            peaks,
            _box(),
            HEURISTICS,
            targets=[TAKEN_MZ],
            scoring=RESOLVED,
            known={},
            known_prior=2.0,
            closed_shell_rivals=True,
        )
        pd.testing.assert_frame_equal(plain, asked)
        assert not {KNOWN_KEPT, KNOWN_DISPLACED, KNOWN_RIVALS} & set(plain.columns)


class TestAListPeakAnEarlierEnvelopeReached:
    """A list's peak is elected on even where a lighter ion's envelope matched
    it as a line first."""

    @staticmethod
    def _search(peaks, targets, known):
        matches, _ = assign_compositions(
            peaks,
            _box(),
            HEURISTICS,
            targets=targets,
            scoring=RESOLVED,
            known=known,
            known_prior=2.0,
            closed_shell_rivals=True,
        )
        return {round(float(row["mz"]), 4): row for _, row in matches.iterrows()}

    def test_the_reading_it_keeps_holds_the_peak(self):
        peaks = pd.DataFrame(
            {
                "mz": [MZ, LISTED_MZ],
                "intensity": [1e5, 1.2e4],
                "signal_to_noise": [1000.0, 120.0],
            }
        )
        targets = [MZ, LISTED_MZ]
        alone = self._search(peaks, targets, None)
        assert alone[round(LISTED_MZ, 4)]["isotope_label"] == "13C"

        rows = self._search(
            peaks,
            targets,
            {LISTED_MZ: ListReading(LISTED_MZ, "C9H15N3O4", "[M+H]+", 0.0)},
        )
        assert len(rows) == 2
        listed = rows[round(LISTED_MZ, 4)]
        assert _kept(listed)
        assert (listed["formula"], listed["isotope_label"]) == ("C9H15N3O4", "M0")
        light = rows[round(MZ, 4)]
        assert (light["formula"], light["isotope_label"]) == ("C11H16O5", "M0")

    def test_a_rival_takes_it_from_the_reading_and_the_envelope(self):
        peaks = pd.DataFrame(
            {
                "mz": [LIGHT_MZ, TAKEN_MZ, LINE_MZ],
                "intensity": [1e5, 1.3e4, 1.8e3],
                "signal_to_noise": [1000.0, 130.0, 18.0],
            }
        )
        targets = [LIGHT_MZ, TAKEN_MZ]
        alone = self._search(peaks, targets, None)
        assert alone[round(TAKEN_MZ, 4)]["formula"] == "C11H17NO2S"
        assert alone[round(TAKEN_MZ, 4)]["isotope_label"] == "13C"

        rows = self._search(
            peaks,
            targets,
            {TAKEN_MZ: ListReading(TAKEN_MZ, "C11H16O5", "[M+H]+", 5.8)},
        )
        assert len(rows) == 3
        taken = rows[round(TAKEN_MZ, 4)]
        assert (taken["formula"], taken["isotope_label"]) == ("C12H12N4O", "M0")
        assert taken[KNOWN_DISPLACED]["formula"] == "C11H16O5"
        assert rows[round(LINE_MZ, 4)]["formula"] == "C12H12N4O"
        assert rows[round(LIGHT_MZ, 4)]["formula"] == "C11H17NO2S"


#: A reading's own line and one line of its envelope, at round m/z.
PEAK, LINE = 300.0, 301.0

#: A sample scored at an Orbitrap-like width, so a line tracks within 1 ppm.
NARROW = PatternScoring(sigma_ppm=0.5)


def _envelope(masses, shares, errors, excess=None):
    return {
        "masses": np.asarray(masses, dtype=float),
        "predicted_intensities": np.asarray(shares, dtype=float),
        "mass_errors_ppm": np.asarray(errors, dtype=float),
        "intensity_errors": np.asarray(
            excess if excess is not None else [0.0] * len(masses), dtype=float
        ),
    }


def _rival(formula, fit, *, required=True, matched=(), excess=0.0):
    """A hand-scored rival: its fit, whether its required lines are there, the
    lines beyond its own that its envelope matched, and how much more those
    lines hold than it predicts."""
    return formula, fit, required, tuple(matched), excess


def _weighed(
    own_fit,
    rivals,
    *,
    prior=2.0,
    own_formula="C11H16O5",
    own_line=None,
    snr=100.0,
    scoring=NARROW,
):
    """How :func:`_weigh` judges hand-scored readings.

    :param own_line: ``(share, mass error)`` of one line of the reading's
        envelope beside its own, matched on :data:`LINE`; none by default.
    """
    own = {"formula": own_formula, "ion": "OWN+", "counts": {}, "candidate": {}}
    scored = [
        {
            "formula": own_formula,
            "ion": "OWN+",
            "isotopic_pattern_score": own_fit,
            PATTERN_REQUIRED_LINES: True,
            PATTERN_BASE_SNR: snr,
        },
        *(
            {
                "formula": formula,
                "ion": f"{formula}+",
                "isotopic_pattern_score": fit,
                PATTERN_REQUIRED_LINES: required,
                PATTERN_BASE_SNR: snr,
            }
            for formula, fit, required, _, _ in rivals
        ),
    ]
    isotopes = [
        _envelope([PEAK], [1.0], [0.1])
        if own_line is None
        else _envelope([PEAK, LINE], [1.0, own_line[0]], [0.1, own_line[1]]),
        *(
            _envelope(
                [PEAK, *matched],
                [1.0, *[0.3] * len(matched)],
                [0.1, *[0.2] * len(matched)],
                [0.0, *[excess] * len(matched)],
            )
            for _, _, _, matched, excess in rivals
        ),
    ]
    return _weigh(
        _ScoredReading(
            own=own, scored=scored, isotopes=isotopes, found=[], rivals=None
        ),
        prior,
        scoring,
    )


class TestTheWeighing:
    def test_past_twice_the_reading_is_not_enough_inside_a_tie(self):
        # 0.9 is past twice 0.44, but not by the gap a tie is counted at: a
        # tenth of the best evidence.
        assert _weighed(0.44, [_rival("C12H12N4O", 0.9)]).winner is None

    def test_past_twice_the_reading_and_the_gap_it_takes_the_peak(self):
        assert _weighed(0.4, [_rival("C12H12N4O", 0.9)]) == _Weighing(winner=1, ahead=1)

    def test_the_prior_is_what_holds_the_peak(self):
        assert _weighed(0.45, [_rival("C12H12N4O", 0.9)], prior=1.0).winner == 1

    def test_a_tie_never_lets_a_rival_in(self):
        weighing = _weighed(0.85, [_rival("C12H12N4O", 0.9)], prior=1.0)
        assert weighing == _Weighing(winner=None)

    def test_a_rival_without_its_required_lines_never_takes_it(self):
        weighing = _weighed(0.1, [_rival("C12H12N4O", 0.9, required=False)])
        assert weighing == _Weighing(winner=None)

    def test_the_rival_with_the_most_evidence_takes_it(self):
        rivals = [_rival("C12H12N4O", 0.6), _rival("C15H16S", 0.9)]
        assert _weighed(0.1, rivals).winner == 2

    def test_an_impossible_rival_has_no_evidence(self):
        assert _weighed(0.1, [_rival("C3H19NO10", 0.99)]).winner is None

    def test_an_impossible_reading_has_none_either(self):
        weighing = _weighed(0.9, [_rival("C12H12N4O", 0.5)], own_formula="C3H19NO10")
        assert weighing.winner == 1

    def test_a_rival_has_to_clear_the_floor_where_the_reading_scored_nothing(self):
        assert _weighed(0.0, [_rival("C12H12N4O", 0.004)]).winner is None
        assert _weighed(0.0, [_rival("C12H12N4O", 0.01)]).winner == 1

    def test_nothing_scored_takes_nothing(self):
        reading = _ScoredReading(own={}, scored=[], isotopes=[], found=[], rivals=None)
        assert _weigh(reading, 2.0, NARROW) == _Weighing(winner=None)


class TestTheReadingsOwnLines:
    """A rival that clears the prior takes the peak only where it explains the
    lines that are evidence for the reading as well."""

    def test_a_rival_leaving_a_tracking_line_unexplained_does_not_take_it(self):
        weighing = _weighed(0.1, [_rival("C12H12N4O", 0.9)], own_line=(0.3, 0.2))
        assert weighing == _Weighing(winner=None, ahead=1, unexplained=(LINE,))

    def test_a_rival_explaining_the_line_takes_it(self):
        weighing = _weighed(
            0.1, [_rival("C12H12N4O", 0.9, matched=[LINE])], own_line=(0.3, 0.2)
        )
        assert weighing == _Weighing(winner=1, ahead=1)

    def test_a_rival_predicting_a_trace_of_the_line_does_not_explain_it(self):
        # Matched, but the line holds four times what the rival predicts there.
        weighing = _weighed(
            0.1,
            [_rival("C12H12N4O", 0.9, matched=[LINE], excess=3.0)],
            own_line=(0.3, 0.2),
        )
        assert weighing == _Weighing(winner=None, ahead=1, unexplained=(LINE,))
        # Half of it is enough.
        weighing = _weighed(
            0.1,
            [_rival("C12H12N4O", 0.9, matched=[LINE], excess=1.0)],
            own_line=(0.3, 0.2),
        )
        assert weighing.winner == 1

    def test_the_best_rival_that_explains_the_lines_takes_it(self):
        rivals = [
            _rival("C15H16S", 0.95),
            _rival("C12H12N4O", 0.9, matched=[LINE]),
        ]
        weighing = _weighed(0.1, rivals, own_line=(0.3, 0.2))
        assert weighing == _Weighing(winner=2, ahead=1, unexplained=(LINE,))

    def test_a_line_the_spectrum_could_not_have_shown_is_no_evidence(self):
        # 2% of a peak at a signal-to-noise of 100 is below the fit's own
        # detection limit.
        weighing = _weighed(0.1, [_rival("C12H12N4O", 0.9)], own_line=(0.02, 0.2))
        assert weighing.winner == 1

    def test_a_line_within_two_widths_of_the_ions_own_error_tracks(self):
        # 0.8 ppm from the ion's own error: past one width, inside two.
        weighing = _weighed(0.1, [_rival("C12H12N4O", 0.9)], own_line=(0.3, 0.9))
        assert weighing.winner is None

    def test_a_line_off_the_ions_own_error_is_no_evidence(self):
        # 2.9 ppm from the ion's own error, where two widths are 1 ppm.
        weighing = _weighed(0.1, [_rival("C12H12N4O", 0.9)], own_line=(0.3, 3.0))
        assert weighing.winner == 1

    def test_without_a_noise_estimate_a_line_counts_by_its_share(self):
        def weigh(share):
            return _weighed(
                0.1, [_rival("C12H12N4O", 0.9)], own_line=(share, 0.2), snr=None
            ).winner

        assert weigh(0.3) is None
        assert weigh(0.05) == 1

    def test_a_sample_with_no_width_is_judged_at_the_fits_fallback(self):
        # 2.9 ppm is inside two widths of the fit's fallback.
        weighing = _weighed(
            0.1, [_rival("C12H12N4O", 0.9)], own_line=(0.3, 3.0), scoring=None
        )
        assert weighing.winner is None


#: A cyclic siloxane through the urea reagent on an Orbitrap sample, with its
#: 29Si and 30Si lines, where a CHO formula through a proton sits 0.2 ppm off.
SILOXANE = pd.DataFrame(
    {
        "mz": [431.13375, 432.133163, 433.130401],
        "intensity": [13840.19, 2394.93, 2202.75],
        "signal_to_noise": [89.01, 15.86, 15.10],
    }
)
SILOXANE_SCORING = PatternScoring(
    sigma_ppm=0.552, mu_ppm=-0.0165, mz_tolerance_ppm=5.0, abundance_floor=1e-5
)


class TestADistinctiveEnvelope:
    def _elect(self, *, keeps_peak=False):
        mz = float(SILOXANE["mz"][0])
        matches, _ = assign_compositions(
            SILOXANE,
            _box(ionizations="[M+H]+,[M+CH4N2O+H]+", ppm=3.0),
            HEURISTICS,
            targets=[mz],
            scoring=SILOXANE_SCORING,
            known={
                mz: ListReading(mz, "C10H30O5Si5", "[M+CH4N2O+H]+", 0.36, keeps_peak)
            },
            known_prior=2.0,
            closed_shell_rivals=True,
        )
        return matches.set_index("mz")

    def test_the_silicon_lines_keep_the_siloxanes_peak(self):
        kept = self._elect().loc[float(SILOXANE["mz"][0])]
        assert _kept(kept)
        assert kept["formula"] == "C10H30O5Si5"
        held = kept[KNOWN_RIVALS].held_against
        # A CHO formula fits the monoisotopic line far better than the
        # siloxane, whose 29Si line is weaker than predicted, and explains
        # neither silicon line.
        assert held["why"] == HELD_BY_LINES
        assert "Si" not in held["formula"]
        assert held["fit_score"] > 2 * kept[KNOWN_RIVALS].fit_score
        assert held["unexplained_lines"] == [432.133163, 433.130401]

    def test_the_lines_stay_the_lists(self):
        matches = self._elect()
        assert set(matches.loc[[432.133163, 433.130401], "formula"]) == {"---"}


class TestTheTargetLibrary:
    def test_a_library_reading_keeps_the_peak_a_rival_would_take(self):
        rows = _elect(RESOLVED, keeps_peak=True)
        kept = rows[round(TAKEN_MZ, 4)]
        assert _kept(kept)
        assert kept["formula"] == "C11H16O5"
        held = kept[KNOWN_RIVALS].held_against
        assert (held["formula"], held["why"]) == ("C12H12N4O", HELD_BY_LIBRARY)
        assert held["unexplained_lines"] == []
        # Its rivals are counted all the same.
        assert "C12H12N4O" in {rival["formula"] for rival in kept[KNOWN_RIVALS].rivals}
        assert rows[round(LINE_MZ, 4)]["formula"] == "---"

    def test_a_library_reading_nothing_clears_the_prior_against_says_nothing(self):
        kept = _elect(UNRESOLVED, keeps_peak=True)[round(TAKEN_MZ, 4)]
        assert kept[KNOWN_RIVALS].held_against is None

    def test_the_hold_names_the_rival_that_would_have_taken_the_peak(self):
        # The best rival leaves the line unexplained; the next one explains it
        # and would take the peak, were the reading not the library's.
        own = {"formula": "C11H16O5", "ion": "OWN+", "counts": {}, "candidate": {}}
        measured = _ScoredReading(
            own=own,
            scored=[
                {"formula": "C11H16O5", "ion": "OWN+"},
                {
                    "formula": "C15H16S",
                    "ion": "C15H16S+",
                    "ionization_mechanism": "[M+H]+",
                },
                {
                    "formula": "C12H12N4O",
                    "ion": "C12H12N4O+",
                    "ionization_mechanism": "[M+H]+",
                    "isotopic_pattern_score": 0.9,
                },
            ],
            isotopes=[],
            found=[],
            rivals=ReadingRivals(
                density=3, rivals=(), fit_score=0.1, candidates=2, in_grid=True
            ),
        )
        weighing = _Weighing(winner=2, ahead=1, unexplained=(LINE,))
        held = _held_against(measured, weighing, 2.0).held_against
        assert held == {
            "formula": "C12H12N4O",
            "ion": "C12H12N4O+",
            "ionization_mechanism": "[M+H]+",
            "fit_score": 0.9,
            "prior": 2.0,
            "why": HELD_BY_LIBRARY,
            "unexplained_lines": [],
        }

    def test_a_reading_that_keeps_its_peak_anyway_names_its_lines_first(self):
        mz = float(SILOXANE["mz"][0])
        matches = TestADistinctiveEnvelope()._elect(keeps_peak=True)
        held = matches.loc[mz][KNOWN_RIVALS].held_against
        assert held["why"] == HELD_BY_LINES
