"""``bench inspect`` (``tooling/assignment_bench/inspection.py``).

The read-only look inside the bench over a fake instance whose database
answers the command's fixed queries: what it prints, that a name planted in
the rows it reads never reaches its output, and that it writes nothing,
takes no lock and opens its database sessions read-only. No host, no server.
"""

import importlib.util
import io
import json
import os
import re
import sys
import threading
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2] / "assignment_bench"


def _load():
    spec = importlib.util.spec_from_file_location("assignment_bench", ROOT / "bench.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()
inspection = bench.bench_inspection

#: The names the instance holds, which no line may print.
WORKSPACE = "Harbour Campaign"
INSTRUMENT = "Kestrelmatic"
SAMPLE = "Zephyrine_neg_0815.raw"
SAMPLE_ID = "S1abcdef42"
RUN_ID = "R7runid0001"

CARD_START = "2026-11-25T10:00:00+02:00"
CARD_END = "2026-11-25T10:40:00+02:00"
SINCE = "2026-11-25T11:00:00+02:00"


class FakeQuery:
    """The bench database as the command's fixed queries read it."""

    def __init__(self):
        self.sql = []
        self.card_runs = [
            {
                "sample_item_id": SAMPLE_ID,
                "decoy": None,
                "status": "completed",
                "error": None,
            },
            {
                "sample_item_id": "S2",
                "decoy": None,
                "status": "failed",
                "error": f"No peaks in sample '{SAMPLE}' after 3 tries at /srv/x/y.raw",
            },
            {
                "sample_item_id": "S3",
                "decoy": None,
                "status": "failed",
                "error": "No peaks in sample 'other' after 5 tries at /srv/z.raw",
            },
            {
                "sample_item_id": SAMPLE_ID,
                "decoy": "mz_shift_ppm",
                "status": "completed",
                "error": None,
            },
        ]
        self.going_runs = [
            {
                "sample_item_id": SAMPLE_ID,
                "decoy": None,
                "status": "running",
                "error": None,
            },
        ]
        self.engines = ["mascope", "peaky"]
        self.batch_status = "completed"

    def __call__(self, sql):
        self.sql.append(sql)
        if "reltuples" in sql:
            return [
                {
                    "table_name": "peak_assignment_run",
                    "rows": 20,
                    "table_bytes": 4096,
                    "index_bytes": 1024,
                },
                {
                    "table_name": "peak_assignment",
                    "rows": 1500,
                    "table_bytes": 3 * 1024 * 1024,
                    "index_bytes": 2048,
                },
                {
                    "table_name": "batch_peak_run",
                    "rows": -1,
                    "table_bytes": 8192,
                    "index_bytes": 8192,
                },
            ]
        if "from pg_stats" in sql:
            return [
                {
                    "table_name": "peak_assignment",
                    "column_name": "provenance",
                    "avg_width": 400,
                    "null_frac": 0.5,
                },
                {
                    "table_name": "peak_assignment",
                    "column_name": "alternatives",
                    "avg_width": 100,
                    "null_frac": 1.0,
                },
                {
                    "table_name": "peak_assignment",
                    "column_name": "sample_peak_id",
                    "avg_width": 21,
                    "null_frac": 0,
                },
                {
                    "table_name": "peak_assignment_run",
                    "column_name": "config",
                    "avg_width": 10,
                    "null_frac": 0,
                },
            ]
        if "from pg_class" in sql:
            return [
                {
                    "table_name": "peak_assignment_run",
                    "column_name": "peak_assignment_run_id",
                    "type": "character varying(16)",
                },
                {
                    "table_name": "peak_assignment_run",
                    "column_name": "config",
                    "type": "json",
                },
                {
                    "table_name": "peak_assignment",
                    "column_name": "sample_peak_id",
                    "type": "character varying(20)",
                },
                {
                    "table_name": "peak_assignment",
                    "column_name": "provenance",
                    "type": "json",
                },
                {
                    "table_name": "peak_assignment",
                    "column_name": "alternatives",
                    "type": "json",
                },
                {
                    "table_name": "batch_peak_run",
                    "column_name": "summary",
                    "type": "json",
                },
            ]
        if "group by engine, decoy, status" in sql:
            return [
                {"engine": "mascope", "decoy": None, "status": "completed", "n": 12},
                {"engine": "mascope", "decoy": None, "status": "failed", "n": 2},
                {
                    "engine": "mascope",
                    "decoy": "mz_shift_ppm",
                    "status": "completed",
                    "n": 4,
                },
                {
                    "engine": self.engines[1],
                    "decoy": None,
                    "status": "completed",
                    "n": 3,
                },
            ]
        if "from batch_peak_run group by" in sql:
            return [{"status": self.batch_status, "n": 1}]
        if "select peak_assignment_run_id as id" in sql:
            return [{"id": RUN_ID}]
        if "select batch_peak_run_id as id" in sql:
            return [{"id": "BR1"}]
        if "select sample_item_id, decoy, status, error" in sql:
            return self.card_runs if "<=" in sql else self.going_runs
        if "join peak_assignment a" in sql:
            return [
                {
                    "engine": "mascope",
                    "decoy": None,
                    "rows": 1000,
                    "provenance:bytes": 400_000,
                    "alternatives:bytes": 100_000,
                },
                {
                    "engine": "mascope",
                    "decoy": "mz_shift_ppm",
                    "rows": 500,
                    "provenance:bytes": 200_000,
                    "alternatives:bytes": 0,
                },
            ]
        raise AssertionError(f"a query inspect does not make: {sql}")


class FakeInstance:
    """The read-only instance: every method that would change it fails."""

    env = "bench"

    def __init__(self, env_dir):
        self._query = FakeQuery()
        self.env_dir = env_dir
        self.names = {"names": [WORKSPACE, INSTRUMENT], "ids": [SAMPLE_ID, "W1"]}

    def db_revisions(self):
        return ["r2"]

    def checkout_revisions(self):
        return {"sha": "c0ffee" * 7, "heads": ["r3"], "revisions": ["r1", "r2", "r3"]}

    def streams(self):
        return ["a"], []

    def private_names(self):
        return self.names

    def __getattr__(self, name):
        # start, stop, migrate, snapshot, restore, bind, checkout, drop, ...
        raise AssertionError(f"inspect called the instance's {name}")


def _state():
    state = bench.new_state("bench")
    state.update(
        slot=3,
        api_port=8093,
        sets={
            "A": {
                "instrument_class": "orbitrap",
                "mode": "Bromide mode of " + INSTRUMENT,
                "label": WORKSPACE,
                "samples": {
                    "h1": {
                        "name": SAMPLE,
                        "sample_file_id": "F1",
                        "problems": [],
                        "items": [{"sample_item_id": SAMPLE_ID}],
                    },
                    "h2": {
                        "name": "second.raw",
                        "sample_file_id": "F2",
                        "problems": [],
                        "items": [{"sample_item_id": "S2"}],
                    },
                },
            },
            "Db": {
                "instrument_class": "tof",
                "mode": "Nitrate",
                "samples": {
                    "h3": {
                        "name": "third.raw",
                        "sample_file_id": "F3",
                        "problems": ["no peaks"],
                        "items": [{"sample_item_id": "S3"}],
                    },
                },
            },
        },
        reference={
            "pin": "v0.9.0",
            "version": "0.9.0",
            "runs": {"A": {"h1": {"run_id": "P1"}}},
        },
        cards=[
            {
                "at": CARD_END,
                "ref": "topic",
                "sha": "b" * 40,
                "path": "topic/20261125-100000",
                "verdict": "pass",
                "sets": ["A", "Db"],
                "decoys": True,
                "reused": None,
            },
            {
                "at": "2026-11-25T10:50:00+02:00",
                "ref": "other-topic",
                "sha": "d" * 40,
                "path": "other-topic/20261125-105000",
                "verdict": "pass",
                "sets": ["A"],
                "decoys": False,
                "reused": "topic/20261125-100000",
            },
        ],
    )
    return state


@pytest.fixture
def rig(tmp_path):
    home = bench.Home(tmp_path / "home")
    home.root.mkdir(parents=True)
    home.state.write_text(json.dumps(_state()))
    folder = home.cards / "topic" / "20261125-100000"
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps({"started": CARD_START, "finished": CARD_END})
    )
    env_dir = tmp_path / "env" / "bench"
    (env_dir / "filestore" / "x").mkdir(parents=True)
    (env_dir / "filestore" / "x" / SAMPLE).write_bytes(b"0" * 2048)
    (env_dir / "filestore" / "other.bin").write_bytes(b"0" * 1024)
    (env_dir / "dev.mascope.toml").write_text(
        f"{inspection.CONFIG_MARK}\n[backend]\nworkers = 8\n"
    )
    return home, FakeInstance(env_dir)


def _inspect(home, instance):
    out = io.StringIO()
    code = bench.inspect_bench(home, instance, out=out)
    return code, out.getvalue()


def _snapshot(root: Path) -> dict:
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ------------------------------------------------------------------ shape


def test_inspect_prints_the_bench_its_runs_sizes_and_schema(rig):
    home, instance = rig
    code, text = _inspect(home, instance)

    assert code == 0, text
    lines = text.splitlines()
    assert lines[0] == "bench inspect: read-only; names scrubbed"
    expected = [
        "env       bench, slot 3, api port 8093",
        "set A  orbitrap, 2 of 2 files ready, 2 samples",
        "set Db  tof, 0 of 1 files ready, 1 samples",
        "reference pin v0.9.0, version 0.9.0",
        "settings  the bench's config layer: backend workers 8",
        "filestore 3.0 kB in 2 files",
        "converter 1 files waiting, 0 quarantined",
        "lock      free; 0 waiting",
        "schema    the database is at r2",
        "schema    this checkout at c0ffeec0f knows it: its head r3 is newer, a run migrates forward",
        "runs      mascope control: completed 12, failed 2",
        "runs      mascope decoy mz_shift_ppm: completed 4",
        "runs      peaky control: completed 3",
        "batch     batch runs: completed 1",
        "card      the newest, 2026-11-25T10:50:00+02:00, other-topic at ddddddddd, "
        "reused an earlier card: it ran nothing",
        f"card      {CARD_END}, topic at bbbbbbbbb, pass; ran from {CARD_START} to {CARD_END}",
        "card      set A control: completed 1, failed 1",
        "card      set A decoy mz_shift_ppm: completed 1",
        "card      set Db control: failed 1",
        "card      failed 2: No peaks in sample '<q>' after <n> tries at <path>",
        "storage   peak_assignment_run: about 20 rows, 4.0 kB stored, 1.0 kB of "
        "indexes; json about config 200 B (10 B a row)",
        "storage   peak_assignment: about 1,500 rows, 3.0 MB stored, 2.0 kB of "
        "indexes; json about provenance 293.0 kB (400 B a row)",
        "storage   batch_peak_run: no planner statistics yet, 8.0 kB stored, "
        "8.0 kB of indexes",
        "ledger    the card's mascope control: 1,000 rows; provenance 390.6 kB "
        "(400 B a row), alternatives 97.7 kB (100 B a row)",
        "ledger    the card's mascope decoy mz_shift_ppm: 500 rows; provenance 195.3 kB "
        "(400 B a row), alternatives 0 B (0 B a row)",
    ]
    for line in expected:
        assert line in lines, f"{line!r} not in:\n{text}"
    # The card's runs are read in its own window, the queries are fixed.
    window = [s for s in instance._query.sql if "select sample_item_id" in s]
    assert len(window) == 1
    assert f">= '{CARD_START}'" in window[0] and f"<= '{CARD_END}'" in window[0]
    assert "engine = 'mascope'" in window[0]
    ledger = [s for s in instance._query.sql if "join peak_assignment a" in s]
    assert len(ledger) == 1 and f">= '{CARD_START}'" in ledger[0]
    # No count or sum over the whole ledger: its sizes are the planner's.
    assert not [s for s in instance._query.sql if "from peak_assignment " in s]


def test_the_schema_line_says_when_the_checkout_does_not_know_the_database(rig):
    home, instance = rig
    instance.checkout_revisions = lambda: {
        "sha": "a" * 40,
        "heads": ["r1"],
        "revisions": ["r1"],
    }
    _, text = _inspect(home, instance)
    assert (
        "schema    this checkout at aaaaaaaaa does not know r2: a later build "
        "migrated the database"
    ) in text

    instance.checkout_revisions = lambda: {
        "sha": "a" * 40,
        "heads": ["r2"],
        "revisions": ["r2"],
    }
    _, text = _inspect(home, instance)
    assert "knows it: the database is at its head" in text

    def broken():
        raise bench.BenchError("cannot read /home/someone/checkout/alembic")

    instance.checkout_revisions = broken
    _, text = _inspect(home, instance)
    assert "schema    this checkout's migrations could not be read" in text
    assert "/home/someone" not in text


def test_settings_of_a_layer_the_bench_did_not_write_name_no_value(rig):
    home, instance = rig
    (instance.env_dir / "dev.mascope.toml").write_text(
        '[backend]\nworkers = 2\n[paths]\nraw = "/data/site/raw"\n'
    )
    _, text = _inspect(home, instance)
    assert (
        "settings  a config layer the bench did not write: backend workers 2; "
        "it sets 2 keys"
    ) in text
    assert "/data" not in text

    (instance.env_dir / "dev.mascope.toml").unlink()
    _, text = _inspect(home, instance)
    assert "settings  no config layer of the env's own: the dev defaults" in text


def test_the_run_that_holds_the_lock_shows_its_runs(rig):
    home, instance = rig
    (home.root / "bench.lock").write_text(
        json.dumps({"pid": os.getpid(), "what": "run topic", "since": SINCE}) + "\n"
    )
    _, text = _inspect(home, instance)

    assert (
        f"lock      held by run topic since {SINCE}, its process runs; 0 waiting"
        in text
    )
    assert "going     set A control: running 1" in text
    going = [
        s for s in instance._query.sql if "select sample_item_id" in s and "<=" not in s
    ]
    assert len(going) == 1 and f">= '{SINCE}'" in going[0]


def test_no_bench_says_so(tmp_path):
    home = bench.Home(tmp_path / "home")
    out = io.StringIO()
    assert bench.inspect_bench(home, FakeInstance(None), out=out) == 1
    assert "no bench here" in out.getvalue()
    assert not home.root.exists()


# ------------------------------------------------------------- the scrub


def test_a_name_planted_in_the_rows_never_reaches_the_output(rig):
    home, instance = rig
    q = instance._query
    # Each from a source of its own on the deny list: an error that names a
    # workspace bare (the instance's names), an engine named as an instrument
    # is (its names too), a decoy arm named by an in-app run's id (the run
    # ids inspect adds), a batch run's status by a batch run's id, and a
    # sample's file name as a card's verdict (the state's samples).
    q.card_runs.append(
        {
            "sample_item_id": SAMPLE_ID,
            "decoy": None,
            "status": "failed",
            "error": f"lost in {WORKSPACE} again",
        }
    )
    q.engines[1] = INSTRUMENT
    q.going_runs[0]["decoy"] = RUN_ID
    q.batch_status = "BR1"
    state = json.loads(home.state.read_text())
    state["cards"][0]["verdict"] = SAMPLE
    home.state.write_text(json.dumps(state))
    (home.root / "bench.lock").write_text(
        json.dumps({"pid": os.getpid(), "what": "run topic", "since": SINCE})
    )
    code, text = _inspect(home, instance)

    assert code == 1, text
    for planted in (
        INSTRUMENT,
        RUN_ID,
        WORKSPACE,
        "Harbour",
        SAMPLE,
        SAMPLE_ID,
        "Zephyrine",
        "/srv",
        "BR1",
    ):
        assert planted.lower() not in text.lower(), planted
    held = [line for line in text.splitlines() if line.startswith("(a line held back")]
    assert len(held) == 5, text
    assert all(re.match(r"^\(a line held back: it holds .+\)$", line) for line in held)
    # The lines around them still print.
    assert "runs      mascope control: completed 12, failed 2" in text


def test_a_name_in_the_state_is_scrubbed_too(rig):
    home, instance = rig
    state = json.loads(home.state.read_text())
    state["sets"]["A"]["instrument_class"] = INSTRUMENT
    state["cards"][0]["verdict"] = WORKSPACE
    home.state.write_text(json.dumps(state))
    code, text = _inspect(home, instance)
    assert code == 1
    assert INSTRUMENT.lower() not in text.lower()
    assert "harbour" not in text.lower()


def test_error_shapes_mark_what_differs_between_samples():
    shape = inspection.error_shape
    assert shape("Run was cancelled") == "Run was cancelled"
    assert shape(None) == "(no error recorded)"
    assert (
        shape("division by zero at peak 12.5 of 'abc' in /a/b.raw\ntraceback")
        == "division by zero at peak <n> of '<q>' in <path>"
    )
    assert shape("run a1b2c3d4e5 timed out") == "run <id> timed out"
    assert len(shape("x " * 200)) == inspection.SHAPE_LIMIT


# ------------------------------------------------- read-only, no lock


def test_inspect_writes_nothing_and_takes_no_lock(rig):
    home, instance = rig
    before = _snapshot(home.root.parent)
    # Another command holds the bench's lock, as a run does.
    held, release = threading.Event(), threading.Event()

    def hold():
        with home.lock(what="run topic"):
            held.set()
            release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(10)
        locked = _snapshot(home.root.parent)
        code, text = _inspect(home, instance)
        after = _snapshot(home.root.parent)
    finally:
        release.set()
        holder.join(10)

    assert code == 0, text
    assert "lock      held by run topic since" in text
    assert after == locked
    # Nothing but the lock's own files changed between the two looks.
    assert set(locked) - set(before) <= {"home/bench.lock", "home/queue.lock"}
    for sql in instance._query.sql:
        assert re.match(r"^select ", sql), sql
        assert not re.search(
            r"\b(insert|update|delete|drop|alter|create|truncate)\b", sql
        )


def test_a_read_only_instance_opens_its_sessions_read_only(tmp_path, monkeypatch):
    seen = []

    class Done:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def run(argv, **kwargs):
        seen.append(argv)
        return Done()

    monkeypatch.setattr(bench.subprocess, "run", run)
    for read_only in (True, False):
        dev = bench.DevInstance(bench.Home(tmp_path), read_only=read_only)
        monkeypatch.setattr(dev, "_db", lambda: ("pg", "user", "db"))
        assert dev._query("select 1") == []
    ro, rw = seen
    assert ro[:5] == [
        "docker",
        "exec",
        "-e",
        "PGOPTIONS=-c default_transaction_read_only=on",
        "pg",
    ]
    assert rw[:3] == ["docker", "exec", "pg"]


def test_the_command_runs_on_a_read_only_instance(tmp_path, monkeypatch, capsys):
    got = {}

    def inspect_bench(home, instance, out=None):
        got["read_only"] = instance.read_only
        return 0

    monkeypatch.setattr(bench, "inspect_bench", inspect_bench)
    assert bench.main(["--home", str(tmp_path / "home"), "inspect"]) == 0
    assert got == {"read_only": True}
    assert not (tmp_path / "home").exists()

    # Every other command keeps its read-write sessions.
    monkeypatch.setattr(
        bench.Bench,
        "status",
        lambda self, out=None: got.update(status=self.instance.read_only) or 0,
    )
    assert bench.main(["--home", str(tmp_path / "home"), "status"]) == 0
    assert got["status"] is False


def test_storage_reads_only_the_tables_it_lists():
    seen = []
    reader = inspection.Reader(lambda sql: seen.append(sql) or [])
    assert reader.storage() == {}
    assert reader.tables() == {}
    listed = "('" + "', '".join(inspection.TABLES) + "')"
    assert len(seen) == 3 and all(listed in sql for sql in seen)


def test_a_window_takes_only_a_time():
    reader = inspection.Reader(lambda sql: [])
    with pytest.raises(inspection.InspectError):
        reader.runs_between("'; drop table x; --", None)
    assert inspection.stamp("2026-11-25T10:00:00+02:00") == "2026-11-25T10:00:00+02:00"
    assert inspection.stamp("yesterday") is None


def test_the_one_pattern_refuses_what_the_card_s_scrub_refuses():
    cr = bench.bench_run._compare_module()
    tokens = cr.deny_tokens(
        [WORKSPACE, "Site-B: Q.1"],
        [SAMPLE_ID, RUN_ID, "a+b"],
        ["A"],
        strict=[INSTRUMENT, SAMPLE],
    )
    found = inspection.matcher(tokens)
    texts = [
        "nothing here",
        f"in {WORKSPACE.upper()} now",
        f"x{RUN_ID}",
        f"{RUN_ID}y",
        f"({RUN_ID})",
        "harbourside",
        "the harbour",
        "Site-B: Q.1 and more",
        "a+b",
        "aa+b",
        f"{SAMPLE}!",
        "set A only",
        "",
    ]
    for text in texts:
        try:
            cr.scrub({"t": text}, tokens)
            refused = False
        except cr.CardRefused:
            refused = True
        hit = found(text)
        assert (hit is not None) == refused, text
        if hit is not None:
            with pytest.raises(cr.CardRefused):
                cr.scrub({"t": text}, hit)
    assert inspection.matcher([])("anything") is None


def test_a_card_s_ref_is_the_card_s_own_word(rig):
    home, instance = rig
    # A workspace named partly as a card's ref: its words are tokens, but a
    # card may name its own ref, as run.json does.
    instance.names["names"].append("topic survey")
    code, text = _inspect(home, instance)
    assert code == 0, text
    assert f"card      {CARD_END}, topic at bbbbbbbbb, pass;" in text


def test_a_card_whose_database_was_put_back_says_its_runs_are_gone(rig):
    home, instance = rig
    # A ``--batch`` card, or a ref that migrates, ran on a restore point that
    # ``_cleanup`` put back: its runs and ledger rows are no longer there.
    folder = home.cards / "topic" / "20261125-100000"
    (folder / "run.json").write_text(
        json.dumps(
            {
                "started": CARD_START,
                "finished": CARD_END,
                "migration": {"restored": True},
            }
        )
    )
    code, text = _inspect(home, instance)

    assert code == 0, text
    assert (
        "card      its database was put back after the run: its runs are gone"
    ) in text
    assert "no in-app runs in its window" not in text
    assert "ledger    " not in text
    assert not [s for s in instance._query.sql if "select sample_item_id" in s]
    assert not [s for s in instance._query.sql if "join peak_assignment a" in s]

    # A restore that failed left the runs in the database: they are read.
    (folder / "run.json").write_text(
        json.dumps(
            {
                "started": CARD_START,
                "finished": CARD_END,
                "migration": {"restored": False},
            }
        )
    )
    _, text = _inspect(home, instance)
    assert "card      set A control: completed 1, failed 1" in text


def test_a_name_of_the_command_s_own_words_holds_no_line_back(rig):
    home, instance = rig
    # A workspace named after the app, and a calibration file: each word is
    # one the command prints of its own, a table's, a column's or an engine's.
    instance.names["names"] += [
        "Mascope demo",
        "Peaky trial",
        "Verification anchor run",
        "Residual survey",
    ]
    state = json.loads(home.state.read_text())
    state["sets"]["A"]["samples"]["h2"]["name"] = "calibration_mix_01.raw"
    home.state.write_text(json.dumps(state))
    q = instance._query
    answer = q.__class__.__call__

    def query(sql):
        rows = answer(q, sql)
        if "from pg_class" in sql:
            rows = rows + [
                {
                    "table_name": table,
                    "column_name": "residual_summary",
                    "type": "json",
                }
                for table in (
                    "assignment_calibration",
                    "assignment_verification",
                    "batch_peak_run_anchor",
                )
            ]
        if "reltuples" in sql:
            rows = rows + [
                {
                    "table_name": "assignment_calibration",
                    "rows": 10,
                    "table_bytes": 4096,
                    "index_bytes": 1024,
                }
            ]
        if "from pg_stats" in sql:
            rows = rows + [
                {
                    "table_name": "assignment_calibration",
                    "column_name": "residual_summary",
                    "avg_width": 50,
                    "null_frac": 0,
                }
            ]
        return rows

    instance._query = query
    code, text = _inspect(home, instance)

    assert code == 0, text
    assert "(a line held back" not in text
    assert "runs      mascope control: completed 12, failed 2" in text
    assert "runs      peaky control: completed 3" in text
    assert (
        "storage   assignment_calibration: about 10 rows, 4.0 kB stored, 1.0 kB "
        "of indexes; json about residual_summary 500 B (50 B a row)"
    ) in text
    for table in (
        "assignment_verification",
        "batch_peak_run_anchor",
    ):
        assert f"storage   {table}: no planner statistics yet" in text, table
    for planted in ("demo", "trial", "survey", "calibration_mix_01", "mix_01"):
        assert planted not in text.lower(), planted


def test_the_checkout_probe_writes_no_bytecode(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    seen = []

    class Done:
        returncode = 0
        stdout = '{"heads": ["r2"], "revisions": ["r1", "r2"]}\n'
        stderr = ""

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return Done()

    monkeypatch.setattr(bench.subprocess, "run", run)
    dev = bench.DevInstance(bench.Home(tmp_path), repo=tmp_path / "repo")
    got = dev.checkout_revisions()
    assert got["heads"] == ["r2"] and got["revisions"] == ["r1", "r2"]
    argv, kwargs = seen[0]
    assert argv[:4] == ["uv", "run", "--no-sync", "python"]
    assert kwargs["cwd"] == tmp_path / "repo" / "server" / "backend"
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert seen[1][0][-2:] == ["rev-parse", "HEAD"]


def test_the_config_mark_is_the_bench_s():
    assert inspection.CONFIG_MARK == bench.CONFIG_MARK
