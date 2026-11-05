"""
Unit tests for the rebuild of peak stores their files no longer read back.

The survey and the run are tested here with stand-ins for the filestore and
the database. The check and the rebuild themselves, on real stores of a
scripted acquisition, are in
``tests/unit/libraries/signal/test_stale_store_rebuild.py``.
"""

import asyncio

import pytest

from mascope_backend.db.scripts import rebuild_stale_peak_stores as script


def _candidates(*rows):
    return [
        {"sample_file_id": f"id-{name}", "filename": name, "instrument": instrument}
        for name, instrument in rows
    ]


# --- the survey ---------------------------------------------------------------


def test_the_survey_finds_the_stale_files_and_counts_every_other():
    candidates = _candidates(
        ("stale", "A"),
        ("current", "A"),
        ("blank", "A"),
        ("zarr", "B"),
        ("broken", "B"),
        ("none", "B"),
    )
    found = {
        "stale": script.Checked(script.STALE, 8, 7),
        "current": script.Checked(script.CURRENT),
        "blank": script.Checked(script.NO_PEAKS),
        "none": script.Checked(script.NO_STORE),
    }
    checked = []

    def check(filename):
        checked.append(filename)
        if filename == "broken":
            raise OSError("cannot open")
        return found[filename]

    stale, counts = asyncio.run(
        script.survey(candidates, lambda filename: filename != "zarr", check)
    )

    assert stale == [{**candidates[0], "stored": 8, "reads": 7}]
    assert "zarr" not in checked
    assert counts["A"] == {script.STALE: 1, script.CURRENT: 1, script.NO_PEAKS: 1}
    assert counts["B"] == {"not_raw": 1, "unreadable": 1, script.NO_STORE: 1}


def test_a_capped_survey_stops_once_it_has_found_enough(monkeypatch):
    """Between batches, so a capped run does not read every file."""
    monkeypatch.setattr(script, "_CHECK_BATCH", 2)
    candidates = _candidates(*[(f"f{i}", "A") for i in range(10)])
    checked = []

    def check(filename):
        checked.append(filename)
        return script.Checked(script.STALE, 2, 1)

    stale, _ = asyncio.run(script.survey(candidates, lambda _: True, check, limit=3))

    assert [row["filename"] for row in stale] == ["f0", "f1", "f2"]
    assert len(checked) == 4


# --- the run ------------------------------------------------------------------


STALE = [
    {
        "sample_file_id": f"id-{name}",
        "filename": name,
        "instrument": "A",
        "stored": 8,
        "reads": 7,
    }
    for name in ("first", "second")
]


class _Recorder:
    """Every step a run takes, as stand-ins that record what they were
    asked."""

    def __init__(self, fail=(), stale_after=()):
        self.calls = []
        self._fail = set(fail)
        self._stale_after = set(stale_after)

    async def samples_of(self, sample_file_id):
        return [f"{sample_file_id}-item"], [f"{sample_file_id}-batch"]

    async def queue_rematch(self, batches):
        self.calls.append(("queue", tuple(batches)))

    async def remove_matches(self, samples):
        self.calls.append(("remove", tuple(samples)))

    async def refresh_tic(self, sample_file_id, filename):
        self.calls.append(("tic", filename))
        return 2

    async def refresh_ms2(self, sample_file_id, filename):
        self.calls.append(("ms2", filename))
        return filename == "second"

    async def rebuild(self, filename):
        self.calls.append(("rebuild", filename))
        if filename in self._fail:
            raise RuntimeError("detection failed")

    def check(self, filename):
        state = script.STALE if filename in self._stale_after else script.CURRENT
        return script.Checked(state)

    def steps(self):
        return script.Steps(
            samples_of=self.samples_of,
            queue_rematch=self.queue_rematch,
            remove_matches=self.remove_matches,
            refresh_tic=self.refresh_tic,
            refresh_ms2=self.refresh_ms2,
            rebuild=self.rebuild,
            check_one=self.check,
        )


def test_a_file_is_set_to_rematch_and_read_again_before_its_store_is_written():
    """The store last: a run that stops partway leaves the file stale, and
    the next one does every step again."""
    recorder = _Recorder()

    outcome = asyncio.run(script.rebuild_all(STALE[:1], recorder.steps()))

    assert recorder.calls == [
        ("queue", ("id-first-batch",)),
        ("remove", ("id-first-item",)),
        ("tic", "first"),
        ("ms2", "first"),
        ("rebuild", "first"),
    ]
    assert outcome.counts["rebuilt"] == 1
    assert outcome.counts["tic_items"] == 2
    assert outcome.batches == {"id-first-batch"}
    assert outcome.samples == {"id-first-item"}


def test_a_dry_run_writes_nothing():
    recorder = _Recorder()

    outcome = asyncio.run(
        script.rebuild_all(STALE, script.Steps(samples_of=recorder.samples_of))
    )

    assert recorder.calls == []
    assert outcome.counts["rebuilt"] == 2
    assert outcome.batches == {"id-first-batch", "id-second-batch"}


def test_a_failed_rebuild_is_named_and_the_run_goes_on():
    recorder = _Recorder(fail={"first"})

    outcome = asyncio.run(script.rebuild_all(STALE, recorder.steps()))

    assert outcome.failed == ["first"]
    assert outcome.counts["failed"] == 1
    assert outcome.counts["rebuilt"] == 1
    assert outcome.counts["ms2_stores"] == 1
    assert ("rebuild", "second") in recorder.calls


def test_a_rebuild_that_does_not_take_is_named():
    """The store is written either way; one that still disagrees with its
    file is a fault, not the routine state after an upgrade."""
    recorder = _Recorder(stale_after={"second"})

    outcome = asyncio.run(script.rebuild_all(STALE, recorder.steps()))

    assert outcome.failed == ["second"]
    assert outcome.counts["still_stale"] == 1
    assert outcome.counts["rebuilt"] == 1


# --- which files are read -----------------------------------------------------


@pytest.mark.parametrize(
    "file_type, raw",
    [("orbi_raw", True), ("orbi_zarr", False), ("tof_h5", False)],
)
def test_only_a_file_that_keeps_its_raw_orbitrap_file_is_read(
    monkeypatch, file_type, raw
):
    """An Orbitrap file converted to zarr reads the scans its conversion
    stored, and its store never goes stale this way."""
    monkeypatch.setattr(script.m_name, "get_sample_file_type", lambda _: file_type)

    assert script._is_raw("a-file") is raw


def test_a_file_whose_type_cannot_be_told_is_not_read(monkeypatch):
    def unknown(_filename):
        raise ValueError("no such sample")

    monkeypatch.setattr(script.m_name, "get_sample_file_type", unknown)

    assert script._is_raw("a-file") is False


# --- the bound ----------------------------------------------------------------


def test_no_limit_by_default(monkeypatch):
    monkeypatch.delenv("REBUILD_LIMIT", raising=False)
    assert script._limit() is None


def test_a_limit(monkeypatch):
    monkeypatch.setenv("REBUILD_LIMIT", "500")
    assert script._limit() == 500


@pytest.mark.parametrize("raw", ["many", "0", "-3"])
def test_a_limit_that_is_not_a_bound_is_refused(monkeypatch, raw):
    monkeypatch.setenv("REBUILD_LIMIT", raw)
    with pytest.raises(script.BadLimit):
        script._limit()
