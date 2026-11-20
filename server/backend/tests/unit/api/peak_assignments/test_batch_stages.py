"""Unit tests for the batch stages' order and their staleness rule's pure parts.

What a pass runs (:func:`batch_stages.plan`), what a stage's outcome says of
it, how a record takes its version, and what the ledger serves of each stage.
The database side is in the integration suite (``test_batch_stages_api.py``).
See ``batch_stages.py``.
"""

from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments import batch_stages
from mascope_backend.api.new.peak_assignments.batch_stages import (
    STAGE_OF_KEY,
    STAGES,
    STATE_CURRENT,
    STATE_NOT_RUN,
    STATE_STALE,
    BatchStage,
    first_not_current,
    plan,
    stages_after,
    stages_from,
    stages_outcome,
    standing_entries,
    stopped_partway,
    upstream_reason,
    with_version,
)


ORDER = [
    "water_ladders",
    "known_species",
    "search",
    "isotope_lines",
    "series",
    "co_variation",
    "continuity",
    "batch_checks",
    "blends",
]


def test_the_stages_run_in_the_order_their_reads_give():
    # Every stage after every stage whose claims or records it reads, and every
    # stage that rewrites members before every stage that only reads.
    assert [spec.stage.value for spec in STAGES] == ORDER
    rewriting = [spec.rewrites for spec in STAGES]
    assert rewriting == [True] * 5 + [False] * 4


def test_each_record_key_names_the_stage_that_writes_it():
    assert STAGE_OF_KEY["water_ladder"] is BatchStage.WATER_LADDERS
    assert STAGE_OF_KEY["known_species"] is BatchStage.KNOWN_SPECIES
    assert STAGE_OF_KEY["merged_isotopologue_of"] is BatchStage.ISOTOPE_LINES
    assert STAGE_OF_KEY["stamped_isotopologue_of"] is BatchStage.ISOTOPE_LINES
    assert STAGE_OF_KEY["series"] is BatchStage.SERIES
    assert STAGE_OF_KEY["co_variation"] is BatchStage.CO_VARIATION
    assert STAGE_OF_KEY["flat_series"] is BatchStage.CO_VARIATION
    assert STAGE_OF_KEY["batch_checks"] is BatchStage.BATCH_CHECKS


def test_stages_from_and_after():
    assert [s.value for s in stages_from("series")] == ORDER[4:]
    assert [s.value for s in stages_after(BatchStage.SERIES)] == ORDER[5:]
    assert stages_after(BatchStage.BLENDS) == ()


class TestThePlan:
    def test_a_named_stage_runs_with_every_stage_after_it(self):
        current = {name: STATE_CURRENT for name in ORDER}
        assert [s.value for s in plan(current, "co_variation")] == ORDER[5:]
        assert [s.value for s in plan({}, BatchStage.WATER_LADDERS)] == ORDER

    def test_a_named_stage_after_one_not_current_starts_at_that_one(self):
        states = {name: STATE_CURRENT for name in ORDER}
        states["series"] = STATE_STALE
        assert [s.value for s in plan(states, "co_variation")] == ORDER[4:]
        # A stale stage after the named one changes nothing.
        assert [s.value for s in plan(states, "search")] == ORDER[2:]
        # Nothing run yet: the pass starts at the first stage.
        assert [s.value for s in plan({}, "blends")] == ORDER

    def test_no_stage_named_starts_at_the_first_not_current(self):
        states = {name: STATE_CURRENT for name in ORDER}
        states["series"] = STATE_STALE
        states["batch_checks"] = STATE_STALE
        assert [s.value for s in plan(states, None)] == ORDER[4:]
        assert first_not_current(states) is BatchStage.SERIES

    def test_a_stage_that_never_ran_is_not_current(self):
        states = {name: STATE_CURRENT for name in ORDER if name != "known_species"}
        assert first_not_current(states) is BatchStage.KNOWN_SPECIES
        assert plan({}, None) == stages_from(BatchStage.WATER_LADDERS)

    def test_nothing_runs_where_every_stage_is_current(self):
        assert plan({name: STATE_CURRENT for name in ORDER}, None) == ()


def test_a_step_that_left_samples_unread_stopped_partway():
    assert stopped_partway({"status": "partial", "data": {"samples_failed": 2}})
    assert not stopped_partway({"status": "partial", "data": {"samples_failed": 0}})
    # A pass with nothing to walk is partial in its notification's words, but
    # it read everything it had to.
    assert not stopped_partway({"status": "partial", "data": {"modes_walked": 0}})
    assert not stopped_partway(None)


def test_the_upstream_reason_names_the_stage():
    assert upstream_reason(BatchStage.SEARCH) == "upstream:search"


class TestTheVersion:
    def test_a_record_of_the_stages_run_takes_its_version(self):
        record = {"formula": "C2H4O2", "batch_peak_run_id": "run-2"}
        assert with_version(record, {"run-1", "run-2"}, 3) == {**record, "version": 3}

    def test_a_record_another_run_wrote_is_left_alone(self):
        assert with_version({"batch_peak_run_id": "run-0"}, {"run-1"}, 3) is None
        assert with_version(None, {"run-1"}, 3) is None

    def test_a_record_already_stamped_is_not_written_again(self):
        record = {"batch_peak_run_id": "run-1", "version": 3}
        assert with_version(record, {"run-1"}, 3) is None


def test_the_ledger_serves_every_stage_in_order():
    rows = {
        "search": SimpleNamespace(
            state=STATE_STALE,
            version=2,
            stale_reason="fold",
            utc_ran=None,
            utc_stale=None,
        )
    }
    entries = standing_entries(rows)
    assert [e["stage"] for e in entries] == ORDER
    by = {e["stage"]: e for e in entries}
    assert by["search"]["state"] == STATE_STALE
    assert by["search"]["version"] == 2
    assert by["search"]["stale_reason"] == "fold"
    assert by["search"]["label"] == "Search untargeted"
    assert by["water_ladders"]["state"] == STATE_NOT_RUN
    assert by["water_ladders"]["phase"] == batch_stages.PHASE_SOURCE


class TestTheOutcome:
    def test_nothing_to_run_is_skipped(self):
        outcome = stages_outcome("b", [], None)
        assert outcome["status"] == "skipped"

    def test_a_stage_left_stale_makes_the_pass_partial(self):
        ran = [
            {
                "stage": "series",
                "label": "Detect series",
                "version": 1,
                "state": STATE_STALE,
                "outcomes": [{"status": "partial", "message": "One sample skipped."}],
            },
            {
                "stage": "blends",
                "label": "Probable blends",
                "version": 4,
                "state": STATE_CURRENT,
                "outcomes": [{"status": "success", "message": "Read again."}],
            },
        ]
        outcome = stages_outcome("b", ran, BatchStage.SERIES)
        assert outcome["status"] == "partial"
        assert outcome["message"].startswith("Ran 2 batch stages from Detect series.")
        assert "Detect series: One sample skipped." in outcome["message"]
        assert "Still stale: Detect series" in outcome["message"]
        assert [s["stage"] for s in outcome["data"]["stages"]] == ["series", "blends"]
