"""
Tests for tooling/disk-check.sh, the bash disk-space monitor the systemd timer
runs every 15 minutes.

The script is run as the timer runs it, with ``df``, ``date`` and ``curl``
replaced by stubs on PATH: ``df`` reports one filesystem sized from env vars,
``date`` returns a fixed clock, and ``curl`` records the URL it was asked to
ping instead of sending it. The history file it keeps is written into a temp
MASCOPE_PATH. Skipped where there is no bash 4 to run it (the script targets
Linux servers; CI runs these on Ubuntu).
"""

import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


doctor = importlib.import_module("mascope_cli.cmd.prod.doctor")

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "disk-check.sh"

GIB = 1024**3
DAY = 86400
NOW = 1_800_000_000
SIZE = 1000 * GIB
URL = "https://hc.example/check"


def _bash() -> str | None:
    """A bash new enough for the script's associative array, or None."""
    if sys.platform == "win32":
        return None
    bash = shutil.which("bash")
    if bash is None:
        return None
    probe = subprocess.run(
        [bash, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True
    )
    major = probe.stdout.strip()
    return bash if major.isdigit() and int(major) >= 4 else None


BASH = _bash()

pytestmark = [
    pytest.mark.skipif(not SCRIPT.is_file(), reason="tooling/disk-check.sh absent"),
    pytest.mark.skipif(BASH is None, reason="needs bash >= 4 on a POSIX host"),
]

_STUBS = {
    "df": (
        "#!/bin/sh\n"
        'echo "Filesystem 1-blocks Used Available Capacity Mounted on"\n'
        'echo "/dev/test $FAKE_SIZE 0 $FAKE_AVAIL 0% /"\n'
    ),
    "date": '#!/bin/sh\necho "$FAKE_NOW"\n',
    # The URL is the last argument.
    "curl": '#!/bin/sh\nfor arg; do url=$arg; done\necho "$url" >>"$CURL_LOG"\n',
}


class DiskCheck:
    """Runs disk-check.sh against a fake filesystem and clock."""

    def __init__(self, tmp_path: Path):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in _STUBS.items():
            stub = self.bin / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)
        self.curl_log = tmp_path / "curl.log"
        self.history = self.home / ".runtime" / "disk-check" / "history.tsv"

    def seed(self, samples):
        """Write history lines from (epoch, avail GiB) on the stub's mount."""
        self.history.parent.mkdir(parents=True, exist_ok=True)
        with self.history.open("a", encoding="utf-8") as f:
            for t, avail in samples:
                f.write(f"{int(t)}\t{int(avail * GIB)}\t{SIZE}\t/\n")

    def run(self, avail_gb, now=NOW, **config):
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.home),
            "MASCOPE_PATH": str(self.home),
            "MASCOPE_DISK_CHECK_ENV": str(self.home / "no-such.env"),
            "DOCKER_ROOT": str(self.home),
            "HEALTHCHECK_URL": URL,
            "CURL_LOG": str(self.curl_log),
            "FAKE_NOW": str(now),
            "FAKE_SIZE": str(SIZE),
            "FAKE_AVAIL": str(int(avail_gb * GIB)),
            **{k: str(v) for k, v in config.items()},
        }
        return subprocess.run(
            [BASH, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
        )

    def pings(self):
        if not self.curl_log.exists():
            return []
        return self.curl_log.read_text(encoding="utf-8").split()


@pytest.fixture
def check(tmp_path):
    return DiskCheck(tmp_path)


def _filling(days, per_day, avail_now):
    """Hourly (epoch, avail GiB) samples up to an hour before NOW."""
    hours = int(days * 24)
    return [(NOW - h * 3600, avail_now + per_day * h / 24) for h in range(hours, 0, -1)]


def test_first_run_records_a_sample_and_has_no_rate_yet(check):
    result = check.run(avail_gb=400)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "has 400.0 GiB / 40% free; no rate yet (0h of history" in result.stdout
    assert check.history.read_text() == f"{NOW}\t{400 * GIB}\t{SIZE}\t/\n"
    assert check.pings() == [URL]


def test_a_fast_fill_alerts_weeks_before_the_floor(check):
    # 19 GiB/day with 400 GiB free: the 10% (100 GiB) floor is ~16 days out.
    check.seed(_filling(10, 19, avail_now=400))
    result = check.run(avail_gb=400)
    assert result.returncode == 1, result.stdout + result.stderr
    line = next(ln for ln in result.stdout.splitlines() if "FILLING:" in ln)
    assert "rate +19.0 GiB/day (7d), +19.0 GiB/day (10d)" in line
    assert "100.0 GiB floor in ~16 days, full in ~21 days" in line
    assert "projected to reach the floor within 30 days" in result.stdout
    assert check.pings() == [f"{URL}/fail"]


def test_a_slow_fill_is_ok_and_logs_the_projection(check):
    check.seed(_filling(10, 2, avail_now=400))
    result = check.run(avail_gb=400)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok:  state (.runtime) (/) has 400.0 GiB / 40% free; rate +2.0" in (
        result.stdout
    )
    assert "100.0 GiB floor in ~150 days, full in ~200 days" in result.stdout
    assert check.pings() == [URL]


def test_the_projection_alert_can_be_turned_off(check):
    check.seed(_filling(10, 19, avail_now=400))
    result = check.run(avail_gb=400, MIN_DAYS_TO_FLOOR=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "floor in ~16 days" in result.stdout
    assert check.pings() == [URL]


def test_a_quiet_week_does_not_hide_a_disk_that_fills_in_bursts(check):
    # Three weeks of a running instrument, then a week idle.
    check.seed((t - 7 * DAY, a) for t, a in _filling(21, 19, avail_now=300))
    check.seed((NOW - h * 3600, 300) for h in range(7 * 24, 0, -1))
    result = check.run(avail_gb=300)
    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(
        r"rate [+-]0\.0 GiB/day \(7d\), \+\d+\.\d GiB/day \(28d\)", result.stdout
    )


def test_below_the_floor_is_low_whatever_the_rate(check):
    check.seed(_filling(3, 1, avail_now=50))
    result = check.run(avail_gb=50)
    assert result.returncode == 1
    assert "LOW: state (.runtime) (/) has 50.0 GiB / 5% free (floor 10 GiB / 10%)" in (
        result.stdout
    )
    assert "full in ~50 days" in result.stdout
    assert "1 filesystem(s) below the free-space floor" in result.stdout
    assert check.pings() == [f"{URL}/fail"]


def test_history_is_trimmed_to_sixty_days(check):
    check.seed([(NOW - 70 * DAY, 500), (NOW - 59 * DAY, 450), (NOW - DAY, 400)])
    check.run(avail_gb=400)
    stamps = [int(ln.split("\t")[0]) for ln in check.history.read_text().splitlines()]
    assert stamps == [NOW - 59 * DAY, NOW - DAY, NOW]


def test_history_is_not_rewritten_on_every_run(check):
    # The oldest sample is past the cutoff, but by less than the day of slack.
    check.seed([(NOW - 60 * DAY - 3600, 500), (NOW - DAY, 400)])
    check.run(avail_gb=400)
    assert len(check.history.read_text().splitlines()) == 3


def test_an_unwritable_history_never_breaks_the_check(check):
    # A file where the history directory should be: mkdir fails, as it would
    # on a read-only or full filesystem.
    (check.home / ".runtime").mkdir()
    (check.home / ".runtime" / "disk-check").write_text("in the way")
    result = check.run(avail_gb=400)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warn: cannot create" in result.stdout
    assert "ok: all monitored filesystems have enough free space" in result.stdout
    assert check.pings() == [URL]


def test_the_doctor_reads_the_same_rate_from_the_same_history(check):
    # An uneven fill - bursts, a cleanup, noise - so agreement is not a
    # coincidence of a straight line.
    samples, avail = [], 600.0
    for h in range(20 * 24, 0, -1):
        avail -= 2.5 if (h // 24) % 3 == 0 else 0.1 * ((h * 7919) % 5)
        if h == 9 * 24:
            avail += 120
        samples.append((NOW - h * 3600, avail))
    check.seed(samples)
    result = check.run(avail_gb=avail)
    bash_rates = [float(r) for r in re.findall(r"([+-]\d+\.\d) GiB/day", result.stdout)]

    history = doctor.read_disk_history(check.history, "/")[:-1]  # drop this run
    rates = doctor.fill_rates(history, NOW, (SIZE / GIB) - avail)
    assert len(bash_rates) == len(rates) == 2
    for bash_rate, rate in zip(bash_rates, rates):
        assert bash_rate == pytest.approx(rate.gb_per_day, abs=0.051)
