"""
Tests for tooling/monitoring/run-assessment.sh, the scheduled security
assessment runner on the monitoring host.

The script runs as it is, under bash, with only its two interactions with the
outside stubbed: ``ssh`` (the suite run on the target and the report stream it
sends back) and ``curl`` (the release lookup and the heartbeat). So what is
under test is the shipped logic: reading the report, the release-lag verdict,
the exit-code overrides and the summary it keeps beside the archive.

The cases are the ones where the runner can go wrong: version ordering a string
compare gets backwards, a lookup that fails (which must never redden an
assessment), and an assessment failure that a deployment verdict must not hide.
"""

import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "monitoring" / "run-assessment.sh"

pytestmark = [
    pytest.mark.skipif(
        not SCRIPT.is_file(), reason="tooling/monitoring/run-assessment.sh absent"
    ),
    pytest.mark.skipif(
        not (shutil.which("bash") and shutil.which("python3") and shutil.which("tar")),
        reason="the runner needs bash, python3 and tar",
    ),
]

RELEASE_API = "https://releases.invalid/latest"
HC_URL = "https://heartbeat.invalid/ping"

# ssh: a suite run exits STUB_SUITE_RC; the report fetch streams STUB_ARCHIVE.
SSH_STUB = """#!/usr/bin/env bash
for last; do :; done
case "$last" in
  *"tar -cz report"*) [ -n "$STUB_ARCHIVE" ] && cat "$STUB_ARCHIVE"; exit 0 ;;
  *) echo "suite output"; exit "${STUB_SUITE_RC:-0}" ;;
esac
"""

# curl: the release lookup answers STUB_RELEASE (or fails when unset); every
# heartbeat is appended to STUB_PINGS as "<url> <body>".
CURL_STUB = """#!/usr/bin/env bash
url= body=
while [ $# -gt 0 ]; do
  case "$1" in
    --data-binary) body="$2"; shift 2 ;;
    -m|-H|--retry) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
case "$url" in
  "$STUB_RELEASE_API") [ -n "${STUB_RELEASE+x}" ] || exit 7; printf '%s' "$STUB_RELEASE" ;;
  *) printf '%s %s\\n' "$url" "$body" >> "$STUB_PINGS" ;;
esac
"""


def _report(build="v1.7.2", findings=0):
    return {
        "build": {"identity": build},
        "summary": {
            "findings": findings,
            "accepted_findings": 0,
            "stale_baseline_entries": 0,
            "controls_verified": 40,
            "not_assessed": 3,
        },
    }


def _archive(path, report_bytes):
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("report/pentest-report.json")
        info.size = len(report_bytes)
        tar.addfile(info, io.BytesIO(report_bytes))


def _run(tmp_path, *, report=None, raw=None, release=None, suite_rc=0):
    """Run the runner once; return (exit code, summary file, heartbeats)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ssh", SSH_STUB), ("curl", CURL_STUB)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    records = tmp_path / "records"
    records.mkdir()

    archive = ""
    if report is not None or raw is not None:
        archive = str(tmp_path / "report.tar.gz")
        _archive(archive, raw if raw is not None else json.dumps(report).encode())

    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "PENTEST_RUNNER_ENV": str(tmp_path / "no-config.env"),
        "TARGET_HOST": "target.invalid",
        "SSH_KEY": str(tmp_path / "key"),
        "RECORDS": str(records),
        "HC_URL": HC_URL,
        "RELEASE_API": RELEASE_API,
        "MASCOPE_PENTEST_TARGET": "https://target.invalid",
        "MASCOPE_PENTEST_EMAIL": "admin@example.com",
        "MASCOPE_PENTEST_PASSWORD": "secret",
        "STUB_RELEASE_API": RELEASE_API,
        "STUB_ARCHIVE": archive,
        "STUB_SUITE_RC": str(suite_rc),
        "STUB_PINGS": str(tmp_path / "pings"),
    }
    if release is not None:
        env["STUB_RELEASE"] = release
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
    )
    summaries = list(records.glob("*-origin.summary"))
    assert len(summaries) == 1, result.stderr
    pings = (tmp_path / "pings").read_text().splitlines()
    return result.returncode, summaries[0].read_text().strip(), pings


def _tag(tag):
    return json.dumps({"tag_name": tag})


COUNTS = "findings=0 accepted=0 stale=0 verified=40 not_assessed=3"

# (id, assessed build, release lookup answer or None for unreachable, suite exit,
#  expected exit, expected lag part of the summary)
MATRIX = [
    ("current", "v1.7.2", _tag("v1.7.2"), 0, 0, "lag=current"),
    # v1.7.9 against v1.7.10 is exactly where a string compare is wrong.
    ("behind", "v1.7.9", _tag("v1.7.10"), 0, 90, "lag=behind current=v1.7.10"),
    ("ahead", "v1.7.10", _tag("v1.7.9"), 0, 0, "lag=ahead current=v1.7.9"),
    # A candidate ranks below its own release, and a box piloting the next one
    # outranks the newest release the API names.
    ("rc-behind", "v2.0.0-rc.1", _tag("v2.0.0"), 0, 90, "lag=behind current=v2.0.0"),
    ("rc-ahead", "v2.1.0-rc.2", _tag("v2.0.0"), 0, 0, "lag=ahead current=v2.0.0"),
    # Nothing the lookup does may redden an assessment.
    ("unreachable", "v1.7.2", None, 0, 0, "lag=unknown"),
    ("malformed", "v1.7.2", "{not json", 0, 0, "lag=unknown"),
    (
        "non-semver-release",
        "v1.7.2",
        _tag("nightly"),
        0,
        0,
        "lag=unknown current=nightly",
    ),
    ("non-semver-build", "latest", _tag("v1.7.2"), 0, 0, "lag=unknown current=v1.7.2"),
    # A merge to master is tagged as a build id, which names no release.
    (
        "build-id",
        "v2026.09.01-9b9e54d",
        _tag("v1.7.2"),
        0,
        0,
        "lag=unknown current=v1.7.2",
    ),
    # An assessment failure outranks a deployment one.
    (
        "failing-and-behind",
        "v1.7.9",
        _tag("v1.7.10"),
        1,
        1,
        "lag=behind current=v1.7.10",
    ),
]


@pytest.mark.parametrize(
    "assessed, release, suite_rc, code, lag",
    [case[1:] for case in MATRIX],
    ids=[case[0] for case in MATRIX],
)
def test_the_lag_verdict(tmp_path, assessed, release, suite_rc, code, lag):
    rc, summary, pings = _run(
        tmp_path, report=_report(build=assessed), release=release, suite_rc=suite_rc
    )
    assert rc == code
    assert summary == f"build={assessed} {COUNTS} {lag}"
    # The heartbeat carries the same verdict, on the fail path when red.
    if code == 0:
        assert pings[-1] == f"{HC_URL} {summary}"
    else:
        assert pings[-1] == f"{HC_URL}/fail suite exit {code} | {summary}"


def test_an_unreadable_report_fails_the_run_and_appends_no_verdict(tmp_path):
    rc, summary, pings = _run(tmp_path, raw=b"{not json", release=_tag("v1.7.10"))
    assert rc == 1
    assert summary == "report unreadable"
    assert pings[-1] == f"{HC_URL}/fail suite exit 1 | report unreadable"


def test_an_empty_archive_fails_the_run(tmp_path):
    rc, summary, _ = _run(tmp_path, release=_tag("v1.7.2"))
    assert rc == 1
    assert summary == "report unreadable"
