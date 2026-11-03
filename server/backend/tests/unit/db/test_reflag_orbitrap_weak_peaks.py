"""
Unit tests for the run of ``reflag_orbitrap_weak_peaks``: which files it
reads, what it writes and in which order, and what a dry run leaves alone.

Stores and props are stand-ins here; the flag a store gets is tested on
detected stores in
``tests/unit/libraries/signal/test_reflag_weak_peaks.py``.
"""

import asyncio

import numpy as np
import pytest

from mascope_backend.db.scripts import reflag_orbitrap_weak_peaks as script


def _candidate(n, instrument="Orbi A"):
    return {"sample_file_id": f"sf-{n}", "filename": f"f{n}", "instrument": instrument}


def _record(result="current", rule=None):
    return {
        script.RECORD_FIELD: {
            "rule": script.rule() if rule is None else rule,
            "result": result,
        }
    }


# --- which files are read -----------------------------------------------------


class TestIsCurrent:
    def test_a_file_never_read_is_not(self):
        assert not script.is_current({})

    def test_a_file_read_under_the_rule_is(self):
        assert script.is_current(_record())

    def test_a_file_read_under_other_parameters_is_not(self):
        """A change of the rule reaches every file again."""
        assert not script.is_current(_record(rule={**script.rule(), "factor": 4.0}))

    def test_an_unreadable_file_is_read_again_only_on_retry(self):
        props = _record(script.UNREADABLE)

        assert script.is_current(props)
        assert not script.is_current(props, retry=True)
        assert script.is_current(_record(script.CHANGED), retry=True)


def _survey(props_of, raw=lambda name: True, limit=None, retry=False, n=5):
    candidates = [_candidate(i) for i in range(n)]
    return asyncio.run(
        script.survey(
            candidates, lambda name: props_of(name), raw, limit=limit, retry=retry
        )
    )


class TestSurvey:
    def test_files_already_read_and_files_without_a_raw_file_are_left_out(self):
        props = {"f0": _record(), "f1": {}, "f2": None, "f3": {}, "f4": {}}

        pending, counts = _survey(props.get, raw=lambda name: name != "f4")

        assert [c["filename"] for c in pending] == ["f1", "f3"]
        assert counts["current"] == 1
        assert counts["no_props"] == 1
        assert counts["not_raw"] == 1
        assert counts["surveyed"] == 5

    def test_the_cap_leaves_the_rest_for_a_later_run(self):
        pending, counts = _survey(lambda name: {}, limit=2)

        assert [c["filename"] for c in pending] == ["f0", "f1"]
        assert counts["beyond_cap"] == 3

    def test_retry_reads_the_unreadable_again(self):
        pending, _ = _survey(lambda name: _record(script.UNREADABLE), retry=True)

        assert len(pending) == 5


@pytest.mark.parametrize("raw", ["", None])
def test_no_limit_is_none(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("REFLAG_LIMIT", raising=False)
    else:
        monkeypatch.setenv("REFLAG_LIMIT", raw)
    assert script._limit() is None


@pytest.mark.parametrize("raw", ["0", "-3", "ten"])
def test_a_limit_that_bounds_nothing_is_refused(monkeypatch, raw):
    monkeypatch.setenv("REFLAG_LIMIT", raw)
    with pytest.raises(script.BadLimit):
        script._limit()


def test_a_limit_is_read(monkeypatch):
    monkeypatch.setenv("REFLAG_LIMIT", "40")
    assert script._limit() == 40


# --- what a run writes --------------------------------------------------------


def _reflag(turned_weak=3, turned_kept=0, overlaps=None):
    return script.Reflag(
        is_weak=np.zeros(10, dtype=bool),
        turned_weak=turned_weak,
        turned_kept=turned_kept,
        overlaps=overlaps,
        inputs=(),
    )


class Recorder:
    """Stand-ins for every writer, in one log of what was done in order."""

    def __init__(self, stores, batches=None, write_raises=None):
        self.stores = stores
        self.batches = batches or {}
        self.write_raises = write_raises or {}
        self.log = []

    def read(self, filename):
        found = self.stores[filename]
        if isinstance(found, Exception):
            raise found
        return found

    def write(self, filename, reflag):
        if filename in self.write_raises:
            raise self.write_raises[filename]
        self.log.append(("write", filename))

    def record(self, filename, result, reflag=None):
        self.log.append(("record", filename, result))

    async def batches_of(self, sample_file_id):
        return self.batches.get(sample_file_id, [])

    async def queue(self, batch_ids):
        self.log.append(("rematch", tuple(batch_ids)))

    def run(self, pending, dry_run=False):
        if dry_run:
            return asyncio.run(
                script.reflag(pending, self.read, None, None, self.batches_of, None)
            )
        return asyncio.run(
            script.reflag(
                pending,
                self.read,
                self.write,
                self.record,
                self.batches_of,
                self.queue,
            )
        )


def test_a_changed_file_has_its_batches_queued_then_its_store_then_its_record():
    """In that order: a run that stops after the queue leaves a batch marked
    for a rematch on the same peaks, and the file is read again; one that
    stopped after the write and before the queue would leave the batch
    matched on the old list with nothing to show for it."""
    recorder = Recorder({"f0": _reflag()}, batches={"sf-0": ["b1", "b2"]})

    outcome = recorder.run([_candidate(0)])

    assert recorder.log == [
        ("rematch", ("b1", "b2")),
        ("write", "f0"),
        ("record", "f0", script.CHANGED),
    ]
    assert outcome.batches == {"b1", "b2"}
    assert outcome.counts["changed"] == 1
    assert outcome.counts["turned_weak"] == 3
    assert outcome.instruments["Orbi A"] == [1, 3, 0]


def test_a_file_already_flagged_by_the_rule_is_recorded_and_not_written():
    recorder = Recorder({"f0": _reflag(0, 0)}, batches={"sf-0": ["b1"]})

    outcome = recorder.run([_candidate(0)])

    assert recorder.log == [("record", "f0", script.CURRENT)]
    assert outcome.batches == set()
    assert outcome.counts["current"] == 1


def test_a_file_with_no_store_is_not_recorded():
    """It gets the rule when its peaks are detected; nothing is written."""
    recorder = Recorder({"f0": None})

    outcome = recorder.run([_candidate(0)])

    assert recorder.log == []
    assert outcome.counts["no_store"] == 1


def test_an_unreadable_store_is_recorded_so_the_next_run_moves_past_it():
    recorder = Recorder({"f0": ValueError("broken"), "f1": _reflag()})

    outcome = recorder.run([_candidate(0), _candidate(1)])

    assert ("record", "f0", script.UNREADABLE) in recorder.log
    assert ("write", "f1") in recorder.log
    assert outcome.counts["unreadable"] == 1


@pytest.mark.parametrize(
    ("error", "count"),
    [(script.Moved("f0"), "moved"), (TimeoutError("busy"), "unwritable")],
)
def test_a_store_not_written_is_not_recorded_and_is_read_again(error, count):
    recorder = Recorder({"f0": _reflag()}, write_raises={"f0": error})

    outcome = recorder.run([_candidate(0)])

    assert not [entry for entry in recorder.log if entry[0] == "record"]
    assert outcome.counts[count] == 1
    assert outcome.counts["changed"] == 0


def test_a_dry_run_writes_nothing_and_counts_what_it_would_do():
    recorder = Recorder(
        {"f0": _reflag(5, 1, overlaps=[]), "f1": _reflag(0, 0)},
        batches={"sf-0": ["b1"]},
    )

    outcome = recorder.run([_candidate(0), _candidate(1, "Orbi B")], dry_run=True)

    assert recorder.log == []
    assert outcome.counts["changed"] == 1
    assert outcome.counts["turned_weak"] == 5
    assert outcome.counts["turned_kept"] == 1
    assert outcome.counts["overlaps"] == 1
    assert outcome.batches == {"b1"}
    assert dict(outcome.instruments) == {"Orbi A": [1, 5, 1]}
