"""What the ledger records when one ion could be read two ways.

Which reading wins is the finder's decision, taken under the same-ion policy and
covered in ``libraries/tools``. What is pinned here is that the engine does not
throw the decision away: the readings it displaced are stored on the winning row,
flagged, carrying the evidence they actually have - which is the winner's own,
because the ion is the same one.
"""

import pandas as pd

from mascope_backend.api.new.peak_assignments.engine import (
    record_mirror_same_ion_readings,
    untargeted_matches_to_peak_assignments,
)
from mascope_tools.composition.heuristic_filter import formula_plausibility
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
)


MECHANISM_IDS = {"+H+": "im-h", "+NH4+": "im-nh4"}

#: The ammonium adduct of glucose, which is the same ion as the protonated
#: amide one ammonia heavier. The finder commits the adduct reading and hands
#: the covalent one over as a same-ion alternative.
GLUCOSE_AMMONIUM = {
    "formula": "C6H15NO6",
    "ion": "C6H16NO6+",
    "ionization_mechanism": "+H+",
    "neutral_mass": 197.0899,
    "unsaturation": None,
}


def _peaks(*specs):
    return pd.DataFrame(
        [
            {"sample_peak_id": pid, "mz": mz, "intensity": intensity}
            for pid, mz, intensity in specs
        ]
    )


def _match(
    mz,
    formula,
    ion,
    mechanism,
    score,
    isotope_label="M0",
    other_candidates="",
    same_ion=None,
):
    row = {
        "mz": mz,
        "formula": formula,
        "ion": ion,
        "ionization_mechanism": mechanism,
        "isotopic_pattern_score": score,
        "isotope_label": isotope_label,
        "other_candidates": other_candidates,
        "mz_error_ppm": -0.4,
        "intensity_error": 0.0,
    }
    if same_ion is not None:
        row["same_ion_alternatives"] = same_ion
    return row


def _assign(matches, peaks, max_alternatives=5):
    return untargeted_matches_to_peak_assignments(
        pd.DataFrame(matches),
        peaks_df=peaks,
        sample_item_id="si-1",
        peak_assignment_run_id="run-1",
        candidate_threshold=0.45,
        assigned_threshold=0.75,
        mechanism_id_by_notation=MECHANISM_IDS,
        max_alternatives=max_alternatives,
    )


class TestTheDisplacedReadingIsRecorded:
    def test_it_is_stored_flagged_and_promotable(self):
        peaks = _peaks(("p1", 198.0972, 1.0e6))
        (row,) = _assign(
            [
                _match(
                    198.0972,
                    "C6H12O6",
                    "C6H16NO6+",
                    "+NH4+",
                    0.95,
                    same_ion=[GLUCOSE_AMMONIUM],
                )
            ],
            peaks,
        )

        (alternative,) = row["alternatives"]
        assert alternative["same_ion"] is True
        assert alternative["assigned_formula"] == "C6H15NO6"
        assert alternative["ion_formula"] == "C6H16NO6+"
        # A formula is only half an assignment: promoting this by hand has to
        # yield the protonated reading, not an adductless claim.
        assert alternative["ionization_mechanism_id"] == "im-h"
        assert alternative["isotope_label"] == "M0"

    def test_it_carries_the_winners_own_evidence(self):
        # Same ion, same mass, same envelope. Reporting a lower fit for the
        # displaced reading would claim the spectrum preferred one of them.
        peaks = _peaks(("p1", 198.0972, 1.0e6))
        (row,) = _assign(
            [
                _match(
                    198.0972,
                    "C6H12O6",
                    "C6H16NO6+",
                    "+NH4+",
                    0.95,
                    same_ion=[GLUCOSE_AMMONIUM],
                )
            ],
            peaks,
        )

        (alternative,) = row["alternatives"]
        assert alternative["fit_score"] == row["fit_score"]
        assert alternative["mz_error_ppm"] == row["mz_error_ppm"]
        # Plausibility is the exception: it is a property of the NEUTRAL, which
        # is the one thing the two readings disagree about, so it is computed
        # for the displaced formula rather than copied from the winner.
        assert alternative["plausibility"] == round(
            float(formula_plausibility("C6H15NO6")), 4
        )

    def test_it_outranks_a_rival_that_lost_on_the_evidence(self):
        # Ordered by how well each explains the peak: a candidate that tied
        # cannot rank below one that lost, and with the cap at one it is the
        # entry that has to survive.
        peaks = _peaks(("p1", 198.0972, 1.0e6))
        (row,) = _assign(
            [
                _match(
                    198.0972,
                    "C6H12O6",
                    "C6H16NO6+",
                    "+NH4+",
                    0.95,
                    same_ion=[GLUCOSE_AMMONIUM],
                ),
                _match(198.0972, "C5H12N3O5", "C5H13N3O5+", "+H+", 0.60),
            ],
            peaks,
            max_alternatives=1,
        )

        assert row["assigned_formula"] == "C6H12O6"
        (alternative,) = row["alternatives"]
        assert alternative["assigned_formula"] == "C6H15NO6"

    def test_the_formula_only_shortlist_does_not_repeat_it(self):
        # The finder's shortlist names every composition it found for the mass,
        # the displaced reading included. Stored twice, the same reading would
        # read as two candidates and the bare copy would say nothing the flagged
        # one does not.
        peaks = _peaks(("p1", 198.0972, 1.0e6))
        (row,) = _assign(
            [
                _match(
                    198.0972,
                    "C6H12O6",
                    "C6H16NO6+",
                    "+NH4+",
                    0.95,
                    other_candidates="C6H15NO6, C2H16N7O3",
                    same_ion=[GLUCOSE_AMMONIUM],
                )
            ],
            peaks,
        )

        formulas = [alt["assigned_formula"] for alt in row["alternatives"]]
        assert formulas == ["C6H15NO6", "C2H16N7O3"]
        assert [alt.get("same_ion") for alt in row["alternatives"]] == [True, None]


class TestARowWithoutAFamily:
    def test_it_is_stored_exactly_as_before(self):
        # Most peaks have one reading. Nothing about them may change, including
        # on a run where some other peak did have a family and the column
        # therefore exists.
        peaks = _peaks(("p1", 198.0972, 1.0e6), ("p2", 300.1, 5.0e5))
        rows = _assign(
            [
                _match(
                    198.0972,
                    "C6H12O6",
                    "C6H16NO6+",
                    "+NH4+",
                    0.95,
                    same_ion=[GLUCOSE_AMMONIUM],
                ),
                _match(300.1, "C12H21NO8", "C12H22NO8+", "+H+", 0.9),
            ],
            peaks,
        )

        lone = next(row for row in rows if row["assigned_formula"] == "C12H21NO8")
        assert lone["alternatives"] is None


URONIUM_IDS = {"+H+": "im-h", "+NH4+": "im-nh4", "+(CH4N2O)H+": "im-urea"}
SEARCH = CompositionSearchConfig(
    ionizations=",".join(URONIUM_IDS),
    element_count_ranges="C1-40 H0-90 N0-5 O0-15 S0-2",
)
HEURISTICS = HeuristicFilterConfig(use_senior=True)


def _stage_a(
    row_id="pa-1",
    formula="C3H7N1O1",
    mechanism="im-h",
    *,
    compound=None,
    role="M0",
    alternatives=None,
):
    """A Stage A row as the inversion writes it: no compound id on a mirror's."""
    return {
        "peak_assignment_id": row_id,
        "sample_peak_id": f"peak-{row_id}",
        "role": role,
        "source": "database",
        "assigned_formula": formula,
        "ion_formula": "C3H8N1O1+",
        "ionization_mechanism_id": mechanism,
        "isotope_label": "M0" if role == "M0" else "M+1",
        "fit_score": 0.93,
        "mz_error_ppm": 0.21,
        "target_compound_id": compound,
        "alternatives": alternatives,
    }


def _record(rows, max_alternatives=5, ids=None):
    return record_mirror_same_ion_readings(
        rows,
        mechanism_id_by_notation=URONIUM_IDS if ids is None else ids,
        search_config=SEARCH,
        heuristics_config=HEURISTICS,
        max_alternatives=max_alternatives,
    )


class TestAReferenceMirrorsRow:
    """A list's formula arrives matched, and is given its ion's family."""

    def test_it_carries_the_reading_an_election_would_have_displaced(self):
        # Dimethylformamide through +H+ is acrolein through +NH4+.
        rows = [_stage_a()]
        assert _record(rows) == 1

        (alternative,) = rows[0]["alternatives"]
        assert alternative == {
            "assigned_formula": "C3H4O",
            "ion_formula": "C3H8N1O1+",
            "ionization_mechanism_id": "im-nh4",
            "isotope_label": "M0",
            # The row's own measurement: the ion is the same one.
            "fit_score": 0.93,
            "mz_error_ppm": 0.21,
            "plausibility": round(float(formula_plausibility("C3H4O")), 4),
            "same_ion": True,
            "source": "database",
        }

    def test_the_readings_go_ahead_of_the_scored_rivals_and_the_cap_holds(self):
        rivals = [
            {"assigned_formula": "C2H3N3", "ionization_mechanism_id": "im-h"},
            {"assigned_formula": "C4H11N", "ionization_mechanism_id": "im-h"},
        ]
        rows = [_stage_a(alternatives=list(rivals))]
        _record(rows, max_alternatives=2)
        assert [alt["assigned_formula"] for alt in rows[0]["alternatives"]] == [
            "C3H4O",
            "C2H3N3",
        ]

    def test_a_rival_that_restates_a_reading_is_that_reading(self):
        # The known set can hold acrolein as a compound of its own, matched to
        # the same peak through the ammonium: that is the family's reading, so
        # it is flagged and moved ahead, keeping what it carries, not repeated.
        rivals = [
            {"assigned_formula": "C4H11N", "ionization_mechanism_id": "im-h"},
            {
                "assigned_formula": "C3H4O1",
                "ionization_mechanism_id": "im-nh4",
                "reference_identities": [{"name": "acrolein"}],
            },
        ]
        rows = [_stage_a(alternatives=list(rivals))]
        _record(rows)
        alternatives = rows[0]["alternatives"]
        assert [alt["assigned_formula"] for alt in alternatives] == [
            "C3H4O1",
            "C4H11N",
        ]
        assert alternatives[0]["same_ion"] is True
        assert alternatives[0]["reference_identities"] == [{"name": "acrolein"}]

    def test_a_target_library_row_is_given_none(self):
        rows = [_stage_a(compound="tc-1")]
        assert _record(rows) == 0
        assert rows[0]["alternatives"] is None

    def test_an_isotopologue_is_given_none(self):
        rows = [_stage_a(role="iso_child")]
        assert _record(rows) == 0
        assert rows[0]["alternatives"] is None

    def test_a_row_through_a_channel_the_run_did_not_search_is_given_none(self):
        rows = [_stage_a(mechanism="im-other")]
        assert _record(rows) == 0

    def test_an_ion_with_one_reading_is_left_as_it_came(self):
        # Carbon dioxide through +H+ has no neutral one ammonia or one urea
        # lighter, so there is no family and nothing is written.
        rows = [_stage_a(formula="C1O2")]
        assert _record(rows) == 0
        assert rows[0]["alternatives"] is None
