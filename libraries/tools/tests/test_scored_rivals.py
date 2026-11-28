"""The search hands over every candidate it scored, where a run counts them.

A committed reading's rival set holds the candidates the search could not
separate from it at its own scale (``finder.CANDIDATE_RIVALS``). With
``CompositionSearchConfig.scored_rivals`` on, the search also hands over every
other formula it scored for the peak, with how far it had the reading in tie
gaps (``finder.SCORED_RIVALS``, ``ReadingRivals.scored``), so a run can count
which of them tie at its refitted model (MAS-1184). With it off it hands over
what it did before, and either way it commits the same readings.
"""

from dataclasses import replace

import pandas as pd
import pytest
from test_list_reading_rivals import (
    HEURISTICS,
    LINE_MZ,
    MZ,
    RESOLVED,
    TAKEN_MZ,
    UNRESOLVED,
    _box,
    _elect,
    _peak,
)

from mascope_tools.composition import utils
from mascope_tools.composition.arbitration import tie_gaps, unseparated
from mascope_tools.composition.finder import (
    CANDIDATE_RIVALS,
    SCORED_RIVALS,
    ListReading,
    assign_compositions,
    rivals_of_readings,
)
from mascope_tools.composition.rivals import scored_rivals


def _on(config=None):
    return replace(config or _box(), scored_rivals=True)


class TestTieGaps:
    @pytest.mark.parametrize(
        "candidates, around",
        [
            ([("C5H10O2", 0.9), ("C4H6O3", 0.86), ("C6H14O", 0.3)], "C5H10O2"),
            ([("C5H10O2", 0.9), ("C4H6O3", 0.86), ("C6H14O", 0.3)], "C4H6O3"),
            ([("C5H10O2", 0.5), ("C9H14N3O4", 0.49), ("C12H12N4O", 0.1)], "C5H10O2"),
            ([("C5H10O2", 0.0), ("C4H6O3", 0.0)], "C5H10O2"),
            ([("C5H10O2", 0.004), ("C4H6O3", 0.001)], "C4H6O3"),
        ],
    )
    def test_a_formula_within_one_gap_is_one_the_count_holds(self, candidates, around):
        gaps = tie_gaps(candidates, around=around)
        counted = unseparated(candidates, around=around)
        assert {formula for formula, gap in gaps.items() if gap <= 1} == set(counted)

    def test_a_distance_is_the_evidence_below_over_the_gap(self):
        gaps = tie_gaps([("C5H10O2", 0.9), ("C6H14O", 0.3)], around="C5H10O2")
        # The gap is a tenth of the best evidence, 0.09: 0.6 below is 6.67.
        assert gaps["C5H10O2"] == 0
        assert gaps["C6H14O"] == pytest.approx(0.6 / 0.09)

    def test_a_formula_above_the_anchor_reads_below_zero(self):
        gaps = tie_gaps([("C5H10O2", 0.5), ("C4H6O3", 0.9)], around="C5H10O2")
        assert gaps["C4H6O3"] < 0

    def test_an_anchor_among_no_candidates_has_no_distances(self):
        assert tie_gaps([("C4H6O3", 0.9)], around="C5H10O2") == {}


class TestAListReading:
    def _measure(self, scoring, config):
        (found,) = rivals_of_readings(
            _peak(),
            config,
            [ListReading(MZ, "C11H16O5", "[M+H]+", 0.0)],
            HEURISTICS,
            scoring,
        )
        return found

    def test_off_it_hands_over_nothing_more(self):
        assert self._measure(RESOLVED, _box()).scored == ()

    def test_on_it_hands_over_every_formula_scored_beside_it(self):
        off = self._measure(RESOLVED, _box())
        on = self._measure(RESOLVED, _on())
        # The same measurement, and the scored ones beside it.
        assert replace(on, scored=()) == off
        formulas = {rival["formula"] for rival in on.scored}
        # Every candidate of the window for another ion, the four the box
        # holds within 12 ppm among them, each once.
        assert len(formulas) == len(on.scored) == on.candidates == 8
        assert {"C12H12N4O", "C9H14N3O4", "C15H16S", "C14H14NO2"} <= formulas
        # The resolved peak separates them all: every one past a tie gap.
        assert off.density == 1
        assert all(rival["tie_gaps"] > 1 for rival in on.scored)
        gaps = [rival["tie_gaps"] for rival in on.scored]
        assert gaps == sorted(gaps)
        assert all(
            {"formula", "ion", "ionization_mechanism", "fit_score", "tie_gaps"}
            <= set(rival)
            for rival in on.scored
        )

    def test_those_it_could_not_separate_read_within_one_gap(self):
        on = self._measure(UNRESOLVED, _on())
        within = {rival["formula"] for rival in on.scored if rival["tie_gaps"] <= 1}
        assert within == {rival["formula"] for rival in on.rivals}


def _rows(matches) -> dict:
    return {round(float(row["mz"]), 4): row for _, row in matches.iterrows()}


class TestACommittedReading:
    def _search(self, config, scoring=UNRESOLVED):
        matches, _ = assign_compositions(
            _peak(snr=50.0), config, HEURISTICS, scoring=scoring
        )
        return matches

    def test_off_the_search_hands_over_what_it_did(self):
        assert SCORED_RIVALS not in self._search(_box()).columns

    def test_on_it_commits_the_same_readings(self):
        off = self._search(_box())
        on = self._search(_on())
        pd.testing.assert_frame_equal(on.drop(columns=[SCORED_RIVALS]), off)

    def test_on_the_committed_reading_names_every_formula_its_peak_scored(self):
        (row,) = [row for _, row in self._search(_on()).iterrows()]
        scored = row[SCORED_RIVALS]
        named = {rival["formula"] for rival in row[CANDIDATE_RIVALS]}
        assert {r["formula"] for r in scored if r["tie_gaps"] <= 1} == named
        assert row["formula"] not in {r["formula"] for r in scored}
        # Another split of the committed ion is the same hypothesis, no rival.
        ion = utils.ion_identity(row["ion"])
        assert all(utils.ion_identity(r["ion"]) != ion for r in scored)

    def test_a_rival_that_took_a_list_peak_hands_over_its_own(self):
        off = _elect(RESOLVED)
        peaks = pd.DataFrame(
            {
                "mz": [TAKEN_MZ, LINE_MZ],
                "intensity": [1e5, 1.4e4],
                "signal_to_noise": [1000.0, 140.0],
            }
        )
        matches, _ = assign_compositions(
            peaks,
            _on(),
            HEURISTICS,
            targets=[TAKEN_MZ],
            scoring=RESOLVED,
            known={TAKEN_MZ: ListReading(TAKEN_MZ, "C11H16O5", "[M+H]+", 5.8)},
            known_prior=2.0,
            closed_shell_rivals=True,
        )
        on = _rows(matches)
        taken = on[round(TAKEN_MZ, 4)]
        assert taken["formula"] == off[round(TAKEN_MZ, 4)]["formula"] == "C12H12N4O"
        # The list's own reading is among what its peak scored.
        assert "C11H16O5" in {r["formula"] for r in taken[SCORED_RIVALS]}
        # An isotope line was never searched, and hands over nothing.
        line = on[round(LINE_MZ, 4)]
        assert not isinstance(line[SCORED_RIVALS], list)


class TestWhatIsHandedOver:
    """``rivals.scored_rivals`` on a stated peak."""

    CANDIDATES = [
        # The committed reading, and its formula through another channel.
        {"formula": "C5H10O2", "ion": "C5H11O2+", "isotopic_pattern_score": 0.9},
        {"formula": "C5H10O2", "ion": "C5H10O2Na+", "isotopic_pattern_score": 0.2},
        # The committed ion split another way: the same hypothesis.
        {"formula": "C4H8O2", "ion": "C5H11O2+", "isotopic_pattern_score": 0.89},
        # Two rivals, the farther one first, each by its best reading.
        {"formula": "C6H14O", "ion": "C6H15O+", "isotopic_pattern_score": 0.3},
        {"formula": "C4H6O3", "ion": "C4H7O3+", "isotopic_pattern_score": 0.5},
        {"formula": "C4H6O3", "ion": "C4H7O3+", "isotopic_pattern_score": 0.85},
    ]

    def test_every_other_ion_once_nearest_first(self):
        handed = scored_rivals(self.CANDIDATES, "C5H10O2", "C5H11O2+")
        assert [r["formula"] for r in handed] == ["C4H6O3", "C6H14O"]
        assert [r["fit_score"] for r in handed] == [0.85, 0.3]
        gaps = tie_gaps(
            [("C5H10O2", 0.9), ("C4H8O2", 0.89), ("C6H14O", 0.3), ("C4H6O3", 0.85)],
            around="C5H10O2",
        )
        assert [r["tie_gaps"] for r in handed] == [gaps["C4H6O3"], gaps["C6H14O"]]

    def test_the_committed_formula_through_another_channel_is_no_rival(self):
        handed = scored_rivals(self.CANDIDATES[1:], "C5H10O2", "C5H11O2+")
        assert "C5H10O2" not in {r["formula"] for r in handed}
