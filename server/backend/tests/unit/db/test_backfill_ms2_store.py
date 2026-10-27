"""
Unit tests for the MS2 store backfill script.

The script decides which Orbitrap files need a store, counts them per
instrument, writes them one at a time with a rest between, remembers the
files it could not write, and reads its settings. Each is tested here with
stand-ins, hermetically; the run over a real store is in
``tests/integration/db/test_ms2_store_backfill_run.py``.
"""

import asyncio
from collections import Counter
from unittest.mock import patch

import pytest
from test_utils import captured_logs

from mascope_backend.db.scripts import backfill_ms2_store as script
from mascope_signal import ms2_store


MS1 = {"signature": {"ms_order": 1, "polarity": "+"}}
MS2 = {"signature": {"ms_order": 2, "polarity": "+"}}
CURRENT = {"layout": ms2_store.LAYOUT, "version": ms2_store.VERSION, "groups": []}


def _always(value):
    return lambda *_args: value


def _classify(props, *, raw=True, retry=False, store_on_disk=True):
    with (
        patch.object(ms2_store, "store_path", _always("/filestore/F/ms2.zarr")),
        patch.object(script.os.path, "isdir", _always(store_on_disk)),
    ):
        return script.classify("F", props, _always(raw), retry)


# --- which files need a store -------------------------------------------------


class TestClassify:
    def test_a_file_with_no_props(self):
        assert _classify(None) == script.NO_PROPS

    @pytest.mark.parametrize("census", [None, [], [{"key": "no signature"}]])
    def test_a_file_with_no_census_is_left_alone(self, census):
        """Whether it holds fragmentation is not known without opening it."""
        props = {} if census is None else {"scan_streams": census}

        assert _classify(props) == script.NO_CENSUS

    def test_a_file_whose_census_holds_no_fragmentation_needs_none(self):
        assert _classify({"scan_streams": [MS1]}) == script.NO_FRAGMENTATION

    def test_a_file_with_a_fragmentation_stream_and_no_store_is_read(self):
        assert _classify({"scan_streams": [MS1, MS2]}) == script.PENDING

    def test_one_whose_store_is_recorded_is_not(self):
        props = {"scan_streams": [MS1, MS2], "ms2_store": CURRENT}

        assert _classify(props) == script.HAS_STORE

    @pytest.mark.parametrize(
        "recorded",
        [
            {**CURRENT, "version": ms2_store.VERSION + 1},
            {**CURRENT, "layout": "another"},
        ],
    )
    def test_one_whose_store_this_build_does_not_read_is(self, recorded):
        """Ms2Store.open reads no other layout or version: the routes would
        read the raw file for it."""
        props = {"scan_streams": [MS2], "ms2_store": recorded}

        assert _classify(props) == script.PENDING

    def test_one_whose_recorded_store_is_not_on_disk_is(self):
        props = {"scan_streams": [MS2], "ms2_store": CURRENT}

        assert _classify(props, store_on_disk=False) == script.PENDING

    def test_one_a_run_could_not_write_is_passed_over(self):
        props = {"scan_streams": [MS2], script.ATTEMPT_FIELD: {"at": "then"}}

        assert _classify(props) == script.TRIED

    def test_unless_asked_to_retry(self):
        props = {"scan_streams": [MS2], script.ATTEMPT_FIELD: {"at": "then"}}

        assert _classify(props, retry=True) == script.PENDING

    def test_one_whose_raw_file_was_not_kept(self):
        assert _classify({"scan_streams": [MS2]}, raw=False) == script.NO_RAW


# --- the survey -----------------------------------------------------------------


def _candidates(*rows: tuple[str, str]) -> list[dict]:
    return [{"filename": f, "instrument": i} for f, i in rows]


def _props_of(table: dict):
    return lambda filename: table.get(filename)


class TestSurvey:
    def test_counts_every_file_per_instrument(self):
        props = {
            "a1": {"scan_streams": [MS2]},
            "a2": {"scan_streams": [MS1]},
            "a3": {},
            "b1": {"scan_streams": [MS2]},
            "b2": {"scan_streams": [MS2], script.ATTEMPT_FIELD: {"at": "then"}},
        }
        candidates = _candidates(
            ("a1", "A"), ("a2", "A"), ("a3", "A"), ("b1", "B"), ("b2", "B"), ("c", None)
        )

        pending, counts = asyncio.run(
            script.survey(candidates, _props_of(props), _always(True))
        )

        assert [p["filename"] for p in pending] == ["a1", "b1"]
        assert counts["A"] == Counter(
            {script.PENDING: 1, script.NO_FRAGMENTATION: 1, script.NO_CENSUS: 1}
        )
        assert counts["B"] == Counter({script.PENDING: 1, script.TRIED: 1})
        assert counts["(none)"] == Counter({script.NO_PROPS: 1})

    def test_stops_at_the_limit(self):
        names = [f"f{i}" for i in range(5)]
        props = {n: {"scan_streams": [MS2]} for n in names}

        pending, _ = asyncio.run(
            script.survey(
                _candidates(*((n, "A") for n in names)),
                _props_of(props),
                _always(True),
                limit=2,
            )
        )

        assert [p["filename"] for p in pending] == ["f0", "f1"]

    def test_says_which_files_a_run_tried_before(self):
        """So a write after MS2_STORE_RETRY=1 clears the note, and no other
        write adds an empty one to every file."""
        props = {
            "new": {"scan_streams": [MS2]},
            "old": {"scan_streams": [MS2], script.ATTEMPT_FIELD: {"at": "then"}},
        }

        pending, _ = asyncio.run(
            script.survey(
                _candidates(("new", "A"), ("old", "A")),
                _props_of(props),
                _always(True),
                retry=True,
            )
        )

        assert {p["filename"]: p["tried"] for p in pending} == {
            "new": False,
            "old": True,
        }


# --- the writes -------------------------------------------------------------------


def _pending(*names: str, tried: bool = False) -> list[dict]:
    return [{"filename": n, "instrument": "A", "tried": tried} for n in names]


class TestFill:
    def test_writes_one_file_at_a_time_and_rests_between(self):
        events = []

        def write(filename, tried):
            events.append(("write", filename, tried))
            return CURRENT

        async def sleep(seconds):
            events.append(("sleep", seconds))

        counts = asyncio.run(
            script.fill(_pending("a", "b", "c"), write, _always(None), 2.5, sleep)
        )

        assert counts == Counter({"written": 3})
        # No rest after the last: nothing is left to protect the server from.
        assert events == [
            ("write", "a", False),
            ("sleep", 2.5),
            ("write", "b", False),
            ("sleep", 2.5),
            ("write", "c", False),
        ]

    def test_a_pause_of_zero_does_not_sleep(self):
        async def sleep(_seconds):
            raise AssertionError("slept")

        asyncio.run(script.fill(_pending("a", "b"), _always(CURRENT), None, 0, sleep))

    def test_logs_each_files_groups(self):
        groups = [
            {"polarity": "+", "key": "195.0877@hcd10.00", "scan_count": 3},
            {"polarity": "-", "key": "163.1230@hcd30.00", "scan_count": 4},
        ]

        with captured_logs() as records:
            asyncio.run(
                script.fill(
                    _pending("a"), _always({**CURRENT, "groups": groups}), None, 0
                )
            )

        line = next(r["message"] for r in records if "a:" in r["message"])
        assert "2 groups (1 +, 1 -)" in line
        assert "195.0877@hcd10.00 (+, 3 scans)" in line
        assert "163.1230@hcd30.00 (-, 4 scans)" in line

    def test_a_file_that_cannot_be_written_is_recorded_and_the_run_goes_on(self):
        recorded = []

        def write(filename, _tried):
            if filename == "bad":
                raise OSError("disk full")
            return CURRENT

        counts = asyncio.run(
            script.fill(
                _pending("bad", "good"),
                write,
                lambda filename, error: recorded.append((filename, str(error))),
                0,
            )
        )

        assert counts == Counter({"written": 1, "failed": 1})
        assert recorded == [("bad", "disk full")]

    def test_a_record_that_fails_does_not_end_the_run(self):
        def record(*_args):
            raise OSError("gone")

        def write(filename, _tried):
            if filename == "bad":
                raise OSError("disk full")
            return CURRENT

        counts = asyncio.run(script.fill(_pending("bad", "good"), write, record, 0))

        assert counts == Counter({"written": 1, "failed": 1})


class TestWriteStore:
    def test_clears_the_note_of_a_file_tried_before(self):
        with (
            patch.object(ms2_store, "write_ms2_store", _always(CURRENT)),
            patch.object(script.m_io, "update_props") as update,
        ):
            assert script.write_store("F", tried=True) == CURRENT

        update.assert_called_once_with("F", {script.ATTEMPT_FIELD: None})

    def test_and_leaves_the_props_of_any_other_alone(self):
        with (
            patch.object(ms2_store, "write_ms2_store", _always(CURRENT)),
            patch.object(script.m_io, "update_props") as update,
        ):
            script.write_store("F", tried=False)

        update.assert_not_called()

    def test_an_attempt_records_when_and_why(self):
        with patch.object(script.m_io, "update_props") as update:
            script.write_attempt("F", OSError("disk full"))

        filename, fields = update.call_args.args
        assert filename == "F"
        assert fields[script.ATTEMPT_FIELD]["error"] == "OSError: disk full"
        assert fields[script.ATTEMPT_FIELD]["at"]


def test_describe_groups_names_a_few_and_counts_the_rest():
    groups = [
        {"polarity": "+", "key": f"{100 + i}.0000@hcd10.00", "scan_count": 1}
        for i in range(script._KEYS_LOGGED + 3)
    ]

    line = script.describe_groups({"groups": groups})

    assert line.startswith(f"{len(groups)} groups ({len(groups)} +)")
    assert line.endswith("; and 3 more")
    assert line.count("@hcd10.00") == script._KEYS_LOGGED


# --- the settings ---------------------------------------------------------------


class TestMode:
    def test_counts_by_default(self, monkeypatch):
        monkeypatch.delenv("MS2_STORE_MODE", raising=False)

        assert script._mode([]) == "count"

    def test_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("MS2_STORE_MODE", "run")

        assert script._mode([]) == "run"

    @pytest.mark.parametrize("flag, mode", [("--count", "count"), ("--run", "run")])
    def test_a_flag_wins(self, monkeypatch, flag, mode):
        monkeypatch.setenv("MS2_STORE_MODE", "run" if mode == "count" else "count")

        assert script._mode([flag]) == mode

    @pytest.mark.parametrize("argv", [["--count", "--run"], ["--write"], ["run"]])
    def test_refuses_anything_else_on_the_command_line(self, argv):
        with pytest.raises(script.BadSetting):
            script._mode(argv)

    def test_refuses_an_unknown_mode(self, monkeypatch):
        monkeypatch.setenv("MS2_STORE_MODE", "write")

        with pytest.raises(script.BadSetting):
            script._mode([])


class TestLimit:
    def test_unset_is_no_cap(self, monkeypatch):
        monkeypatch.delenv("MS2_STORE_LIMIT", raising=False)

        assert script._limit() is None

    def test_a_number(self, monkeypatch):
        monkeypatch.setenv("MS2_STORE_LIMIT", "500")

        assert script._limit() == 500

    @pytest.mark.parametrize("raw", ["0", "-1", "lots", "1.5"])
    def test_refuses_what_is_not_a_bound(self, monkeypatch, raw):
        """An operator who set it wanted a bounded run."""
        monkeypatch.setenv("MS2_STORE_LIMIT", raw)

        with pytest.raises(script.BadSetting):
            script._limit()


class TestPause:
    def test_unset_is_the_default(self, monkeypatch):
        monkeypatch.delenv("MS2_STORE_PAUSE", raising=False)

        assert script._pause() == script.DEFAULT_PAUSE

    @pytest.mark.parametrize("raw, pause", [("0", 0.0), ("2.5", 2.5)])
    def test_a_number_of_seconds(self, monkeypatch, raw, pause):
        monkeypatch.setenv("MS2_STORE_PAUSE", raw)

        assert script._pause() == pause

    @pytest.mark.parametrize("raw", ["-1", "soon", "nan"])
    def test_refuses_anything_else(self, monkeypatch, raw):
        monkeypatch.setenv("MS2_STORE_PAUSE", raw)

        with pytest.raises(script.BadSetting):
            script._pause()


def test_a_count_counts_every_file_whatever_the_cap(monkeypatch):
    """The count is the whole server's, so an operator sizes the run by it."""
    names = [f"f{i}" for i in range(3)]

    async def candidates():
        return _candidates(*((n, "A") for n in names))

    monkeypatch.setattr(script, "_candidates", candidates)
    monkeypatch.setattr(script, "_read_props", _always({"scan_streams": [MS2]}))
    monkeypatch.setattr(script, "_has_raw", _always(True))
    monkeypatch.setattr(script, "_SURVEY_BATCH", 1)

    counts, written = asyncio.run(script.backfill("count", limit=1))

    assert counts["A"] == Counter({script.PENDING: 3})
    assert written == Counter()


def test_a_run_reads_every_setting(monkeypatch):
    monkeypatch.setenv("MS2_STORE_LIMIT", "7")
    monkeypatch.setenv("MS2_STORE_PAUSE", "3")
    monkeypatch.setenv("MS2_STORE_RETRY", "1")
    calls = []

    async def backfill(mode, **settings):
        calls.append((mode, settings))

    async def configure():
        pass

    monkeypatch.setattr(script, "backfill", backfill)
    monkeypatch.setattr(script, "configure_database_engine", configure)

    asyncio.run(script.run(["--run"]))

    assert calls == [("run", {"limit": 7, "pause": 3.0, "retry": True})]


def test_settings_are_refused_before_the_database_is_touched(monkeypatch):
    monkeypatch.setenv("MS2_STORE_LIMIT", "lots")

    with (
        patch.object(script, "configure_database_engine") as configure,
        pytest.raises(script.BadSetting),
    ):
        asyncio.run(script.run(["--run"]))

    configure.assert_not_called()


def test_the_script_can_be_run():
    """`mascope db script run` finds a script by its callable main()."""
    assert callable(script.main)
