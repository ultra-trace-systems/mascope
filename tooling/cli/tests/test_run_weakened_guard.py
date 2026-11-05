"""
Tests for tooling/monitoring/run-weakened-guard.sh, the scheduled weakened-target
guard on the monitoring host.

The script runs as it is, under bash, with its interactions stubbed: ``docker``
(the demo stack's compose commands, recorded), ``curl`` (the frontend's answer
and the heartbeat) and the suite copy's interpreter (the guard itself, whose
verdict each test sets). What is under test is the shipped logic: the stack is
published on loopback only and always taken down, the guard's exit code is the
run's, and a stack that never answered is never read as a guard that passed.
"""

import fcntl
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "monitoring" / "run-weakened-guard.sh"
HC_URL = "https://heartbeat.invalid/weakened"

pytestmark = [
    pytest.mark.skipif(not SCRIPT.is_file(), reason="run-weakened-guard.sh absent"),
    pytest.mark.skipif(
        not all(shutil.which(tool) for tool in ("bash", "flock")),
        reason="the runner needs bash and flock",
    ),
]

# docker: every call is appended to STUB_DOCKER with the variables a recreate
# depends on; `up` fails when STUB_UP_FAIL is set.
DOCKER_STUB = """#!/usr/bin/env bash
printf '%s | port=%s weaken=[%s] backend=[%s] version=%s\\n' "$*" \\
  "$MASCOPE_HTTP_PORT" "$MASCOPE_PENTEST_WEAKEN" "$MASCOPE_PENTEST_WEAKEN_BACKEND" \\
  "$MASCOPE_VERSION" >> "$STUB_DOCKER"
case " $* " in *" up "*) [ -z "${STUB_UP_FAIL:-}" ] || exit 1 ;; esac
exit 0
"""

# curl: the frontend answers when STUB_FRONTEND is "up"; heartbeats are
# appended to STUB_PINGS as "<url> <body>".
CURL_STUB = """#!/usr/bin/env bash
url= body=
while [ $# -gt 0 ]; do
  case "$1" in
    --data-binary) body="$2"; shift 2 ;;
    -m|-o|--retry) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
case "$url" in
  http://127.0.0.1:*) [ "${STUB_FRONTEND:-up}" = up ] || exit 7 ;;
  *) printf '%s %s\\n' "$url" "$body" >> "$STUB_PINGS" ;;
esac
"""

# The suite copy's interpreter: records what it was given and prints the
# guard's verdict line, then exits STUB_GUARD_RC.
GUARD_STUB = """#!/usr/bin/env bash
{
  echo "args=$*"
  echo "email=[${MASCOPE_PENTEST_EMAIL-unset}] header=[${MASCOPE_PENTEST_HEADER_VALUE-unset}]"
  echo "port=$MASCOPE_HTTP_PORT weaken=[$MASCOPE_PENTEST_WEAKEN] backend=[$MASCOPE_PENTEST_WEAKEN_BACKEND]"
} > "$STUB_GUARD_ENV"
echo "${STUB_GUARD_VERDICT:-detected 8  missed 0  unproven 0}"
exit "${STUB_GUARD_RC:-0}"
"""

SLEEP_STUB = "#!/usr/bin/env bash\nexit 0\n"


def _run(tmp_path, *, extra_env=None, hold_lock=False):
    """Run the guard's job once; return (exit code, summary, pings, docker calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("docker", DOCKER_STUB),
        ("curl", CURL_STUB),
        ("sleep", SLEEP_STUB),
    ):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    suite = tmp_path / "suite"
    (suite / ".venv" / "bin").mkdir(parents=True)
    (suite / ".venv" / "bin" / "python").write_text(GUARD_STUB)
    (suite / ".venv" / "bin" / "python").chmod(0o755)
    records = tmp_path / "records"
    records.mkdir()
    # Staging's credentials and the edge's header, as the shared config file
    # holds them for run-assessment.sh.
    config = tmp_path / "config.env"
    config.write_text(
        "MASCOPE_PENTEST_EMAIL=admin@example.com\n"
        "MASCOPE_PENTEST_HEADER_VALUE=edge-bypass-0123456789\n"
    )
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "PENTEST_RUNNER_ENV": str(config),
        "RECORDS": str(records),
        "WEAKENED_SUITE_DIR": str(suite),
        "WEAKENED_DEMO_COMPOSE": str(tmp_path / "docker-compose.demo.yaml"),
        "WEAKENED_HC_URL": HC_URL,
        "WEAKENED_START_TIMEOUT": "0",
        "STUB_DOCKER": str(tmp_path / "docker-calls"),
        "STUB_PINGS": str(tmp_path / "pings"),
        "STUB_GUARD_ENV": str(tmp_path / "guard-env"),
        **(extra_env or {}),
    }
    lock = None
    if hold_lock:
        lock = open(records / ".weakened.lock", "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        result = subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
        )
    finally:
        if lock:
            lock.close()
    summaries = list(records.glob("*-weakened.summary"))
    summary = summaries[0].read_text().strip() if summaries else None
    read = lambda name: (  # noqa: E731
        (tmp_path / name).read_text().splitlines() if (tmp_path / name).exists() else []
    )
    return result.returncode, summary, read("pings"), read("docker-calls")


def test_a_guard_that_passes_pings_success_with_its_counts(tmp_path):
    rc, summary, pings, _ = _run(tmp_path)
    assert rc == 0
    assert summary == "detected 8  missed 0  unproven 0 version=latest"
    assert pings == [f"{HC_URL}/start ", f"{HC_URL} {summary}"]


@pytest.mark.parametrize("guard_rc", [1, 2])
def test_a_guard_that_fails_fails_the_run_and_pages(tmp_path, guard_rc):
    rc, summary, pings, _ = _run(
        tmp_path,
        extra_env={
            "STUB_GUARD_RC": str(guard_rc),
            "STUB_GUARD_VERDICT": "detected 7  missed 1  unproven 0",
        },
    )
    assert rc == guard_rc
    assert summary == "detected 7  missed 1  unproven 0 version=latest"
    assert pings[-1] == f"{HC_URL}/fail guard exit {guard_rc} | {summary}"


def test_the_stack_is_published_on_loopback_and_taken_down(tmp_path):
    # A weakening left in cron's environment must not reach the healthy stack.
    _, _, _, calls = _run(
        tmp_path,
        extra_env={
            "WEAKENED_PORT": "18123",
            "MASCOPE_PENTEST_WEAKEN": "strip-referrer-policy",
            "MASCOPE_PENTEST_WEAKEN_BACKEND": "raise-upload-cap",
        },
    )
    assert calls, "docker was never called"
    for call in calls:
        assert "-p mascope-weakened" in call
        assert f"-f {tmp_path}/docker-compose.demo.yaml" in call
        assert f"-f {tmp_path}/suite/mutations/weakened.compose.yaml" in call
        # The guard recreates the frontend and the backend with the same
        # environment, so the port and the empty weakenings are exported, not
        # given to one command.
        assert call.endswith(
            "| port=127.0.0.1:18123 weaken=[] backend=[] version=latest"
        )
    commands = [call.split(" | ")[0] for call in calls]
    assert commands[0].endswith(" pull -q")
    assert commands[1].endswith(" up -d")
    assert commands[-1].endswith(" down")
    guard = (tmp_path / "guard-env").read_text()
    assert "port=127.0.0.1:18123 weaken=[] backend=[]" in guard
    assert "--target http://127.0.0.1:18123" in guard
    assert "--project mascope-weakened" in guard


def test_staging_credentials_do_not_reach_the_demo_stack(tmp_path):
    _run(tmp_path)
    guard = (tmp_path / "guard-env").read_text()
    assert "email=[] header=[]" in guard


def test_a_stack_that_never_answers_is_not_a_pass(tmp_path):
    rc, summary, pings, calls = _run(tmp_path, extra_env={"STUB_FRONTEND": "down"})
    assert rc == 2
    assert summary == "stack did not answer within 0s"
    assert pings[-1].startswith(f"{HC_URL}/fail guard exit 2 | ")
    assert not (tmp_path / "guard-env").exists(), "the guard ran on no stack"
    assert calls[-1].split(" | ")[0].endswith(" down")


def test_a_stack_that_does_not_start_is_not_a_pass(tmp_path):
    rc, summary, pings, calls = _run(tmp_path, extra_env={"STUB_UP_FAIL": "1"})
    assert rc == 2
    assert summary.startswith("stack did not start")
    assert pings[-1].startswith(f"{HC_URL}/fail ")
    assert calls[-1].split(" | ")[0].endswith(" down")


def test_a_guard_that_prints_no_verdict_says_so(tmp_path):
    rc, summary, _, _ = _run(
        tmp_path, extra_env={"STUB_GUARD_RC": "2", "STUB_GUARD_VERDICT": "Traceback"}
    )
    assert rc == 2
    assert summary == "guard printed no verdict version=latest"


def test_a_run_already_in_progress_does_nothing(tmp_path):
    rc, summary, pings, calls = _run(tmp_path, hold_lock=True)
    assert rc == 75
    assert summary is None and pings == [] and calls == []


def test_the_version_weakened_is_the_one_configured(tmp_path):
    _, summary, _, calls = _run(tmp_path, extra_env={"MASCOPE_VERSION": "v1.20.0"})
    assert summary.endswith("version=v1.20.0")
    assert all(c.endswith("version=v1.20.0") for c in calls)


def test_no_heartbeat_falls_back_to_the_assessments(tmp_path):
    """HC_URL is the weekly assessment's dead-man switch; this run must not ping it."""
    rc, _, pings, _ = _run(
        tmp_path,
        extra_env={"WEAKENED_HC_URL": "", "HC_URL": "https://heartbeat.invalid/assess"},
    )
    assert rc == 0 and pings == []
