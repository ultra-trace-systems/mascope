"""
Unit tests for the rebuild of peak stores their files no longer read back.

The survey and the run are tested here with stand-ins for the filestore and
the database. The check and the rebuild themselves, on real stores of a
scripted acquisition, are in
``tests/unit/libraries/signal/test_stale_store_rebuild.py``.
"""

import asyncio

import pytest
from test_utils import captured_logs

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

    stale, counts, end = asyncio.run(
        script.survey(candidates, lambda filename: filename != "zarr", check)
    )

    assert stale == [{**candidates[0], "state": script.STALE, "stored": 8, "reads": 7}]
    assert "zarr" not in checked
    assert counts["A"] == {script.STALE: 1, script.CURRENT: 1, script.NO_PEAKS: 1}
    assert counts["B"] == {"not_raw": 1, "unreadable": 1, script.NO_STORE: 1}
    assert end == len(candidates)


def test_a_capped_survey_stops_once_it_has_found_enough(monkeypatch):
    """Between batches, so a capped run does not read every file."""
    monkeypatch.setattr(script, "_CHECK_BATCH", 2)
    candidates = _candidates(*[(f"f{i}", "A") for i in range(10)])
    checked = []

    def check(filename):
        checked.append(filename)
        return script.Checked(script.STALE, 2, 1)

    stale, _, end = asyncio.run(
        script.survey(candidates, lambda _: True, check, limit=3)
    )

    assert [row["filename"] for row in stale] == ["f0", "f1", "f2", "f3"]
    assert len(checked) == 4
    assert end == 4


def test_a_survey_goes_on_where_an_earlier_one_stopped(monkeypatch):
    monkeypatch.setattr(script, "_CHECK_BATCH", 2)
    candidates = _candidates(*[(f"f{i}", "A") for i in range(5)])
    checked = []

    def check(filename):
        checked.append(filename)
        return script.Checked(script.STALE, 2, 1)

    _, counts, _ = asyncio.run(
        script.survey(candidates, lambda _: True, check, limit=1)
    )
    stale, counts, end = asyncio.run(
        script.survey(candidates, lambda _: True, check, start=2, counts=counts)
    )

    assert [row["filename"] for row in stale] == ["f2", "f3", "f4"]
    assert checked == [f"f{i}" for i in range(5)]
    assert counts["A"][script.STALE] == 5
    assert end == 5


def test_a_survey_that_goes_on_keeps_the_run_s_progress_and_traceback_budget(
    monkeypatch,
):
    """The progress line counts from the run's first file, and the run, not
    each call, logs at most ``_TRACEBACK_LIMIT`` tracebacks."""
    monkeypatch.setattr(script, "_CHECK_BATCH", 2)
    monkeypatch.setattr(script, "_PROGRESS_EVERY", 1)
    monkeypatch.setattr(script, "_TRACEBACK_LIMIT", 1)
    candidates = _candidates(*[(f"f{i}", "A") for i in range(4)])

    def check(filename):
        raise OSError("cannot open")

    with captured_logs("INFO") as records:
        _, counts, start = asyncio.run(
            script.survey(candidates, lambda _: True, check, limit=0)
        )
        asyncio.run(
            script.survey(candidates, lambda _: True, check, start=start, counts=counts)
        )

    progress = [r["message"] for r in records if "checked" in r["message"]]
    assert progress[-1] == "  ... 4 of 4 checked"
    assert sum(r["exception"] is not None for r in records) == 1


def test_a_store_without_scan_counts_is_counted_and_rebuilt_only_when_asked():
    """It is not stale: every read takes it as it is. The run says how many
    there are, and rebuilds them on ``FILL_SCAN_COUNTS=1`` beside the stale."""
    candidates = _candidates(("stale", "A"), ("uncounted", "A"), ("current", "A"))
    found = {
        "stale": script.Checked(script.STALE, 8, 7),
        "uncounted": script.Checked(script.UNCOUNTED),
        "current": script.Checked(script.CURRENT),
    }

    def survey(**kwargs):
        return asyncio.run(
            script.survey(candidates, lambda _: True, found.__getitem__, **kwargs)
        )

    stale, counts, _ = survey()
    assert [row["filename"] for row in stale] == ["stale"]
    assert counts["A"][script.UNCOUNTED] == 1

    both, _, _ = survey(uncounted=True)
    assert [(row["filename"], row["state"]) for row in both] == [
        ("stale", script.STALE),
        ("uncounted", script.UNCOUNTED),
    ]


@pytest.mark.parametrize("value, asked", [("1", True), ("0", False), (None, False)])
def test_the_stores_without_scan_counts_are_asked_for_by_name(
    monkeypatch, value, asked
):
    if value is None:
        monkeypatch.delenv("FILL_SCAN_COUNTS", raising=False)
    else:
        monkeypatch.setenv("FILL_SCAN_COUNTS", value)

    assert script._fill_scan_counts() is asked


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

    def __init__(self, fail=(), stale_after=(), uncounted_after=()):
        self.calls = []
        self._fail = set(fail)
        self._stale_after = set(stale_after)
        self._uncounted_after = set(uncounted_after)

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
        if filename in self._uncounted_after:
            state = script.UNCOUNTED
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


def test_a_capped_rebuild_stops_once_it_has_rebuilt_enough():
    recorder = _Recorder()

    outcome = asyncio.run(script.rebuild_all(STALE, recorder.steps(), limit=1))

    assert ("rebuild", "second") not in recorder.calls
    assert outcome.counts["rebuilt"] == 1
    assert outcome.left_over


# --- a capped run -------------------------------------------------------------


def _capped_run(monkeypatch, files, limit, fail=(), stale_after=(), batch=2):
    """A run over ``files``, newest first, every one stale, ``fail`` failing
    and ``stale_after`` still stale once rebuilt."""
    monkeypatch.setattr(script, "_CHECK_BATCH", batch)
    candidates = _candidates(*[(name, "A") for name in files])
    recorder = _Recorder(fail=fail, stale_after=stale_after)
    checked = []

    def check(filename):
        checked.append(filename)
        return script.Checked(script.STALE, 8, 7)

    outcome, counts = asyncio.run(
        script.find_and_rebuild(
            candidates, lambda _: True, check, recorder.steps(), limit
        )
    )
    rebuilt = [name for step, name in recorder.calls if step == "rebuild"]
    return outcome, counts, rebuilt, checked


def test_a_capped_run_does_not_count_the_files_it_cannot_rebuild(monkeypatch):
    """Files that keep failing at the top of the list would otherwise fill
    every capped run, and none would reach the older ones."""
    files = [f"f{i}" for i in range(8)]

    outcome, counts, rebuilt, checked = _capped_run(
        monkeypatch, files, limit=2, fail={"f0", "f1"}, stale_after={"f2"}
    )

    assert rebuilt == ["f0", "f1", "f2", "f3", "f4"]
    assert outcome.failed == ["f0", "f1", "f2"]
    assert outcome.counts["rebuilt"] == 2
    assert outcome.left_over
    assert checked == files[:6]
    assert counts["A"][script.STALE] == 6


def test_a_capped_run_surveys_on_for_as_many_as_it_still_needs(monkeypatch):
    """Not for the whole cap again: every file checked is a read."""
    files = [f"f{i}" for i in range(8)]

    outcome, _, rebuilt, checked = _capped_run(
        monkeypatch, files, limit=3, fail={"f0", "f1"}, batch=1
    )

    assert rebuilt == files[:5]
    assert checked == files[:5]
    assert outcome.left_over


def test_a_capped_run_that_reaches_the_oldest_file_leaves_nothing_over(
    monkeypatch,
):
    """Only failures are left: another run would fail on them again, so
    it does not advise one."""
    files = [f"f{i}" for i in range(5)]

    outcome, _, rebuilt, checked = _capped_run(
        monkeypatch, files, limit=3, fail={"f0", "f1", "f2"}
    )

    assert rebuilt == files
    assert outcome.counts["rebuilt"] == 2
    assert not outcome.left_over
    assert checked == files


def test_a_capped_run_that_rebuilds_the_last_file_leaves_nothing_over(
    monkeypatch,
):
    files = [f"f{i}" for i in range(4)]

    outcome, _, rebuilt, _ = _capped_run(monkeypatch, files, limit=4)

    assert rebuilt == files
    assert not outcome.left_over


def test_a_capped_run_with_files_left_to_check_leaves_them_over(monkeypatch):
    files = [f"f{i}" for i in range(4)]

    outcome, _, rebuilt, checked = _capped_run(monkeypatch, files, limit=2)

    assert rebuilt == ["f0", "f1"]
    assert checked == ["f0", "f1"]
    assert outcome.left_over


def test_an_uncapped_run_rebuilds_every_stale_file(monkeypatch):
    files = [f"f{i}" for i in range(5)]

    outcome, _, rebuilt, _ = _capped_run(monkeypatch, files, limit=None)

    assert rebuilt == files
    assert outcome.found == 5
    assert not outcome.left_over


def _run(monkeypatch, files, limit, fail=(), current=(), uncounted=(), fill=False):
    """:func:`script.run` over ``files``, every one stale but those
    ``current`` or ``uncounted``, with stand-ins for the database and the
    filestore; the messages it logs. ``fill`` sets ``FILL_SCAN_COUNTS=1``."""
    monkeypatch.setattr(script, "_CHECK_BATCH", 2)
    monkeypatch.setenv("REBUILD_LIMIT", str(limit))
    monkeypatch.delenv("DRY_RUN", raising=False)
    if fill:
        monkeypatch.setenv("FILL_SCAN_COUNTS", "1")
    else:
        monkeypatch.delenv("FILL_SCAN_COUNTS", raising=False)
    recorder = _Recorder(fail=fail)

    async def nothing(*_):
        return None

    async def candidates():
        return _candidates(*[(name, "A") for name in files])

    monkeypatch.setattr(script, "configure_database_engine", nothing)
    monkeypatch.setattr(script, "_candidates", candidates)
    monkeypatch.setattr(script, "_is_raw", lambda _: True)
    monkeypatch.setattr(script, "_samples_of", recorder.samples_of)
    monkeypatch.setattr(script, "_queue_rematch", recorder.queue_rematch)
    monkeypatch.setattr(script, "_remove_matches", recorder.remove_matches)
    monkeypatch.setattr(script, "_refresh_tic", recorder.refresh_tic)
    monkeypatch.setattr(script, "_refresh_ms2", recorder.refresh_ms2)
    rebuilt = set(current)

    async def rebuild(filename):
        await recorder.rebuild(filename)
        rebuilt.add(filename)

    monkeypatch.setattr(script, "_rebuild", rebuild)

    def check(filename):
        if filename in rebuilt:
            return script.Checked(script.CURRENT)
        if filename in uncounted:
            return script.Checked(script.UNCOUNTED)
        return script.Checked(script.STALE, 8, 7)

    monkeypatch.setattr(script, "check", check)
    with captured_logs("INFO") as records:
        asyncio.run(script.run())
    return [record["message"] for record in records]


def test_a_capped_run_with_stores_left_says_to_run_again(monkeypatch):
    messages = _run(monkeypatch, [f"f{i}" for i in range(4)], limit=1, fail={"f0"})

    assert any(
        "Stale stores found: 2, without scan counts: 0; rebuilt 1" in m
        for m in messages
    )
    assert any("run this again until it reports nothing" in m for m in messages)
    assert any(m.endswith("The next run tries them again.") for m in messages)


def test_a_run_with_nothing_stale_says_so(monkeypatch):
    messages = _run(monkeypatch, ["f0", "f1"], limit=1, current={"f0", "f1"})

    assert "No peak store to rebuild." in messages
    assert not any("Stale stores found" in m for m in messages)


def test_a_run_rebuilds_the_stores_without_scan_counts_only_when_asked(
    monkeypatch,
):
    """Without ``FILL_SCAN_COUNTS=1`` it only says how many there are."""
    files = ["f0", "f1", "f2"]

    messages = _run(monkeypatch, files, limit=5, current={"f2"}, uncounted={"f1"})

    assert any(m.startswith("  1 store(s) predate the scan counts") for m in messages)
    assert any("Stale stores found: 1, without scan counts: 0;" in m for m in messages)

    messages = _run(
        monkeypatch, files, limit=5, current={"f2"}, uncounted={"f1"}, fill=True
    )

    assert not any("predate the scan counts" in m for m in messages)
    assert any("f1: keeps no scan counts;" in m for m in messages)
    assert any(
        "Stale stores found: 1, without scan counts: 1; rebuilt 2" in m
        for m in messages
    )


def test_a_capped_run_left_with_failures_alone_says_so(monkeypatch):
    """No advice to run again: the next run would fail on the same files."""
    messages = _run(monkeypatch, ["f0", "f1", "f2"], limit=2, fail={"f0", "f1"})

    assert any(
        "Stale stores found: 3, without scan counts: 0; rebuilt 1" in m
        for m in messages
    )
    assert not any("run this again" in m for m in messages)
    assert any("every other stale store was rebuilt" in m for m in messages)


def test_a_rebuild_that_leaves_the_store_without_counts_is_named():
    recorder = _Recorder(uncounted_after={"first"})
    uncounted = [{**STALE[0], "state": script.UNCOUNTED}]

    outcome = asyncio.run(script.rebuild_all(uncounted, recorder.steps()))

    assert outcome.failed == ["first"]
    assert outcome.counts["still_stale"] == 1
    assert outcome.counts["rebuilt"] == 0


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
