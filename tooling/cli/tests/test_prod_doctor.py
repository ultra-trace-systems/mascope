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
import subprocess

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


def test_doctor_command_json(monkeypatch, cli_runner):
    monkeypatch.setattr(
        prod_main.prod_doctor, "build_report", lambda **k: _healthy_report()
    )
    result = cli_runner.invoke(prod_main.prod_app, ["doctor", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True and payload["images"]["count"] == 11
