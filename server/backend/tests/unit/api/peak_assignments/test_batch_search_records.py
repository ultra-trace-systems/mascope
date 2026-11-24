"""What the batch search remembers of an anchor it found nothing for
(``batch_search_records.py``, MAS-1189): the key, the record and the test a
later search makes of it, one way it is broken at a time. The search's own
use of them is covered in ``test_batch_untargeted_api.py``."""

import json
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments import batch_search_records
from mascope_backend.api.new.peak_assignments.batch_search_records import (
    found_nothing,
    search_key,
    searched_before_note,
    searched_empty_record,
    searched_in_vain,
)
from mascope_backend.api.new.peak_assignments.batch_untargeted import search_outcome
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig


_LIBRARY = {"target_collections": {"c1": "abc"}, "reference_lists": {}}


def _member(anchor="a1", sample="s1", peak="p1", row="r1", intensity=300.0):
    return SimpleNamespace(
        batch_peak_id=anchor,
        sample_item_id=sample,
        sample_peak_id=peak,
        peak_assignment_id=row,
        intensity=intensity,
    )


def _key(**over):
    args = {
        "config": PeakAssignmentConfig(),
        "library": _LIBRARY,
        "chemistry": "chem",
    }
    args.update(over)
    return search_key(**args)


def test_the_key_reads_alike_for_searches_that_read_alike():
    assert _key() == _key()
    # A field sent as its default reads as one left out.
    assert _key(config=PeakAssignmentConfig(max_untargeted_peaks=None)) == _key()


@pytest.mark.parametrize(
    "over",
    [
        {"config": PeakAssignmentConfig(candidate_threshold=0.5)},
        {"library": {**_LIBRARY, "target_collections": {"c1": "abd"}}},
        {"library": {**_LIBRARY, "reference_lists": {"r": {"version": "2"}}}},
        {"chemistry": "other chem"},
        {"engine_version": "99.0.0"},
        {"mascope_version": "99.0.0"},
    ],
    ids=[
        "settings",
        "a collection",
        "a reference list",
        "chemistry",
        "engine",
        "mascope",
    ],
)
def test_the_key_changes_with_what_the_search_reads(over):
    assert _key(**over) != _key()


def test_the_key_reads_the_engine_version_when_it_is_made(monkeypatch):
    before = _key()
    monkeypatch.setattr(batch_search_records, "PEAK_ASSIGNMENT_ENGINE_VERSION", "9.9")
    assert _key() != before


def test_the_key_reads_the_deployed_version_when_it_is_made(monkeypatch):
    """The engine version is not bumped with every change to the search, so
    an upgrade moves the key by the version GET /api/version reports."""
    monkeypatch.setattr(
        batch_search_records, "runtime", SimpleNamespace(version="1.0.0")
    )
    before = _key()
    monkeypatch.setattr(
        batch_search_records, "runtime", SimpleNamespace(version="1.1.0")
    )
    assert _key() != before
    # Unset, it reads as GET /api/version does: "unknown".
    monkeypatch.setattr(batch_search_records, "runtime", SimpleNamespace(version=None))
    assert _key() == _key(mascope_version="unknown")


def _record(member=None, persistent=False, key=None):
    return searched_empty_record(
        key or _key(),
        member or _member(),
        persistent=persistent,
        by_persistence=False,
        batch_peak_run_id="run-1",
    )


def test_an_anchor_read_as_it_was_is_searched_in_vain():
    assert searched_in_vain(_record(), _key(), _member(), False)


@pytest.mark.parametrize(
    "now",
    [
        _member(sample="s2"),
        _member(peak="p2"),
        _member(row="r2"),
        _member(row=None),
        _member(intensity=301.0),
    ],
    ids=["another sample", "another peak", "another run's row", "no row", "height"],
)
def test_another_representative_is_searched_again(now):
    assert not searched_in_vain(_record(), _key(), now, False)


def test_another_key_persistence_or_no_record_is_searched_again():
    assert not searched_in_vain(_record(), _key(chemistry="other"), _member(), False)
    assert not searched_in_vain(_record(), None, _member(), False)
    assert not searched_in_vain(None, _key(), _member(), False)
    assert not searched_in_vain(_record(), _key(), _member(), True)
    assert searched_in_vain(_record(persistent=True), _key(), _member(), True)


def test_a_record_survives_its_json_round_trip():
    record = json.loads(json.dumps(_record(_member(intensity=1234.5678))))
    assert searched_in_vain(record, _key(), _member(intensity=1234.5678), False)


def test_found_nothing_takes_the_unread_anchors_of_vouched_samples():
    by_sample = {
        "s1": [_member("a1", "s1"), _member("a2", "s1", peak="p2")],
        "s2": [_member("a3", "s2", peak="p3")],
    }
    records = found_nothing(
        "k",
        by_sample,
        read={"a2"},
        vouched=lambda sample: sample != "s2",
        persistent={"a1": object()},
        admitted_by_sample={"s1": {"p1"}},
        batch_peak_run_id="run-1",
    )
    # a2 was read by some row; s2's search stands for nothing.
    assert set(records) == {"a1"}
    assert records["a1"]["key"] == "k"
    assert records["a1"]["persistent"] is True
    assert records["a1"]["by_persistence"] is True
    assert records["a1"]["batch_peak_run_id"] == "run-1"
    assert records["a1"]["representative"]["sample_peak_id"] == "p1"


def test_the_note_counts_the_peaks_left_out():
    assert searched_before_note(0) == ""
    assert "1 unassigned batch peak the last search found nothing for was not" in (
        searched_before_note(1)
    )
    assert "3 unassigned batch peaks the last search found nothing for were " in (
        searched_before_note(3)
    )


def test_a_search_that_left_out_every_peak_says_so():
    counts = {
        "anchors_searched": 0,
        "anchors_searched_before": 4,
        "anchors_annotated": 0,
        "members_propagated": 0,
        "samples_searched": 0,
    }
    outcome = search_outcome(counts, "b1")
    assert outcome["status"] == "success"
    assert (
        "4 unassigned batch peaks the last search found nothing" in (outcome["message"])
    )
