"""The formula search's election on the peaks the lists read, as a run applies it."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from mascope_backend.api.new.peak_assignments.engine import (
    DISPLACED_BY_RIVAL,
    GRID_RIVALS,
    LIST_PRIOR_WEIGHT,
    LIST_READING,
    REFERENCE_IDENTITIES_COL,
    list_readings,
    reading_as_alternative,
    record_displaced_list_readings,
    settle_list_election,
)
from mascope_tools.composition.finder import (
    HELD_BY_LIBRARY,
    HELD_BY_LINES,
    KNOWN_DISPLACED,
    KNOWN_KEPT,
    KNOWN_RIVALS,
    ListReading,
    ReadingRivals,
)


IDENTITIES = [{"reference_list": "target library", "name": "glucose"}]


def _list_hit(row_id: str, peak: str, **fields) -> dict:
    return {
        "peak_assignment_id": row_id,
        "sample_peak_id": peak,
        "role": "M0",
        "tier": "assigned",
        "source": "database",
        "assigned_formula": "C6H12O6",
        "ion_formula": "C6H13O6+",
        "ionization_mechanism_id": "im-1",
        "isotope_label": "M0",
        "target_compound_id": "tc-1",
        "target_ion_id": "ti-1",
        "fit_score": 0.8,
        "mz_error_ppm": 1.0,
        "owner_peak_assignment_id": None,
        "alternatives": None,
        "provenance": {
            "candidate_density": 1,
            "plausibility": 1.0,
            REFERENCE_IDENTITIES_COL: IDENTITIES,
        },
        **fields,
    }


def _child(row_id: str, peak: str, owner: str) -> dict:
    return _list_hit(
        row_id,
        peak,
        role="iso_child",
        isotope_label="13C",
        owner_peak_assignment_id=owner,
    )


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sample_peak_id": ["p1", "p2", "p3", "p4"],
            "mz": [181.0707, 182.0741, 203.0526, 250.5],
            "intensity": [1e4, 660.0, 5e3, 100.0],
        }
    )


def _rivals(*formulas: str, held_against: dict | None = None) -> ReadingRivals:
    return ReadingRivals(
        density=1 + len(formulas),
        rivals=tuple(
            {
                "formula": formula,
                "ion": f"{formula}H+",
                "ionization_mechanism": "+H+",
                "fit_score": 0.9,
                "mz_error_ppm": 0.2,
            }
            for formula in formulas
        ),
        fit_score=0.85,
        candidates=6,
        in_grid=True,
        held_against=held_against,
    )


def _held(why: str) -> dict:
    return {
        "formula": "C7H16O5",
        "ion": "C7H17O5+",
        "ionization_mechanism": "+H+",
        "fit_score": 0.912345,
        "prior": 2.0,
        "why": why,
        "unexplained_lines": [182.07412345] if why == HELD_BY_LINES else [],
    }


def _weighing(peak_mz: float) -> dict:
    return {
        "peak_mz": peak_mz,
        "formula": "C6H12O6",
        "ion": "C6H13O6+",
        "ionization_mechanism": "+H+",
        "fit_score": 0.312345,
        "evidence": 0.312345,
        "rival_evidence": 0.954321,
        "prior": 2.0,
    }


def _reading(mz: float) -> ListReading:
    return ListReading(mz, "C6H12O6", "+H+", 1.0)


class TestTheReadingsAsked:
    def test_each_monoisotopic_list_hit_at_its_peak_through_its_channel(self):
        rows = [
            _list_hit("a", "p1", mz_error_ppm=-0.7),
            _child("a-13c", "p2", "a"),
            _list_hit("b", "p3", assigned_formula="C6H12O6Na", mz_error_ppm=None),
        ]
        readings = list_readings(rows, _frame(), {"im-1": "+H+"})
        assert readings == {
            "a": ListReading(181.0707, "C6H12O6", "+H+", -0.7, keeps_peak=True),
            "b": ListReading(203.0526, "C6H12O6Na", "+H+", None, keeps_peak=True),
        }

    def test_a_target_library_reading_keeps_its_peak_and_a_list_s_does_not(self):
        rows = [
            _list_hit("a", "p1"),
            _list_hit("b", "p3", target_compound_id=None),
            # A pass's row with a compound id is no Stage A library hit.
            _list_hit("c", "p4", source="untargeted"),
        ]
        readings = list_readings(rows, _frame(), {"im-1": "+H+"})
        assert (readings["a"].keeps_peak, readings["b"].keeps_peak) == (True, False)
        assert readings["c"].keeps_peak is False

    def test_a_channel_the_search_does_not_run_is_not_asked(self):
        rows = [_list_hit("a", "p1", ionization_mechanism_id="im-2")]
        assert list_readings(rows, _frame(), {"im-1": "+H+"}) == {}

    def test_a_row_the_frame_does_not_hold_is_not_asked(self):
        rows = [_list_hit("a", "p9")]
        assert list_readings(rows, _frame(), {"im-1": "+H+"}) == {}

    def test_a_row_without_a_formula_is_not_asked(self):
        rows = [_list_hit("a", "p1", assigned_formula=None)]
        assert list_readings(rows, _frame(), {"im-1": "+H+"}) == {}

    def test_a_missing_mass_error_is_none(self):
        rows = [_list_hit("a", "p1", mz_error_ppm=float("nan"))]
        (reading,) = list_readings(rows, _frame(), {"im-1": "+H+"}).values()
        assert reading.mz_error_ppm is None

    def test_the_mz_is_the_frames_own_value(self):
        frame = _frame().astype({"mz": np.float32})
        (reading,) = list_readings(
            [_list_hit("a", "p1")], frame, {"im-1": "+H+"}
        ).values()
        assert reading.mz == float(np.float32(181.0707))


def _kept_marker(mz: float, found) -> dict:
    return {
        "mz": mz,
        "formula": "C6H12O6",
        "ion": "C6H13O6+",
        "isotope_label": "M0",
        "other_candidates": "",
        KNOWN_KEPT: True,
        KNOWN_RIVALS: found,
    }


def _search_row(mz: float, label: str = "M0", **fields) -> dict:
    return {
        "mz": mz,
        "formula": "C7H16O5",
        "ion": "C7H17O5+",
        "isotope_label": label,
        "ionization_mechanism": "+H+",
        "other_candidates": "",
        **fields,
    }


class TestAKeptReading:
    def test_its_row_counts_the_grids_rivals_and_the_marker_goes(self):
        rows = [_list_hit("a", "p1"), _child("a-13c", "p2", "a")]
        matches = pd.DataFrame(
            [_kept_marker(181.0707, _rivals("C7H16O5")), _search_row(250.5)]
        )

        election = settle_list_election(rows, matches, {"a": _reading(181.0707)})

        assert election.stage_a == rows
        assert rows[0]["provenance"]["candidate_density"] == 2
        assert rows[0]["provenance"][GRID_RIVALS]["added"] == 1
        assert election.matches["mz"].tolist() == [250.5]
        assert (election.released, election.displaced) == (set(), {})
        assert election.summary == {
            "measured": 1,
            "with_rivals": 1,
            "taken_by_rivals": 0,
            "kept_by_lines": 0,
            "kept_by_library": 0,
            "prior": LIST_PRIOR_WEIGHT,
        }
        assert "held_against" not in rows[0]["provenance"][GRID_RIVALS]

    def test_the_rival_it_was_held_against_is_recorded_and_counted(self):
        rows = [_list_hit("a", "p1"), _list_hit("b", "p3")]
        matches = pd.DataFrame(
            [
                _kept_marker(
                    181.0707, _rivals("C7H16O5", held_against=_held(HELD_BY_LINES))
                ),
                _kept_marker(
                    203.0526, _rivals("C7H16O5", held_against=_held(HELD_BY_LIBRARY))
                ),
            ]
        )

        election = settle_list_election(
            rows, matches, {"a": _reading(181.0707), "b": _reading(203.0526)}
        )

        assert rows[0]["provenance"][GRID_RIVALS]["held_against"] == {
            "formula": "C7H16O5",
            "ion_formula": "C7H17O5+",
            "ionization_mechanism": "+H+",
            "fit_score": 0.9123,
            "prior": 2.0,
            "why": "unexplained_lines",
            "unexplained_lines": [182.07412],
        }
        assert rows[1]["provenance"][GRID_RIVALS]["held_against"]["why"] == (
            "target_library"
        )
        assert (
            election.summary["kept_by_lines"],
            election.summary["kept_by_library"],
        ) == (1, 1)

    def test_a_frame_of_markers_alone_is_read_the_same(self):
        rows = [_list_hit("a", "p1")]
        matches = pd.DataFrame([_kept_marker(181.0707, _rivals())])
        assert matches[KNOWN_KEPT].dtype == bool

        election = settle_list_election(rows, matches, {"a": _reading(181.0707)})

        assert election.matches.empty
        assert election.summary["measured"] == 1
        assert rows[0]["provenance"][GRID_RIVALS]["added"] == 0

    def test_a_reading_the_search_could_not_read_is_not_measured(self):
        rows = [_list_hit("a", "p1")]
        # The frame turns the missing measurement into a missing value.
        matches = pd.DataFrame([_kept_marker(181.0707, None), _search_row(250.5)])
        assert matches[KNOWN_RIVALS].isna().all()

        election = settle_list_election(rows, matches, {"a": _reading(181.0707)})

        assert GRID_RIVALS not in rows[0]["provenance"]
        assert rows[0]["provenance"]["candidate_density"] == 1
        assert election.summary["measured"] == 0
        assert election.matches["mz"].tolist() == [250.5]

    def test_a_marker_no_reading_names_changes_nothing(self):
        rows = [_list_hit("a", "p1")]
        matches = pd.DataFrame([_kept_marker(250.5, _rivals("C7H16O5"))])

        election = settle_list_election(rows, matches, {"a": _reading(181.0707)})

        assert GRID_RIVALS not in rows[0]["provenance"]
        assert election.summary["measured"] == 0
        assert election.matches.empty


class TestAReadingARivalBeat:
    def test_it_leaves_with_its_lines_and_frees_their_peaks(self):
        rows = [
            _list_hit("a", "p1"),
            _child("a-13c", "p2", "a"),
            _list_hit("b", "p3"),
        ]
        weighing = _weighing(181.0707)
        matches = pd.DataFrame(
            [
                _search_row(
                    181.0707,
                    **{KNOWN_DISPLACED: weighing, KNOWN_RIVALS: _rivals("C7H16O5")},
                ),
                _search_row(182.0741, label="13C"),
                _kept_marker(203.0526, _rivals()),
            ]
        )

        election = settle_list_election(
            rows,
            matches,
            {"a": _reading(181.0707), "b": _reading(203.0526)},
        )

        assert [row["peak_assignment_id"] for row in election.stage_a] == ["b"]
        assert election.released == {"p1", "p2"}
        assert election.displaced == {"p1": (rows[0], weighing)}
        assert election.matches["mz"].tolist() == [181.0707, 182.0741]
        assert election.summary == {
            "measured": 2,
            "with_rivals": 0,
            "taken_by_rivals": 1,
            "kept_by_lines": 0,
            "kept_by_library": 0,
            "prior": LIST_PRIOR_WEIGHT,
        }
        # The displaced reading is not the kept one's to count against.
        assert GRID_RIVALS not in rows[0]["provenance"]

    def test_a_weighing_no_reading_names_takes_nothing(self):
        rows = [_list_hit("a", "p1")]
        matches = pd.DataFrame(
            [_search_row(250.5, **{KNOWN_DISPLACED: _weighing(250.5)})]
        )

        election = settle_list_election(rows, matches, {"a": _reading(181.0707)})

        assert election.stage_a == rows
        assert (election.released, election.displaced) == (set(), {})
        assert election.summary["taken_by_rivals"] == 0


class TestASearchThatAskedNothing:
    def test_its_rows_pass_through(self):
        rows = [_list_hit("a", "p1")]
        matches = pd.DataFrame([_search_row(250.5)])

        election = settle_list_election(rows, matches, {})

        assert election.stage_a == rows
        assert election.matches is matches
        assert election.summary == {
            "measured": 0,
            "with_rivals": 0,
            "taken_by_rivals": 0,
            "kept_by_lines": 0,
            "kept_by_library": 0,
            "prior": LIST_PRIOR_WEIGHT,
        }

    def test_an_empty_result_is_one(self):
        election = settle_list_election(
            [_list_hit("a", "p1")], pd.DataFrame(), {"a": _reading(181.0707)}
        )
        assert election.matches.empty
        assert election.summary["measured"] == 0


class TestTheListReadingAsAnAlternative:
    def test_it_carries_the_compound_and_what_the_list_said(self):
        alternative = reading_as_alternative(_list_hit("a", "p1"), DISPLACED_BY_RIVAL)
        assert alternative == {
            "assigned_formula": "C6H12O6",
            "ion_formula": "C6H13O6+",
            "ionization_mechanism_id": "im-1",
            "isotope_label": "M0",
            "target_compound_id": "tc-1",
            "target_ion_id": "ti-1",
            "fit_score": 0.8,
            "mz_error_ppm": 1.0,
            "plausibility": 1.0,
            "source": "database",
            "displaced_by_rival": True,
            REFERENCE_IDENTITIES_COL: IDENTITIES,
        }

    def test_a_list_hit_without_identities_carries_none(self):
        row = _list_hit("a", "p1", provenance=None)
        alternative = reading_as_alternative(row, DISPLACED_BY_RIVAL)
        assert REFERENCE_IDENTITIES_COL not in alternative
        assert alternative["plausibility"] is None


def _search_assignment(peak: str, role: str = "M0", alternatives=None) -> dict:
    return {
        "peak_assignment_id": f"sb-{peak}",
        "sample_peak_id": peak,
        "role": role,
        "source": "untargeted",
        "assigned_formula": "C7H16O5",
        "alternatives": alternatives,
        "provenance": {"candidate_density": 2},
    }


class TestTheRowThatTookThePeak:
    def test_it_names_the_reading_first_and_the_weighing(self):
        displaced_row = _list_hit("a", "p1")
        runner_up = {"assigned_formula": "C8H20O4", "source": "untargeted"}
        took = _search_assignment("p1", alternatives=[runner_up])
        line = _search_assignment("p2", role="iso_child")
        other = _search_assignment("p4")

        held = record_displaced_list_readings(
            [took, line, other],
            {"p1": (displaced_row, _weighing(181.0707))},
            max_alternatives=5,
        )

        assert held == 1
        assert took["alternatives"] == [
            reading_as_alternative(displaced_row, DISPLACED_BY_RIVAL),
            runner_up,
        ]
        assert took["provenance"][LIST_READING] == {
            "assigned_formula": "C6H12O6",
            "ion_formula": "C6H13O6+",
            "source": "database",
            "target_compound_id": "tc-1",
            "tier": "assigned",
            "evidence": 0.3123,
            "fit_score": 0.3123,
            "rival_evidence": 0.9543,
            "prior": 2.0,
            REFERENCE_IDENTITIES_COL: IDENTITIES,
        }
        assert took["provenance"]["candidate_density"] == 2
        for untouched in (line, other):
            assert LIST_READING not in untouched["provenance"]
            assert untouched["alternatives"] is None

    def test_the_cap_keeps_the_reading_before_the_search_rivals(self):
        took = _search_assignment(
            "p1", alternatives=[{"assigned_formula": "C8H20O4"}] * 3
        )
        record_displaced_list_readings(
            [took], {"p1": (_list_hit("a", "p1"), _weighing(181.0707))}, 2
        )
        assert [entry["assigned_formula"] for entry in took["alternatives"]] == [
            "C6H12O6",
            "C8H20O4",
        ]

    def test_a_run_that_keeps_no_alternatives_keeps_none(self):
        took = _search_assignment("p1")
        record_displaced_list_readings(
            [took], {"p1": (_list_hit("a", "p1"), _weighing(181.0707))}, 0
        )
        assert took["alternatives"] is None
        assert took["provenance"][LIST_READING]["assigned_formula"] == "C6H12O6"

    def test_a_list_hit_without_identities_records_none(self):
        took = _search_assignment("p1")
        record_displaced_list_readings(
            [took],
            {"p1": (_list_hit("a", "p1", provenance={}), _weighing(181.0707))},
            5,
        )
        assert REFERENCE_IDENTITIES_COL not in took["provenance"][LIST_READING]

    def test_a_line_on_the_taken_peak_is_not_the_row_that_took_it(self):
        line = _search_assignment("p1", role="iso_child")
        held = record_displaced_list_readings(
            [line], {"p1": (_list_hit("a", "p1"), _weighing(181.0707))}, 5
        )
        assert held == 0
        assert LIST_READING not in line["provenance"]
        assert line["alternatives"] is None

    def test_a_taken_peak_no_search_row_holds_counts_nothing(self):
        held = record_displaced_list_readings(
            [_search_assignment("p4")],
            {"p1": (_list_hit("a", "p1"), _weighing(181.0707))},
            5,
        )
        assert held == 0

    def test_a_row_without_provenance_gets_one(self):
        took = _search_assignment("p1")
        del took["provenance"]
        record_displaced_list_readings(
            [took], {"p1": (_list_hit("a", "p1"), _weighing(181.0707))}, 5
        )
        assert math.isclose(took["provenance"][LIST_READING]["evidence"], 0.3123)
