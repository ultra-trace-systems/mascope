"""The assignment timing script's own logic (MAS-1179).

``tooling/assignment_perf/perf.py`` times peak assignment on a dev env and
``profile_report.py`` reads a py-spy profile of such a run. What runs
against a database is exercised by running them; here: which reads count
as repeated, the meter's nesting, how two ledgers' decisions are compared,
the stages' own times, the table, and the profile's buckets. No database.
"""

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3] / "tooling" / "assignment_perf"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"assignment_perf_{name}", ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


perf = _load("perf")
profile_report = _load("profile_report")


# --- repeated reads ------------------------------------------------------------


def test_a_read_is_keyed_by_its_statement_and_its_parameters():
    one = perf.statement_key("SELECT * FROM t WHERE a = $1", ("x",))
    assert one == perf.statement_key("SELECT * FROM t WHERE a = $1", ("x",))
    assert one != perf.statement_key("SELECT * FROM t WHERE a = $1", ("y",))
    assert one != perf.statement_key("SELECT * FROM u WHERE a = $1", ("x",))
    assert perf.statement_key("WITH q AS (SELECT 1) SELECT * FROM q", ()) is not None


@pytest.mark.parametrize(
    "statement", ["INSERT INTO t VALUES ($1)", "UPDATE t SET a = 1", "DELETE FROM t"]
)
def test_a_write_is_never_a_repeated_read(statement):
    assert perf.statement_key(statement, ()) is None


def _execute(meter, statement, params, took=0.01):
    """What the cursor events do for one statement, without a database."""
    meter.count(statement, params, took)


def test_the_meter_credits_a_nested_scope_and_its_parent():
    meter = perf.Meter()
    with meter.scope("step", "pass"):
        _execute(meter, "SELECT 1", ())
        with meter.scope("stage", "series"):
            _execute(meter, "SELECT 2", (1,))
            _execute(meter, "SELECT 2", (1,))
            _execute(meter, "SELECT 2", (2,))
            time.sleep(0.01)
        _execute(meter, "SELECT 2", (1,))
    stage, step = meter.records
    assert (stage["kind"], stage["name"], stage["parent"]) == (
        "stage",
        "series",
        "step:pass",
    )
    assert stage["queries"] == 3
    assert stage["reads"] == 3
    assert stage["repeat_reads"] == 1
    assert stage["repeat_read_s"] == pytest.approx(0.01)
    assert stage["wall_s"] >= 0.01
    # CPU is the span's own, not the process's since it started: never more
    # than the span's wall time on one thread.
    assert 0 <= stage["cpu_s"] <= stage["wall_s"] + 0.05
    assert 0 <= step["cpu_s"] <= step["wall_s"] + 0.05
    assert step["parent"] is None
    assert step["queries"] == 5
    # The step saw SELECT 2 with (1,) three times: two of them repeats.
    assert step["repeat_reads"] == 2
    assert step["db_s"] == pytest.approx(0.05)
    assert "top_statements" in step and "top_statements" not in stage
    (text, n, took), *_ = step["top_statements"]
    assert (text, n, took) == ("SELECT 2", 4, pytest.approx(0.04))


def test_a_scope_records_its_meta_and_closes_on_an_error():
    meter = perf.Meter()
    with pytest.raises(RuntimeError):
        with meter.scope("judging", "s1", stage="series"):
            raise RuntimeError("boom")
    assert meter.stack == []
    assert meter.records[0]["stage"] == "series"


# --- the ledger's decisions ----------------------------------------------------


def _entry(**over):
    base = dict(
        tier=1,
        role=0,
        candidate=1,
        consensus_formula="C2H4O2",
        consensus_ion="C2H4O2H+",
        consensus_tier="assigned",
        engine_tier="assigned",
        isotope_line=False,
        consensus_role="parent",
        records={"series": False},
    )
    base.update(over)
    return [base[f] for f in perf.DIGEST_FIELDS]


def test_digest_changes_counts_each_decision_and_each_member_once():
    before = {"s/1": _entry(), "s/2": _entry(), "s/3": _entry(), "gone/1": _entry()}
    after = {
        "s/1": _entry(consensus_formula="C3H6O"),
        "s/2": _entry(tier=2, records={"series": True}),
        "s/3": _entry(),
        "new/1": _entry(isotope_line=True),
    }
    changes = perf.digest_changes(before, after)
    assert changes["members_compared"] == 3
    assert changes["members_changed"] == 2
    assert changes["consensus formula"] == 1
    assert changes["tier"] == 1
    assert changes["series and records"] == 1
    assert changes["isotope line"] == 0
    only = perf.digest_changes(before, after, only={"s/3"})
    assert only["members_compared"] == 1 and only["members_changed"] == 0


@pytest.mark.parametrize("field", ["tier", "consensus_tier", "engine_tier"])
def test_each_tier_column_is_a_tier_decision(field):
    before = {"s/1": _entry()}
    after = {"s/1": _entry(**{field: "candidate"})}
    changes = perf.digest_changes(before, after)
    assert (changes["tier"], changes["members_changed"]) == (1, 1)


def test_a_record_is_compared_by_its_scalars_without_ids_versions_or_times():
    one = {
        "step": "CH2",
        "n": 4,
        "batch_peak_id": "a",
        "stage_version": 3,
        "utc_made": "x",
        "members": [{"sample_item_id": "a"}],
    }
    two = {
        "step": "CH2",
        "n": 4,
        "batch_peak_id": "b",
        "stage_version": 4,
        "utc_made": "y",
        "members": [{"sample_item_id": "b"}],
    }
    assert perf._record_shape(one) == perf._record_shape(two)
    assert perf._record_shape(one) == {"members": [{}], "n": 4, "step": "CH2"}
    assert perf._record_shape(None) is False
    assert perf._record_shape([1]) is True


# --- the stages' own times and the table ----------------------------------------


def test_stage_spans_reads_the_pass_record():
    summary = {
        "stages": [
            {
                "stage": "fold",
                "utc_started": "2026-11-23T10:00:00+00:00",
                "utc_ended": "2026-11-23T10:01:30+00:00",
            },
            {
                "stage": "series",
                "utc_started": "2026-11-23T10:01:30+00:00",
                "utc_ended": None,
            },
        ]
    }
    assert perf.stage_spans(summary) == {"fold": 90.0}
    assert perf.stage_spans(None) == {}


def _record(kind, name, parent=None, wall=1.0, **over):
    return {
        "kind": kind,
        "name": name,
        "parent": parent,
        "wall_s": wall,
        "cpu_s": wall,
        "db_s": 0.1,
        "queries": 3,
        "reads": 2,
        "repeat_reads": 1,
        "repeat_read_s": 0.05,
        "rss_peak_mb": 500.0,
        **over,
    }


def test_the_table_has_the_steps_the_stages_with_their_judging_and_the_pipeline():
    result = {
        "use": "foldin",
        "n": 20,
        "commit": "abc",
        "utc_started": "t",
        "load_at_start": [3.0, 2.0, 1.0],
        "cpus": 24,
        "records": [
            _record("pipeline", "cap", parent="judging:s1", wall=2.0),
            _record("judging", "s1", parent="judge_again:series", wall=3.0),
            _record("judging", "s2", parent="judge_again:series", wall=5.0),
            _record("judge_again", "series", parent="stage:series", wall=8.0),
            _record("stage", "series", parent="step:pass", wall=10.0),
            _record(
                "sample_fold",
                "s3",
                parent="step:arrival fold",
                via="fold_sample_peaks_without_run",
                wall=4.0,
            ),
            _record("step", "arrival fold", wall=4.0),
            _record("step", "pass", wall=10.0),
        ],
    }
    table = perf.render(result)
    assert "**foldin**, N = 20" in table
    assert "| arrival fold (landing unknown) | 4.0 | 4.0 | 1.00 |" in table
    assert "| series | 10.0 | 8.0 | 2 | 10.0 | 1.00 |" in table
    assert "| judging | cap | 2.0 | 0.1 | 1 |" in table
    assert "| judging | 2 | 5.0 | 5.0 | 8.0 |" in table
    assert "| fold without a run | 1 | 4.0 | 4.0 | 4.0 |" in table
    # A stage row has one cell per header cell.
    header = next(line for line in table.splitlines() if line.startswith("| stage |"))
    row = next(line for line in table.splitlines() if line.startswith("| series |"))
    assert header.count("|") == row.count("|")


def test_table_command_reads_a_saved_run(tmp_path, capsys):
    out = tmp_path / "run.json"
    out.write_text(
        json.dumps({"use": "whole", "n": 4, "records": [_record("step", "pass")]})
    )
    perf.main(["table", str(out)])
    assert "| pass (status unknown) | 1.0 |" in capsys.readouterr().out


def test_a_measuring_command_needs_an_env(monkeypatch):
    monkeypatch.delenv("MASCOPE_ENV", raising=False)
    with pytest.raises(SystemExit, match="MASCOPE_ENV"):
        perf.main(["whole", "b", "out.json"])


# --- the profile ------------------------------------------------------------------

LOOP = "thread (0x1): MainThread"
WORKER = "thread (0x2): asyncio_0"
SAMPLER = "thread (0x3): Thread-1 (sample)"
PANDAS_ROW = "_getitem_axis (pandas/core/indexing.py:1400)"
CAP = "cap (peak_assignments/pipeline.py:10)"
ELECT = "elect (peak_assignments/pipeline.py:20)"
ENVELOPE = "predicted_envelope (peak_assignments/tiering.py:30)"
SQL = "execute (asyncpg/connection.py:5)"
IDLE = "select (selectors.py:468)"


def _profile(lines):
    return profile_report.parse(f"{s} {n}\n" for s, n in lines)


def test_the_profile_counts_every_threads_work_by_kind_and_by_function():
    stacks = _profile(
        [
            (f"{LOOP};run (asyncio/base_events.py:1);{ELECT};{PANDAS_ROW}", 20),
            (f"{LOOP};run (asyncio/base_events.py:1);{ELECT};{SQL}", 10),
            (f"{LOOP};run (asyncio/base_events.py:1);{IDLE}", 70),
            (f"{WORKER};run (threading.py:1);{CAP};{ENVELOPE}", 40),
            (f"{WORKER};_worker (concurrent/futures/thread.py:90)", 60),
            (f"{SAMPLER};run (threading.py:1);busy (perf.py:1)", 100),
        ]
    )
    text = profile_report.report(stacks, rate=10.0)
    # The loop thread's samples are the run's length; the work is every
    # thread's busy samples, the sampler's own left out.
    assert text.startswith(
        "Run: 10.0 s. Busy, all threads: 7.0 s (0.70 cores on average), of it on the loop thread 3.0 s."
    )
    assert "| Python, ours and the rest | 4.0 | 57.1 |" in text
    assert "| pandas row access | 2.0 | 28.6 |" in text
    assert "| SQL | 1.0 | 14.3 |" in text
    assert "| `peak_assignments.pipeline.elect` | 3.0 | 42.9 |" in text
    assert "| `peak_assignments.tiering.predicted_envelope` | 4.0 | 57.1 |" in text
    assert "asyncio" not in text


def test_a_frame_is_named_by_its_module():
    frame = "f (/x/.venv/lib/python3.12/site-packages/pandas/core/frame.py:10)"
    assert profile_report.short(frame) == "pandas.core.frame.f"
    assert (
        profile_report.short("g (/w/server/backend/src/mascope_backend/a.py:3)")
        == "mascope_backend.a.g"
    )
    assert profile_report.short("<no frame>") == "<no frame>"
    assert profile_report.ours("cap (peak_assignments/tiering.py:3)")
    assert profile_report.ours("f (mascope_tools/composition/line_model.py:1)")
    assert not profile_report.ours("f (pandas/core/frame.py:1)")
    assert not profile_report.ours("run (asyncio/events.py:1)")


def test_an_arrival_shows_where_its_peaks_landed_and_what_it_changed():
    result = {
        "use": "foldin",
        "n": 4,
        "landing": {"peaks": 10, "on_decided": 6, "on_undecided": 1, "new": 3},
        "pass": {"status": "success"},
        "changes": {
            "members_compared": 100,
            "members_changed": 7,
            "consensus formula": 2,
            "tier": 5,
        },
        "records": [],
    }
    assert "| 1 | 10 | 6 | 1 | 3 | yes | 100 | 7 | 2 | 5 | - | - |" in perf.render(
        result
    )
    arrivals = {
        "use": "arrivals",
        "n": 4,
        "records": [],
        "arrivals": [{"i": 2, "landing": {"peaks": 1}}],
    }
    assert "| 2 | 1 | - | - | - | no |" in perf.render(arrivals)


def test_a_reading_that_moves_is_not_a_changed_decision():
    before = {
        "s/1": _entry(
            records={"series": {"unit": "O", "ppm": 0.1}, "co_variation": {"r": 0.5}}
        )
    }
    moved = {
        "s/1": _entry(
            records={"series": {"unit": "O", "ppm": 0.3}, "co_variation": {"r": 0.9}}
        )
    }
    other_unit = {
        "s/1": _entry(
            records={"series": {"unit": "CH2", "ppm": 0.1}, "co_variation": {"r": 0.5}}
        )
    }
    assert perf.digest_changes(before, moved)["members_changed"] == 0
    assert perf.digest_changes(before, other_unit)["series and records"] == 1


def test_recount_and_converge_read_saved_digests(tmp_path, capsys):
    start = {"digest": {"s/1": _entry()}}
    run = {
        "every": 2,
        "arrivals": [
            {"i": 1, "digest": {"s/1": _entry(tier=2), "s/2": _entry()}},
            {
                "i": 2,
                "pass": {},
                "digest": {"s/1": _entry(tier=2), "s/2": _entry(consensus_formula="X")},
            },
        ],
    }
    perf.recount(run, start)
    assert [a["changes"]["members_changed"] for a in run["arrivals"]] == [1, 1]
    # Each arrival against the ledger before it, not against the start.
    assert [a["changes"]["members_compared"] for a in run["arrivals"]] == [1, 2]
    # The every-time sequence has a pass where the other has none: the row
    # says whether the other sequence passed there.
    every_time = {
        "arrivals": [
            {"i": 1, "pass": {}, "digest": {"s/1": _entry(), "s/2": _entry()}},
            run["arrivals"][1],
        ]
    }
    rows = perf.converge(every_time, run)
    assert [(r["pass"], r["members_changed"]) for r in rows] == [(False, 1), (True, 0)]
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(every_time))
    b.write_text(json.dumps(run))
    perf.main(["converge", str(a), str(b)])
    assert "| 1 | no | 2 | 1 | 0 | 1 | 0 | 0 |" in capsys.readouterr().out


# --- the records nested in a record --------------------------------------------


def _checked(**over):
    """A batch peak's records with a neutral-pair check, a stamped line and a
    known-species claim, as the stages write them."""
    records = {
        "batch_checks": {
            "channel": "[M+H]+",
            "neutral_pair": {"verdict": "established", "r": 0.8, "n_present": 20},
        },
        "diagnostic_isotopologues": {
            "n_stamped": 1,
            "predicted": [
                {"label": "13C", "stamped": True, "verdict": "stamped", "share": 0.9}
            ],
        },
        "known_species": {
            "verdict": "present",
            "claimed": True,
            "present": 20,
            "files": 20,
            "files_recorded": [{"sample_item_id": "x", "tier": "candidate"}],
            "co_variation": {"verdict": "untestable", "reason": "few_spectra"},
        },
    }
    for path, value in over.items():
        *keys, last = path.split(".")
        node = records
        for key in keys:
            node = node[key] if not key.isdigit() else node[int(key)]
        node[last] = value
    return {name: perf.record_of(name, records.get(name)) for name in perf.RECORD_KEYS}


@pytest.mark.parametrize(
    "path,value",
    [
        ("batch_checks.neutral_pair.verdict", "not_established"),
        ("diagnostic_isotopologues.predicted.0.stamped", False),
        ("diagnostic_isotopologues.predicted.0.verdict", "untestable"),
        ("known_species.claimed", False),
        ("known_species.verdict", "absent"),
    ],
)
def test_a_nested_verdict_or_a_claim_is_a_changed_decision(path, value):
    before = {"s/1": _entry(records=_checked())}
    after = {"s/1": _entry(records=_checked(**{path: value}))}
    assert perf.digest_changes(before, after)["series and records"] == 1


@pytest.mark.parametrize(
    "path,value",
    [
        ("batch_checks.neutral_pair.r", 0.6),
        ("batch_checks.neutral_pair.n_present", 21),
        ("diagnostic_isotopologues.predicted.0.share", 0.8),
        ("known_species.present", 21),
        ("known_species.files", 21),
        ("known_species.files_recorded", [{"sample_item_id": "y", "tier": "assigned"}]),
        ("known_species.co_variation", {"verdict": "established", "reason": None}),
    ],
)
def test_a_count_or_a_reading_nested_in_a_record_is_not_a_decision(path, value):
    before = {"s/1": _entry(records=_checked())}
    after = {"s/1": _entry(records=_checked(**{path: value}))}
    assert perf.digest_changes(before, after)["members_changed"] == 0


def test_a_record_that_appears_is_a_changed_decision():
    without = _checked()
    without["known_species"] = perf.record_of("known_species", None)
    before = {"s/1": _entry(records=without)}
    after = {"s/1": _entry(records=_checked())}
    assert perf.digest_changes(before, after)["series and records"] == 1


# --- a run that measured nothing ----------------------------------------------


def test_an_arrival_that_landed_no_peak_or_an_unfinished_pass_is_refused():
    landed = {"peaks": 3, "on_decided": 3, "on_undecided": 0, "new": 0}
    good = {"sample": "s9", "landing": landed, "pass": {"status": "success"}}
    assert perf.refusal(good) is None
    assert "landed no peak" in perf.refusal({**good, "landing": {**landed, "peaks": 0}})
    assert "'partial'" in perf.refusal({**good, "pass": {"status": "partial"}})
    assert "'skipped'" in perf.refusal({**good, "pass": {"status": "skipped"}})
    # The whole batch has no arrival; an arrival without a pass is fine.
    assert perf.refusal({"pass": {"status": "success"}}) is None
    sequence = {"arrivals": [{"i": 1, **good}, {"i": 2, "landing": {"peaks": 0}}]}
    assert "landed no peak" in perf.refusal(sequence)


def test_a_refused_run_is_written_then_exits_non_zero(tmp_path, monkeypatch):
    meter = perf.Meter()
    with meter.scope("step", "arrival fold", sample="s9"):
        pass
    with meter.scope("step", "pass", status="partial"):
        pass
    monkeypatch.setattr(perf, "METER", meter)
    out = tmp_path / "run.json"
    result = {
        "use": "foldin",
        "n": 4,
        "sample": "s9",
        "landing": {"peaks": 0, "on_decided": 0, "on_undecided": 0, "new": 0},
        "pass": {"status": "partial", "message": "Still stale: series"},
    }
    with pytest.raises(SystemExit, match="Not a measurement: the arrival of s9"):
        perf._write(str(out), result)
    saved = json.loads(out.read_text())
    assert saved["refused"].startswith("the arrival of s9")
    table = perf.render(saved)
    assert "**Not a measurement:**" in table
    assert "| arrival fold (0 peaks landed) |" in table
    assert "| pass (partial) |" in table


def test_the_step_table_says_what_each_step_did():
    result = {
        "use": "arrivals",
        "n": 4,
        "arrivals": [{"i": 1, "sample": "s1", "landing": {"peaks": 12}}],
        "records": [
            _record("step", "arrival fold", sample="s1"),
            _record("step", "pass", status="success"),
            _record("step", "arrival fold", sample="s2"),
        ],
    }
    table = perf.render(result)
    assert "| arrival fold (12 peaks landed) | 1.0 |" in table
    assert "| pass (success) | 1.0 |" in table
    assert "| arrival fold (landing unknown) |" in table


def _fake_backend(monkeypatch, **service_attrs):
    """The backend modules ``instrument`` wraps, as stand-ins."""
    import types

    async def fn(*a, **k):
        return {}

    mods = {
        "pipeline": types.SimpleNamespace(STAGE_FUNCTIONS={}),
        "batch_rejudge": types.SimpleNamespace(rejudge_sample=fn),
        "batch_stages": types.SimpleNamespace(
            _run_stage=fn, judge_again=fn, rebuild_batch_ledger=fn
        ),
        "batch_fold": types.ModuleType("batch_fold"),
        "service": types.ModuleType("service"),
        "batch_untargeted": types.SimpleNamespace(_search_sample=fn),
    }
    mods["batch_fold"].fold_sample_into_batch_peaks = fn
    mods["batch_fold"].fold_sample_peaks_without_run = fn
    for name, value in service_attrs.items():
        setattr(mods["service"], name, value)
    package = types.ModuleType("mascope_backend.api.new.peak_assignments")
    for name, module in mods.items():
        setattr(package, name, module)
    monkeypatch.setitem(sys.modules, package.__name__, package)
    return mods


def test_instrument_wraps_every_fold_and_refuses_a_missing_one(monkeypatch):
    async def fold(*a, **k):
        return {}

    mods = _fake_backend(monkeypatch, fold_sample_peaks_without_run=fold)
    perf.instrument()
    for module, name in perf.FOLD_TARGETS:
        assert getattr(getattr(mods[module], name), "_perf_wrapped", False)
    _fake_backend(monkeypatch)
    with pytest.raises(
        SystemExit, match="service.fold_sample_peaks_without_run is gone"
    ):
        perf.instrument()


def test_instrument_times_each_samples_search_and_runs_without_it(monkeypatch):
    import asyncio

    seen = []

    async def search(sample_item_id, *a, **k):
        seen.append(sample_item_id)
        return ["rows"]

    async def fold(*a, **k):
        return {}

    mods = _fake_backend(monkeypatch, fold_sample_peaks_without_run=fold)
    mods["batch_untargeted"]._search_sample = search
    monkeypatch.setattr(perf, "METER", perf.Meter())
    perf.instrument()
    got = asyncio.run(mods["batch_untargeted"]._search_sample("s1", {"p"}, None))
    assert got == ["rows"] and seen == ["s1"]
    (record,) = perf.METER.records
    assert (record["kind"], record["name"]) == ("sample_search", "s1")
    # A build without the function is timed without the span, not refused.
    mods = _fake_backend(monkeypatch, fold_sample_peaks_without_run=fold)
    del mods["batch_untargeted"]._search_sample
    perf.instrument()
    assert not hasattr(mods["batch_untargeted"], "_search_sample")


# --- what would cross a process boundary ------------------------------------------


def test_pickled_gives_the_size_and_the_seconds_or_why_not():
    got = perf.pickled({"a": list(range(100))})
    assert got["bytes"] > 100 and got["dumps_s"] >= 0 and got["loads_s"] >= 0
    assert "error" in perf.pickled(lambda: None)


def test_the_kept_entry_is_the_samples_three_parts():
    import types

    kept = types.SimpleNamespace(
        samples={"s1": "kept", "s2": "other"},
        reads={"s1": "read"},
        envelopes={"s1": "memo"},
    )
    assert perf.kept_entry(kept, "s1") == {
        "samples": "kept",
        "reads": "read",
        "envelopes": "memo",
    }
    assert perf.kept_entry(kept, "s3") == {
        "samples": None,
        "reads": None,
        "envelopes": None,
    }
    assert perf.kept_entry(None, "s1") == {}


def test_the_crossing_table_reads_the_median_judging_and_names_a_refusal():
    def part(n):
        return {"bytes": n, "dumps_s": 0.01, "loads_s": 0.02}

    judgings = [
        {
            "in": {"inputs": part(n), "kept": part(10 * n)},
            "out": {"result": part(2 * n), "kept": {"error": "TypeError: no"}},
            "judging_s": float(n),
        }
        for n in (100, 300, 200)
    ]
    table = perf.crossing_table({"n": 20, "commit": "abc", "judgings": judgings})
    assert "| inputs, in | 200 | 300 | 0.030 | 0.030 |" in table
    assert "| kept entry, in | 2,000 | 3,000 |" in table
    assert "| result, out | 400 | 600 |" in table
    assert "| kept entry, out | TypeError: no |" in table
    assert "median judging 200.00 s" in table


# --- the digests kept on disk ---------------------------------------------------


def test_a_spilled_digest_reads_back_wherever_a_digest_is_read(tmp_path, capsys):
    digest = {"s/1": _entry(tier=2)}
    path = perf.spill(tmp_path / "run.json.digests" / "after-1.json", digest)
    assert isinstance(path, str) and perf.unspill(path) == json.loads(
        json.dumps(digest)
    )
    assert perf.unspill(digest) is digest
    # A sequence stopped midway holds each arrival's digest as its path.
    stopped = {"arrivals": [{"i": 1, "digest": path}]}
    assert perf.last_digest(stopped) == perf.unspill(path)
    perf.recount(stopped, {"digest": {"s/1": _entry()}})
    assert stopped["arrivals"][0]["changes"]["tier"] == 1
    rows = perf.converge({"arrivals": [{"i": 1, "digest": {"s/1": _entry()}}]}, stopped)
    assert rows[0]["tier"] == 1


def test_a_third_party_or_standard_library_frame_is_not_ours():
    assert not profile_report.ours("take (pyarrow/compute.py:1)")
    assert not profile_report.ours("open (pathlib.py:1)")
    assert not profile_report.ours("method (nanoid/method.py:1)")
    assert not profile_report.ours("busy (perf.py:1)")
    assert profile_report.ours(
        "resolve_lines (mascope_tools/composition/line_model.py:1)"
    )
    assert profile_report.ours(
        "f (/x/.venv/lib/python3.12/site-packages/mascope_match/compute.py:1)"
    )
    assert not profile_report.ours(
        "f (/x/.venv/lib/python3.12/site-packages/pyarrow/compute.py:1)"
    )


def test_our_paths_are_the_source_roots_tails_less_the_standard_librarys(tmp_path):
    backend = tmp_path / "server/backend/src/mascope_backend/api"
    library = tmp_path / "libraries/tools/src/mascope_tools"
    for path in (
        backend / "tiering.py",
        backend / "copy.py",
        library / "line_model.py",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    (tmp_path / "tooling").mkdir()
    (tmp_path / "tooling/perf.py").write_text("")
    tails = profile_report.our_paths(tmp_path)
    assert {"api/tiering.py", "tiering.py", "mascope_tools/line_model.py"} <= tails
    # A module of ours named like the standard library's claims only its
    # longer paths; a file outside the source roots is not ours.
    assert "mascope_backend/api/copy.py" in tails and "copy.py" not in tails
    assert "perf.py" not in tails and "tooling/perf.py" not in tails
