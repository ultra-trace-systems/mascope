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

from mascope_backend.api.new.peak_assignments.service import (
    _provenance_scalars,
    provenance_with_calibration,
)


_CURVE = {"instrument": "orbi", "provisional": True, "source": "demo goldens"}
_DATABASE_ROW = {"confidence": 1.0, "evidence": 0.85, "p_correct": 0.91}
_UNTARGETED_ROW = {"plausibility": 1.0, "evidence": 0.42}


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
