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

import importlib.util
from pathlib import Path

import pytest

from mascope_backend.api.new.peak_assignments import tiering
from mascope_backend.api.new.peak_assignments.ledger_reads import _provenance_scalars
from mascope_backend.api.new.peak_assignments.model_readers import (
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
        row = {**_UNTARGETED_ROW, "cross_channel": {"channels": ["[M+H]+", "[M+NH4]+"]}}
        assert _provenance_scalars(row, None)["corroboration_channels"] == 2

    def test_a_row_seen_in_one_channel_says_one(self):
        # Not None: one channel is a measured answer, and the marker's own
        # threshold is what decides whether it is worth rendering.
        row = {**_UNTARGETED_ROW, "cross_channel": {"channels": ["[M+NH4]+"]}}
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
        # An isotopologue, or a row imported from an engine that sends none.
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


class TestTheRuleRecordsOnTheLedger:
    """What the gate card reads of every committed row, flattened (#3134).

    Each is None where the row holds no such record, so a run that predates
    the record reads as not measured rather than as nothing found.
    """

    def test_the_reasons_rules_in_order(self):
        row = {
            "tier_reasons": [
                {"rule": "evidence_band", "detail": "x", "caps": False},
                {"rule": "lone_peak", "detail": "y", "caps": True},
            ]
        }
        assert _provenance_scalars(row)["tier_reason_rules"] == [
            "evidence_band",
            "lone_peak",
        ]

    def test_a_reason_naming_no_rule_is_left_out(self):
        # The column is a list of names; a None in it would fail the row's
        # validation and with it the whole page.
        row = {"tier_reasons": [{"detail": "x"}, "text", {"rule": "lone_peak"}]}
        assert _provenance_scalars(row)["tier_reason_rules"] == ["lone_peak"]

    def test_no_reasons_is_none(self):
        assert _provenance_scalars({})["tier_reason_rules"] is None
        assert _provenance_scalars({"tier_reasons": []})["tier_reason_rules"] is None

    def test_the_mass_gate_corroboration(self):
        def read(gate):
            return _provenance_scalars({"mass_gate": gate})["mass_gate_corroborated"]

        assert read({"corroborated_by": "list"}) is True
        assert read({"corroborated_by": ["second_channel"]}) is True
        assert read({"corroborated_by": None, "capped": True}) is False
        assert _provenance_scalars({})["mass_gate_corroborated"] is None

    def test_the_absent_lines_are_counted(self):
        envelope = {
            "lines": [
                {"state": "absent"},
                {"state": "found"},
                {"state": "untestable"},
                {"state": "absent"},
            ]
        }
        scalars = _provenance_scalars({"predicted_envelope": envelope})
        assert scalars["envelope_absent_lines"] == 2
        # Asked and nothing missing is 0; never asked is None.
        assert (
            _provenance_scalars({"predicted_envelope": {"lines": []}})[
                "envelope_absent_lines"
            ]
            == 0
        )
        assert _provenance_scalars({})["envelope_absent_lines"] is None

    def test_an_unseparated_peak_with_no_separated_partner(self):
        def read(provenance):
            return _provenance_scalars(provenance)["peak_unseparated"]

        unseparated = {"peak_separation": {"separated": False}}
        assert read(unseparated) is True
        assert read({**unseparated, "cross_channel": {"separated_through": []}}) is True
        # A partner channel committing the neutral on a separated peak clears it.
        assert (
            read({**unseparated, "cross_channel": {"separated_through": ["[M+Br]-"]}})
            is False
        )
        assert read({"peak_separation": {"separated": True}}) is False
        assert read({}) is None


def _card_metrics():
    """The gate card's metrics module (``tooling/assignment_compare``), loaded
    from the checkout: it is a script folder, not a package."""
    root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "tooling" / "assignment_compare" / "metrics.py").is_file()
    )
    path = root / "tooling" / "assignment_compare" / "metrics.py"
    spec = importlib.util.spec_from_file_location("card_metrics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Provenance shapes for the card's agreement test below.
_RULE_SHAPES = {
    "an empty blob": {},
    "reasons, a corroborated gate, an envelope, a separated peak": {
        "tier_reasons": [{"rule": "corroborated", "detail": "", "caps": False}],
        "mass_gate": {"corroborated_by": "list"},
        "predicted_envelope": {"lines": [{"state": "found"}]},
        "peak_separation": {"separated": True},
    },
    "an uncorroborated gate, two absent lines, an unseparated peak": {
        "tier_reasons": [{"rule": "absent_line", "detail": "", "caps": True}],
        "mass_gate": {"corroborated_by": None},
        "predicted_envelope": {
            "lines": [{"state": "absent"}, {"state": "absent"}, {"state": "found"}]
        },
        "peak_separation": {"separated": False},
        "cross_channel": {"channels": ["a", "b"]},
    },
    "an unseparated peak a partner channel clears": {
        "tier_reasons": [],
        "peak_separation": {"separated": False},
        "cross_channel": {"separated_through": ["b"]},
    },
    "an envelope with no lines": {"predicted_envelope": {}},
    "a row a series of its batch keeps at assigned": {
        "tier_reasons": [
            {"rule": "series_anchor", "detail": "", "caps": False},
            {"rule": "no_close_rival", "detail": "", "caps": False},
        ],
        "series": {"has_anchor": True},
    },
}


@pytest.mark.parametrize("provenance", _RULE_SHAPES.values(), ids=list(_RULE_SHAPES))
def test_the_card_reads_off_the_ledger_what_it_read_off_the_detail(provenance):
    """The gate card read these four from each row's detail provenance; it now
    reads them off the ledger where the server serves them. Both reads must
    give the card the same columns, or a card's readings would move with the
    server it ran against rather than with the engine."""
    metrics = _card_metrics()
    assert metrics.ledger_columns(
        _provenance_scalars(dict(provenance))
    ) == metrics.provenance_columns(dict(provenance))


def test_g12_knows_every_escape_of_the_tiering_rule():
    """G12 names the four second observations ``tiering.lone_peak_reason``
    lets a search row escape on, by the reasons the rule's rows keep their
    tier on; the series one is the reason it reads off the ledger."""
    metrics = _card_metrics()
    assert metrics.LONE_PEAK_ESCAPES == (
        tiering.REASON_SECOND_LINE,
        tiering.REASON_CORROBORATED,
        tiering.REASON_SERIES_ANCHOR,
        tiering.REASON_ON_A_LIST,
    )
    assert metrics.SERIES_ANCHOR == tiering.REASON_SERIES_ANCHOR
    # The rule's series escape is what raises that reason on a kept row.
    row = {
        "source": "untargeted",
        "assigned_formula": "C6H10O4",
        "provenance": {
            "cross_channel": {"channels": ["dep"]},
            tiering.SERIES: {"has_anchor": True, "unit": "CH2"},
        },
    }
    assert tiering.lone_peak_reason(row, []) is None
    assert metrics.SERIES_ANCHOR in [r["rule"] for r in tiering.standing_reasons(row)]
    row["provenance"].pop(tiering.SERIES)
    assert tiering.lone_peak_reason(row, [])["rule"] == tiering.REASON_LONE_PEAK
