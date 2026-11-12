"""The assignment bench's ``run`` and ``baseline`` (``tooling/assignment_bench/run.py``).

A ref built and migrated onto a fake instance, its in-app engine run on every
sample of a fake server (with the decoy arms), the card made by a fake
comparison, scrubbed again by the bench, diffed against develop's rolling
baseline, and the ref's backend and worktree gone afterwards. The lock that
makes two runs wait for each other, and the command line. No host, no server.
"""

import importlib.util
import json
import os
import sys
import threading
import time
import tomllib
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
brun = bench.bench_run
cr = brun._compare_module()

DEVELOP = "d" * 40
BRANCH = "b" * 40
PIN_VERSION = "1.0.0+assign0.4"


# ------------------------------------------------------------- the fakes


class FakeInstance:
    """The bench env as a run uses it: git, alembic, the backend, the database."""

    env = "bench"

    def __init__(self, home):
        self.home = home
        self.calls = []
        self.shas = {"origin/develop": DEVELOP, "develop": DEVELOP, "topic": BRANCH}
        self.db = ["r2"]
        self.revisions = {
            DEVELOP: (["r2"], ["r1", "r2"]),
            BRANCH: (["r3"], ["r1", "r2", "r3"]),
        }
        self.sha = None
        self.on_develop = {DEVELOP}
        self.workspaces = {"S1": "W1", "S2": "W1", "S3": "W2", "S4": "W1"}
        self.names = {
            "names": ["Acquisitions Instrument9", "2025-08-11 Bromide acquisition"],
            "ids": ["W1", "W2", "DS1", "B1"],
        }

    def check_env(self):
        self.calls.append("check_env")

    def bind(self):
        self.calls.append("bind")
        return 3, 8093

    def checkout(self, ref):
        self.calls.append(("checkout", ref))
        if ref not in self.shas:
            raise bench.BenchError(f"no commit {ref!r}")
        self.sha = self.shas[ref]
        self.home.worktree.mkdir(parents=True, exist_ok=True)
        return self.sha

    def develop_sha(self):
        return DEVELOP

    def is_on_develop(self, sha):
        return sha in self.on_develop

    def db_revisions(self):
        return list(self.db)

    def ref_revisions(self):
        return self.revisions[self.sha]

    def migrate(self):
        self.calls.append("migrate")
        self.db = list(self.revisions[self.sha][0])

    def start(self, peak_assignment=False, workers=1):
        self.calls.append(("start", peak_assignment))
        self.workers = workers
        return "http://localhost:8093"

    def stop(self):
        self.calls.append("stop")

    def remove_worktree(self):
        self.calls.append("remove_worktree")
        if self.home.worktree.exists():
            self.home.worktree.rmdir()

    def sample_workspaces(self):
        return dict(self.workspaces)

    def private_names(self):
        return self.names


class FakeServer:
    """The two routes a run uses, over runs kept per sample, newest first."""

    def __init__(self, samples):
        self.lock = threading.Lock()
        self.listing = {
            s: [
                {
                    "peak_assignment_run_id": f"P-{s}",
                    "engine": "peaky",
                    "engine_version": PIN_VERSION,
                    "status": "completed",
                    "decoy": None,
                    "error": None,
                }
            ]
            for s in samples
        }
        self.assigned = []
        self.n = 0
        #: Polls of a run before it ends.
        self.polls = 1
        #: Per sample, how many assign requests answer 409 first.
        self.busy = {}
        #: Per (sample, arm or None), the code and reason a request is refused with.
        self.refuse = {}
        #: Per (sample, arm or None), how the run ends instead of completing:
        #: a status, an error, or None for never.
        self.ends = {}
        #: Called after every listing, to change what the server holds.
        self.after = None

    def assign(self, sample, config):
        arm = brun.arm_name(config["decoy"]) if config else None
        with self.lock:
            self.assigned.append((sample, arm, config))
            if self.busy.get(sample):
                self.busy[sample] -= 1
                return brun.Launch(busy=True, code="assignment_in_flight")
            if (sample, arm) in self.refuse:
                code, reason = self.refuse[(sample, arm)]
                return brun.Launch(code=code, reason=reason)
            self.n += 1
            run_id = f"R{self.n}-{sample}"
            # The server's control: the newest completed in-app run, no decoy.
            control = (
                next(
                    r
                    for r in self.listing[sample]
                    if r["engine"] == "mascope"
                    and r["status"] == "completed"
                    and not r["decoy"]
                )
                if arm
                else None
            )
            end = self.ends.get((sample, arm), ("completed", None))
            self.listing[sample].insert(
                0,
                {
                    "peak_assignment_run_id": run_id,
                    "engine": "mascope",
                    "engine_version": "0.6.0",
                    "status": "running",
                    "decoy": arm,
                    "config": {
                        "decoy_arm": {
                            "control_run_id": control["peak_assignment_run_id"]
                        }
                    }
                    if arm
                    else {},
                    "error": None,
                    "_polls": 0,
                    "_end": end,
                },
            )
            return brun.Launch(run_id=run_id)

    def runs(self, sample, include_decoys=False):
        with self.lock:
            out = []
            for run in self.listing[sample]:
                if run["status"] == "running" and run.get("_end") is not None:
                    run["_polls"] += 1
                    if run["_polls"] >= self.polls:
                        run["status"], run["error"] = run["_end"]
                if run.get("decoy") and not include_decoys:
                    continue
                out.append({k: v for k, v in run.items() if not k.startswith("_")})
        if self.after:
            self.after(self, sample)
        return out

    def controls(self, sample):
        return [
            r
            for r in self.listing[sample]
            if r["engine"] == "mascope" and not r["decoy"]
        ]


class FakeCompare:
    """The comparison: a card of the sets it is given, written where it is told."""

    def __init__(self):
        self.calls = []
        self.verdict = "pass"
        #: (file, text) planted into the card, as a leak would put it there.
        self.plant = None
        #: Changes the card's JSON before it is written.
        self.edit = None
        #: Exit with this and write nothing, as a refused card does.
        self.refuse = None

    def __call__(self, *, sets, out, decoy, baseline, deny, env, log, stall_s):
        manifest = json.loads(sets.read_text())
        self.calls.append(
            {
                "manifest": manifest,
                "decoy": decoy,
                "baseline": baseline,
                "deny": deny.read_text().split("\n"),
                "env": env,
                "stall_s": stall_s,
            }
        )
        if self.refuse is not None:
            return self.refuse
        out.mkdir(parents=True)
        columns = {
            name: {
                "instrument": spec["instrument"],
                "readings": {"samples": {"value": len(spec["samples"]), "n": None}},
            }
            for name, spec in manifest["sets"].items()
        }
        columns["pooled"] = {
            "instrument": "mixed",
            "readings": {
                "samples": {
                    "value": sum(len(s["samples"]) for s in manifest["sets"].values()),
                    "n": None,
                }
            },
        }
        card = {
            "format": cr.CARD_FORMAT,
            "engines": {"a": "mascope", "b": "peaky"},
            "columns": columns,
            "gate": {
                "stage": "stage3",
                "verdict": self.verdict,
                "met": 4,
                "missed": [],
                "not_read": [],
                "unread_sets": [],
                "marks": {},
            },
            "thresholds": None,
            "baseline": None,
        }
        if baseline is not None:
            card["baseline"] = cr.diff_cards(json.loads(baseline.read_text()), card)
        if self.edit:
            self.edit(card)
        files = {
            "card.json": json.dumps(card),
            "card.md": "# Gate card: mascope against peaky\n",
        }
        for name in manifest["sets"]:
            files[f"card_{name}.md"] = f"# Card {name}\n"
            files[f"card_{name}.json"] = json.dumps(
                {**card, "columns": {name: columns[name]}}
            )
        if self.plant:
            where, text = self.plant
            files[where] += text
        for name, text in files.items():
            (out / name).write_text(text)
        with open(log, "a") as fh:
            fh.write("compare ran\n")
        return 0 if self.verdict == "pass" else 1


def _record(name, samples, problems=()):
    return {
        "name": name,
        "sample_file_id": f"F-{name}",
        "status": "done",
        "items": [{"sample_item_id": s, "mode": "m", "peaks": 100} for s in samples],
        "problems": list(problems),
    }


def _state():
    state = bench.new_state("bench")
    state["sets"] = {
        "Db": {
            "mode": "Bromide",
            "label": "demo bromide",
            "instrument_class": "orbitrap",
            "profile": "BR",
            "reference_window_ppm": None,
            "samples": {
                "k1": _record("a1.raw", ["S1"]),
                "k2": _record("a2.raw", ["S2"]),
            },
        },
        "Et": {
            "mode": "Bromide TOF",
            "instrument_class": "tof",
            "profile": "BR",
            "reference_window_ppm": {"trust": 8.0, "search": 25.0},
            "samples": {"k3": _record("c3.h5", ["S3"])},
        },
    }
    key = {"pin": "v1.0.0", "library": "2026.9.30"}
    state["reference"] = {
        "block": "blk",
        "pin": "v1.0.0",
        "version": PIN_VERSION,
        "library": "2026.9.30",
        "runs": {
            "Db": {
                s: {"key": key, "run_id": f"P-{s}", "status": "completed"}
                for s in ("S1", "S2")
            },
            "Et": {"S3": {"key": key, "run_id": "P-S3", "status": "completed"}},
        },
        "unrunnable": {},
    }
    return state


class Clock:
    """A clock that moves only when the run sleeps."""

    def __init__(self):
        self.t = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.t

    def sleep(self, s):
        with self.lock:
            self.t += s


class Rig:
    def __init__(self, tmp_path):
        self.home = bench.Home(tmp_path / "home")
        self.home.make()
        bench.save_state(self.home, _state())
        self.instance = FakeInstance(self.home)
        self.server = FakeServer(["S1", "S2", "S3", "S4"])
        self.compare = FakeCompare()
        self.clock = Clock()
        self.stamps = iter(f"20261112-1200{n:02d}" for n in range(60))
        self.apis = []

    def connect(self, base):
        self.home.write_private(
            self.home.env_file, f"MASCOPE_URL={base}\nMASCOPE_ACCESS_TOKEN=tok\n"
        )

    def api_of(self, url, token):
        self.apis.append((url, token))
        return self.server

    def runner(self, **kwargs):
        options = {
            "home": self.home,
            "instance": self.instance,
            "connect": self.connect,
            "load_state": lambda: bench.load_state(self.home, "bench"),
            "save_state": lambda state: bench.save_state(self.home, state),
            "compare": self.compare,
            "api_of": self.api_of,
            "jobs": 1,
            "budget_s": 600.0,
            "poll_s": 1.0,
            "sleep": self.clock.sleep,
            "clock": self.clock,
            "stamp": lambda: next(self.stamps),
        }
        options.update(kwargs)
        return brun.BenchRun(**options)

    def run(self, ref="topic", **kwargs):
        options = {
            k: kwargs.pop(k)
            for k in ("jobs", "budget_s", "workers", "stall_s")
            if k in kwargs
        }
        self.instance.calls.clear()
        return self.runner(**options).run(ref, **kwargs)

    def state(self):
        return bench.load_state(self.home, "bench")

    def card_dir(self, n=-1):
        return self.home.cards / self.state()["cards"][n]["path"]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ---------------------------------------------------------------- tests


def test_a_run_builds_the_ref_runs_every_sample_and_writes_the_card(rig, capsys):
    assert rig.run("topic") == 0

    calls = rig.instance.calls
    # Built and migrated before the backend starts, with peak assignment on;
    # stopped and its worktree removed once the card is written.
    assert calls[:3] == ["check_env", "bind", ("checkout", "topic")]
    assert calls.index("migrate") < calls.index(("start", True))
    assert calls[-2:] == ["stop", "remove_worktree"]
    assert rig.instance.db == ["r3"]
    assert not rig.home.worktree.exists()
    # Every sample once, with the plan's default config: the engine's own.
    assert sorted((s, c) for s, _, c in rig.server.assigned) == [
        ("S1", None),
        ("S2", None),
        ("S3", None),
    ]
    assert rig.apis == [("http://localhost:8093", "tok")]
    call = rig.compare.calls[0]
    assert call["manifest"]["sets"] == {
        "Db": {"workspace": "W1", "samples": ["S1", "S2"], "instrument": "orbitrap"},
        "Et": {"workspace": "W2", "samples": ["S3"], "instrument": "tof"},
    }
    assert call["decoy"] is False and call["baseline"] is None

    out = rig.home.cards / "topic" / "20261112-120000"
    assert sorted(p.name for p in out.iterdir()) == [
        "card.json",
        "card.md",
        "card_Db.json",
        "card_Db.md",
        "card_Et.json",
        "card_Et.md",
        "run.json",
        "run.log",
        "work",
    ]
    summary = json.loads((out / "run.json").read_text())
    assert summary["sha"] == BRANCH and summary["develop"] == DEVELOP
    assert summary["migration"] == {"from": ["r2"], "to": ["r3"]}
    assert summary["reference"] == {"pin": "v1.0.0", "version": PIN_VERSION}
    assert summary["sets"]["Db"]["read"] == 2 and summary["sets"]["Et"]["read"] == 1
    assert summary["card"]["verdict"] == "pass" and summary["is_baseline"] is False
    assert oct((out / "run.log").stat().st_mode & 0o777) == "0o600"
    assert rig.state()["cards"][-1] == {
        "at": summary["finished"],
        "ref": "topic",
        "sha": BRANCH,
        "path": "topic/20261112-120000",
        "verdict": "pass",
        "sets": ["Db", "Et"],
        "decoys": False,
        "baseline": None,
    }
    printed = capsys.readouterr().out
    assert "GATE stage3: PASS" in printed and f"card: {out}" in printed


def test_a_database_already_at_the_ref_s_head_is_not_migrated(rig):
    assert rig.run("develop") == 0
    assert "migrate" not in rig.instance.calls


def test_a_ref_older_than_the_database_is_refused_before_anything_starts(rig):
    rig.instance.db = ["r3"]

    with pytest.raises(brun.RunError, match="r3, which develop at d{9} does not know"):
        rig.run("develop")

    assert ("start", True) not in rig.instance.calls
    assert rig.server.assigned == []
    assert rig.instance.calls[-2:] == ["stop", "remove_worktree"]
    assert not rig.home.worktree.exists()
    log = next(rig.home.cards.glob("develop/*/run.log")).read_text()
    assert "stopped: RunError" in log


def test_a_migration_that_misses_the_head_is_refused(rig):
    rig.instance.migrate = lambda: None

    with pytest.raises(
        brun.RunError, match="left the database at r2, not at the ref's head r3"
    ):
        rig.run("topic")
    assert rig.server.assigned == []


def test_sets_runs_only_the_sets_named(rig):
    assert rig.run("topic", sets=["Et"]) == 0
    assert [s for s, _, _ in rig.server.assigned] == ["S3"]
    assert list(rig.compare.calls[0]["manifest"]["sets"]) == ["Et"]

    with pytest.raises(brun.RunError, match="no set Zz; it holds Db, Et"):
        rig.run("topic", sets=["Zz"])


def test_the_decoy_arms_of_each_class_run_after_their_control(rig):
    rig.server.refuse[("S2", "mz_shift_ppm=-9")] = ("decoy_refused", "no calibration")

    assert rig.run("topic", decoys=True) == 0

    by_sample = {}
    for sample, arm, config in rig.server.assigned:
        by_sample.setdefault(sample, []).append(arm)
        if arm:
            assert config == {"decoy": {k: v for k, v in config["decoy"].items()}}
    assert by_sample["S1"] == [
        None,
        "mz_shift_ppm=+9",
        "mz_shift_ppm=-9",
        "swap_adducts",
    ]
    assert by_sample["S3"] == [None, "mz_shift_da=+0.35", "swap_adducts"]
    # Each arm is made against this run's control.
    for sample in ("S1", "S3"):
        control = rig.server.controls(sample)[0]["peak_assignment_run_id"]
        arms = [r for r in rig.server.listing[sample] if r["decoy"]]
        assert {r["config"]["decoy_arm"]["control_run_id"] for r in arms} == {control}
    assert rig.compare.calls[0]["decoy"] is True
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    # A refused arm is reported, and is no problem.
    assert summary["sets"]["Db"]["decoys"] == {
        "mz_shift_ppm=+9": {"completed": 2},
        "mz_shift_ppm=-9": {"completed": 1, "refused": 1},
        "swap_adducts": {"completed": 2},
    }
    assert summary["sets"]["Et"]["decoys"] == {
        "mz_shift_da=+0.35": {"completed": 1},
        "swap_adducts": {"completed": 1},
    }


def test_a_decoy_the_engine_refuses_at_run_time_is_refused_and_one_that_fails_is_a_problem(
    rig, capsys
):
    rig.server.ends[("S1", "swap_adducts")] = ("failed", "no decoy: nothing to swap")
    rig.server.ends[("S2", "swap_adducts")] = ("failed", "Traceback: boom")

    assert rig.run("topic", decoys=True, sets=["Db"]) == 1

    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["decoys"]["swap_adducts"] == {
        "failed": 1,
        "refused": 1,
    }
    assert "set Db: 1 decoy run(s) failed" in capsys.readouterr().out


def test_no_decoys_without_the_flag(rig):
    rig.run("topic")
    assert all(arm is None for _, arm, _ in rig.server.assigned)


def test_a_sample_without_a_reference_run_is_not_run_nor_read(rig, capsys):
    state = rig.state()
    del state["reference"]["runs"]["Db"]["S2"]
    bench.save_state(rig.home, state)

    assert rig.run("topic") == 1

    assert "S2" not in [s for s, _, _ in rig.server.assigned]
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S1"]
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["left_out"] == {"no_reference": 1}
    printed = capsys.readouterr().out
    assert "1 sample(s) not read: no_reference" in printed
    # The console names no sample; the private run log does.
    assert "S2" not in printed
    assert "S2: no_reference" in (rig.card_dir() / "run.log").read_text()


def test_a_sample_whose_newest_reference_is_not_the_pin_s_is_not_read(rig):
    rig.server.listing["S1"].insert(
        0,
        {
            "peak_assignment_run_id": "P-later",
            "engine": "peaky",
            "engine_version": "1.1.0",
            "status": "completed",
            "decoy": None,
        },
    )
    rig.server.listing["S2"][0]["engine_version"] = "0.0.1"

    assert rig.run("topic", sets=["Db"]) == 2

    assert rig.server.assigned == []
    assert rig.compare.calls == []
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["left_out"] == {"reference_moved": 2}
    assert summary["card"]["verdict"] == "none"
    log = (rig.card_dir() / "run.log").read_text()
    assert "the newest reference run is P-later, not the pin's P-S1" in log
    assert "version 0.0.1, not the pin's" in log


def _once_s1_completes(row):
    """A server hook: once S1's control has completed, a run lands on top."""

    def hook(server, sample):
        controls = server.controls("S1")
        if sample == "S1" and controls and controls[0]["status"] == "completed":
            server.listing["S1"].insert(0, dict(row))
            server.after = None

    return hook


def test_a_reference_republished_during_the_run_is_caught_before_the_card(rig):
    rig.server.after = _once_s1_completes(
        {
            "peak_assignment_run_id": "P-new",
            "engine": "peaky",
            "engine_version": PIN_VERSION,
            "status": "completed",
            "decoy": None,
        }
    )
    assert rig.run("topic", sets=["Db"]) == 1
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S2"]
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["left_out"] == {"reference_moved": 1}


def test_a_run_without_a_pinned_reference_is_refused(rig):
    state = rig.state()
    state["reference"] = None
    bench.save_state(rig.home, state)

    with pytest.raises(brun.RunError, match="no reference at a pin"):
        rig.run("topic")
    assert rig.instance.calls == []


def test_a_bench_without_sets_says_to_init_first(rig):
    bench.save_state(rig.home, bench.new_state("bench"))
    with pytest.raises(brun.RunError, match="holds no sets yet"):
        rig.run("topic")


def test_a_sample_init_left_with_a_problem_is_not_read(rig):
    state = rig.state()
    state["sets"]["Db"]["samples"]["k2"]["problems"] = ["has no peaks"]
    state["sets"]["Db"]["samples"]["k9"] = {
        "name": "lost.raw",
        "sample_file_id": None,
        "items": [],
        "problems": ["never ingested"],
    }
    bench.save_state(rig.home, state)

    assert rig.run("topic", sets=["Db"]) == 1
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"] | {"decoys": None} == {
        "samples": 3,
        "read": 1,
        "left_out": {"not_ready": 2},
        "minutes": summary["sets"]["Db"]["minutes"],
        "over_budget": False,
        "decoys": None,
    }


def test_a_busy_sample_is_asked_again(rig):
    rig.server.busy["S1"] = 2
    assert rig.run("topic", sets=["Db"]) == 0
    assert [s for s, _, _ in rig.server.assigned].count("S1") == 3


def test_an_ineligible_or_failed_sample_is_named_and_not_read(rig, capsys):
    rig.server.refuse[("S1", None)] = ("sample_ineligible", "it has no peaks")
    rig.server.ends[("S2", None)] = ("failed", "the engine crashed")

    assert rig.run("topic") == 1

    assert rig.compare.calls[0]["manifest"]["sets"] == {
        "Et": {"workspace": "W2", "samples": ["S3"], "instrument": "tof"}
    }
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["left_out"] == {"ineligible": 1, "failed": 1}
    printed = capsys.readouterr().out
    assert "set Db: 1 sample(s) ineligible" in printed
    assert "set Db: 1 sample(s) failed" in printed
    log = (rig.card_dir() / "run.log").read_text()
    assert "S2: failed: failed:" in log and "the engine crashed" in log


def test_a_set_over_its_budget_is_not_on_the_card(rig, capsys):
    rig.server.ends[("S2", None)] = None  # never ends

    assert rig.run("topic", budget_s=30.0) == 1

    assert list(rig.compare.calls[0]["manifest"]["sets"]) == ["Et"]
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["over_budget"] is True
    assert summary["sets"]["Db"]["left_out"] == {"over_budget": 2}
    assert summary["sets"]["Et"]["read"] == 1
    assert (
        "set Db: over its budget of 0 min; not on the card" in capsys.readouterr().out
    )
    assert rig.instance.calls[-2:] == ["stop", "remove_worktree"]


def test_each_set_has_a_budget_of_its_own(rig):
    # Each run takes 12 s: Db's two take 24 s of the 30 s budget, and Et's
    # would not fit in a budget shared with them, but fits one of its own.
    rig.server.polls = 13
    assert rig.run("topic", budget_s=30.0) == 0
    assert list(rig.compare.calls[0]["manifest"]["sets"]) == ["Db", "Et"]


def test_samples_run_side_by_side(rig):
    rig.server.polls = 3
    assert rig.run("topic", jobs=3, decoys=True) == 0
    assert len(rig.server.assigned) == 3 + 2 * 3 + 2


def test_a_card_reads_this_run_s_control_or_leaves_the_sample_out(rig):
    rig.server.after = _once_s1_completes(
        {
            "peak_assignment_run_id": "R-someone-else",
            "engine": "mascope",
            "engine_version": "0.6.0",
            "status": "completed",
            "decoy": None,
        }
    )
    assert rig.run("topic", sets=["Db"]) == 1
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S2"]
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["left_out"] == {"not_latest": 1}


def test_the_comparison_reaches_the_bench_and_not_the_caller_s_server(rig, monkeypatch):
    monkeypatch.setenv("MASCOPE_URL", "https://testbed.example")
    monkeypatch.setenv("MASCOPE_WORKSPACE", "elsewhere")
    rig.run("topic")
    env = rig.compare.calls[0]["env"]
    assert env["MASCOPE_URL"] == "http://localhost:8093"
    assert env["MASCOPE_ACCESS_TOKEN"] == "tok"
    assert "MASCOPE_WORKSPACE" not in env
    assert env["PYTHONUNBUFFERED"] == "1"


def test_the_ref_s_backend_starts_with_the_run_s_workers(rig):
    rig.run("topic", workers=3)
    assert rig.instance.workers == 3
    rig.run("topic")
    assert rig.instance.workers == brun.WORKERS > 1


def test_the_comparison_is_stopped_only_when_it_stalls(rig):
    rig.run("topic", stall_s=42.0)
    assert rig.compare.calls[0]["stall_s"] == 42.0
    rig.run("topic")
    assert rig.compare.calls[1]["stall_s"] == brun.COMPARE_STALL_MIN * 60


def _comparison(tmp_path, body):
    """A comparison script that runs ``body``, and what to call it with."""
    script = tmp_path / "compare.py"
    script.write_text("import sys, time\n" + body)
    sets = tmp_path / "work" / "sets.json"
    sets.parent.mkdir()
    sets.write_text("{}")
    log = tmp_path / "run.log"
    log.write_text("")
    kwargs = {
        "sets": sets,
        "out": tmp_path / "card",
        "decoy": False,
        "baseline": None,
        "deny": tmp_path / "deny.txt",
        "env": {"PYTHONUNBUFFERED": "1"},
        "log": log,
    }
    return brun.CompareRuns(script=script, poll_s=0.05), kwargs


def test_a_comparison_that_keeps_writing_runs_past_the_stall_window(tmp_path):
    compare, kwargs = _comparison(
        tmp_path,
        "for n in range(12):\n    print(n)\n    time.sleep(0.1)\nsys.exit(1)\n",
    )
    started = time.monotonic()
    assert compare(**kwargs, stall_s=0.5) == 1
    assert time.monotonic() - started > 1.0
    log = kwargs["log"].read_text()
    assert log.startswith("$ compare_runs.py --sets ")
    assert "\n11\n" in log


def test_a_comparison_that_writes_nothing_is_stopped(tmp_path):
    pid = tmp_path / "pid"
    compare, kwargs = _comparison(
        tmp_path,
        f"import os\nopen({str(pid)!r}, 'w').write(str(os.getpid()))\n"
        "print('read', flush=True)\ntime.sleep(60)\n",
    )
    started = time.monotonic()
    with pytest.raises(brun.RunError, match="wrote nothing for 0 min and was stopped"):
        compare(**kwargs, stall_s=0.5)
    assert time.monotonic() - started < 30
    assert "read" in kwargs["log"].read_text()
    # Stopped, not left running behind the bench's lock.
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_a_comparison_is_stopped_when_the_bench_stops(tmp_path):
    pid = tmp_path / "pid"
    compare, kwargs = _comparison(
        tmp_path,
        f"import os\nopen({str(pid)!r}, 'w').write(str(os.getpid()))\n"
        "print('read', flush=True)\ntime.sleep(60)\n",
    )
    looks = [0]

    class Log(type(kwargs["log"])):
        def stat(self, **kwargs):
            looks[0] += 1
            if looks[0] > 1 and pid.exists():
                raise OSError("the bench stopped")
            return super().stat(**kwargs)

    kwargs["log"] = Log(kwargs["log"])
    started = time.monotonic()
    with pytest.raises(OSError, match="the bench stopped"):
        compare(**kwargs, stall_s=600.0)
    assert time.monotonic() - started < 30
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_the_comparison_is_given_every_name_and_id_to_deny(rig):
    rig.run("topic", sets=["Db"])
    deny = set(rig.compare.calls[0]["deny"])
    # The instance's names and ids, every sample of every set, the reference
    # runs, this run's runs, and the files' names.
    assert {"Acquisitions Instrument9", "W1", "S1", "S3", "P-S3", "F-a1.raw"} <= deny
    assert {"a1.raw", "c3.h5", "Bromide TOF"} <= deny
    assert any(t.startswith("R1-") for t in deny)
    assert rig.compare.calls[0]["manifest"]["deny"] == ["a1.raw", "a2.raw", "c3.h5"]


def _engine_version(card):
    card["columns"]["Db"]["engine_versions"] = {"a": ["P-S1"]}


@pytest.mark.parametrize(
    "where, plant, edit",
    [
        ("card.md", "\nsample S3 read\n", None),
        ("card_Db.json", " Instrument9", None),
        ("card_Et.md", " a1.raw ", None),
        ("card.json", None, _engine_version),
    ],
)
def test_a_planted_name_never_reaches_the_card(rig, capsys, where, plant, edit):
    rig.compare.plant = (where, plant) if plant else None
    rig.compare.edit = edit

    assert rig.run("topic") == 2

    out = rig.home.cards / "topic" / "20261112-120000"
    assert sorted(p.name for p in out.iterdir()) == ["run.json", "run.log", "work"]
    assert not (out / "work" / "card").exists()
    assert rig.state()["cards"][-1]["verdict"] == "refused"
    printed = capsys.readouterr().out
    assert f"the card was refused by the scrub: {where} holds a denied name" in printed
    for token in ("S3", "Instrument9", "a1.raw", "P-S1"):
        assert token not in printed
    assert "the bench's scrub refused the card" in (out / "run.log").read_text()
    # The stack is still taken down.
    assert rig.instance.calls[-2:] == ["stop", "remove_worktree"]


def test_a_card_the_comparison_refused_is_reported_and_never_written(rig, capsys):
    rig.compare.refuse = 2
    assert rig.run("topic") == 2
    out = rig.card_dir()
    assert not (out / "card.md").exists()
    assert "refused by the scrub: nothing written" in capsys.readouterr().out
    assert json.loads((out / "run.json").read_text())["card"]["verdict"] == "refused"


def test_a_comparison_that_wrote_no_card_is_an_error(rig, capsys):
    rig.compare.refuse = 1
    assert rig.run("topic") == 2
    assert "the comparison wrote no card (exit 1)" in capsys.readouterr().out


def test_a_failed_gate_exits_1_with_the_card_written(rig):
    rig.compare.verdict = "fail"
    assert rig.run("topic") == 1
    assert (rig.card_dir() / "card.md").exists()


def test_the_backend_stops_and_the_worktree_goes_when_the_comparison_breaks(rig):
    def broken(**kwargs):
        raise RuntimeError("compare broke")

    rig.compare = broken
    with pytest.raises(RuntimeError, match="compare broke"):
        rig.run("topic")
    assert rig.instance.calls[-2:] == ["stop", "remove_worktree"]


def test_develop_s_head_with_baseline_becomes_the_rolling_baseline(rig, capsys):
    # A branch without a migration of its own, so develop runs after it.
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.run("origin/develop", baseline=True) == 0
    assert rig.compare.calls[0]["baseline"] is None
    printed = capsys.readouterr().out
    assert "baseline: none yet" in printed
    assert "this card is develop's rolling baseline now" in printed
    first = rig.state()["baseline"]
    assert first["path"] == "origin-develop/20261112-120000"
    assert (first["sha"], first["sets"], first["decoys"]) == (
        DEVELOP,
        ["Db", "Et"],
        False,
    )
    assert json.loads((rig.card_dir() / "run.json").read_text())["is_baseline"] is True

    # A branch diffs against it and does not replace it.
    assert rig.run("topic", baseline=True) == 0
    assert (
        rig.compare.calls[1]["baseline"] == rig.home.cards / first["path"] / "card.json"
    )
    assert rig.state()["baseline"] == first
    out = rig.card_dir()
    diff = json.loads((out / "diff.json").read_text())
    assert diff["baseline"] == {k: first[k] for k in ("ref", "sha", "at", "path")}
    assert diff["compared"] == 3 and diff["moved"] == []
    assert (
        "Against develop's baseline card: origin/develop at ddddddddd"
        in (out / "diff.md").read_text()
    )
    assert rig.state()["cards"][-1]["baseline"] == first["path"]
    assert "BASELINE: 0 of 3 readings moved" in capsys.readouterr().out

    # The next run of develop's head diffs against it, then replaces it.
    assert rig.run("develop", baseline=True) == 0
    assert (
        rig.compare.calls[2]["baseline"] == rig.home.cards / first["path"] / "card.json"
    )
    assert rig.state()["baseline"]["path"] == "develop/20261112-120002"


def test_a_run_of_some_sets_diffs_them_and_not_the_pooled_column(rig, capsys):
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    rig.run("origin/develop", baseline=True)

    assert rig.run("topic", baseline=True, sets=["Et"]) == 0

    given = rig.compare.calls[1]["baseline"]
    assert given.parent.name == "work"
    assert list(json.loads(given.read_text())["columns"]) == ["Et"]
    diff = json.loads((rig.card_dir() / "diff.json").read_text())
    assert diff["by_column"] == {"Et": 1}
    assert diff["only_card"] == ["pooled"]
    assert "not on the baseline: pooled" in capsys.readouterr().out


def test_without_the_flag_there_is_no_diff_and_no_baseline(rig):
    rig.run("origin/develop", baseline=True)
    first = rig.state()["baseline"]
    rig.run("develop")
    assert rig.compare.calls[1]["baseline"] is None
    assert not (rig.card_dir() / "diff.json").exists()
    assert rig.state()["baseline"] == first


@pytest.mark.parametrize(
    "spoil, why",
    [
        (
            lambda rig: rig.server.ends.update({("S1", None): ("failed", "x")}),
            "samples were left out",
        ),
        (lambda rig: None, "it reads only some of the bench's sets"),
    ],
)
def test_a_card_of_develop_that_is_not_whole_is_not_the_baseline(
    rig, capsys, spoil, why
):
    spoil(rig)
    sets = ["Db"] if "some" in why else None
    rig.run("origin/develop", baseline=True, sets=sets)
    assert rig.state()["baseline"] is None
    assert f"not made the baseline: {why}" in capsys.readouterr().out


def test_a_baseline_whose_card_is_gone_is_none(rig, capsys):
    rig.run("origin/develop", baseline=True)
    (rig.home.cards / rig.state()["baseline"]["path"] / "card.json").unlink()
    rig.run("topic", baseline=True)
    assert rig.compare.calls[1]["baseline"] is None
    assert "baseline: none yet" in capsys.readouterr().out


def test_two_runs_of_one_second_get_folders_of_their_own(rig):
    rig.stamps = iter(["20261112-120000"] * 2)
    rig.run("topic")
    rig.run("topic")
    assert sorted(p.name for p in (rig.home.cards / "topic").iterdir()) == [
        "20261112-120000",
        "20261112-120000-2",
    ]


def test_a_ref_is_one_folder_name():
    assert brun.ref_slug("origin/develop") == "origin-develop"
    assert brun.ref_slug("mas-840-bench-run") == "mas-840-bench-run"
    assert brun.ref_slug("../x y") == "x-y"


def test_an_arm_is_named_as_the_run_row_names_it():
    assert [brun.arm_name(a) for a in brun.DECOY_ARMS["orbitrap"]] == [
        "mz_shift_ppm=+9",
        "mz_shift_ppm=-9",
        "swap_adducts",
    ]
    assert [brun.arm_name(a) for a in brun.DECOY_ARMS["tof"]] == [
        "mz_shift_da=+0.35",
        "swap_adducts",
    ]


@pytest.mark.parametrize(
    "status, body, expected",
    [
        (202, {"data": [{"peak_assignment_run_id": "R1"}]}, brun.Launch(run_id="R1")),
        (
            409,
            {"detail": {"code": "assignment_in_flight"}},
            brun.Launch(busy=True, code="assignment_in_flight"),
        ),
        (
            422,
            {
                "error": "x",
                "detail": {"code": "sample_ineligible", "reason": "no peaks"},
            },
            brun.Launch(code="sample_ineligible", reason="no peaks"),
        ),
        (
            403,
            {"error": "Peak assignment is off"},
            brun.Launch(code="http_403", reason="Peak assignment is off"),
        ),
    ],
)
def test_an_assign_answer_is_read_by_its_status_and_code(status, body, expected):
    assert brun.launch_of(status, body) == expected


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_the_token_api_sends_the_sdk_s_headers_and_the_engine_s_defaults(monkeypatch):
    api = brun.TokenApi("http://localhost:8093/", "tok")
    sent = []

    def request(method, url, timeout, **kwargs):
        sent.append((method, url, kwargs))
        if method == "POST":
            return _Response(202, {"data": [{"peak_assignment_run_id": "R1"}]})
        return _Response(200, {"data": [{"peak_assignment_run_id": "R1"}]})

    monkeypatch.setattr(api.session, "request", request)
    assert api.assign("S1", None) == brun.Launch(run_id="R1")
    assert api.assign("S1", {"decoy": {"swap_adducts": True}}).run_id == "R1"
    assert api.runs("S1", include_decoys=True) == [{"peak_assignment_run_id": "R1"}]
    assert sent == [
        (
            "POST",
            "http://localhost:8093/api/peak-assignments/sample/S1/assign",
            {"json": {}},
        ),
        (
            "POST",
            "http://localhost:8093/api/peak-assignments/sample/S1/assign",
            {"json": {"config": {"decoy": {"swap_adducts": True}}}},
        ),
        (
            "GET",
            "http://localhost:8093/api/peak-assignments/sample/S1/runs",
            {"params": {"include_decoys": "true"}},
        ),
    ]
    assert api.session.headers["Authorization"] == "Bearer tok"
    assert api.session.headers["X-Service-Name"] == "mascope_sdk"


def test_the_token_api_tells_a_passing_failure_from_an_answer(monkeypatch):
    import requests

    api = brun.TokenApi("http://localhost:8093", "tok")
    answers = iter(
        [requests.ConnectionError("refused"), _Response(502, {}), _Response(404, {})]
    )

    def request(*args, **kwargs):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(api.session, "request", request)
    with pytest.raises(brun.TransientError):
        api.runs("S1")
    with pytest.raises(brun.TransientError):
        api.runs("S1")
    with pytest.raises(brun.RunError, match="answered 404"):
        api.runs("S1")


def test_a_server_that_stops_answering_fails_the_sample_not_the_run(rig):
    calls = {"n": 0}
    real = rig.server.runs

    def flaky(sample, include_decoys=False):
        if sample == "S1" and include_decoys:
            calls["n"] += 1
            raise brun.TransientError("no answer")
        return real(sample, include_decoys)

    rig.server.runs = flaky
    assert rig.run("topic", sets=["Db"]) == 1
    assert calls["n"] == brun.TRANSIENT_TRIES
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["left_out"] == {"failed": 1}


# ---------------------------------------------------------------- baseline


def test_baseline_shows_develop_s_card_or_says_there_is_none(rig, capsys):
    runner = rig.runner()
    assert runner.baseline() == 1
    assert "no baseline yet" in capsys.readouterr().out
    rig.run("origin/develop", baseline=True)
    capsys.readouterr()
    assert runner.baseline() == 0
    assert "baseline  origin/develop at ddddddddd" in capsys.readouterr().out


def test_baseline_makes_a_card_of_develop_the_baseline(rig, capsys):
    rig.run("develop")
    rig.run("topic")
    runner = rig.runner()

    assert runner.baseline("develop/20261112-120000") == 0
    assert rig.state()["baseline"]["path"] == "develop/20261112-120000"
    assert rig.state()["baseline"]["sets"] == ["Db", "Et"]

    with pytest.raises(brun.RunError, match="is not on develop"):
        runner.baseline(str(rig.home.cards / "topic" / "20261112-120001"))
    with pytest.raises(brun.RunError, match="not a card of this bench"):
        runner.baseline(str(rig.home.root))
    with pytest.raises(brun.RunError, match="holds no written card"):
        runner.baseline("develop")


def test_status_shows_the_baseline_and_each_card_s_verdict(rig):
    import io

    rig.compare.verdict = "fail"
    rig.run("develop")
    rig.compare.verdict = "pass"
    rig.run("origin/develop", baseline=True)
    out = io.StringIO()
    bench.Bench(home=rig.home, instance=rig.instance, connect=rig.connect).status(out)
    text = out.getvalue()
    assert "baseline  origin/develop at ddddddddd" in text
    assert "develop at ddddddddd  fail  develop/20261112-120000" in text
    assert "origin/develop at ddddddddd  pass  origin-develop/20261112-120001" in text


# --------------------------------------------------------------- the lock


def test_a_second_run_waits_for_the_first(tmp_path, capsys):
    home = bench.Home(tmp_path / "home")
    order = []
    held = threading.Event()

    def first():
        with home.lock(wait=True):
            order.append("first in")
            held.set()
            time.sleep(0.5)
            order.append("first out")

    def second():
        held.wait()
        with home.lock(wait=True):
            order.append("second in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert order == ["first in", "first out", "second in"]
    assert "holds the lock (pid" in capsys.readouterr().out


def test_no_wait_refuses_while_another_command_runs(tmp_path, capsys):
    home = bench.Home(tmp_path / "home")
    home.make()
    bench.save_state(home, _state())
    with home.lock():
        assert bench.main(["--home", str(home.root), "run", "topic", "--no-wait"]) == 2
    err = capsys.readouterr().err
    assert "holds the lock" in err and "without --no-wait a run waits for it" in err


# ------------------------------------------------------------ the command


def test_the_command_line_runs_a_run(tmp_path, monkeypatch):
    calls = []

    class Runner:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run(self, ref, sets, decoys, baseline):
            calls.append((ref, sets, decoys, baseline))
            return 0

        def baseline(self, card=None):
            calls.append(("baseline", card))
            return 0

    monkeypatch.setattr(brun, "BenchRun", Runner)
    home = str(tmp_path / "home")
    argv = ["--home", home, "run", "topic", "--sets", "Db, Et", "--decoys"]
    assert (
        bench.main(
            argv + ["--baseline", "--jobs", "2", "--budget-min", "5", "--workers", "6"]
        )
        == 0
    )
    assert calls[0]["jobs"] == 2 and calls[0]["budget_s"] == 300.0
    assert calls[0]["workers"] == 6
    assert calls[1] == ("topic", ["Db", "Et"], True, True)

    assert bench.main(["--home", home, "run", "topic"]) == 0
    assert (
        calls[2]["jobs"] == brun.JOBS and calls[2]["budget_s"] == brun.BUDGET_MIN * 60
    )
    assert calls[2]["workers"] == brun.WORKERS
    assert calls[3] == ("topic", None, False, False)

    assert bench.main(["--home", home, "baseline"]) == 0
    assert bench.main(["--home", home, "baseline", "develop/x"]) == 0
    assert [c for c in calls if isinstance(c, tuple) and c[0] == "baseline"] == [
        ("baseline", None),
        ("baseline", "develop/x"),
    ]


def test_a_run_error_is_a_refusal_on_the_command_line(tmp_path, capsys):
    home = bench.Home(tmp_path / "home")
    home.make()
    bench.save_state(home, bench.new_state("bench"))
    assert bench.main(["--home", str(home.root), "run", "topic"]) == 2
    assert "bench: the bench holds no sets yet" in capsys.readouterr().err


# ------------------------------------------------- the reference's build


def test_the_reference_builds_its_backend_before_it_starts_it():
    seen = []

    class Instance:
        def check_env(self):
            seen.append("check_env")

        def bind(self):
            seen.append("bind")

        def start(self, peak_assignment=False):
            seen.append("start")
            return "http://localhost:8093"

        def stop(self):
            seen.append("stop")

    def connect(base):
        raise bench.BenchError("stop here")

    runner = bench.bench_reference.Reference(
        peaky=None,
        instance=Instance(),
        connect=connect,
        env_file=Path("unused"),
        load_state=dict,
        save_state=lambda state: None,
        prepare=lambda: seen.append("prepare"),
    )
    with pytest.raises(bench.BenchError, match="stop here"):
        runner._run_jobs({}, {}, [])
    assert seen == ["check_env", "bind", "prepare", "start", "stop"]


# --------------------------------------------------------- the dev instance


def test_the_dev_checkout_syncs_the_ref_s_lock_as_it_is(tmp_path, monkeypatch):
    home = bench.Home(tmp_path / "home")
    home.make()
    (home.worktree / ".git").mkdir(parents=True)
    dev = bench.DevInstance(home, runtime=tmp_path)
    seen = []

    class Done:
        returncode = 0
        stdout = "abc\n"
        stderr = ""

    def fake_run(command, **kwargs):
        seen.append(command)
        return Done()

    monkeypatch.setattr(bench.subprocess, "run", fake_run)
    assert dev.checkout("topic") == "abc"
    assert ["uv", "sync", "--frozen", "--all-groups", "-q"] in seen
    # The bench's own build: what a sync left in it does not stop a checkout.
    assert [
        "git",
        "-C",
        str(home.worktree),
        "checkout",
        "-q",
        "--force",
        "--detach",
        "abc",
    ] in seen


def test_the_dev_worktree_is_removed_and_pruned(tmp_path, monkeypatch):
    home = bench.Home(tmp_path / "home")
    home.make()
    (home.worktree / ".git").mkdir(parents=True)
    (home.worktree / "left.txt").write_text("x")
    dev = bench.DevInstance(home, repo=tmp_path / "repo", runtime=tmp_path)
    seen = []

    class Done:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(
        bench.subprocess,
        "run",
        lambda command, **kwargs: seen.append(command) or Done(),
    )
    dev.remove_worktree()
    assert not home.worktree.exists()
    assert seen == [
        [
            "git",
            "-C",
            str(tmp_path / "repo"),
            "worktree",
            "remove",
            "--force",
            str(home.worktree),
        ],
        ["git", "-C", str(tmp_path / "repo"), "worktree", "prune"],
    ]


def test_the_dev_ref_revisions_are_read_in_the_ref_s_own_backend(tmp_path, monkeypatch):
    home = bench.Home(tmp_path / "home")
    home.make()
    dev = bench.DevInstance(home, runtime=tmp_path)
    seen = []

    class Done:
        returncode = 0
        stdout = 'noise\n{"heads": ["r3"], "revisions": ["r3", "r2"]}\n'
        stderr = ""

    def fake_run(command, cwd, **kwargs):
        seen.append((command[:4], cwd))
        return Done()

    monkeypatch.setattr(bench.subprocess, "run", fake_run)
    assert dev.ref_revisions() == (["r3"], ["r3", "r2"])
    assert seen == [
        (["uv", "run", "--no-sync", "python"], home.worktree / "server" / "backend")
    ]


def test_the_alembic_probe_reads_a_script_directory(tmp_path):
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nscript_location = %(here)s/alembic\n"
    )
    (tmp_path / "alembic" / "script.py.mako").write_text("")
    for rev, down in (("r1", None), ("r2", "r1")):
        (tmp_path / "alembic" / "versions" / f"{rev}.py").write_text(
            f"revision = {rev!r}\ndown_revision = {down!r}\n"
            "branch_labels = None\ndepends_on = None\n"
        )
    import subprocess

    out = subprocess.run(
        [sys.executable, "-c", bench.ALEMBIC_PROBE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    found = json.loads(out.stdout.strip().splitlines()[-1])
    assert found == {"heads": ["r2"], "revisions": ["r2", "r1"]}


# ------------------------------------------------- the rest of the paths


def test_a_server_that_does_not_take_the_request_fails_the_sample(rig):
    real = rig.server.assign

    def unreachable(sample, config):
        if sample == "S1":
            raise brun.TransientError("no answer")
        return real(sample, config)

    rig.server.assign = unreachable
    assert rig.run("topic", sets=["Db"]) == 1
    log = (rig.card_dir() / "run.log").read_text()
    assert "S1: failed: failed: unreachable no answer" in log
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S2"]


def test_a_run_that_leaves_the_listing_or_cannot_be_read_fails_its_sample(rig):
    real = rig.server.runs

    def odd(sample, include_decoys=False):
        rows = real(sample, include_decoys)
        if include_decoys and sample == "S1":
            return [r for r in rows if r["engine"] != "mascope"]
        if include_decoys and sample == "S2":
            raise brun.RunError("the runs of a sample answered 404")
        return rows

    rig.server.runs = odd
    assert rig.run("topic", sets=["Db"]) == 2
    log = (rig.card_dir() / "run.log").read_text()
    assert "S1: failed: lost:" in log and "the run left the listing" in log
    assert "S2: failed: failed: unreadable the runs of a sample answered 404" in log


def test_a_sample_whose_runs_cannot_be_read_before_it_runs_is_failed(rig):
    real = rig.server.runs

    def unreadable(sample, include_decoys=False):
        if sample == "S2" and not include_decoys:
            raise brun.RunError("the runs of a sample answered 404")
        return real(sample, include_decoys)

    rig.server.runs = unreadable
    assert rig.run("topic", sets=["Db"]) == 1
    assert "S2" not in [s for s, _, _ in rig.server.assigned]
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["left_out"] == {"failed": 1}


def test_the_decoy_runs_are_denied_too(rig):
    rig.run("topic", decoys=True, sets=["Et"])
    deny = set(rig.compare.calls[0]["deny"])
    arms = [r["peak_assignment_run_id"] for r in rig.server.listing["S3"] if r["decoy"]]
    assert len(arms) == 2 and set(arms) <= deny


def test_the_url_s_host_is_denied(rig, capsys):
    rig.compare.plant = ("card.md", " localhost ")
    assert rig.run("topic") == 2
    assert "card.md holds a denied name" in capsys.readouterr().out


def test_a_name_of_the_card_s_own_words_or_the_ref_s_does_not_refuse_it(rig):
    # Every word of these is a key of the card's JSON or a word of the ref:
    # printing them names nothing.
    rig.instance.names["names"] += ["Verdict Engines", "Topic study"]
    assert rig.run("topic") == 0
    out = rig.card_dir()
    assert (out / "card.json").exists() and (out / "run.json").exists()


def test_a_summary_that_would_name_a_site_is_not_written(rig, capsys):
    state = rig.state()
    state["reference"]["version"] = "1.0.0+Instrument9"
    bench.save_state(rig.home, state)
    for rows in rig.server.listing.values():
        rows[0]["engine_version"] = "1.0.0+Instrument9"

    assert rig.run("topic") == 0

    out = rig.card_dir()
    assert (out / "card.md").exists() and not (out / "run.json").exists()
    printed = capsys.readouterr().out
    assert "run.json was refused by the scrub: a denied name; not written" in printed
    assert "Instrument9" not in printed
    assert "the scrub refused run.json" in (out / "run.log").read_text()


def test_every_line_of_the_bench_s_log_reaches_the_run_log(tmp_path, capsys):
    with brun.RunLog(tmp_path / "run.log") as runlog:
        bench.log("uv sync in the worktree")
        runlog.note("only in the log")
    bench.log("after the run")
    text = (tmp_path / "run.log").read_text()
    assert "uv sync in the worktree" in text and "only in the log" in text
    assert "after the run" not in text
    assert "only in the log" not in capsys.readouterr().out


def test_a_new_state_has_no_baseline():
    assert bench.new_state("bench")["baseline"] is None


def test_the_reference_command_builds_the_ref_init_built_from(tmp_path, monkeypatch):
    home = bench.Home(tmp_path / "home")
    home.make()
    state = _state()
    state["build"] = {"ref": "release", "sha": "x", "at": "t"}
    bench.save_state(home, state)
    seen = {}

    class Runner:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self, pin, reinstall, only):
            seen["prepare"]()
            return 0

    monkeypatch.setattr(bench.bench_reference, "Reference", Runner)
    monkeypatch.setattr(
        bench.DevInstance,
        "checkout",
        lambda self, ref: seen.setdefault("ref", ref) and "c0ffee",
    )
    monkeypatch.setattr(
        brun,
        "migrate",
        lambda instance, ref, sha, say: seen.update(migrated=(ref, sha)),
    )
    assert bench.main(["--home", str(home.root), "reference", "--pin", "v1.0.0"]) == 0
    assert seen["ref"] == "release" and seen["migrated"] == ("release", "c0ffee")

    state["build"] = None
    bench.save_state(home, state)
    seen.clear()
    bench.main(["--home", str(home.root), "reference", "--pin", "v1.0.0"])
    assert seen["ref"] == "develop"


def _dev(tmp_path, monkeypatch, answers):
    home = bench.Home(tmp_path / "home")
    home.make()
    dev = bench.DevInstance(home, repo=tmp_path / "repo", runtime=tmp_path)
    seen = []

    class Done:
        def __init__(self, returncode=0, stdout=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, ""

    def fake_run(command, **kwargs):
        seen.append(command)
        return Done(*answers.get(tuple(command[3:5]), (0, "")))

    monkeypatch.setattr(bench.subprocess, "run", fake_run)
    return dev, seen


def test_the_dev_instance_reads_develop_s_head_and_its_history(tmp_path, monkeypatch):
    dev, seen = _dev(
        tmp_path,
        monkeypatch,
        {
            ("rev-parse", "--verify"): (0, "d" * 40 + "\n"),
            ("merge-base", "--is-ancestor"): (1, ""),
        },
    )
    assert dev.develop_sha() == "d" * 40
    assert [
        "git",
        "-C",
        str(tmp_path / "repo"),
        "rev-parse",
        "--verify",
        "-q",
        "origin/develop^{commit}",
    ] in seen
    assert dev.is_on_develop("b" * 40) is False
    assert [
        "git",
        "-C",
        str(tmp_path / "repo"),
        "fetch",
        "-q",
        "origin",
        "develop",
    ] in seen
    assert seen[-1][3:] == ["merge-base", "--is-ancestor", "b" * 40, "origin/develop"]

    dev, _ = _dev(tmp_path, monkeypatch, {("rev-parse", "--verify"): (1, "")})
    with pytest.raises(bench.BenchError, match="no origin/develop"):
        dev.develop_sha()


def test_the_dev_instance_reads_its_revisions_and_names_from_the_database(
    tmp_path, monkeypatch
):
    home = bench.Home(tmp_path / "home")
    dev = bench.DevInstance(home, runtime=tmp_path)
    asked = []

    def query(sql):
        asked.append(sql)
        if "alembic_version" in sql:
            return [{"version_num": "r2"}]
        if "workspace_name" in sql:
            return [{"name": "Acquisitions Instrument9"}, {"name": "a.raw"}]
        return [{"id": "W1"}, {"id": "S1"}]

    monkeypatch.setattr(dev, "_query", query)
    assert dev.db_revisions() == ["r2"]
    assert dev.private_names() == {
        "names": ["Acquisitions Instrument9", "a.raw"],
        "ids": ["W1", "S1"],
    }
    names_sql, ids_sql = asked[1], asked[2]
    for column in (
        "workspace_name",
        "dataset_name",
        "sample_batch_name",
        "sample_item_name",
        "filename",
        "source_filename",
        "ionization_mode_name",
        "target_collection_name",
    ):
        assert (
            f"select {column}" in names_sql or f"select {column} as name" in names_sql
        )
    for column in (
        "workspace_id",
        "dataset_id",
        "sample_batch_id",
        "sample_item_id",
        "sample_file_id",
    ):
        assert column in ids_sql


def test_the_dev_instance_migrates_with_the_worktree_s_cli(tmp_path, monkeypatch):
    dev = bench.DevInstance(bench.Home(tmp_path / "home"), runtime=tmp_path)
    seen = []
    monkeypatch.setattr(dev, "_cli", lambda *args, check=True: seen.append(args))
    dev.migrate()
    assert seen == [("dev", "migrate", "upgrade")]


def test_the_dev_backend_starts_with_the_workers_asked_for(tmp_path, monkeypatch):
    home = bench.Home(tmp_path / "home")
    home.make()
    dev = bench.DevInstance(home, runtime=tmp_path)
    dev.api_port = 1
    config = tmp_path / ".runtime" / "env" / "bench" / "dev.mascope.toml"
    assert dev.config_file == config
    seen = []

    class Stop(Exception):
        pass

    def popen(command, **kwargs):
        seen.append(config.read_text() if config.exists() else None)
        raise Stop

    monkeypatch.setattr(bench.subprocess, "Popen", popen)
    with pytest.raises(Stop):
        dev.start(peak_assignment=True, workers=4)
    # The env's own layer, which every ref's runtime reads over its defaults.
    assert seen[0].startswith(bench.CONFIG_MARK)
    assert tomllib.loads(seen[0]) == {"backend": {"workers": 4}}
    with pytest.raises(Stop):
        dev.start(peak_assignment=True)
    assert seen[1] is None

    # A file the bench did not write is the operator's: left as it is.
    config.write_text("[backend]\nlog_level = 'info'\n")
    for workers in (4, 1):
        with pytest.raises(Stop):
            dev.start(workers=workers)
    assert seen[2:] == ["[backend]\nlog_level = 'info'\n"] * 2


def test_a_sample_whose_runs_cannot_be_read_after_it_ran_is_failed(rig):
    real = rig.server.runs

    def unreadable(sample, include_decoys=False):
        ran = rig.server.controls(sample)
        if sample == "S2" and not include_decoys and ran:
            raise brun.RunError("the runs of a sample answered 404")
        return real(sample, include_decoys)

    rig.server.runs = unreadable
    assert rig.run("topic", sets=["Db"]) == 1
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S1"]
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["sets"]["Db"]["left_out"] == {"failed": 1}
