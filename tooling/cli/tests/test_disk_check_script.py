"""
Tests for tooling/disk-check.sh, the bash disk-space monitor the systemd timer
runs every 15 minutes.

The script is run as the timer runs it, with ``df``, ``date`` and ``curl``
replaced by stubs on PATH: ``df`` reports a filesystem sized from env vars (a
second one, "/data", for paths under FAKE_VOLUME_DIR), ``date`` returns a fixed
clock, and ``curl`` records the URL it was asked to ping instead of sending
it. The history file it keeps is written into a temp MASCOPE_PATH. One test
keeps the real ``df``, to pin the mount name the doctor joins the history on.
Skipped where there is no bash 4 to run it (the script targets Linux servers;
CI runs these on Ubuntu).
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
URL = "https://hc.example/floor"
FILLING_URL = "https://hc.example/filling"


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
    # The path is the last argument.
    "df": """#!/bin/sh
for path; do :; done
case "$path" in /*) ;; *) path="$(pwd)/$path" ;; esac  # as df resolves "."
echo "Filesystem 1-blocks Used Available Capacity Mounted on"
if [ -n "$FAKE_VOLUME_DIR" ]; then
    case "$path" in
        "$FAKE_VOLUME_DIR"*)
            echo "/dev/vol $FAKE_VOLUME_SIZE 0 $FAKE_VOLUME_AVAIL 0% /data"
            exit 0
            ;;
    esac
fi
echo "/dev/test $FAKE_SIZE 0 $FAKE_AVAIL 0% /"
""",
    "date": '#!/bin/sh\necho "$FAKE_NOW"\n',
    # The URL is the last argument; a --data-raw body goes to a file of its own.
    "curl": """#!/bin/sh
while [ $# -gt 1 ]; do
    if [ "$1" = "--data-raw" ]; then printf '%s' "$2" >>"$CURL_LOG.body"; shift; fi
    shift
done
echo "$1" >>"$CURL_LOG"
""",
}


class DiskCheck:
    """Runs disk-check.sh against a fake filesystem and clock."""

    def __init__(self, tmp_path: Path, stub_df: bool = True):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in _STUBS.items():
            if name == "df" and not stub_df:
                continue
            stub = self.bin / name
            stub.write_text(body, encoding="utf-8")
            stub.chmod(0o755)
        self.curl_log = tmp_path / "curl.log"
        self.history = self.home / ".runtime" / "disk-check" / "history.tsv"

    def seed(self, samples, mount="/"):
        """Write history lines from (epoch, avail GiB) on ``mount``."""
        self.history.parent.mkdir(parents=True, exist_ok=True)
        with self.history.open("a", encoding="utf-8") as f:
            for t, avail in samples:
                f.write(f"{int(t)}\t{int(avail * GIB)}\t{SIZE}\t{mount}\n")

    def run(self, avail_gb, now=NOW, cwd=None, **config):
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
            "FAKE_VOLUME_DIR": "",
            **{k: str(v) for k, v in config.items()},
        }
        return subprocess.run(
            [BASH, str(SCRIPT)],
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def pings(self):
        if not self.curl_log.exists():
            return []
        return self.curl_log.read_text(encoding="utf-8").split()

    def log_body(self):
        body = Path(f"{self.curl_log}.body")
        return body.read_text(encoding="utf-8") if body.exists() else ""


@pytest.fixture
def check(tmp_path):
    return DiskCheck(tmp_path)


def _filling(days, per_day, avail_now):
    """Hourly (epoch, avail GiB) samples up to an hour before NOW."""
    hours = int(days * 24)
    return [(NOW - h * 3600, avail_now + per_day * h / 24) for h in range(hours, 0, -1)]


def _out(result):
    return result.stdout + result.stderr


# --- the projection ---


def test_first_run_records_a_sample_and_has_no_rate_yet(check):
    result = check.run(avail_gb=400)
    assert result.returncode == 0, _out(result)
    assert "has 400.0 GiB / 40% free; no rate yet (0h of history" in result.stdout
    assert check.history.read_text() == f"{NOW}\t{400 * GIB}\t{SIZE}\t/\n"
    assert check.pings() == [URL]


def test_a_fast_fill_warns_weeks_before_the_floor(check):
    # 19 GiB/day with 400 GiB free: the 10% (100 GiB) floor is ~16 days out.
    check.seed(_filling(10, 19, avail_now=400))
    result = check.run(avail_gb=400, HEALTHCHECK_FILLING_URL=FILLING_URL)
    assert result.returncode == 1, _out(result)
    line = next(ln for ln in result.stdout.splitlines() if "FILLING:" in ln)
    assert "rate +19.0 GiB/day (7d), +19.0 GiB/day (10d)" in line
    assert "100.0 GiB floor in ~16 days, full in ~21 days" in line
    assert "WARNING: 1 filesystem(s) projected to reach the free-space floor" in (
        result.stdout
    )
    # The floor check stays up: only the filling check goes down.
    assert check.pings() == [URL, f"{FILLING_URL}/fail"]


def test_a_slow_fill_is_ok_and_logs_the_projection(check):
    check.seed(_filling(10, 2, avail_now=400))
    result = check.run(avail_gb=400, HEALTHCHECK_FILLING_URL=FILLING_URL)
    assert result.returncode == 0, _out(result)
    assert "ok:  state (.runtime) (/) has 400.0 GiB / 40% free; rate +2.0" in (
        result.stdout
    )
    assert "100.0 GiB floor in ~150 days, full in ~200 days" in result.stdout
    assert check.pings() == [URL, FILLING_URL]


def test_without_a_filling_check_the_projection_is_logged_not_failed(check):
    # The floor check must keep meaning "below the floor": the projection goes
    # to its event log, which neither changes its state nor notifies.
    check.seed(_filling(10, 19, avail_now=400))
    result = check.run(avail_gb=400)
    assert result.returncode == 1, _out(result)
    assert check.pings() == [URL, f"{URL}/log"]
    assert check.log_body().startswith("FILLING: state (.runtime) (/)")


def test_crossing_the_floor_fails_the_floor_check_while_filling_stays_its_own(
    check, tmp_path
):
    # The state filesystem is below the floor while a filestore volume is
    # projected to reach its own: one check down for each.
    volume = tmp_path / "volume"
    (volume / "filestore").mkdir(parents=True)
    env_dir = check.home / ".runtime" / "env" / "prod"
    env_dir.mkdir(parents=True)
    (env_dir / "filestore").symlink_to(volume / "filestore")
    check.seed(_filling(10, 19, avail_now=400), mount="/data")

    result = check.run(
        avail_gb=50,
        HEALTHCHECK_FILLING_URL=FILLING_URL,
        FAKE_VOLUME_DIR=volume.resolve(),
        FAKE_VOLUME_SIZE=SIZE,
        FAKE_VOLUME_AVAIL=400 * GIB,
    )
    assert result.returncode == 1, _out(result)
    assert "LOW: state (.runtime) (/) has 50.0 GiB" in result.stdout
    assert "FILLING: filestore (prod) (/data) has 400.0 GiB" in result.stdout
    assert check.pings() == [f"{URL}/fail", f"{FILLING_URL}/fail"]


def test_a_filling_disk_that_crosses_the_floor_keeps_the_filling_check_down(check):
    # Below the floor a filesystem is LOW, not FILLING; the filling check must
    # not read that as recovered, nor turn down again if a cleanup lifts the
    # disk just above the floor.
    check.seed(_filling(10, 19, avail_now=120))
    first = check.run(avail_gb=120, HEALTHCHECK_FILLING_URL=FILLING_URL)
    assert "FILLING: state (.runtime) (/) has 120.0 GiB" in first.stdout, _out(first)
    second = check.run(
        avail_gb=95, now=NOW + 3 * 3600, HEALTHCHECK_FILLING_URL=FILLING_URL
    )
    assert "LOW: state (.runtime) (/) has 95.0 GiB" in second.stdout, _out(second)
    assert check.pings() == [
        URL,
        f"{FILLING_URL}/fail",
        f"{URL}/fail",
        f"{FILLING_URL}/fail",
    ]


def test_the_projection_alert_can_be_turned_off(check):
    check.seed(_filling(10, 19, avail_now=400))
    result = check.run(avail_gb=400, MIN_DAYS_TO_FLOOR=0)
    assert result.returncode == 0, _out(result)
    assert "floor in ~16 days" in result.stdout
    assert check.pings() == [URL]


def test_a_quiet_week_does_not_hide_a_disk_that_fills_in_bursts(check):
    # Three weeks of a running instrument, then a week idle.
    check.seed((t - 7 * DAY, a) for t, a in _filling(21, 19, avail_now=300))
    check.seed((NOW - h * 3600, 300) for h in range(7 * 24, 0, -1))
    result = check.run(avail_gb=300)
    assert result.returncode == 1, _out(result)
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
    assert "ALERT: 1 filesystem(s) below the free-space floor" in result.stdout
    assert check.pings() == [f"{URL}/fail"]


# --- what is measured ---


def test_a_filestore_on_its_own_volume_is_measured(check, tmp_path):
    volume = tmp_path / "volume"
    (volume / "filestore").mkdir(parents=True)
    env_dir = check.home / ".runtime" / "env" / "prod"
    env_dir.mkdir(parents=True)
    (env_dir / "filestore").symlink_to(volume / "filestore")

    result = check.run(
        avail_gb=400,
        FAKE_VOLUME_DIR=volume.resolve(),
        FAKE_VOLUME_SIZE=SIZE,
        FAKE_VOLUME_AVAIL=20 * GIB,
    )
    assert result.returncode == 1, _out(result)
    assert "ok:  state (.runtime) (/) has 400.0 GiB" in result.stdout
    assert "LOW: filestore (prod) (/data) has 20.0 GiB / 2% free" in result.stdout
    mounts = [ln.split("\t")[3] for ln in check.history.read_text().splitlines()]
    assert mounts == ["/", "/data"]


@pytest.mark.parametrize("mount_point_exists", [True, False], ids=["dir", "none"])
def test_an_unmounted_filestore_volume_is_never_measured_from_the_cwd(
    check, tmp_path, mount_point_exists
):
    # The filestore links into a volume that is not mounted. With its mount
    # point present it is measured there, where its writes would land; with
    # nothing there it falls back to the link itself (the state filesystem).
    # Either way, never the directory the run happens to start in - which
    # here lies under the fake volume, so measuring it would report /data.
    vol = tmp_path / "vol"
    (vol / "cwd").mkdir(parents=True)
    if mount_point_exists:
        (vol / "mnt").mkdir()
    env_dir = check.home / ".runtime" / "env" / "prod"
    env_dir.mkdir(parents=True)
    (env_dir / "filestore").symlink_to(vol / "mnt" / "filestore")

    result = check.run(
        avail_gb=400,
        cwd=vol / "cwd",
        FAKE_VOLUME_DIR=vol.resolve(),
        FAKE_VOLUME_SIZE=SIZE,
        FAKE_VOLUME_AVAIL=20 * GIB,
    )
    if mount_point_exists:
        assert "LOW: filestore (prod) (/data)" in result.stdout, _out(result)
    else:
        assert result.returncode == 0, _out(result)
        assert "/data" not in result.stdout


def test_a_filestore_on_the_state_filesystem_is_one_report(check):
    (check.home / ".runtime" / "env" / "prod" / "filestore").mkdir(parents=True)
    result = check.run(avail_gb=400)
    assert result.returncode == 0, _out(result)
    assert "filestore" not in result.stdout
    assert len(check.history.read_text().splitlines()) == 1


# --- configuration ---


@pytest.mark.parametrize("value", ["thirty", "30d", "-5", ""])
def test_a_threshold_that_is_not_a_number_falls_back_to_its_default(check, value):
    # awk would compare these as strings: "thirty" warned about every filling
    # disk, "30d" about almost none. A 150-day projection must stay quiet.
    check.seed(_filling(10, 2, avail_now=400))
    result = check.run(avail_gb=400, MIN_DAYS_TO_FLOOR=value)
    assert result.returncode == 0, _out(result)
    if value:
        assert f"warn: MIN_DAYS_TO_FLOOR={value} is not a number; using 30" in (
            result.stdout
        )
    assert "floor in ~150 days" in result.stdout


def test_the_floors_are_validated_too(check):
    result = check.run(avail_gb=400, MIN_FREE_GB="lots", MIN_FREE_PCT="50%")
    assert "warn: MIN_FREE_GB=lots is not a number; using 10" in result.stdout
    assert "warn: MIN_FREE_PCT=50% is not a number; using 10" in result.stdout
    assert result.returncode == 0, _out(result)


# --- the history file ---


def test_history_is_trimmed_to_sixty_days(check):
    check.seed([(NOW - 70 * DAY, 500), (NOW - 59 * DAY, 450), (NOW - DAY, 400)])
    check.history.chmod(0o644)
    check.run(avail_gb=400)
    stamps = [int(ln.split("\t")[0]) for ln in check.history.read_text().splitlines()]
    assert stamps == [NOW - 59 * DAY, NOW - DAY, NOW]
    # The rewrite keeps the file's mode and leaves no temp file behind.
    assert check.history.stat().st_mode & 0o777 == 0o644
    left = {p.name for p in check.history.parent.iterdir()} - {".lock"}
    assert left == {"history.tsv"}


def test_history_is_not_rewritten_on_every_run(check):
    # The oldest sample is past the cutoff, but by less than the day of slack.
    check.seed([(NOW - 60 * DAY - 3600, 500), (NOW - DAY, 400)])
    check.run(avail_gb=400)
    assert len(check.history.read_text().splitlines()) == 3


@pytest.mark.skipif(shutil.which("flock") is None, reason="needs flock(1)")
def test_a_run_that_cannot_lock_the_history_still_checks(check):
    # Another run holds the history: this one measures, alerts and pings as
    # usual, and leaves the history to the run that holds it.
    import fcntl  # POSIX only, like this whole module

    check.seed([(NOW - DAY, 400)])
    with open(check.history.parent / ".lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = check.run(avail_gb=400, HISTORY_LOCK_WAIT="0.2")
    assert result.returncode == 0, _out(result)
    assert "another disk check holds the history" in result.stdout
    # The history is still read, only not written.
    assert re.search(r"rate [+-]0\.0 GiB/day \(1d\)", result.stdout)
    assert len(check.history.read_text().splitlines()) == 1
    assert check.pings() == [URL]


def test_an_unwritable_history_never_breaks_the_check(check):
    # A file where the history directory should be: mkdir fails, as it would
    # on a read-only or full filesystem.
    (check.home / ".runtime").mkdir()
    (check.home / ".runtime" / "disk-check").write_text("in the way")
    result = check.run(avail_gb=400)
    assert result.returncode == 0, _out(result)
    assert "warn: cannot create" in result.stdout
    assert "ok: all monitored filesystems have enough free space" in result.stdout
    assert check.pings() == [URL]


# --- agreement with the doctor ---


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


def test_the_doctor_finds_the_history_under_the_name_df_gives_it(tmp_path):
    # The real df: the script records df's mount name, and the doctor must look
    # the history up under that same name - its own mount lookup is the one
    # join between the two halves.
    check = DiskCheck(tmp_path, stub_df=False)
    result = check.run(avail_gb=0)  # FAKE_AVAIL is unused with the real df
    assert result.returncode in (0, 1), _out(result)
    recorded = check.history.read_text().splitlines()[0].split("\t")[3]
    assert doctor._mount_point(check.home) == recorded

    check.seed([(NOW - 2 * DAY, 1), (NOW - DAY, 1)], mount=recorded)
    disk = doctor.disk_usage("state", check.home, 5, history=check.history, now=NOW)
    assert disk.mount == recorded and disk.rates
