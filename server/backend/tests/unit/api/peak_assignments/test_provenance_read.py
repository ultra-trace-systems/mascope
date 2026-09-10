"""
Unit tests for how the read path folds the run's confidence calibration back
into a row.

The engine records `p_correct` per row and the curve it came from once per run
(`PeakAssignmentRun.confidence_calibration`). Readers still see the pair the
engine used to write into every row - `calibrated` / `calibration` - because
`provenance_with_calibration` puts it back on the detail row and
`_provenance_scalars` reads the provisional flag off the run for the list row.
These pin both, including the rows that never had the pair and the rows written
before the move that still carry their own.
"""

import pytest

from mascope_backend.api.new.peak_assignments.service import (
    _provenance_scalars,
    provenance_with_calibration,
)


_CURVE = {"instrument": "orbi", "provisional": True, "source": "demo goldens"}
_DATABASE_ROW = {"confidence": 1.0, "evidence": 0.85, "p_correct": 0.91}
_UNTARGETED_ROW = {"plausibility": 1.0, "evidence": 0.42}

#: Every provenance shape the column can hold, for the agreement test below.
_SHAPES = {
    "a row written after the move": {"p_correct": 0.91},
    "a row carrying its own block": {
        **_DATABASE_ROW,
        "calibrated": True,
        "calibration": {"provisional": False},
    },
    "a row carrying calibrated alone": {**_DATABASE_ROW, "calibrated": False},
    "a row carrying calibration alone": {
        **_DATABASE_ROW,
        "calibration": {"provisional": False},
    },
    "an uncalibrated legacy row": {
        "p_correct": None,
        "calibrated": False,
        "calibration": None,
    },
    "an untargeted row": _UNTARGETED_ROW,
    "an imported row with the client's own calibrated": {
        "engine_note": "external",
        "calibrated": True,
    },
    "an empty blob": {},
}


class TestProvenanceWithCalibration:
    def test_a_database_row_gets_the_pair_from_the_run(self):
        folded = provenance_with_calibration(_DATABASE_ROW, _CURVE)
        assert folded == {**_DATABASE_ROW, "calibrated": True, "calibration": _CURVE}
        # A new dict, not the caller's mutated in place.
        assert "calibration" not in _DATABASE_ROW

    def test_an_uncalibrated_run_says_so(self):
        row = {**_DATABASE_ROW, "p_correct": None}
        assert provenance_with_calibration(row, None) == {
            **row,
            "calibrated": False,
            "calibration": None,
        }

    def test_a_row_without_p_correct_never_had_the_pair(self):
        """Stage B rows, unassigned rows, imported rows: untouched."""
        assert provenance_with_calibration(_UNTARGETED_ROW, _CURVE) == _UNTARGETED_ROW
        assert provenance_with_calibration(None, _CURVE) is None
        assert provenance_with_calibration({}, _CURVE) == {}

    def test_a_row_that_still_carries_its_own_block_is_left_as_it_is(self):
        """Written before the move, or seeded that way: its block wins."""
        own = {
            **_DATABASE_ROW,
            "calibrated": True,
            "calibration": {"provisional": False},
        }
        assert provenance_with_calibration(own, _CURVE) is own
        stripped = {**_DATABASE_ROW, "calibrated": False}
        assert provenance_with_calibration(stripped, _CURVE) is stripped


class TestProvenanceScalars:
    def test_the_provisional_flag_comes_off_the_run(self):
        scalars = _provenance_scalars(_DATABASE_ROW, _CURVE)
        assert scalars["p_correct"] == 0.91
        assert scalars["p_correct_provisional"] is True

    def test_a_row_with_its_own_block_reads_that_block(self):
        own = {**_DATABASE_ROW, "calibration": {"provisional": False}}
        assert _provenance_scalars(own, _CURVE)["p_correct_provisional"] is False

    def test_rows_without_p_correct_report_no_flag(self):
        """The run's curve says nothing about a row it did not calibrate."""
        assert (
            _provenance_scalars(_UNTARGETED_ROW, _CURVE)["p_correct_provisional"]
            is None
        )
        assert _provenance_scalars(None, _CURVE)["p_correct_provisional"] is None

    def test_an_uncalibrated_run_reports_no_flag(self):
        row = {**_DATABASE_ROW, "p_correct": None}
        assert _provenance_scalars(row, None)["p_correct_provisional"] is None

    def test_the_other_scalars_are_unchanged(self):
        row = {**_DATABASE_ROW, "corroboration": {"n_adducts": 2}}
        scalars = _provenance_scalars(row, _CURVE)
        assert scalars["evidence"] == 0.85
        assert scalars["corroboration_adducts"] == 2

    def test_the_channel_count_reaches_the_ledger(self):
        # The corroboration marker is blank on an untargeted row without this:
        # `corroboration_adducts` counts the adducts a CURATED compound matched
        # through, so it is null on most of a ledger.
        row = {**_UNTARGETED_ROW, "cross_channel": {"channels": ["+H+", "+NH4+"]}}
        assert _provenance_scalars(row, None)["corroboration_channels"] == 2

    def test_a_row_seen_in_one_channel_says_one(self):
        # Not None: one channel is a measured answer, and the marker's own
        # threshold is what decides whether it is worth rendering.
        row = {**_UNTARGETED_ROW, "cross_channel": {"channels": ["+NH4+"]}}
        assert _provenance_scalars(row, None)["corroboration_channels"] == 1

    def test_a_row_the_pass_never_reached_has_no_count(self):
        # Absent rather than 0, so "not measured" stays distinguishable from
        # "measured and found nothing" the way every other scalar here is.
        assert (
            _provenance_scalars(_UNTARGETED_ROW, None)["corroboration_channels"] is None
        )
        assert (
            _provenance_scalars({"cross_channel": {"inherited_from": "pa-1"}}, None)[
                "corroboration_channels"
            ]
            is None
        )


class TestTheCandidateDensityOnTheLedger:
    """Step 2.4: how many formulas the peak's evidence could not tell apart.

    A ledger column rather than inspector detail because it cannot be recovered
    from what the row stores: `alternatives` is capped at the run's
    `max_alternatives`, so a reader counting those counts the cap.
    """

    def test_the_count_reaches_the_ledger(self):
        row = {**_UNTARGETED_ROW, "candidate_density": 3}
        assert _provenance_scalars(row, None)["candidate_density"] == 3

    def test_an_uncontested_peak_says_one(self):
        # Not None: one is a measured answer - the winner stood alone at the top
        # of the arbitration - and it is the answer the tiering acts on.
        row = {**_UNTARGETED_ROW, "candidate_density": 1}
        assert _provenance_scalars(row, None)["candidate_density"] == 1

    def test_a_row_nothing_measured_has_no_count(self):
        # A satellite, or a row imported from an engine that sends none.
        # Absent rather than 0, so "not measured" stays distinguishable from
        # "measured and found nothing", as every other scalar here is.
        assert _provenance_scalars(_UNTARGETED_ROW, None)["candidate_density"] is None
        assert _provenance_scalars(None, None)["candidate_density"] is None

    def test_it_is_not_the_number_of_candidates(self):
        # Stage A records both, and they answer different questions: how many
        # the confidence was normalised across, and how many of those the
        # evidence could not separate.
        row = {**_DATABASE_ROW, "n_candidates": 9, "candidate_density": 2}
        assert _provenance_scalars(row, _CURVE)["candidate_density"] == 2


@pytest.mark.parametrize("provenance", _SHAPES.values(), ids=list(_SHAPES))
def test_the_detail_fold_and_the_ledger_scalars_agree(provenance):
    """One row, two readers, one answer.

    The detail response carries both: the folded `provenance` the inspector
    reads its provisional marker out of, and the flattened
    `p_correct_provisional` the ledger column renders. They are produced by
    different functions on adjacent lines, so a row they resolve differently
    makes a single response contradict itself - `calibrated: false` beside a
    provisional flag read off the run. Both go through `_row_calibration`,
    and this is what says they still do.
    """
    folded = provenance_with_calibration(dict(provenance), _CURVE)
    detail_curve = folded.get("calibration") if isinstance(folded, dict) else None
    ledger = _provenance_scalars(dict(provenance), _CURVE)
    assert (detail_curve or {}).get("provisional") == ledger["p_correct_provisional"]
