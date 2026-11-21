"""Unit tests for the pure parts of the batch's assignment status.

Which state wins where several hold; each reason in ``behind``, measured
against what a pass recorded; which stale marks are a reason and which are
not; and the pass in flight as its run records it. The read over the database
is ``integration/api/peak_assignments/test_batch_assignment_status_api.py``.
See ``batch_assignment_status.py``.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.batch_assignment_status import (
    SAMPLE_IDS_CAP,
    engine_reason,
    library_reason,
    running_record,
    sample_reasons,
    stale_reason,
    state_of,
)
from mascope_backend.api.new.peak_assignments.batch_stages import STAGES


ORDER = [spec.stage.value for spec in STAGES]
T0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def _row(state="stale", reason="fold", at=T0):
    return SimpleNamespace(state=state, stale_reason=reason, utc_stale=at)


# --- the state ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "state"),
    [
        ({"running": True, "stopped": "failed", "n_batch_peaks": 0}, "running"),
        ({"stopped": "failed", "n_batch_peaks": 0}, "failed"),
        ({"stopped": "cancelled", "n_batch_peaks": 3}, "cancelled"),
        ({"n_batch_peaks": 0, "has_pass": True}, "none"),
        ({"n_batch_peaks": 3, "has_pass": False}, "folded_only"),
        ({"n_batch_peaks": 3, "has_pass": True, "behind": [{}]}, "behind"),
        ({"n_batch_peaks": 3, "has_pass": True}, "current"),
    ],
)
def test_the_first_state_that_holds_wins(kwargs, state):
    args = {
        "running": False,
        "stopped": None,
        "n_batch_peaks": 3,
        "has_pass": True,
        "behind": [],
    }
    assert state_of(**{**args, **kwargs}) == state


# --- the samples -------------------------------------------------------------------


def test_the_samples_against_the_pass_give_new_removed_and_resampled():
    reasons = sample_reasons(
        covered=["a", "b", "c"], now=["b", "c", "d"], resampled=["c", "d", "a"]
    )
    assert reasons == [
        {"reason": "new_samples", "count": 1, "sample_item_ids": ["d"]},
        {"reason": "removed_samples", "count": 1, "sample_item_ids": ["a"]},
        # d is new, a is gone: only c was re-run of what the pass covered.
        {"reason": "resampled", "count": 1, "sample_item_ids": ["c"]},
    ]


def test_the_same_samples_give_no_reason():
    assert sample_reasons(["a", "b"], ["b", "a"], []) == []


def test_a_sample_reason_lists_at_most_the_cap_and_counts_all():
    now = [f"s{i:05d}" for i in range(SAMPLE_IDS_CAP + 50)]
    (reason,) = sample_reasons([], now, [])
    assert reason["count"] == SAMPLE_IDS_CAP + 50
    assert reason["sample_item_ids"] == now[:SAMPLE_IDS_CAP]


# --- the library and the engine ----------------------------------------------------


def test_the_library_names_what_was_attached_detached_and_edited():
    read = {
        "target_collections": {"kept": "1", "edited": "2", "gone": "3"},
        "reference_lists": {"7": {"version": "1"}, "8": {"version": "1"}},
    }
    now = {
        "target_collections": {"kept": "1", "edited": "9", "new": "4"},
        "reference_lists": {"7": {"version": "2"}, "9": {"version": "1"}},
    }
    reason = library_reason(read, now)
    assert reason["reason"] == "library_changed"
    assert reason["detail"] == {
        "target_collections": {
            "attached": ["new"],
            "detached": ["gone"],
            "edited": ["edited"],
        },
        "reference_lists": {"added": ["9"], "removed": ["8"], "changed": ["7"]},
    }
    assert reason["count"] == 6


def test_the_same_library_or_none_recorded_is_no_reason():
    library = {"target_collections": {"a": "1"}, "reference_lists": {}}
    assert library_reason(dict(library), library) is None
    assert library_reason(None, library) is None


def test_another_engine_version_is_a_reason_and_none_recorded_is_not():
    assert engine_reason("0.1.0", "0.2.0") == {
        "reason": "engine_changed",
        "count": 1,
        "detail": {"pass": "0.1.0", "now": "0.2.0"},
    }
    assert engine_reason("0.2.0", "0.2.0") is None
    assert engine_reason(None, "0.2.0") is None


# --- stale stages ------------------------------------------------------------------


def test_a_curations_and_an_imports_marks_are_no_reason():
    rows = {"search": _row(reason="curation"), "series": _row(reason="import")}
    assert stale_reason(rows, T0, samples_explain=False) is None


def test_a_folds_mark_is_a_reason_only_where_no_sample_explains_it():
    rows = {"search": _row(reason="fold")}
    assert stale_reason(rows, T0, samples_explain=True) is None
    assert stale_reason(rows, T0, samples_explain=False)["detail"] == [
        {"stage": "search", "stale_reason": "fold"}
    ]


def test_a_consensus_mark_after_the_pass_is_no_reason_and_one_inside_it_is():
    after = {"search": _row(reason="consensus", at=T0 + timedelta(seconds=1))}
    inside = {"search": _row(reason="consensus", at=T0 - timedelta(seconds=1))}
    assert stale_reason(after, T0, samples_explain=False) is None
    assert stale_reason(inside, T0, samples_explain=False)["count"] == 1


@pytest.mark.parametrize("mark", ["rebuild", "interrupted", "partial", "upstream:x"])
def test_any_other_mark_is_a_reason(mark):
    reason = stale_reason({"series": _row(reason=mark)}, T0, samples_explain=True)
    assert reason == {
        "reason": "stages_stale",
        "count": 1,
        "detail": [{"stage": "series", "stale_reason": mark}],
    }


def test_stale_stages_are_listed_in_the_stages_order_and_current_ones_not():
    rows = {
        "blends": _row(reason="rebuild"),
        "water_ladders": _row(reason="rebuild"),
        "search": _row(state="current", reason=None),
    }
    detail = stale_reason(rows, T0, samples_explain=False)["detail"]
    assert [e["stage"] for e in detail] == ["water_ladders", "blends"]


# --- the pass in flight ------------------------------------------------------------


def _run(**values):
    base = {
        "batch_peak_run_id": "run-1",
        "action": "assign",
        "created_by": 7,
        "batch_peak_run_utc_created": T0,
        "cancel_requested_utc": None,
        "summary": None,
    }
    return SimpleNamespace(**{**base, **values})


def test_a_pass_in_flight_is_served_from_the_progress_its_run_keeps():
    run = _run(
        summary={
            "progress": {
                "process_id": "p-1",
                "stage": "series",
                "label": "Detect series",
                "stage_index": 5,
                "n_stages": 10,
                "utc_stage_started": "2026-11-20T12:05:00+00:00",
            }
        }
    )
    record = running_record(run, None, {7: "ana"})
    assert record == {
        "batch_peak_run_id": "run-1",
        "action": "assign",
        "process_id": "p-1",
        "started_by": {"id": 7, "username": "ana"},
        "utc_started": T0.isoformat(),
        "stage": "series",
        "label": "Detect series",
        "stage_index": 5,
        "n_stages": 10,
        "utc_stage_started": "2026-11-20T12:05:00+00:00",
        "progress": 0.5,
        "cancel_requested_utc": None,
    }


def test_a_run_with_no_progress_takes_the_running_stage_row():
    stage_row = SimpleNamespace(stage="continuity", utc_started=T0)
    record = running_record(_run(created_by=None), stage_row, {})
    assert record["stage"] == "continuity"
    assert record["label"] == next(
        s.label for s in STAGES if s.stage.value == "continuity"
    )
    assert record["started_by"] is None
    assert record["progress"] is None
    # A stage row alone: no run to name.
    alone = running_record(None, stage_row, {})
    assert alone["batch_peak_run_id"] is None
    assert alone["utc_started"] == T0.isoformat()


def test_nothing_in_flight_is_none():
    assert running_record(None, None, {}) is None
