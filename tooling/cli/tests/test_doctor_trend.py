"""
Tests for tooling/monitoring/doctor_trend.py, the reader of the doctor's JSON
that puts the fill rate into doctor-push.sh's Uptime Kuma message.

The helper consumes `mascope prod doctor --json`, so these feed it the JSON of
a real ``doctor.Report``: renaming a field on the doctor's side fails here,
instead of silently dropping the suffix from every server's heartbeat.
"""

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


doctor = importlib.import_module("mascope_cli.cmd.prod.doctor")

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "monitoring" / "doctor_trend.py"
PUSH_SCRIPT = REPO_ROOT / "tooling" / "monitoring" / "doctor-push.sh"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="tooling/monitoring/doctor_trend.py absent"
)


def _load():
    spec = importlib.util.spec_from_file_location("doctor_trend", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(*disks):
    return doctor.Report(
        containers=[doctor.ContainerHealth("backend", "b", "running", "healthy")],
        versions=doctor.VersionStatus("v1.6.1", []),
        disks=list(disks),
        updates=doctor.UpdateStatus(None, None, None),
        backups=doctor.BackupStatus(5, 8.0),
        images=doctor.ImageFootprint(11, "6.2GB", "2.1GB"),
    )


def _disk(label, mount, rates=(), days_to_full=None):
    return doctor.DiskUsage(
        label,
        f"/{label}",
        400.0,
        40.0,
        False,
        mount=mount,
        rates=[doctor.FillRate(7, 7.0, r) for r in rates],
        days_to_full=days_to_full,
    )


def _json(report):
    return json.dumps(report.to_dict())


def test_the_filesystem_nearest_to_full_is_reported():
    report = _report(
        _disk("state", "/", rates=[2.0], days_to_full=200.0),
        _disk("filestore", "/data", rates=[19.04, 17.5], days_to_full=21.4),
        _disk("docker", "/", rates=[2.0], days_to_full=200.0),
    )
    suffix = _load().summarize(_json(report))
    assert suffix == "_filestore_fill19.0GiBperday_full21d"


def test_a_disk_that_is_not_filling_reports_its_rate_alone():
    report = _report(_disk("state", "/", rates=[-0.3]))
    assert _load().summarize(_json(report)) == "_state_fill-0.3GiBperday"


def test_the_suffix_is_safe_in_a_query_string():
    report = _report(_disk("state", "/", rates=[3.0], days_to_full=100.0))
    suffix = _load().summarize(_json(report))
    assert "+" not in suffix and " " not in suffix and "&" not in suffix


@pytest.mark.parametrize(
    "stdin",
    [
        _json(_report(_disk("state", "/"))),  # no history yet
        '{"ok": true, "disks": [{"label": "state", "free_gb": 3}]}',  # older doctor
        "",
        "not json",
    ],
    ids=["no rate yet", "doctor without rates", "empty", "garbage"],
)
def test_nothing_is_printed_without_a_rate(stdin):
    assert _load().summarize(stdin) == ""


def test_it_runs_as_doctor_push_runs_it():
    # doctor-push.sh pipes the JSON into the file on the host's python3.
    report = _report(_disk("state", "/", rates=[5.0], days_to_full=80.0))
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=_json(report),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "_state_fill5.0GiBperday_full80d"
    assert "tooling/monitoring/doctor_trend.py" in PUSH_SCRIPT.read_text(
        encoding="utf-8"
    )
