"""How a bench command waits its turn and how it ends (MAS-1055).

The lock's queue, with live processes waiting on a live lock; a run stopped
by a signal, in the process and through the command line with a stand-in
backend listening on a port; a backend an earlier run left on the port and a
stranger there; and the stopper itself (``tooling/assignment_bench/stopping.py``).
No host, no server.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import test_assignment_bench_run as base


bench = base.bench
brun = base.brun
stopping = sys.modules["stopping"]

TESTS = Path(__file__).resolve().parent
PYTHON = sys.executable


def _until(check, within_s=20.0, what="the condition"):
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError(f"{what} did not hold within {within_s} s")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def rig(tmp_path):
    return base.Rig(tmp_path)


@pytest.fixture
def stopper():
    """The process's stopper, taken over for the test and given back."""
    stopping.STOPPER.install()
    try:
        yield stopping.STOPPER
    finally:
        stopping.STOPPER.uninstall()


# ------------------------------------------------------------- the queue

WAITER = """
import sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
import bench
bench.LOCK_POLL_S = 0.02
home, name, out = bench.Home(Path(sys.argv[1])), sys.argv[2], Path(sys.argv[3])
with home.lock(wait=True, what="run " + name):
    with open(out, "a") as fh:
        fh.write(name + "\\n")
    time.sleep(0.1)
"""


def _tickets(home):
    return sorted(p.name for p in home.queue.glob("*.json"))


@pytest.mark.parametrize("trial", range(3))
def test_waiting_runs_take_the_lock_in_the_order_they_asked(tmp_path, trial):
    # Three trials: flock alone would let them in in any order, one in six.
    home = bench.Home(tmp_path / "home")
    script = tmp_path / "waiter.py"
    script.write_text(WAITER.format(root=str(base.ROOT)))
    order = tmp_path / "order.txt"
    procs = {}
    try:
        with home.lock(what="run holder"):
            for n, name in enumerate("ABC", start=1):
                procs[name] = subprocess.Popen(
                    [PYTHON, str(script), str(home.root), name, str(order)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                _until(lambda n=n: len(_tickets(home)) == n, what=f"{name}'s ticket")
            # Long enough for every waiter to look many times: flock alone
            # would have let any of them in first.
            time.sleep(0.3)
            assert not order.exists()
        outputs = {
            name: proc.communicate(timeout=30)[0] for name, proc in procs.items()
        }
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
    assert [p.returncode for p in procs.values()] == [0, 0, 0]
    assert order.read_text().split() == ["A", "B", "C"]
    # Each says where it stands and who is ahead, the holder first.
    assert "number 1 in the queue; ahead: run holder, pid" in outputs["A"]
    assert "(holds it)" in outputs["A"]
    third = next(
        line for line in outputs["C"].splitlines() if "number 3 in the queue" in line
    )
    assert "run holder" in third
    assert "run A, pid" in third and "run B, pid" in third
    assert "waiting since" in third
    assert _tickets(home) == []
    assert (home.root / "bench.lock").read_text() == ""


def test_a_newcomer_does_not_take_a_free_lock_ahead_of_a_waiter(tmp_path):
    home = bench.Home(tmp_path / "home")
    home.make()
    home.queue.mkdir()
    # A live waiter's ticket (this process), as if it had not looked again yet.
    (home.queue / "0000000001.json").write_text(
        json.dumps({"pid": os.getpid(), "start": bench._start_time(os.getpid())})
    )
    with pytest.raises(bench.BenchError, match="and 1 wait for it"):
        with home.lock():
            pass


def test_a_ticket_whose_process_is_gone_is_dropped(tmp_path):
    home = bench.Home(tmp_path / "home")
    home.make()
    home.queue.mkdir()
    gone = subprocess.Popen([PYTHON, "-c", "pass"])
    gone.wait()
    (home.queue / "0000000001.json").write_text(json.dumps({"pid": gone.pid}))
    # A pid given to a later process is not its ticket's either.
    (home.queue / "0000000002.json").write_text(
        json.dumps({"pid": os.getpid(), "start": "1"})
    )
    with home.lock():
        pass
    assert _tickets(home) == []


def test_a_start_time_is_when_the_process_started_in_ticks_since_boot():
    # A process started now started about now: read stat's field 22
    # (starttime) and no other, which a pid reused by a later process changes.
    hz = os.sysconf("SC_CLK_TCK")
    child = subprocess.Popen([PYTHON, "-c", "import time; time.sleep(30)"])
    try:
        start = bench._start_time(child.pid)
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    finally:
        child.kill()
        child.wait()
    assert start is not None
    assert abs(uptime - int(start) / hz) < 5
    assert bench._start_time(child.pid) is None


def test_a_waiter_that_is_stopped_leaves_no_ticket(tmp_path):
    home = bench.Home(tmp_path / "home")
    script = tmp_path / "waiter.py"
    script.write_text(WAITER.format(root=str(base.ROOT)))
    with home.lock():
        proc = subprocess.Popen(
            [PYTHON, str(script), str(home.root), "A", str(tmp_path / "o")]
        )
        _until(lambda: len(_tickets(home)) == 1, what="the ticket")
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
        assert _tickets(home) == []


def test_without_wait_a_busy_lock_is_still_refused_with_its_holder(tmp_path):
    home = bench.Home(tmp_path / "home")
    with home.lock(what="run topic"):
        with pytest.raises(bench.BenchError) as refused:
            with home.lock(hint="--wait to queue"):
                pass
    message = str(refused.value)
    assert "another bench command holds the lock (run topic, pid" in message
    assert message.endswith("; --wait to queue")
    assert _tickets(home) == []


@pytest.mark.parametrize(
    "argv, target, what",
    [
        (["init", "m.json"], ("Bench", "init"), "init"),
        (["reference", "--pin", "v1.0.0"], ("Reference", "run"), "reference v1.0.0"),
        (["review", "--record", "s.csv"], ("Review", "record"), "review --record"),
        (["baseline", "card"], ("BenchRun", "baseline"), "baseline"),
    ],
)
def test_every_command_that_locks_names_itself(
    tmp_path, monkeypatch, argv, target, what
):
    seen = []
    lock = tmp_path / "home" / "bench.lock"
    owner = {
        "Bench": bench.Bench,
        "Reference": bench.bench_reference.Reference,
        "Review": bench.bench_review.Review,
        "BenchRun": brun.BenchRun,
    }[target[0]]
    monkeypatch.setattr(
        owner, target[1], lambda *a, **k: seen.append(json.loads(lock.read_text())) or 0
    )
    assert bench.main(["--home", str(tmp_path / "home"), *argv]) == 0
    assert seen[0]["what"] == what


def test_the_command_line_names_what_takes_the_lock(tmp_path, monkeypatch):
    seen = []

    class Runner:
        def __init__(self, **kwargs):
            pass

        def reuse(self, ref, **kwargs):
            return None

        def run(self, ref, **kwargs):
            seen.append((tmp_path / "home" / "bench.lock").read_text())
            return 0

    monkeypatch.setattr(brun, "BenchRun", Runner)
    assert bench.main(["--home", str(tmp_path / "home"), "run", "topic"]) == 0
    assert json.loads(seen[0])["what"] == "run topic"


# ------------------------------------------------------------- a stopped run


def _stalled(rig, at="S1"):
    """A rig whose run of ``at`` never ends, and that is signalled once it runs."""
    rig.server.ends[(at, None)] = None
    sent = threading.Event()

    def after(server, sample):
        if sample == at and not sent.is_set() and len(server.listing[at]) > 1:
            sent.set()
            os.kill(os.getpid(), signal.SIGTERM)

    rig.server.after = after
    return sent


def test_a_run_sent_sigterm_mid_set_cleans_up_and_writes_a_stopped_summary(
    rig, stopper, capsys
):
    sent = _stalled(rig)

    with pytest.raises(stopping.Stopped) as stopped:
        rig.runner(sleep=lambda s: time.sleep(0.01), jobs=2).run("topic")

    assert sent.is_set() and stopped.value.signum == signal.SIGTERM
    # The ref's backend stopped, the database put back to the revision the
    # run found, the worktree gone: the cleanup of a normal end.
    assert rig.instance.calls[-3:] == base.BRANCH_CLEANUP
    assert rig.instance.db == ["r2"]
    assert not rig.home.restore_point.exists()
    record = rig.state()["cards"][-1]
    assert record["verdict"] == "stopped" and record["sha"] == base.BRANCH
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["card"] == {"verdict": "stopped", "sets": []}
    assert summary["stopped"] == "SIGTERM"
    assert summary["migration"]["restored"] is True
    assert summary["finished"] is not None
    assert set(summary["sets"]) == {"Db", "Et"}
    assert not (rig.card_dir() / "card.json").exists()
    assert "the run was stopped (SIGTERM); no card" in capsys.readouterr().out


def test_a_signal_during_the_cleanup_waits_for_it(rig, stopper):
    stop = rig.instance.stop

    def stop_then_signalled():
        stop()
        # A second signal while the backend is stopped and the database not
        # yet back: the cleanup goes on to the end.
        os.kill(os.getpid(), signal.SIGTERM)

    _stalled(rig)
    rig.instance.stop = stop_then_signalled
    with pytest.raises(stopping.Stopped):
        rig.runner(sleep=lambda s: time.sleep(0.01)).run("topic")
    assert rig.instance.calls[-3:] == base.BRANCH_CLEANUP
    assert rig.instance.db == ["r2"]
    assert rig.state()["cards"][-1]["verdict"] == "stopped"


def test_a_signal_after_the_card_keeps_the_card_on_record(rig, stopper):
    remove = rig.instance.remove_worktree

    def remove_then_signalled():
        remove()
        os.kill(os.getpid(), signal.SIGTERM)

    rig.instance.remove_worktree = remove_then_signalled
    with pytest.raises(stopping.Stopped):
        rig.run("topic")
    record = rig.state()["cards"][-1]
    assert record["verdict"] == "pass"
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["card"]["verdict"] == "pass" and "stopped" not in summary


def test_a_run_that_breaks_records_no_stop(rig):
    def broken(**kwargs):
        raise RuntimeError("compare broke")

    rig.compare = broken
    with pytest.raises(RuntimeError):
        rig.run("topic")
    assert rig.instance.calls[-3:] == base.BRANCH_CLEANUP
    assert rig.state()["cards"] == []


#: A stand-in for ``mascope dev run``: it listens on its port until stopped.
BACKEND = """#!{python}
import os, signal, socket, sys, time
if os.environ.get("IGNORE_TERM"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if len(sys.argv) > 4:
    open(sys.argv[4], "w").write(str(os.getpid()))
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", int(sys.argv[3])))
s.listen()
while True:
    time.sleep(1)
"""


def _backend_script(tmp_path) -> Path:
    script = tmp_path / "bin" / "mascope"
    script.parent.mkdir(exist_ok=True)
    script.write_text(BACKEND.format(python=PYTHON))
    script.chmod(0o755)
    return script


def _spawn_backend(script, cwd, port, tmp_path, env=None) -> int:
    """A stand-in backend leading its own group, as a run that was killed
    leaves it: not this process's child, so nothing here reaps it."""
    pidfile = tmp_path / f"backend-{port}.pid"
    subprocess.run(
        ["setsid", "-f", str(script), "dev", "run", str(port), str(pidfile)],
        cwd=cwd,
        check=True,
        env={**os.environ, **(env or {})},
    )
    _until(lambda: bench.port_listened(port), what="the stand-in backend")
    return int(pidfile.read_text())


def _gone(pid) -> bool:
    return not Path(f"/proc/{pid}").exists()


STOPPED_RUN = """
import os, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, {tests!r})
import test_assignment_bench_run as base
bench, brun = base.bench, base.brun
tmp, port, script = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
rig = base.Rig(tmp)
inst = rig.instance
inst.api_port = port


def start(peak_assignment=False, workers=1):
    inst.calls.append(("start", peak_assignment))
    proc = subprocess.Popen(
        [script, "dev", "run", str(port)], cwd=inst.home.worktree,
        start_new_session=True,
    )
    inst.home.pid.write_text(str(proc.pid))
    dev._proc = proc  # as DevInstance.start keeps it, to reap on stop
    while not bench.port_listened(port):
        time.sleep(0.05)
    return f"http://localhost:{{port}}"


dev = bench.DevInstance(inst.home, runtime=tmp)
dev.api_port = port
inst.start, inst.stop, inst.free_port = start, dev.stop, dev.free_port
rig.server.ends[("S1", None)] = None


def after(server, sample):
    if sample == "S1" and len(server.listing["S1"]) > 1:
        (tmp / "mid-set").touch()


rig.server.after = after
bench.DevInstance = lambda home, env: inst
runner = rig.runner(sleep=lambda s: time.sleep(0.05))
bench.bench_run.BenchRun = lambda **kwargs: runner
sys.exit(bench.main(["--home", str(rig.home.root), "run", "topic"]))
"""


def test_a_run_stopped_from_outside_frees_its_port_and_the_lock(tmp_path):
    port = _free_port()
    backend = _backend_script(tmp_path)
    script = tmp_path / "run.py"
    script.write_text(STOPPED_RUN.format(tests=str(TESTS)))
    proc = subprocess.Popen(
        [PYTHON, str(script), str(tmp_path), str(port), str(backend)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _until(lambda: (tmp_path / "mid-set").exists(), what="the run's set")
        assert bench.port_listened(port)
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 128 + signal.SIGTERM, err
    assert "bench: stopped (SIGTERM); cleaned up" in err
    assert not bench.port_listened(port)
    home = bench.Home(tmp_path / "home")
    assert not home.pid.exists()
    with home.lock():  # free
        pass
    state = bench.load_state(home, "bench")
    out_dir = home.cards / state["cards"][-1]["path"]
    summary = json.loads((out_dir / "run.json").read_text())
    assert summary["card"]["verdict"] == "stopped"
    assert summary["migration"]["from"] == ["r2"]
    assert summary["migration"]["restored"] is True


def test_a_run_whose_starter_goes_away_stops_cleanly(tmp_path):
    port = _free_port()
    backend = _backend_script(tmp_path)
    script = tmp_path / "run.py"
    script.write_text(STOPPED_RUN.format(tests=str(TESTS)))
    pidfile = tmp_path / "run.pid"
    # A starter shell that the run outlives: it is killed outright, with no
    # signal to its child, as a session the watchdog ends can be.
    starter = subprocess.Popen(
        [
            "sh",
            "-c",
            f'"{PYTHON}" "{script}" "{tmp_path}" {port} "{backend}" '
            f'> "{tmp_path}/out" 2> "{tmp_path}/err" & echo $! > "{pidfile}"; wait',
        ],
        start_new_session=True,
    )
    run_pid = None
    try:
        _until(lambda: (tmp_path / "mid-set").exists(), what="the run's set")
        run_pid = int(pidfile.read_text())
        os.kill(starter.pid, signal.SIGKILL)
        starter.wait()
        # Handed to another parent, the run sees its starter gone within a
        # look or two, and cleans up.
        _until(
            lambda: not Path(f"/proc/{run_pid}").exists(),
            within_s=60,
            what="the run's end",
        )
    finally:
        if run_pid is not None and Path(f"/proc/{run_pid}").exists():
            os.kill(run_pid, signal.SIGKILL)
    err = (tmp_path / "err").read_text()
    assert f"bench: stopped ({stopping.PARENT_GONE}); cleaned up" in err
    assert not bench.port_listened(port)
    home = bench.Home(tmp_path / "home")
    state = bench.load_state(home, "bench")
    assert state["cards"][-1]["verdict"] == "stopped"


# ------------------------------------------------------------- the port


def _dev(tmp_path, port):
    home = bench.Home(tmp_path / "home")
    home.make()
    home.worktree.mkdir()
    dev = bench.DevInstance(home, runtime=tmp_path)
    dev.api_port = port
    return dev, home


def test_a_stale_bench_backend_on_the_port_is_stopped(tmp_path, capsys):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    pid = _spawn_backend(_backend_script(tmp_path), home.worktree, port, tmp_path)
    try:
        home.pid.write_text(str(pid))
        assert bench.listeners(port) == {pid}

        dev.free_port()

        assert not bench.port_listened(port)
        _until(lambda: _gone(pid), what="the stale backend's end")
        assert not home.pid.exists()
        printed = capsys.readouterr().out
        assert f"held by an earlier bench backend (pid {pid})" in printed
    finally:
        if not _gone(pid):
            os.kill(pid, signal.SIGKILL)


def test_a_stale_backend_that_ignores_sigterm_is_killed(tmp_path, monkeypatch):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    pid = _spawn_backend(
        _backend_script(tmp_path),
        home.worktree,
        port,
        tmp_path,
        env={"IGNORE_TERM": "1"},
    )
    monkeypatch.setattr(bench, "STALE_STOP_S", 1.0)
    try:
        home.pid.write_text(str(pid))
        dev.free_port()
        assert not bench.port_listened(port)
    finally:
        if not _gone(pid):
            os.kill(pid, signal.SIGKILL)


def test_a_listener_in_the_worktree_outside_the_recorded_group_is_refused(
    tmp_path, monkeypatch
):
    port, other = _free_port(), _free_port()
    dev, home = _dev(tmp_path, port)
    script = _backend_script(tmp_path)
    # The recorded backend runs, on another port; a stranger in the bench's
    # worktree, in a group of its own, holds the bench's.
    recorded = _spawn_backend(script, home.worktree, other, tmp_path)
    stranger = _spawn_backend(script, home.worktree, port, tmp_path)
    monkeypatch.setattr(bench, "STALE_STOP_S", 1.0)
    try:
        home.pid.write_text(str(recorded))
        with pytest.raises(bench.BenchError, match="a process the bench did not start"):
            dev.free_port()
        assert not _gone(recorded) and not _gone(stranger)
    finally:
        for pid in (recorded, stranger):
            if not _gone(pid):
                os.kill(pid, signal.SIGKILL)


def test_a_bench_like_listener_with_no_pid_file_is_refused(tmp_path):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    pid = _spawn_backend(_backend_script(tmp_path), home.worktree, port, tmp_path)
    try:
        with pytest.raises(bench.BenchError, match="a process the bench did not start"):
            dev.free_port()
    finally:
        os.kill(pid, signal.SIGKILL)


def test_start_frees_the_port_before_it_starts_anything(tmp_path, monkeypatch):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    monkeypatch.setattr(
        bench.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("nothing starts")),
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen()
        with pytest.raises(bench.BenchError, match="a process the bench did not start"):
            dev.start()
    assert not home.pid.exists()


def test_stop_reaps_the_backend_it_started_without_waiting_out_its_time(
    tmp_path, monkeypatch
):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    proc = subprocess.Popen(
        [str(_backend_script(tmp_path)), "dev", "run", str(port)],
        cwd=home.worktree,
        start_new_session=True,
    )
    _until(lambda: bench.port_listened(port), what="the backend")
    home.pid.write_text(str(proc.pid))
    dev._proc = proc
    slept = []
    sleep = time.sleep
    monkeypatch.setattr(bench.time, "sleep", lambda s: slept.append(s) or sleep(0.05))
    try:
        dev.stop()
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode is not None and proc.returncode < 0
    # Gone at the first look or two, not after the full 30 s and a SIGKILL.
    assert len(slept) <= 3
    assert dev._proc is None


#: A stand-in for a backend whose worker listens from another folder: the
#: leader, ``mascope dev run``, starts it in its own group and waits.
LEADER = """#!{python}
import os, subprocess, sys, time
subprocess.Popen([sys.argv[4], "dev", "run", sys.argv[3]], cwd=sys.argv[5])
open(sys.argv[6], "w").write(str(os.getpid()))
while True:
    time.sleep(1)
"""


@pytest.mark.parametrize("where", ["elsewhere", "worktree/server/backend"])
def test_a_stale_backend_s_worker_is_known_by_its_leader_or_its_folder(tmp_path, where):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    folder = tmp_path / "home" / where if where != "elsewhere" else tmp_path / where
    folder.mkdir(parents=True)
    worker = _backend_script(tmp_path)
    leader = tmp_path / "lead" / "mascope"
    leader.parent.mkdir()
    leader.write_text(LEADER.format(python=PYTHON))
    leader.chmod(0o755)
    pidfile = tmp_path / "leader.pid"
    subprocess.run(
        [
            "setsid",
            "-f",
            str(leader),
            "dev",
            "run",
            str(port),
            str(worker),
            str(folder),
            str(pidfile),
        ],
        cwd=home.worktree,
        check=True,
    )
    _until(lambda: bench.port_listened(port), what="the worker")
    pid = int(pidfile.read_text())
    try:
        home.pid.write_text(str(pid))
        dev.free_port()
        assert not bench.port_listened(port)
    finally:
        with __import__("contextlib").suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)


def test_start_keeps_the_backend_it_started_to_reap_it(tmp_path, monkeypatch):
    dev, home = _dev(tmp_path, _free_port())

    class Proc:
        pid = 4242

        def poll(self):
            return None

    class Answer:
        status_code = 200

    import requests

    monkeypatch.setattr(bench.subprocess, "Popen", lambda *a, **k: Proc())
    monkeypatch.setattr(requests, "get", lambda *a, **k: Answer())
    assert dev.start().startswith("http://localhost:")
    assert isinstance(dev._proc, Proc) and home.pid.read_text() == "4242"


@pytest.mark.parametrize("sub", ["", "server/backend"])
def test_a_stale_backend_whose_leader_is_gone_is_stopped(tmp_path, sub):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    backend = _backend_script(tmp_path)
    child = tmp_path / "child.pid"
    cwd = home.worktree / sub
    cwd.mkdir(parents=True, exist_ok=True)
    # The group's leader ends; its worker, in its group and the worktree or
    # a folder of it, listens.
    leader = subprocess.Popen(
        ["sh", "-c", f'"{backend}" dev run {port} & echo $! > "{child}"'],
        cwd=cwd,
        start_new_session=True,
    )
    leader.wait()
    _until(lambda: bench.port_listened(port), what="the worker")
    worker = int(child.read_text())
    try:
        home.pid.write_text(str(leader.pid))
        dev.free_port()
        assert not bench.port_listened(port)
    finally:
        try:
            os.kill(worker, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("recorded", [False, True])
def test_a_listener_the_bench_did_not_start_is_refused(tmp_path, recorded):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    pid = _spawn_backend(_backend_script(tmp_path), elsewhere, port, tmp_path)
    try:
        if recorded:
            # A pid file naming it does not make a process outside the
            # bench's worktree the bench's.
            home.pid.write_text(str(pid))
        with pytest.raises(bench.BenchError, match="a process the bench did not start"):
            dev.free_port()
        assert bench.port_listened(port) and not _gone(pid)
    finally:
        os.kill(pid, signal.SIGKILL)


def test_a_listener_in_this_process_is_refused(tmp_path):
    port = _free_port()
    dev, home = _dev(tmp_path, port)
    home.pid.write_text(str(os.getpid()))
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen()
        with pytest.raises(bench.BenchError, match=f"\\({os.getpid()}\\)"):
            dev.free_port()


def test_a_free_port_is_left_alone(tmp_path):
    dev, home = _dev(tmp_path, _free_port())
    home.pid.write_text("1")
    dev.free_port()
    assert home.pid.exists()


def test_a_start_with_a_stranger_on_the_port_is_exit_2(tmp_path, monkeypatch, capsys):
    port = _free_port()

    class Runner:
        def __init__(self, **kwargs):
            pass

        def reuse(self, ref, **kwargs):
            return None

        def run(self, ref, **kwargs):
            dev, home = _dev(tmp_path, port)
            dev.free_port()
            return 0

    monkeypatch.setattr(brun, "BenchRun", Runner)
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen()
        assert bench.main(["--home", str(tmp_path / "h"), "run", "topic"]) == 2
    assert "a process the bench did not start" in capsys.readouterr().err


# ------------------------------------------------------------- the stopper


def test_a_signal_raises_stopped_once(stopper):
    with pytest.raises(stopping.Stopped) as stopped:
        os.kill(os.getpid(), signal.SIGHUP)
        time.sleep(1)
    assert stopped.value.exit_code == 128 + signal.SIGHUP
    assert stopped.value.why == "SIGHUP"
    # A second one on the way out is not raised.
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(0.05)


def test_a_shield_holds_a_signal_until_it_ends(stopper):
    done = []
    with pytest.raises(stopping.Stopped):
        with stopper.shield():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
            done.append("cleanup")
    assert done == ["cleanup"]


def test_a_shield_that_fails_keeps_its_error_and_the_stop(stopper):
    with pytest.raises(ValueError):
        with stopper.shield():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
            raise ValueError("cleanup broke")
    assert stopper.stopping is not None


def test_the_command_line_exits_128_and_the_signal(tmp_path, monkeypatch, capsys):
    class Runner:
        def __init__(self, **kwargs):
            pass

        def reuse(self, ref, **kwargs):
            return None

        def run(self, ref, **kwargs):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)
            return 0

    monkeypatch.setattr(brun, "BenchRun", Runner)
    before = signal.getsignal(signal.SIGTERM)
    assert bench.main(["--home", str(tmp_path / "home"), "run", "topic"]) == 143
    assert "bench: stopped (SIGTERM); cleaned up" in capsys.readouterr().err
    assert signal.getsignal(signal.SIGTERM) == before


def test_the_command_line_survives_a_console_that_closed(tmp_path, monkeypatch):
    class Closed:
        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

    class Runner:
        def __init__(self, **kwargs):
            pass

        def reuse(self, ref, **kwargs):
            return None

        def run(self, ref, **kwargs):
            print("the ref's backend is stopped", flush=True)
            return 0

    monkeypatch.setattr(brun, "BenchRun", Runner)
    monkeypatch.setattr(sys, "stdout", Closed())
    assert bench.main(["--home", str(tmp_path / "home"), "run", "topic"]) == 0


def test_an_error_on_the_way_out_of_a_stop_exits_as_the_stop(
    tmp_path, monkeypatch, capsys
):
    class Runner:
        def __init__(self, **kwargs):
            pass

        def reuse(self, ref, **kwargs):
            return None

        def run(self, ref, **kwargs):
            with stopping.STOPPER.shield():
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(0.05)
                raise bench.BenchError("the restore failed")

    monkeypatch.setattr(brun, "BenchRun", Runner)
    assert bench.main(["--home", str(tmp_path / "home"), "run", "topic"]) == 143
    assert "bench: the restore failed" in capsys.readouterr().err


def test_a_stop_after_the_card_posts_no_failure_over_it(rig, stopper):
    import test_assignment_bench_post as ptests

    gh = ptests.FakeGh()
    remove = rig.instance.remove_worktree

    def remove_then_signalled():
        remove()
        os.kill(os.getpid(), signal.SIGTERM)

    rig.instance.remove_worktree = remove_then_signalled
    with pytest.raises(stopping.Stopped):
        rig.run("topic", post=bench.bench_post.Poster(gh=gh, number=7))
    # The card is on record for `bench.py post`; nothing claims it failed.
    assert rig.state()["cards"][-1]["verdict"] == "pass"
    assert gh.comments == []


def test_a_stopped_run_whose_deny_list_fails_still_writes_its_summary(rig, stopper):
    def no_database():
        raise RuntimeError("the database is down")

    _stalled(rig)
    rig.instance.private_names = no_database
    with pytest.raises(stopping.Stopped):
        rig.runner(sleep=lambda s: time.sleep(0.01)).run("topic")
    summary = json.loads((rig.card_dir() / "run.json").read_text())
    assert summary["card"]["verdict"] == "stopped"


def test_a_stopped_summary_is_not_refused_for_saying_why(rig, stopper):
    _stalled(rig)
    # A name that shares a word with the stop's reason.
    rig.instance.names["names"].append("SIGTERM acquisitions")
    with pytest.raises(stopping.Stopped):
        rig.runner(sleep=lambda s: time.sleep(0.01)).run("topic")
    assert (rig.card_dir() / "run.json").exists()


def test_the_watch_sends_a_hangup_once_the_parent_changes():
    parents = iter([10, 10, 10, 1])
    sent = []
    stopper = stopping.Stopper()
    done = stopping.watch_parent(
        stopper,
        poll_s=0.01,
        getppid=lambda: next(parents),
        kill=lambda pid, signum: sent.append((pid, signum)),
    )
    _until(lambda: sent, what="the hangup")
    done.set()
    assert sent == [(os.getpid(), signal.SIGHUP)]
    assert stopper.why == stopping.PARENT_GONE


def test_the_watch_ends_when_told():
    sent, told = [], []
    done = stopping.watch_parent(
        stopping.Stopper(),
        poll_s=0.01,
        # The parent goes only once the watch was told to end.
        getppid=lambda: 1 if told else 10,
        kill=lambda pid, signum: sent.append(signum),
    )
    done.set()
    told.append(True)
    time.sleep(0.1)
    assert sent == []


def test_a_closed_console_drops_what_it_cannot_print():
    class Closed:
        def write(self, text):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

    quiet = stopping.QuietStream(Closed())
    assert quiet.write("a line\n") == 7
    quiet.flush()
    print("another", file=quiet, flush=True)
    assert quiet.closed_under
