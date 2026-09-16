"""A list hit's density, with the formula search's rivals counted in."""

from __future__ import annotations

from mascope_backend.api.new.peak_assignments.engine import (
    GRID_RIVALS,
    GRID_RIVALS_KEPT,
    record_grid_rivals,
)
from mascope_backend.api.new.peak_assignments.tiering import (
    REASON_CANDIDATE_DENSITY,
    apply_tiering,
)
from mascope_tools.composition.finder import ReadingRivals


def _rival(formula: str, fit: float = 0.9) -> dict:
    return {
        "formula": formula,
        "ion": f"{formula}H+",
        "ionization_mechanism": "+H+",
        "fit_score": fit,
        "mz_error_ppm": -0.4,
    }


def _found(*formulas: str, in_grid: bool = True) -> ReadingRivals:
    return ReadingRivals(
        density=1 + len(formulas),
        rivals=tuple(_rival(formula) for formula in formulas),
        fit_score=0.95,
        candidates=12,
        in_grid=in_grid,
    )


def _list_hit(
    row_id: str = "pa-1",
    *,
    density: int | None = 1,
    alternatives: list[dict] | None = None,
    channels: list[str] | None = None,
) -> dict:
    provenance: dict = {"cross_channel": {"channels": channels or ["+H+"]}}
    if density is not None:
        provenance["candidate_density"] = density
    return {
        "peak_assignment_id": row_id,
        "sample_peak_id": f"peak-{row_id}",
        "sample_peak_mz": 229.107,
        "sample_peak_intensity": 1000.0,
        "role": "M0",
        "tier": "assigned",
        "source": "database",
        "assigned_formula": "C11H16O5",
        "ion_formula": None,
        "mz_error_ppm": 0.1,
        "alternatives": alternatives,
        "provenance": provenance,
    }


class TestTheCount:
    def test_the_grid_s_rivals_are_added_to_the_known_set_s(self):
        row = _list_hit(density=2)
        summary = record_grid_rivals([row], [_found("C12H12N4O", "C15H16S")])
        assert row["provenance"]["candidate_density"] == 4
        block = row["provenance"][GRID_RIVALS]
        assert (block["known_density"], block["added"]) == (2, 2)
        assert summary == {"measured": 1, "with_rivals": 1}

    def test_a_rival_the_known_set_already_counted_is_not_counted_twice(self):
        # The known set's formulas are the row's alternatives, however a list
        # spelled them.
        row = _list_hit(density=2, alternatives=[{"assigned_formula": "C12H12N4O1"}])
        record_grid_rivals([row], [_found("C12H12N4O", "C15H16S")])
        assert row["provenance"]["candidate_density"] == 3
        assert [r["formula"] for r in row["provenance"][GRID_RIVALS]["rivals"]] == [
            "C15H16S"
        ]

    def test_a_peak_the_grid_holds_no_rival_for_keeps_its_count(self):
        row = _list_hit(density=1)
        summary = record_grid_rivals([row], [_found()])
        assert row["provenance"]["candidate_density"] == 1
        assert row["provenance"][GRID_RIVALS]["added"] == 0
        assert summary == {"measured": 1, "with_rivals": 0}

    def test_a_row_the_search_could_not_measure_is_left_as_it_was(self):
        row = _list_hit(density=1)
        summary = record_grid_rivals([row], [None])
        assert row["provenance"] == {
            "cross_channel": {"channels": ["+H+"]},
            "candidate_density": 1,
        }
        assert summary == {"measured": 0, "with_rivals": 0}

    def test_a_row_with_no_count_counts_as_standing_alone(self):
        row = _list_hit(density=None)
        record_grid_rivals([row], [_found("C15H16S")])
        assert row["provenance"]["candidate_density"] == 2
        assert row["provenance"][GRID_RIVALS]["known_density"] == 1


class TestWhatTheRowSays:
    def test_the_rivals_are_named_with_their_readings(self):
        row = _list_hit()
        record_grid_rivals([row], [_found("C15H16S")])
        block = row["provenance"][GRID_RIVALS]
        assert block["rivals"] == [
            {
                "formula": "C15H16S",
                "ion_formula": "C15H16SH+",
                "ionization_mechanism": "+H+",
                "fit_score": 0.9,
                "mz_error_ppm": -0.4,
            }
        ]
        assert (block["fit_score"], block["grid_candidates"], block["in_grid"]) == (
            0.95,
            12,
            True,
        )

    def test_only_the_first_few_are_named_and_all_are_counted(self):
        formulas = [f"C{n}H10O2" for n in range(10, 10 + GRID_RIVALS_KEPT + 3)]
        row = _list_hit()
        record_grid_rivals([row], [_found(*formulas)])
        block = row["provenance"][GRID_RIVALS]
        assert len(block["rivals"]) == GRID_RIVALS_KEPT
        assert block["added"] == len(formulas)
        assert row["provenance"]["candidate_density"] == 1 + len(formulas)

    def test_a_formula_outside_the_searched_box_says_so(self):
        row = _list_hit()
        record_grid_rivals([row], [_found(in_grid=False)])
        assert row["provenance"][GRID_RIVALS]["in_grid"] is False


class TestTheDensityRuleReadsIt:
    @staticmethod
    def _tier(row: dict) -> str:
        apply_tiering([row], mz_tolerance_ppm=5.0, abundance_floor=0.01)
        return row["tier"]

    @staticmethod
    def _detail(row: dict) -> str:
        return next(
            reason["detail"]
            for reason in row["provenance"]["tier_reasons"]
            if reason["rule"] == REASON_CANDIDATE_DENSITY
        )

    def test_a_list_hit_with_a_rival_loses_the_top_tier(self):
        row = _list_hit()
        record_grid_rivals([row], [_found("C15H16S")])
        assert self._tier(row) == "candidate"

    def test_the_reason_names_the_rivals_the_search_found(self):
        row = _list_hit()
        record_grid_rivals([row], [_found("C15H16S", "C12H12N4O")])
        self._tier(row)
        detail = self._detail(row)
        assert "3 formulas" in detail
        assert "2 of them from the formula search (C15H16S, C12H12N4O)" in detail

    def test_the_reason_says_when_it_names_only_some(self):
        row = _list_hit()
        record_grid_rivals(
            [row], [_found("C15H16S", "C12H12N4O", "C13H10O3", "C9H14O6")]
        )
        self._tier(row)
        assert (
            "4 of them from the formula search (C15H16S, C12H12N4O, C13H10O3, ...)"
            in (self._detail(row))
        )

    def test_a_second_channel_keeps_the_tier(self):
        row = _list_hit(channels=["+H+", "+NH4+"])
        record_grid_rivals([row], [_found("C15H16S")])
        assert self._tier(row) == "assigned"

    def test_a_known_set_s_tie_is_worded_as_before(self):
        row = _list_hit(density=2)
        self._tier(row)
        assert self._detail(row) == (
            "2 formulas this peak's evidence could not separate, and no second "
            "channel of this run committed the same neutral"
        )
