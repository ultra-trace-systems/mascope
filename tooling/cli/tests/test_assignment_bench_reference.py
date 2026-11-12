"""The assignment bench's ``reference`` (``tooling/assignment_bench/reference.py``).

peaky at a release tag, installed into the bench's own tool environment and
run over every sample of every set, each run published into the instance.
The upstream is a local git repository with tags; ``uv`` is a fake on the
path that installs a fake ``peaky`` (and the tool environment's ``python``,
which answers the probe) from the exported tag; the fake ``peaky`` writes a
ledger per ``assign`` and answers ``publish`` as the real one does, logging
every call. No network, no server.
"""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
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
ref = bench.bench_reference


# ------------------------------------------------------------- the fakes


FAKE_UV = """
import json, os, shutil, sys, tomllib
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"cmd": "uv", "argv": args}) + "\\n")
tools, bin_ = Path(os.environ["UV_TOOL_DIR"]), Path(os.environ["UV_TOOL_BIN_DIR"])
if args[:2] == ["tool", "uninstall"]:
    shutil.rmtree(tools / args[2], ignore_errors=True)
    (bin_ / "peaky").unlink(missing_ok=True)
    sys.exit(0)
assert args[:4] == ["tool", "install", "--python", "3.12"], args
src = Path(args[-1])
dist = tomllib.loads((src / "pyproject.toml").read_text())["project"]["name"]
env = tools / dist
(env / "bin").mkdir(parents=True)
bin_.mkdir(parents=True, exist_ok=True)
fake = json.loads((src / "fake.json").read_text())
fake["library"] = os.environ["FAKE_LIBRARY"]
(env / "fake.json").write_text(json.dumps(fake))
for name, target in (("python", env / "bin" / "python"), ("peaky", bin_ / "peaky")):
    target.write_text("#!" + sys.executable + "\\n" + os.environ["FAKE_" + name.upper()])
    target.chmod(0o755)
"""

# The tool environment's python: answers the bench's probe from fake.json.
FAKE_PYTHON = """
import json, sys
from pathlib import Path

fake = json.loads((Path(__file__).parent.parent / "fake.json").read_text())
profiles = {p: (p.capitalize() if p.lower() in fake["profiles"] else None) for p in sys.argv[3:]}
print(json.dumps({"version": fake["version"], "library": fake["library"], "profiles": profiles}))
"""

FAKE_PEAKY = """
import csv, json, os, sys
from pathlib import Path

args = sys.argv[1:]
tools = Path(__file__).resolve().parent.parent / "tools"
fake = json.loads(next(tools.glob("*/fake.json")).read_text())
seen = {k: os.environ.get(k) for k in ("MASCOPE_URL", "MASCOPE_ACCESS_TOKEN", "MASCOPE_WORKSPACE", "HOME")}
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps({"cmd": "peaky", "argv": args, "env": seen, "version": fake["version"]}) + "\\n")

def opt(name):
    return args[args.index(name) + 1] if name in args else None

spec = json.loads(Path(os.environ["FAKE_SPEC"]).read_text())
command = args[2]
sample = opt("--sample-id")
if sample in spec.get("fail", []):
    print("ERROR: the sample would not load")
    sys.exit(1)
if command == "assign":
    rows = spec.get("ledgers", {}).get(fake["version"], {}).get(sample) or [
        ["p1", "M0", "C2H2O4", "[M+Br]-", "Assigned"],
        ["p2", "iso_child", "", "", ""],
        ["p3", "unexplained", "", "", ""],
    ]
    out = Path(opt("--output-dir"))
    with open(out / f"{sample}_20261112-0900_ledger.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["peak_id", "mz", "role", "neutral_formula", "adduct", "tier", "height"])
        for peak, role, formula, adduct, tier in rows:
            w.writerow([peak, "100.0", role, formula, adduct, tier, "1"])
    print("assigned 1 | iso 1 | reagent 0 | unexplained 1")
elif command == "publish":
    with open(args[3]) as fh:
        n = sum(1 for _ in fh) - 1
    status = spec.get("status", "completed")
    print(f"published run R-{sample}-{opt('--import-id')[-6:]} ({n} rows, {status})")
"""


def _upstream(tmp_path):
    """peaky's repository: three release tags and a pre-release."""
    repo = tmp_path / "upstream"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.org")
    git("config", "user.name", "t")
    for tag, version, profiles in (
        ("v1.0.0", "1.0.0+assign0.4", ["br", "ur"]),
        ("v1.1.0", "1.1.0+assign0.5", ["br", "ur"]),
        ("v1.2.0b1", "1.2.0b1", ["br", "ur"]),
    ):
        (repo / "pyproject.toml").write_text(
            f'[project]\nname = "mascope-peaky"\nversion = "{version}"\n'
        )
        (repo / "fake.json").write_text(
            json.dumps({"version": version, "profiles": profiles})
        )
        git("add", "-A")
        git("commit", "-q", "-m", tag)
        git("tag", tag)
    return repo


def _executable(path, body):
    path.write_text(f"#!{sys.executable}\n{textwrap.dedent(body)}")
    path.chmod(0o755)


class FakeInstance:
    """The bench env, as ``reference`` uses it."""

    env = "bench"

    def __init__(self):
        self.calls = []
        self.workspaces = {}

    def check_env(self):
        self.calls.append("check_env")

    def bind(self):
        self.calls.append("bind")
        return 3, 8093

    def start(self, peak_assignment=False):
        self.calls.append(("start", peak_assignment))
        return "http://localhost:8093"

    def stop(self):
        self.calls.append("stop")

    def sample_workspaces(self):
        return dict(self.workspaces)


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
            "instrument_class": "orbitrap",
            "profile": "BR",
            "reference_window_ppm": None,
            "samples": {
                "k1": _record("a.raw", ["S1"]),
                "k2": _record("b.raw", ["S2"]),
            },
        },
        "Et": {
            "mode": "Bromide TOF",
            "instrument_class": "tof",
            "profile": "BR",
            "reference_window_ppm": {"trust": 8.0, "search": 25.0},
            "samples": {"k3": _record("c.h5", ["S3"])},
        },
    }
    return state


class Rig:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.upstream = _upstream(tmp_path)
        fakes = tmp_path / "fakes"
        fakes.mkdir()
        _executable(fakes / "uv", FAKE_UV)
        self.log = tmp_path / "calls.jsonl"
        self.spec_file = tmp_path / "spec.json"
        self.spec = {}
        monkeypatch.setenv("PATH", f"{fakes}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_LOG", str(self.log))
        monkeypatch.setenv("FAKE_SPEC", str(self.spec_file))
        monkeypatch.setenv("FAKE_LIBRARY", "2026.9.30")
        monkeypatch.setenv("FAKE_PYTHON", FAKE_PYTHON)
        monkeypatch.setenv("FAKE_PEAKY", FAKE_PEAKY)
        self.home = bench.Home(tmp_path / "home")
        self.home.make()
        bench.save_state(self.home, _state())
        self.instance = FakeInstance()
        self.instance.workspaces = {"S1": "W1", "S2": "W1", "S3": "W2", "S4": "W1"}
        self.tokens = 0

    def connect(self, base):
        self.tokens += 1
        self.home.write_private(
            self.home.env_file,
            f"MASCOPE_URL={base}\nMASCOPE_ACCESS_TOKEN=tok{self.tokens}\n",
        )

    def runner(self, jobs=1):
        return ref.Reference(
            peaky=ref.Peaky(self.home.root / "reference", url=str(self.upstream)),
            instance=self.instance,
            connect=self.connect,
            env_file=self.home.env_file,
            load_state=lambda: bench.load_state(self.home, "bench"),
            save_state=lambda state: bench.save_state(self.home, state),
            jobs=jobs,
        )

    def run(self, pin="v1.0.0", **kwargs):
        self.spec_file.write_text(json.dumps(self.spec))
        self.instance.calls.clear()
        if self.log.exists():
            self.log.unlink()
        return self.runner(kwargs.pop("jobs", 1)).run(pin, **kwargs)

    def calls(self, cmd=None):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [r for r in rows if cmd is None or r["cmd"] == cmd]

    def peaky(self, command):
        return [c for c in self.calls("peaky") if c["argv"][2] == command]

    def state(self):
        return bench.load_state(self.home, "bench")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


def _opt(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


# ---------------------------------------------------------------- tests


def test_a_first_call_installs_the_pin_and_publishes_every_sample(rig):
    assert rig.run("v1.0.0") == 0

    install = [c["argv"] for c in rig.calls("uv") if c["argv"][1] == "install"]
    assert len(install) == 1
    assert install[0][-1].endswith(str(Path("reference") / "src" / "v1.0.0"))
    assert (rig.home.root / "reference" / "bin" / "peaky").exists()
    assigns = {_opt(c["argv"], "--sample-id"): c["argv"] for c in rig.peaky("assign")}
    assert sorted(assigns) == ["S1", "S2", "S3"]
    assert _opt(assigns["S1"], "--reagent") == "BR"
    assert _opt(assigns["S1"], "--workspace") == "W1"
    assert "--ppm" not in assigns["S1"]
    # peaky's peak cache is keyed by sample id alone, whichever server.
    assert all("--no-cache" in argv for argv in assigns.values())
    assert _opt(assigns["S3"], "--workspace") == "W2"
    assert (_opt(assigns["S3"], "--ppm"), _opt(assigns["S3"], "--search-ppm")) == (
        "8",
        "25",
    )
    publishes = rig.peaky("publish")
    assert sorted(_opt(c["argv"], "--sample-id") for c in publishes) == [
        "S1",
        "S2",
        "S3",
    ]
    assert {_opt(c["argv"], "--engine-version") for c in publishes} == {
        "1.0.0+assign0.4"
    }
    assert rig.instance.calls == ["check_env", "bind", ("start", True), "stop"]

    reference = rig.state()["reference"]
    assert (reference["pin"], reference["version"], reference["library"]) == (
        "v1.0.0",
        "1.0.0+assign0.4",
        "2026.9.30",
    )
    s1 = reference["runs"]["Db"]["S1"]
    assert s1["run_id"].startswith("R-S1-") and s1["rows"] == 3
    assert s1["key"] == {"pin": "v1.0.0", "library": "2026.9.30"}
    assert s1["import_id"].startswith("bench-") and len(s1["import_id"]) <= 64
    assert reference["unrunnable"] == {} and reference["changed"] is None


def test_peaky_reaches_the_bench_and_not_the_callers_server(rig, monkeypatch):
    monkeypatch.setenv("MASCOPE_URL", "http://the-testbed:8090")
    monkeypatch.setenv("MASCOPE_ACCESS_TOKEN", "testbed-token")
    monkeypatch.setenv("MASCOPE_WORKSPACE", "another")

    assert rig.run() == 0

    for call in rig.calls("peaky"):
        assert call["env"]["MASCOPE_URL"] == "http://localhost:8093"
        assert call["env"]["MASCOPE_ACCESS_TOKEN"] == "tok1"
        assert call["env"]["MASCOPE_WORKSPACE"] is None
        assert call["env"]["HOME"] == str(rig.home.root / "reference" / "home")


def test_a_second_call_at_the_same_pin_publishes_and_starts_nothing(rig, monkeypatch):
    assert rig.run() == 0
    before = rig.home.state.read_bytes()
    # A save would stamp another time.
    monkeypatch.setattr(bench, "now", lambda: "2099-01-01T00:00:00+00:00")

    assert rig.run() == 0

    assert rig.calls() == []
    assert rig.instance.calls == []
    assert rig.home.state.read_bytes() == before


def test_a_new_pin_publishes_every_set_again_and_lists_what_changed(rig, capsys):
    assert rig.run("v1.0.0") == 0
    rig.spec = {
        "ledgers": {
            "1.1.0+assign0.5": {"S2": [["p1", "M0", "C3H4O4", "[M+Br]-", "Assigned"]]}
        }
    }

    assert rig.run("v1.1.0") == 0

    assert sorted(_opt(c["argv"], "--sample-id") for c in rig.peaky("publish")) == [
        "S1",
        "S2",
        "S3",
    ]
    assert {c["version"] for c in rig.peaky("assign")} == {"1.1.0+assign0.5"}
    reference = rig.state()["reference"]
    assert reference["pin"] == "v1.1.0"
    assert reference["previous"]["pin"] == "v1.0.0"
    assert reference["changed"] == {
        "against": "v1.0.0",
        "sets": {
            "Db": {"changed": 1, "compared": 2},
            "Et": {"changed": 0, "compared": 1},
        },
    }
    assert "changed since v1.0.0: Db (1/2 samples)" in capsys.readouterr().out
    status = _status(rig)
    assert "reference v1.1.0  version 1.1.0+assign0.5" in status
    assert "changed since v1.0.0: Db 1/2" in status


def test_an_install_is_reused_until_a_reinstall_resolves_another_library(
    rig, monkeypatch
):
    assert rig.run() == 0
    monkeypatch.setenv("FAKE_LIBRARY", "2026.11.1")
    assert rig.run() == 0
    assert rig.calls() == []

    assert rig.run(reinstall=True) == 0

    install = [c["argv"] for c in rig.calls("uv") if c["argv"][1] == "install"]
    assert len(install) == 1 and "--refresh" in install[0]
    assert len(rig.peaky("publish")) == 3
    reference = rig.state()["reference"]
    assert reference["library"] == "2026.11.1"
    assert reference["previous"]["library"] == "2026.9.30"


def test_a_reinstall_that_resolves_the_same_library_publishes_nothing(rig):
    assert rig.run() == 0

    assert rig.run(reinstall=True) == 0

    assert [c["argv"][1] for c in rig.calls("uv")] == ["uninstall", "install"]
    assert rig.calls("peaky") == []


@pytest.mark.parametrize("pin", ["v1.2.0b1", "main", "1.0.0", "v1.0"])
def test_a_pin_that_is_not_a_release_tag_is_refused(rig, pin):
    with pytest.raises(ref.ReferenceError, match="not a release tag"):
        rig.run(pin)
    assert rig.calls() == []


def test_a_tag_peaky_does_not_have_is_named(rig):
    with pytest.raises(ref.ReferenceError, match="no tag v9.9.9"):
        rig.run("v9.9.9")


def test_a_set_whose_profile_the_pin_lacks_is_reported_not_skipped(rig, capsys):
    state = rig.state()
    state["sets"]["Kc"] = {
        "mode": "Charge transfer",
        "instrument_class": "orbitrap",
        "profile": "CT",
        "reference_window_ppm": None,
        "samples": {"k4": _record("d.raw", ["S4"])},
    }
    bench.save_state(rig.home, state)

    assert rig.run() == 1

    assert "S4" not in {_opt(c["argv"], "--sample-id") for c in rig.calls("peaky")}
    assert len(rig.peaky("publish")) == 3
    out = capsys.readouterr().out
    assert "set Kc not run: peaky v1.0.0 has no profile 'CT'" in out
    assert "Kc" in rig.state()["reference"]["unrunnable"]
    assert "Kc   not run: peaky v1.0.0 has no profile 'CT'" in _status(rig)


def test_a_set_the_pin_can_run_again_leaves_the_unrunnable_list(rig):
    state = rig.state()
    state["sets"]["Kc"] = {
        "mode": "Charge transfer",
        "instrument_class": "orbitrap",
        "profile": "CT",
        "reference_window_ppm": None,
        "samples": {"k4": _record("d.raw", ["S4"])},
    }
    bench.save_state(rig.home, state)
    assert rig.run() == 1
    assert rig.run(only=["Db"]) == 1

    state = rig.state()
    state["sets"]["Kc"]["profile"] = "UR"
    bench.save_state(rig.home, state)
    assert rig.run() == 0

    assert rig.state()["reference"]["unrunnable"] == {}
    assert [_opt(c["argv"], "--sample-id") for c in rig.peaky("assign")] == ["S4"]


def test_a_sample_that_fails_is_named_and_run_again_next_time(rig, capsys):
    rig.spec = {"fail": ["S2"]}

    assert rig.run() == 1

    assert "sample S2: peaky assign exited 1" in capsys.readouterr().out
    entry = rig.state()["reference"]["runs"]["Db"]["S2"]
    assert "run_id" not in entry and "would not load" in entry["error"]
    assert "3 sample(s) published" not in _status(rig)
    assert "1 failed" in _status(rig)

    rig.spec = {}
    assert rig.run() == 0
    assert [_opt(c["argv"], "--sample-id") for c in rig.peaky("assign")] == ["S2"]


def test_a_publish_that_did_not_complete_is_not_cached(rig):
    rig.spec = {"status": "assembling"}

    assert rig.run() == 1
    assert all(
        "error" in e
        for r in rig.state()["reference"]["runs"].values()
        for e in r.values()
    )

    rig.spec = {}
    assert rig.run() == 0
    assert len(rig.peaky("publish")) == 3


def test_a_sample_init_did_not_finish_is_reported_and_not_run(rig, capsys):
    state = rig.state()
    state["sets"]["Db"]["samples"]["k2"]["problems"] = ["has no peaks"]
    bench.save_state(rig.home, state)

    assert rig.run() == 1

    assert sorted(_opt(c["argv"], "--sample-id") for c in rig.peaky("assign")) == [
        "S1",
        "S3",
    ]
    assert "set Db: b.raw is not ready" in capsys.readouterr().out


def test_a_sample_the_instance_lacks_is_named(rig):
    del rig.instance.workspaces["S3"]

    assert rig.run() == 1

    assert "no such sample" in rig.state()["reference"]["runs"]["Et"]["S3"]["error"]


def test_set_runs_only_the_sets_named(rig):
    assert rig.run(only=["Et"]) == 0
    assert [_opt(c["argv"], "--sample-id") for c in rig.peaky("assign")] == ["S3"]

    with pytest.raises(ref.ReferenceError, match=r"no set \['Zz'\]"):
        rig.run(only=["Zz"])


def test_samples_run_side_by_side_all_land_in_the_state(rig):
    assert rig.run(jobs=3) == 0

    runs = rig.state()["reference"]["runs"]
    assert sorted(
        (s, e["run_id"][:5]) for r in runs.values() for s, e in r.items()
    ) == [
        ("S1", "R-S1-"),
        ("S2", "R-S2-"),
        ("S3", "R-S3-"),
    ]


def test_a_bench_without_sets_says_to_init_first(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    bench.save_state(rig.home, bench.new_state("bench"))

    with pytest.raises(ref.ReferenceError, match="run `bench.py init` first"):
        rig.run()


def test_the_ledger_digest_reads_conclusions_not_row_order(tmp_path):
    a, b, c = (tmp_path / n for n in ("a.csv", "b.csv", "c.csv"))
    a.write_text("peak_id,mz,role,tier\np1,1,M0,Assigned\np2,2,iso_child,\n")
    b.write_text("mz,peak_id,role,tier\n2,p2,iso_child,\n1.5,p1,M0,Assigned\n")
    c.write_text("peak_id,mz,role,tier\np1,1,M0,Candidate\np2,2,iso_child,\n")

    assert ref.ledger_digest(a) == ref.ledger_digest(b) != ref.ledger_digest(c)
    key = {"pin": "v1.0.0", "library": "x"}
    assert ref.import_id(key, "Db", "S1", "d") == ref.import_id(key, "Db", "S1", "d")
    assert ref.import_id(key, "Db", "S1", "d") != ref.import_id(key, "Db", "S1", "e")


def _status(rig):
    import io

    out = io.StringIO()
    bench.Bench(home=rig.home, instance=rig.instance, connect=None).status(out)
    return out.getvalue()


def test_status_shows_the_pin_and_the_version_string(rig):
    assert "reference none yet" in _status(rig)
    assert rig.run() == 0

    assert (
        "reference v1.0.0  version 1.0.0+assign0.4, mascope-tools 2026.9.30, "
        "3 sample(s) published"
    ) in _status(rig)


def test_the_command_line_runs_the_reference(rig, monkeypatch, capsys):
    calls = []

    class Runner:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def run(self, pin, reinstall, only):
            calls.append((pin, reinstall, only))
            return 0

    monkeypatch.setattr(ref, "Reference", Runner)
    argv = ["--home", str(rig.home.root), "reference", "--pin", "v1.0.0"]

    assert bench.main(argv + ["--jobs", "4", "--set", "Db", "--reinstall"]) == 0

    assert calls[0]["jobs"] == 4
    assert calls[0]["peaky"].root == rig.home.root / "reference"
    assert calls[1] == ("v1.0.0", True, ["Db"])

    monkeypatch.undo()
    assert bench.main(["--home", str(rig.home.root), "reference", "--pin", "main"]) == 2
    assert "not a release tag" in capsys.readouterr().err


def test_the_dev_backend_starts_with_peak_assignment_when_asked(tmp_path, monkeypatch):
    home = bench.Home(tmp_path / "home")
    home.make()
    dev = bench.DevInstance(home, runtime=tmp_path)
    dev.api_port = 1
    seen = []

    class Stop(Exception):
        pass

    def popen(command, env, **kwargs):
        seen.append(env.get("MASCOPE_PEAK_ASSIGNMENT"))
        raise Stop

    monkeypatch.setattr(bench.subprocess, "Popen", popen)
    monkeypatch.delenv("MASCOPE_PEAK_ASSIGNMENT", raising=False)
    for flag in (False, True):
        with pytest.raises(Stop):
            dev.start(peak_assignment=flag)

    assert seen == [None, "1"]
