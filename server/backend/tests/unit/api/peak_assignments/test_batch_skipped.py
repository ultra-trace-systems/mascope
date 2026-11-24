"""Unit tests for what a batch stage records of the samples it could not read.

What a step names of the samples it skipped (``count_unread``), what a
stage's steps say they skipped (``skipped_in``), and which skipped samples a
current stage waits for and marks it stale when they read again
(``read_again``, ``stage_view``). The database side is in the integration
suite (``test_batch_stages_api.py``). See ``batch_skipped.py``.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments import batch_skipped
from mascope_backend.api.new.peak_assignments.batch_skipped import (
    read_again,
    skipped_ids,
    skipped_in,
    stage_view,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import (
    REASON_READ_AGAIN,
    STATE_CURRENT,
    STATE_RUNNING,
    STATE_STALE,
    count_unread,
)


def _row(state=STATE_CURRENT, skipped=None, **over):
    fields = {
        "stage": "search",
        "version": 3,
        "state": state,
        "stale_reason": None,
        "utc_started": datetime(2026, 11, 24, tzinfo=timezone.utc),
        "utc_ran": datetime(2026, 11, 24, 1, tzinfo=timezone.utc),
        "utc_stale": None,
        "batch_peak_run_id": "run-1",
        "skipped_samples": skipped,
    }
    return SimpleNamespace(**{**fields, **over})


def _entry(sample_item_id, peaks_read):
    return {"sample_item_id": sample_item_id, "peaks_read": peaks_read}


class TestCountUnread:
    def test_it_counts_every_failure_and_names_each_sample_once(self):
        counts = {"samples_failed": 0}
        count_unread(counts, "s2")
        count_unread(counts, "s2")
        count_unread(counts, "s1")
        assert counts["samples_failed"] == 3
        assert counts["samples_unread"] == ["s2", "s1"]

    def test_it_starts_a_count_a_step_did_not(self):
        counts = {}
        count_unread(counts, "s1")
        assert counts == {"samples_failed": 1, "samples_unread": ["s1"]}


class TestSkippedIn:
    def test_the_samples_every_step_named_sorted_once(self):
        outcomes = [
            {"data": {"samples_failed": 2, "samples_unread": ["s3", "s1"]}},
            {"data": {"samples_failed": 1, "samples_unread": ["s1"]}},
            {"data": {"anchors": 4}},
        ]
        assert skipped_in(outcomes) == (["s1", "s3"], False)

    def test_a_step_that_counted_without_naming_is_unnamed(self):
        assert skipped_in([{"data": {"samples_failed": 2}}]) == ([], True)

    def test_a_step_that_read_everything_names_nothing(self):
        outcomes = [
            {"status": "partial", "data": {"samples_failed": 0}},
            # Partial in its notification's words, with nothing to walk: it
            # read everything it had to.
            {"status": "partial", "data": {"modes_walked": 0}},
            None,
            {"status": "success"},
        ]
        assert skipped_in(outcomes) == ([], False)


def test_skipped_ids_reads_the_record_and_the_ones_not_read():
    record = [_entry("s1", True), _entry("s2", False), "junk", {"peaks_read": False}]
    assert skipped_ids(record) == ["s1", "s2"]
    assert skipped_ids(record, unread_only=True) == ["s2"]
    assert skipped_ids(None) == []


@pytest.mark.asyncio
class TestReadAgain:
    @pytest.fixture
    def reads(self, monkeypatch):
        """The peak lists that read now, and the samples asked for."""
        state = {"now": set(), "asked": []}

        async def peaks_read(ids):
            ids = sorted(set(ids))
            state["asked"].append(ids)
            return {i: i in state["now"] for i in ids}

        monkeypatch.setattr(batch_skipped, "peaks_read", peaks_read)
        return state

    async def test_a_sample_whose_file_is_back_marks_its_stage(self, reads):
        reads["now"] = {"s2"}
        rows = {
            "search": _row(skipped=[_entry("s2", False), _entry("s3", False)]),
            "series": _row(skipped=[_entry("s3", False)]),
        }
        assert await read_again(rows) == {"search": ["s2"]}
        assert reads["asked"] == [["s2", "s3"]]

    async def test_one_whose_peak_list_read_as_the_run_ended_is_never_read(self, reads):
        """A failure further in than the peak list: reading it again would
        find it readable on every press and run the stage every time."""
        reads["now"] = {"s1"}
        rows = {"search": _row(skipped=[_entry("s1", True)])}
        assert await read_again(rows) == {}
        assert reads["asked"] == []

    async def test_only_a_current_stage_is_read(self, reads):
        reads["now"] = {"s1"}
        rows = {
            "search": _row(state=STATE_STALE, skipped=[_entry("s1", False)]),
            "series": _row(state=STATE_RUNNING, skipped=[_entry("s1", False)]),
            "blends": _row(),
        }
        assert await read_again(rows) == {}
        assert reads["asked"] == []

    async def test_the_same_samples_unread_leave_it_current(self, reads):
        rows = {"search": _row(skipped=[_entry("s2", False)])}
        assert await read_again(rows) == {}


class TestStageView:
    def test_it_names_the_skipped_samples_and_keeps_the_rest(self):
        row = _row(skipped=[_entry("s2", False)])
        view = stage_view(row, {"s2": "Blank 2"})
        assert view.state == STATE_CURRENT
        assert view.stale_reason is None
        assert view.version == 3
        assert view.utc_ran == row.utc_ran
        assert view.skipped_samples == [
            {"sample_item_id": "s2", "peaks_read": False, "sample_item_name": "Blank 2"}
        ]
        # The row itself is left as it was.
        assert row.skipped_samples == [_entry("s2", False)]

    def test_a_sample_read_again_makes_it_stale(self):
        view = stage_view(_row(skipped=[_entry("s2", False)]), {}, ["s2"])
        assert view.state == STATE_STALE
        assert view.stale_reason == REASON_READ_AGAIN
        assert view.utc_stale is not None
        assert view.skipped_samples[0]["sample_item_name"] is None
