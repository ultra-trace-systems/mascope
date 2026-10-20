"""
Tests for tooling/monitoring/doctor-push.sh, the cron job that pushes
`mascope prod doctor` to Uptime Kuma.

The script is run as cron runs it, from a copy with its placeholders filled
in as an operator fills them, and with `mascope`, `curl`, `docker`, `df` and
`python3` stubbed in `$HOME/.local/bin`, which it puts first on PATH. `mascope`
answers with a real `doctor.Report`'s JSON and a chosen exit code; `curl`
records the URL it would push. The copy reads a temp file where the real one
sources /etc/environment, so a host's own MASCOPE_PATH cannot leak in. Skipped
where there is no bash (the script targets Linux servers; CI runs these on
Ubuntu).
"""

import importlib
import json
import shutil
import sys
from pathlib import Path
from subprocess import run
from urllib.parse import parse_qs, urlsplit

import pytest


doctor = importlib.import_module("mascope_cli.cmd.prod.doctor")

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "monitoring" / "doctor-push.sh"
KUMA = "http://kuma.example:3001"

BASH = None if sys.platform == "win32" else shutil.which("bash")

pytestmark = [
    pytest.mark.skipif(not SCRIPT.is_file(), reason="doctor-push.sh absent"),
    pytest.mark.skipif(BASH is None, reason="needs bash on a POSIX host"),
]

_STUBS = {
    "mascope": '#!/bin/sh\ncat "$FAKE_REPORT"\nexit "$FAKE_RC"\n',
    # The URL is the last argument.
    "curl": '#!/bin/sh\nfor arg; do url=$arg; done\necho "$url" >>"$CURL_LOG"\n',
    "docker": """#!/bin/sh
case "$*" in
    *--format*) printf '%s\\n' "$FAKE_DOCKER_STATUS" ;;
    *" -q"*) i=0; while [ "$i" -lt "$FAKE_DOCKER_RUNNING" ]; do echo "c$i"; i=$((i + 1)); done ;;
esac
""",
    "df": """#!/bin/sh
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/test 100 37 63 37% /"
""",
    "python3": f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
}


def _report(*, filling=False, low=False, container="running"):
    disk = doctor.DiskUsage(
        "state",
        "/srv/.runtime",
        400.0,
        40.0,
        low,
        mount="/",
        rates=[doctor.FillRate(7, 7.0, 2.0)],
        days_to_full=200.0,
        filling=filling,
    )
    return doctor.Report(
        containers=[doctor.ContainerHealth("backend", "b", container, None)],
        versions=doctor.VersionStatus("v1.6.1", []),
        disks=[disk],
        updates=doctor.UpdateStatus(None, None, None),
        backups=doctor.BackupStatus(5, 8.0),
        images=doctor.ImageFootprint(11, "6.2GB", "2.1GB"),
    )


class DoctorPush:
    """Runs a filled-in copy of doctor-push.sh against stubbed commands."""

    def __init__(self, tmp_path: Path, filling_token: str = "ftok"):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        for name, body in _STUBS.items():
            stub = bin_dir / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)
        self.curl_log = tmp_path / "curl.log"
        self.report = tmp_path / "report.json"

        environment = tmp_path / "environment"
        environment.write_text("", encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")
        for old, new, count in (
            ('TOKEN="__TOKEN__"', 'TOKEN="tok"', 1),
            ('KUMA_URL="__KUMA_URL__"', f'KUMA_URL="{KUMA}"', 1),
            ('FILLING_TOKEN=""', f'FILLING_TOKEN="{filling_token}"', 1),
            ("/etc/environment", str(environment), 2),
        ):
            assert script.count(old) == count, f"doctor-push.sh no longer has {old!r}"
            script = script.replace(old, new)
        self.script = tmp_path / "doctor-push.sh"
        self.script.write_text(script, encoding="utf-8")

    def run(self, rc, report=None, *, helper_root=REPO_ROOT, running=5, status=""):
        self.report.write_text(
            "" if report is None else json.dumps(report.to_dict()), encoding="utf-8"
        )
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(self.home),
            "MASCOPE_PATH": str(helper_root),
            "CURL_LOG": str(self.curl_log),
            "FAKE_REPORT": str(self.report),
            "FAKE_RC": str(rc),
            "FAKE_DOCKER_RUNNING": str(running),
            "FAKE_DOCKER_STATUS": status,
        }
        result = run(
            [BASH, str(self.script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return self.pushes()

    def pushes(self):
        """{monitor token: (status, msg)} for each push made."""
        if not self.curl_log.exists():
            return {}
        pushed = {}
        for url in self.curl_log.read_text(encoding="utf-8").split():
            parts = urlsplit(url)
            assert f"{parts.scheme}://{parts.netloc}" == KUMA
            token = parts.path.rsplit("/", 1)[-1]
            query = parse_qs(parts.query)
            assert query["ping"] == ["37"]
            pushed[token] = (query["status"][0], query["msg"][0])
        return pushed


@pytest.fixture
def push(tmp_path):
    return DoctorPush(tmp_path)


TREND = "disk37pct_docker37pct_state_fill2.0GiBperday_full200d"


def test_a_healthy_stack_is_up_on_both_monitors(push):
    assert push.run(0, _report()) == {
        "tok": ("up", f"OK_{TREND}"),
        "ftok": ("up", f"OK_{TREND}"),
    }


def test_a_filling_disk_downs_only_the_warning_monitor(push):
    # Exit 3 on the main monitor would mask every later failure on the server:
    # Kuma notifies on a change of state, and down -> down sends nothing.
    assert push.run(doctor.EXIT_FILLING, _report(filling=True)) == {
        "tok": ("up", f"doctor_disk_filling_{TREND}"),
        "ftok": ("down", f"disk_filling_{TREND}"),
    }


def test_an_unhealthy_stack_with_a_filling_disk_downs_both(push):
    # The exit code is 1 either way; the warning is read out of the JSON.
    report = _report(filling=True, container="exited")
    assert push.run(1, report) == {
        "tok": ("down", f"doctor_unhealthy_{TREND}"),
        "ftok": ("down", f"disk_filling_{TREND}"),
    }


def test_a_disk_below_its_floor_keeps_the_warning_monitor_down(push):
    # Crossing the floor must not read as the warning recovering.
    assert push.run(1, _report(low=True)) == {
        "tok": ("down", f"doctor_unhealthy_{TREND}"),
        "ftok": ("down", f"disk_filling_{TREND}"),
    }


def test_an_unhealthy_stack_alone_leaves_the_warning_monitor_up(push):
    assert push.run(1, _report(container="exited")) == {
        "tok": ("down", f"doctor_unhealthy_{TREND}"),
        "ftok": ("up", f"OK_{TREND}"),
    }


def test_exit_3_downs_the_warning_monitor_without_the_helper(push, tmp_path):
    # A checkout without doctor_trend.py: no suffix, and the exit code decides.
    assert push.run(
        doctor.EXIT_FILLING, _report(filling=True), helper_root=tmp_path
    ) == {
        "tok": ("up", "doctor_disk_filling_disk37pct_docker37pct"),
        "ftok": ("down", "disk_filling_disk37pct_docker37pct"),
    }


@pytest.mark.parametrize(
    "running, status, expected",
    [
        (5, "mascope_prod_backend Up 3 hours (healthy)", ("up", "OK_")),
        (
            5,
            "mascope_prod_backend Exited (1) 2 minutes ago",
            ("down", "containers_5_running_"),
        ),
        (2, "", ("down", "containers_2_running_")),
    ],
    ids=["healthy", "exited", "too few"],
)
def test_a_cli_without_doctor_falls_back_to_container_health(
    push, running, status, expected
):
    pushed = push.run(2, None, running=running, status=status)
    assert pushed["tok"] == (expected[0], f"{expected[1]}disk37pct_docker37pct")
    assert pushed["ftok"] == ("up", "OK_disk37pct_docker37pct")


def test_without_a_filling_token_only_the_main_monitor_is_pushed(tmp_path):
    push = DoctorPush(tmp_path, filling_token="")
    assert push.run(doctor.EXIT_FILLING, _report(filling=True)) == {
        "tok": ("up", f"doctor_disk_filling_{TREND}"),
    }
