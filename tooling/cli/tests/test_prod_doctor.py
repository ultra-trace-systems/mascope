"""
Tests for `mascope prod doctor` and its gather module.

The gather functions are tested directly with the docker/subprocess seam and
filesystem stubbed; the command wiring is tested through main.py, asserting the
exit code tracks the report's health and that --json emits the structured form.
"""

import datetime
import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import mascope_cli.cmd.prod.auto_update as au


# The prod package does `from .main import *`, so the `doctor` command function
# shadows the `doctor` submodule in the package namespace. Import both modules
# explicitly (as test_prod_update does for `main`) to get the modules, not the
# re-exported function.
doctor = importlib.import_module("mascope_cli.cmd.prod.doctor")
prod_main = importlib.import_module("mascope_cli.cmd.prod.main")


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["docker"], returncode, stdout, stderr)


# --- container_health ---


def test_container_health_running_and_healthy(monkeypatch):
    monkeypatch.setattr(
        doctor, "_run", lambda cmd, **k: _completed(stdout="running|healthy")
    )
    c = doctor.container_health("backend", "mascope_prod_backend")
    assert c.state == "running" and c.health == "healthy" and c.ok


def test_container_health_running_no_healthcheck_is_ok(monkeypatch):
    monkeypatch.setattr(
        doctor, "_run", lambda cmd, **k: _completed(stdout="running|none")
    )
    c = doctor.container_health("redis", "mascope_prod_redis")
    assert c.health is None and c.ok


def test_container_health_exited_not_ok(monkeypatch):
    monkeypatch.setattr(
        doctor, "_run", lambda cmd, **k: _completed(stdout="exited|none")
    )
    c = doctor.container_health("backend", "mascope_prod_backend")
    assert c.state == "exited" and not c.ok


def test_container_health_absent_when_inspect_fails(monkeypatch):
    monkeypatch.setattr(doctor, "_run", lambda cmd, **k: _completed(returncode=1))
    c = doctor.container_health("backend", "missing")
    assert c.state == "absent" and not c.ok


def test_container_health_unhealthy_not_ok(monkeypatch):
    monkeypatch.setattr(
        doctor, "_run", lambda cmd, **k: _completed(stdout="running|unhealthy")
    )
    assert not doctor.container_health("backend", "b").ok


# --- version_status ---


def test_image_tag_parsing():
    assert doctor._image_tag("ghcr.io/org/mascope/backend:v1.6.1") == "v1.6.1"
    assert doctor._image_tag("registry:5000/org/backend:latest") == "latest"
    assert doctor._image_tag("backend") is None
    # digest-pinned references carry no tag
    assert doctor._image_tag("ghcr.io/org/backend@sha256:abc123") is None


def test_version_status_no_drift(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "_run",
        lambda cmd, **k: _completed(stdout="ghcr.io/org/mascope/backend:v1.6.1\n"),
    )
    v = doctor.version_status("v1.6.1", [("backend", "mascope_prod_backend")])
    assert v.running[0].tag == "v1.6.1"
    assert v.drift is False


def test_version_status_detects_the_reboot_channel_switch(monkeypatch):
    # The checkout is at a release tag but the containers came back on the
    # rolling `latest` build - the drift this check exists for.
    monkeypatch.setattr(
        doctor,
        "_run",
        lambda cmd, **k: _completed(stdout="ghcr.io/org/mascope/frontend:latest\n"),
    )
    v = doctor.version_status("v1.6.1", [("frontend", "mascope_prod_frontend")])
    assert v.drift is True


def test_version_status_absent_container_is_not_drift(monkeypatch):
    monkeypatch.setattr(doctor, "_run", lambda cmd, **k: _completed(returncode=1))
    v = doctor.version_status("v1.6.1", [("backend", "missing")])
    assert v.running[0].tag is None and v.drift is False


def test_version_status_without_an_expected_tag_is_not_drift(monkeypatch):
    monkeypatch.setattr(
        doctor, "_run", lambda cmd, **k: _completed(stdout="org/backend:v1.6.1")
    )
    assert doctor.version_status(None, [("backend", "b")]).drift is False


# --- disk_usage ---


def test_disk_usage_low_flag(tmp_path):
    huge = doctor.disk_usage("state", tmp_path, min_free_gb=10**9)
    assert huge.low is True
    ample = doctor.disk_usage("state", tmp_path, min_free_gb=0)
    assert ample.low is False
    assert ample.free_gb is not None and 0 <= ample.free_pct <= 100


def test_disk_usage_walks_up_to_existing(tmp_path):
    missing = tmp_path / "no" / "such" / "dir"
    d = doctor.disk_usage("docker", missing, min_free_gb=0)
    assert d.free_gb is not None  # resolved to an existing ancestor


# --- fill rates ---

GIB = 1024**3
NOW = 1_800_000_000.0
DAY = 86400


def _history(path, mount, samples):
    """Write disk-check history lines from (epoch, avail GiB, size GiB)."""
    with path.open("a", encoding="utf-8") as f:
        for t, avail, size in samples:
            f.write(f"{int(t)}\t{int(avail * GIB)}\t{int(size * GIB)}\t{mount}\n")


def _filling(days, per_day, start_avail=400.0, size=1000.0):
    """Hourly (epoch, avail GiB, size GiB) samples up to an hour before NOW."""
    hours = int(days * 24)
    return [
        (NOW - (hours - i) * 3600, start_avail - per_day * i / 24, size)
        for i in range(hours)
    ]


def _used(history):
    return [(t, size - avail) for t, avail, size in history]


def test_read_disk_history_filters_by_mount_and_skips_garbage(tmp_path):
    history = tmp_path / "history.tsv"
    _history(history, "/", [(NOW - 60, 300, 1000)])
    _history(history, "/data", [(NOW - 60, 10, 50)])
    with history.open("a", encoding="utf-8") as f:
        f.write("not a sample\n")
        f.write("1799999999\t-1\t5\t/\n")
        f.write("1799999999\t12\n")
    # used = size - avail, in GiB
    assert doctor.read_disk_history(history, "/") == [(NOW - 60, 700.0)]
    assert doctor.read_disk_history(tmp_path / "missing.tsv", "/") == []


def test_fill_rates_need_a_day_of_history():
    assert doctor.fill_rates(_used(_filling(0.5, 19)), NOW, 600.0) == []


def test_fill_rates_steady_fill_over_both_windows():
    rates = doctor.fill_rates(_used(_filling(12, 19)), NOW, 1000 - (400 - 19 * 12))
    assert [r.window_days for r in rates] == [7, 30]
    assert all(r.gb_per_day == pytest.approx(19, abs=0.01) for r in rates)
    # The 30-day window holds only the 12 days there are, and says so.
    assert round(rates[1].span_days) == 12


def test_fill_rates_skip_the_30_day_window_until_it_adds_history():
    rates = doctor.fill_rates(_used(_filling(5, 19)), NOW, 1000 - (400 - 19 * 5))
    assert [r.window_days for r in rates] == [7]


def test_fill_rates_ride_out_a_transient_current_reading():
    # A 30 GiB dump being written at the moment of the reading: a
    # first-to-last difference would add 30 / 7 = 4.3 GiB/day to the pace.
    current_used = 1000 - (400 - 19 * 7) + 30
    rate = doctor.fill_rates(_used(_filling(7, 19)), NOW, current_used)[0]
    assert rate.gb_per_day == pytest.approx(19, abs=0.5)


def test_fill_rates_ignore_a_grown_filesystem():
    # 500 GiB added to the filesystem five days ago: avail jumps, used does not.
    grown = [
        (t, a + 500, size + 500) if t > NOW - 5 * DAY else (t, a, size)
        for t, a, size in _filling(10, 19)
    ]
    rates = doctor.fill_rates(_used(grown), NOW, 1000 - (400 - 19 * 10))
    assert all(r.gb_per_day == pytest.approx(19, abs=0.01) for r in rates)


def test_project_uses_the_faster_rate_so_a_quiet_week_does_not_hide_bursts():
    # Three weeks of a running instrument, then a week idle.
    busy = [(t - 7 * DAY, a, s) for t, a, s in _filling(21, 19, start_avail=600)]
    idle_avail = busy[-1][1]
    idle = [(NOW - h * 3600, idle_avail, 1000.0) for h in range(7 * 24, 0, -1)]
    week, month = doctor.fill_rates(_used(busy + idle), NOW, 1000 - idle_avail)
    assert week.gb_per_day == pytest.approx(0, abs=0.01)
    assert month.gb_per_day > 5

    days_to_floor, days_to_full = doctor.project(idle_avail, 5.0, [week, month])
    assert days_to_full == pytest.approx(idle_avail / month.gb_per_day)
    assert days_to_floor == pytest.approx((idle_avail - 5) / month.gb_per_day)


def test_project_not_filling():
    rates = [doctor.FillRate(7, 7.0, -0.3), doctor.FillRate(30, 30.0, 0.0)]
    assert doctor.project(100.0, 5.0, rates) == (None, None)
    assert doctor.project(100.0, 5.0, []) == (None, None)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, doctor.DEFAULT_MIN_DAYS_TO_FLOOR),
        ("14", 14.0),
        ("0", 0.0),
        ("soon", doctor.DEFAULT_MIN_DAYS_TO_FLOOR),
        # float() takes these, and either would quietly switch the flag off
        # (nan) or on for every filling disk (inf).
        ("nan", doctor.DEFAULT_MIN_DAYS_TO_FLOOR),
        ("inf", doctor.DEFAULT_MIN_DAYS_TO_FLOOR),
        ("-3", doctor.DEFAULT_MIN_DAYS_TO_FLOOR),
    ],
)
def test_min_days_to_floor_from_the_environment(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR", raising=False)
    else:
        monkeypatch.setenv("MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR", raw)
    assert doctor.min_days_to_floor() == expected


def _fake_disk(monkeypatch, free_gb, total_gb=1000.0):
    """Stub the measured filesystem: ``free_gb`` of ``total_gb`` free, on /."""
    usage = shutil._ntuple_diskusage(
        int(total_gb * GIB), int((total_gb - free_gb) * GIB), int(free_gb * GIB)
    )
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda p: usage)
    monkeypatch.setattr(doctor, "_mount_point", lambda p: "/")


def test_disk_usage_flags_a_disk_filling_inside_the_horizon(tmp_path, monkeypatch):
    # 19 GiB/day with 100 GiB free: the 5 GiB floor is five days out.
    history = tmp_path / "history.tsv"
    _history(history, "/", _filling(10, 19, start_avail=100 + 19 * 10))
    _fake_disk(monkeypatch, free_gb=100.0)

    def usage(min_days):
        return doctor.disk_usage(
            "state", tmp_path, 5, history=history, min_days=min_days, now=NOW
        )

    d = usage(min_days=7)
    assert d.low is False and d.filling is True
    assert d.days_to_floor == pytest.approx(95 / 19, rel=0.01)
    assert d.days_to_full == pytest.approx(100 / 19, rel=0.01)
    assert usage(min_days=3).filling is False
    assert usage(min_days=0).filling is False


def test_disk_usage_without_history_has_no_rate(tmp_path, monkeypatch):
    _fake_disk(monkeypatch, free_gb=100.0)
    d = doctor.disk_usage(
        "state", tmp_path, 5, history=tmp_path / "none.tsv", min_days=7, now=NOW
    )
    assert d.rates == [] and d.days_to_full is None and d.filling is False


def test_disk_usage_low_is_not_also_filling(tmp_path, monkeypatch):
    history = tmp_path / "history.tsv"
    _history(history, "/", _filling(10, 19, start_avail=3 + 19 * 10))
    _fake_disk(monkeypatch, free_gb=3.0)
    d = doctor.disk_usage("state", tmp_path, 5, history=history, min_days=7, now=NOW)
    assert d.low is True and d.filling is False
    assert d.days_to_floor == 0.0


def test_mount_point_is_the_name_df_gives(monkeypatch, tmp_path):
    # A bind mount shares its parent's device, so an ismount walk would pass
    # it by; disk-check.sh records df's name for it, and so must the doctor.
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return _completed(
            stdout="Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/sda1 100 50 50 50% /srv/bind\n"
        )

    monkeypatch.setattr(doctor, "_run", fake_run)
    assert doctor._mount_point(tmp_path) == "/srv/bind"
    assert seen == [["df", "-P", str(tmp_path)]]


@pytest.mark.parametrize(
    "answer",
    [FileNotFoundError("df"), _completed(returncode=1), _completed(stdout="garbage")],
    ids=["no df", "df fails", "unparsable"],
)
def test_mount_point_falls_back_to_the_nearest_mount(monkeypatch, tmp_path, answer):
    def fake_run(cmd, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(doctor, "_run", fake_run)
    mount = doctor._mount_point(tmp_path)
    assert os.path.ismount(mount)
    assert str(tmp_path.resolve()).startswith(mount.rstrip(os.sep))


# --- backup_status ---


def test_backup_status_empty(tmp_path):
    assert doctor.backup_status(tmp_path).count == 0
    assert doctor.backup_status(tmp_path / "missing").count == 0


def test_backup_status_counts_and_age(tmp_path):
    now = datetime.datetime(2026, 7, 11, 12, 0, 0)
    old = tmp_path / "mascope_20260711_000000_cron.dump"
    old.write_text("x")
    os.utime(old, (now.timestamp() - 3 * 3600, now.timestamp() - 3 * 3600))
    (tmp_path / "notes.txt").write_text("ignored")  # non-dump ignored

    status = doctor.backup_status(tmp_path, now=now)
    assert status.count == 1
    assert 2.9 < status.latest_age_hours < 3.1


# --- update_status ---


def test_update_status_reads_pending_and_log(tmp_path):
    root = str(tmp_path)
    au.save_pending(
        root, au.PendingUpdate("v1.4.0", "abc123def456", "2026-07-01T00:00:00")
    )
    au.record_status(root, "something happened")

    u = doctor.update_status(root)
    assert u.pending_version == "v1.4.0"
    assert u.pending_first_seen == "2026-07-01T00:00:00"
    assert "something happened" in u.last_status


def test_update_status_none_when_absent(tmp_path):
    u = doctor.update_status(str(tmp_path))
    assert u.pending_version is None and u.last_status is None


# --- image_footprint ---


def test_image_footprint_parses_images_row(monkeypatch):
    table = "Images|11|6.2GB|2.1GB (33%)\nContainers|5|0B|0B\nLocal Volumes|2|1GB|0B\n"
    monkeypatch.setattr(doctor, "_run", lambda cmd, **k: _completed(stdout=table))
    im = doctor.image_footprint()
    assert im.count == 11 and im.size == "6.2GB" and im.reclaimable == "2.1GB (33%)"


def test_image_footprint_none_when_docker_unavailable(monkeypatch):
    monkeypatch.setattr(doctor, "_run", lambda cmd, **k: _completed(returncode=1))
    im = doctor.image_footprint()
    assert im.count is None


# --- report / rendering ---


def _healthy_report():
    return doctor.Report(
        containers=[
            doctor.ContainerHealth("backend", "b", "running", "healthy"),
            doctor.ContainerHealth("redis", "r", "running", None),
        ],
        versions=doctor.VersionStatus(
            "v1.6.1", [doctor.RunningImage("backend", "v1.6.1")]
        ),
        disks=[doctor.DiskUsage("state", "/x", 142.0, 61.0, False)],
        updates=doctor.UpdateStatus(None, None, None),
        backups=doctor.BackupStatus(5, 8.0),
        images=doctor.ImageFootprint(11, "6.2GB", "2.1GB"),
    )


def test_report_ok_and_json_serialisable():
    report = _healthy_report()
    assert report.ok is True
    # round-trips through JSON (would raise on a non-serialisable field)
    dumped = json.loads(json.dumps(report.to_dict()))
    assert dumped["ok"] is True
    assert dumped["containers"][0]["label"] == "backend"


def test_report_not_ok_when_container_down():
    report = _healthy_report()
    report.containers[0].state = "exited"
    assert report.ok is False


def test_report_not_ok_when_disk_low():
    report = _healthy_report()
    report.disks[0].low = True
    assert report.ok is False


def test_a_filling_disk_is_a_warning_not_a_failure():
    # The fleet playbooks gate on the doctor's exit code: a disk projected to
    # fill within days must not read as an unhealthy stack.
    report = _healthy_report()
    report.disks[0].filling = True
    assert report.ok is True and report.filling is True
    assert report.exit_code == doctor.EXIT_FILLING == 3
    assert doctor.format_text(report).startswith("[WARNING]")
    assert report.to_dict()["filling"] is True


def test_an_unhealthy_stack_outranks_a_filling_disk():
    report = _healthy_report()
    report.disks[0].filling = True
    report.containers[0].state = "exited"
    assert report.exit_code == 1
    assert doctor.format_text(report).startswith("[ATTENTION]")


def test_report_not_ok_on_version_drift():
    report = _healthy_report()
    report.versions.running[0].tag = "latest"
    assert report.ok is False
    assert report.to_dict()["versions"]["drift"] is True


def test_format_text_contains_sections():
    text = doctor.format_text(_healthy_report())
    assert text.startswith("[OK]")
    for section in ("Stack", "Version", "Disk", "Updates", "Backups", "Images"):
        assert section in text


def test_format_text_shows_fill_rates_and_projection():
    report = _healthy_report()
    disk = report.disks[0]
    disk.rates = [doctor.FillRate(7, 6.99, 19.04), doctor.FillRate(30, 12.2, 17.5)]
    disk.days_to_full = 7.4
    disk.filling = True
    report.disks.append(doctor.DiskUsage("docker", "/y", 38.0, 40.0, False))

    text = doctor.format_text(report)
    assert text.startswith("[WARNING]")
    assert (
        "Disk     state 142 GiB / 61% free · +19.0 GiB/day (7d), "
        "+17.5 GiB/day (12d) · full in ~7 days  FILLING\n"
    ) in text
    # one row per filesystem, the second aligned under the first
    assert "\n         docker 38 GiB / 40% free\n" in text


def test_format_text_folds_paths_on_one_filesystem_into_one_row():
    report = _healthy_report()
    report.disks = [
        doctor.DiskUsage("state", "/m/.runtime", 142.0, 61.0, False, mount="/"),
        doctor.DiskUsage("filestore", "/data/fs", 12.0, 3.0, True, mount="/data"),
        doctor.DiskUsage("docker", "/var/lib/docker", 142.0, 61.0, False, mount="/"),
    ]
    text = doctor.format_text(report)
    assert "Disk     state, docker 142 GiB / 61% free\n" in text
    assert "\n         filestore 12 GiB / 3% free  LOW\n" in text


@pytest.mark.parametrize(
    "days, word",
    [
        (0.4, "<1 day"),
        (1.0, "~1 day"),
        (1.49, "~1 day"),
        (1.5, "~2 days"),
        (21.4, "~21 days"),
        (4000, ">10 years"),
    ],
)
def test_days_are_worded_as_disk_check_words_them(days, word):
    assert doctor._days_word(days) == word


def test_format_text_not_filling():
    report = _healthy_report()
    report.disks[0].rates = [doctor.FillRate(7, 7.0, -0.25)]
    assert "-0.2 GiB/day (7d) · not filling" in doctor.format_text(report)


def test_report_json_carries_fill_rates():
    report = _healthy_report()
    report.disks[0].rates = [doctor.FillRate(7, 7.0, 19.0)]
    report.disks[0].days_to_full = 7.4
    report.disks[0].mount = "/"
    disk = json.loads(json.dumps(report.to_dict()))["disks"][0]
    assert disk["rates"] == [{"window_days": 7, "span_days": 7.0, "gb_per_day": 19.0}]
    assert disk["days_to_full"] == 7.4 and disk["filling"] is False
    assert disk["mount"] == "/"


def test_format_text_spells_out_drift():
    report = _healthy_report()
    report.versions.running[0].tag = "latest"
    text = doctor.format_text(report)
    assert "DRIFT" in text and "v1.6.1" in text and "backend latest" in text


# --- command wiring ---


def test_doctor_command_exit_ok(monkeypatch, cli_runner):
    monkeypatch.setattr(
        prod_main.prod_doctor, "build_report", lambda **k: _healthy_report()
    )
    result = cli_runner.invoke(prod_main.prod_app, ["doctor"])
    assert result.exit_code == 0
    assert "[OK]" in result.stdout


def test_doctor_command_exit_attention(monkeypatch, cli_runner):
    report = _healthy_report()
    report.disks[0].low = True
    monkeypatch.setattr(prod_main.prod_doctor, "build_report", lambda **k: report)
    result = cli_runner.invoke(prod_main.prod_app, ["doctor"])
    assert result.exit_code == 1
    assert "ATTENTION" in result.stdout


def test_doctor_command_exits_3_for_a_filling_disk(monkeypatch, cli_runner):
    report = _healthy_report()
    report.disks[0].filling = True
    monkeypatch.setattr(prod_main.prod_doctor, "build_report", lambda **k: report)
    result = cli_runner.invoke(prod_main.prod_app, ["doctor"])
    assert result.exit_code == 3
    assert "[WARNING]" in result.stdout and "FILLING" in result.stdout


def test_doctor_command_reads_the_disk_check_history(
    monkeypatch, cli_runner, mascope_home
):
    seen = {}

    def fake_build_report(**kwargs):
        seen.update(kwargs)
        return _healthy_report()

    monkeypatch.setenv("MASCOPE_DOCTOR_MIN_DAYS_TO_FLOOR", "3")
    monkeypatch.setattr(prod_main.prod_doctor, "build_report", fake_build_report)
    result = cli_runner.invoke(prod_main.prod_app, ["doctor"])
    assert result.exit_code == 0
    history = mascope_home / ".runtime" / "disk-check" / "history.tsv"
    assert seen["disk_history"] == history
    assert seen["min_days_to_floor"] == 3.0
    # The filestore is measured on its own: it may be a volume of its own.
    labels = [label for label, _ in seen["disk_specs"]]
    assert labels == ["state", "filestore", "docker"]
    filestore = dict(seen["disk_specs"])["filestore"]
    assert filestore == Path(prod_main.runtime.meta.filestore)


def test_doctor_command_json(monkeypatch, cli_runner):
    monkeypatch.setattr(
        prod_main.prod_doctor, "build_report", lambda **k: _healthy_report()
    )
    result = cli_runner.invoke(prod_main.prod_app, ["doctor", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["images"]["count"] == 11
