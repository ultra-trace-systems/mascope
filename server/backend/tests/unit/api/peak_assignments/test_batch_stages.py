"""Unit tests for the batch stages' order and their staleness rule's pure parts.

What a pass runs (:func:`batch_stages.plan`), what a stage's outcome says of
it, how a record takes its version, what the ledger serves of each stage,
when a pass folds first (:func:`batch_stages.fold_reasons`), which run's
settings a stage reads, and what a pass's summary says of each stage.
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
    fold_reasons,
    pass_outcome,
    plan,
    settings_recorded_by,
    stages_after,
    stages_from,
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


def _record(entries: dict[str, dict], planned: list[str]) -> batch_stages.PassRecord:
    record = batch_stages.PassRecord(None)
    record.plan(planned)
    for stage, fields in entries.items():
        record.entries[stage].update(fields)
    return record


class TestTheOutcome:
    def test_nothing_to_run_is_skipped(self):
        outcome = pass_outcome("b", None, None)
        assert outcome["status"] == "skipped"

    def test_a_stage_left_stale_makes_the_pass_partial(self):
        record = _record(
            {
                "series": {
                    "status": "done",
                    "version": 1,
                    "state": STATE_STALE,
                    "outcomes": [
                        {"status": "partial", "message": "One sample skipped."}
                    ],
                },
                "blends": {
                    "status": "done",
                    "version": 4,
                    "state": STATE_CURRENT,
                    "outcomes": [{"status": "success", "message": "Read again."}],
                },
            },
            ["series", "blends"],
        )
        outcome = pass_outcome("b", record, "run-1", BatchStage.SERIES)
        assert outcome["status"] == "partial"
        assert outcome["message"].startswith("Ran 2 batch stages from Detect series.")
        assert "Detect series: One sample skipped." in outcome["message"]
        assert "Still stale: Detect series" in outcome["message"]
        assert "was asked for" not in outcome["message"]
        assert [s["stage"] for s in outcome["data"]["stages"]] == ["series", "blends"]
        assert outcome["data"]["batch_peak_run_id"] == "run-1"

    def test_a_pass_started_earlier_than_asked_says_so(self):
        record = _record(
            {"search": {"status": "done", "state": STATE_CURRENT, "outcomes": []}},
            ["search"],
        )
        outcome = pass_outcome("b", record, "run-1", BatchStage.SERIES)
        assert "Detect series was asked for" in outcome["message"]

    def test_a_cancelled_pass_is_cancelled(self):
        record = _record(
            {
                "search": {"status": "done", "state": STATE_CURRENT, "outcomes": []},
                "isotope_lines": {"status": "cancelled"},
            },
            ["search", "isotope_lines"],
        )
        outcome = pass_outcome("b", record, "run-1")
        assert outcome["status"] == "cancelled"
        assert "Cancelled" in outcome["message"]


class TestTheRecord:
    def test_every_stage_is_listed_in_order_the_fold_first(self):
        summary = batch_stages.PassRecord("series").summary()
        assert [e["stage"] for e in summary["stages"]] == ["fold"] + ORDER
        assert {e["status"] for e in summary["stages"]} == {"not_run"}
        assert summary["from_stage"] == "series"
        assert summary["first_stage"] is None

    def test_a_cancel_leaves_what_was_planned_and_not_reached_cancelled(self):
        record = batch_stages.PassRecord(None)
        record.plan(["series", "co_variation", "continuity"])
        record.start("series")
        record.done("series", [{"status": "success", "message": "ok", "data": {}}])
        record.start("co_variation")
        record.stop("cancelled")
        by = {e["stage"]: e for e in record.summary()["stages"]}
        assert by["series"]["status"] == "done"
        assert by["series"]["outcomes"] == [
            {"status": "success", "message": "ok", "data": {}}
        ]
        assert by["co_variation"]["status"] == "cancelled"
        assert by["co_variation"]["utc_ended"] is not None
        assert by["continuity"]["status"] == "cancelled"
        # Before the first stage it ran: not run.
        assert by["search"]["status"] == "not_run"
        assert record.summary()["first_stage"] == "series"

    def test_a_failure_leaves_the_rest_not_run_and_says_why(self):
        record = batch_stages.PassRecord(None)
        record.plan(["series", "co_variation"])
        record.start("series")
        record.stop("failed", "RuntimeError: gone")
        by = {e["stage"]: e for e in record.summary()["stages"]}
        assert by["series"]["status"] == "failed"
        assert by["series"]["error"] == "RuntimeError: gone"
        assert by["co_variation"]["status"] == "not_run"

    def test_a_steps_outcome_keeps_its_settings(self):
        record = batch_stages.PassRecord(None)
        record.plan(["search"])
        record.start("search")
        record.done(
            "search",
            [
                {
                    "status": "success",
                    "message": "m",
                    "data": {"n": 1},
                    "config": {"a": 1},
                }
            ],
        )
        entry = record.entries["search"]
        assert entry["outcomes"][0]["config"] == {"a": 1}


LIBRARY = {"target_collections": {"tc-1": "abc"}, "reference_lists": {}}


class TestWhenAPassFoldsFirst:
    def _reasons(self, **overrides):
        args = {
            "n_batch_peaks": 5,
            "current_action": "assign",
            "last_pass": {"library": LIBRARY, "engine_version": "1.0.0"},
            "library": LIBRARY,
            "engine_version": "1.0.0",
        }
        args.update(overrides)
        return fold_reasons(**args)

    def test_a_ledger_a_pass_can_start_from_needs_no_fold(self):
        assert self._reasons() == []

    def test_no_batch_peaks(self):
        assert self._reasons(n_batch_peaks=0) == ["no_batch_peaks"]

    def test_an_import_current(self):
        assert self._reasons(current_action="import") == ["import"]

    def test_a_library_changed_since_the_last_pass(self):
        changed = {"target_collections": {"tc-1": "abd"}, "reference_lists": {}}
        assert self._reasons(library=changed) == ["library_changed"]
        named = {"target_collections": {}, "reference_lists": {}}
        assert self._reasons(library=named) == ["library_changed"]

    def test_another_engine_version(self):
        assert self._reasons(engine_version="1.1.0") == ["engine_changed"]

    def test_no_pass_yet_compares_no_library_and_no_engine(self):
        assert self._reasons(last_pass=None, engine_version="9.9.9") == []
        assert self._reasons(last_pass=None, n_batch_peaks=0) == ["no_batch_peaks"]


class TestTheSettingsARunRecorded:
    def test_a_pass_records_them_on_the_stage(self):
        summary = {
            "stages": [
                {"stage": "search", "settings": {"mz_precision_ppm": 2.0}},
                {"stage": "series", "settings": None},
            ]
        }
        assert settings_recorded_by(
            "assign",
            {"fold": "auto", "settings": None},
            summary,
            BatchStage.SEARCH,
            "search_untargeted",
        ) == (True, {"mz_precision_ppm": 2.0})
        # A pass that did not run the stage says nothing of it.
        assert settings_recorded_by(
            "search_untargeted",
            {"fold": "auto", "settings": None},
            summary,
            BatchStage.SERIES,
            "detect_series",
        ) == (False, None)

    def test_a_steps_own_run_recorded_them_as_its_config(self):
        assert settings_recorded_by(
            "search_untargeted",
            {"mz_precision_ppm": 3.0},
            {"anchors": 1},
            BatchStage.SEARCH,
            "search_untargeted",
        ) == (True, {"mz_precision_ppm": 3.0})
        assert settings_recorded_by(
            "detect_series",
            {"mz_precision_ppm": 3.0},
            None,
            BatchStage.SEARCH,
            "search_untargeted",
        ) == (False, None)

    def test_a_pass_with_no_summary_is_never_read_as_settings(self):
        # A pass a step's route started carries the step's action; its
        # config is the pass's, not settings.
        assert settings_recorded_by(
            "search_untargeted",
            {"from_stage": "search", "fold": "auto", "settings": None},
            None,
            BatchStage.SEARCH,
            "search_untargeted",
        ) == (False, None)
