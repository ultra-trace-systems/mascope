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
assessment), an assessment failure that a deployment verdict must not hide, and
the two triggers: a deploy run once per release and never inside another run or
the interval after one, a periodic run that always runs.
"""

import fcntl
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "monitoring" / "run-assessment.sh"
VANTAGE_DIFF = REPO_ROOT / "security" / "pentest" / "vantage_diff.py"

pytestmark = [
    pytest.mark.skipif(
        not SCRIPT.is_file(), reason="tooling/monitoring/run-assessment.sh absent"
    ),
    pytest.mark.skipif(
        not all(shutil.which(tool) for tool in ("bash", "python3", "tar", "flock")),
        reason="the runner needs bash, python3, tar and flock",
    ),
]

RELEASE_API = "https://releases.invalid/latest"
HC_URL = "https://heartbeat.invalid/ping"

# ssh: runs the remote command here, in SUITE_DIR, a directory standing in for
# the target's suite copy; its state (a report left by an earlier run) persists
# as it would on the target. Every argument list is appended to STUB_SSH_ARGS,
# so a test can see what reached the target.
SSH_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_SSH_ARGS"
for last; do :; done
exec bash -c "$last"
"""

# The target's suite interpreter: records the release it was given and whether
# the runner's lock was free while it ran, writes the report bytes
# STUB_ORIGIN_REPORT names, as the suite would, and exits STUB_SUITE_RC.
ORIGIN_PYTHON_STUB = """#!/usr/bin/env bash
echo "suite output"
echo "release=${MASCOPE_PENTEST_CURRENT_RELEASE-<unset>}" > "$STUB_ORIGIN_ENV"
if flock -n "$STUB_LOCK" true; then echo free; else echo held; fi > "$STUB_LOCK_STATE"
mkdir -p report
[ -n "$STUB_ORIGIN_REPORT" ] && cp "$STUB_ORIGIN_REPORT" report/pentest-report.json
exit "${STUB_SUITE_RC:-0}"
"""

# curl: the release lookup answers STUB_RELEASE (or fails when unset), the
# target's version route STUB_DEPLOYED (or fails when unset); every heartbeat
# is appended to STUB_PINGS as "<url> <body>".
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
  */api/version/public) [ -n "${STUB_DEPLOYED+x}" ] || exit 22; printf '%s' "$STUB_DEPLOYED" ;;
  *) printf '%s %s\\n' "$url" "$body" >> "$STUB_PINGS" ;;
esac
"""


# The CDN vantage's suite runs on this host from CDN_SUITE_DIR. Its interpreter
# is stubbed: it records the environment it was given and writes the report
# STUB_CDN_REPORT names, as the suite would.
CDN_PYTHON_STUB = """#!/usr/bin/env bash
{
  echo "target=$MASCOPE_PENTEST_TARGET"
  echo "vantage=$MASCOPE_PENTEST_VANTAGE"
  echo "verify=$MASCOPE_PENTEST_VERIFY_TLS"
  echo "header=$MASCOPE_PENTEST_HEADER_NAME:$MASCOPE_PENTEST_HEADER_VALUE"
  echo "release=${MASCOPE_PENTEST_CURRENT_RELEASE-<unset>}"
  echo "args=$*"
} > "$STUB_CDN_ENV"
mkdir -p report
[ -n "$STUB_CDN_REPORT" ] && cp "$STUB_CDN_REPORT" report/pentest-report.json
exit "${STUB_CDN_RC:-0}"
"""

# sleep: the gap between the two vantages, recorded instead of waited out.
SLEEP_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$1" >> "$STUB_SLEEPS"
"""

CDN_TARGET = "https://app.invalid"
HEADER_VALUE = "edge-bypass-0123456789"


def _report(build="v1.7.2", findings=0, rows=None, throttled=0):
    report = {
        **(rows or {}),
        "build": {"identity": build},
        "summary": {
            "findings": findings,
            "accepted_findings": 0,
            "stale_baseline_entries": 0,
            "controls_verified": 40,
            "not_assessed": 3,
            "throttled": throttled,
        },
    }
    if throttled is None:  # a report from a suite that predates the count
        del report["summary"]["throttled"]
    return report


def _deployed(version):
    """The target's answer on its anonymous version route."""
    return json.dumps({"message": "", "data": {"version": version}})


def _run(
    tmp_path,
    *,
    report=None,
    raw=None,
    release=None,
    suite_rc=0,
    cdn=None,
    cdn_rc=0,
    old_records=(),
    leftover=None,
    extra_env=None,
    args=(),
    deployed=_deployed("v1.7.2"),
    state=None,
):
    """
    Run the runner once; return (exit code, summary file, heartbeats).

    The summary is ``None`` when the run assessed nothing; its console output
    is left in ``tmp_path / "stdout"``.

    :param cdn: The CDN run's report, or ``...`` for a CDN run that writes none.
        ``None`` configures no CDN vantage at all.
    :param old_records: Names of files to leave in RECORDS beforehand, dated
        past the retention.
    :param leftover: A report an earlier run left in both suite copies'
        ``report/``, at the target and here.
    :param extra_env: Further variables in the runner's own environment.
    :param args: The runner's arguments: none for the periodic run.
    :param deployed: The target's version route's answer, ``None`` to fail.
    :param state: Triggers' state to leave in RECORDS beforehand,
        ``{"last-run-end": ..., "last-run-version": ...}``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ssh", SSH_STUB), ("curl", CURL_STUB), ("sleep", SLEEP_STUB)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    records = tmp_path / "records"
    # Made beforehand by a test that holds the runner's lock in it.
    records.mkdir(exist_ok=True)
    for name in old_records:
        (records / name).write_text("old")
        os.utime(records / name, (0, 0))
    for name, value in (state or {}).items():
        (records / f".{name}").write_text(f"{value}\n")

    target = tmp_path / "target"
    (target / ".venv" / "bin").mkdir(parents=True)
    (target / ".venv" / "bin" / "python").write_text(ORIGIN_PYTHON_STUB)
    (target / ".venv" / "bin" / "python").chmod(0o755)
    origin_report = ""
    if report is not None or raw is not None:
        origin_report = str(tmp_path / "origin-report.json")
        Path(origin_report).write_bytes(
            raw if raw is not None else json.dumps(report).encode()
        )

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
        "SUITE_DIR": str(target),
        "STUB_ORIGIN_REPORT": origin_report,
        "STUB_SUITE_RC": str(suite_rc),
        "STUB_PINGS": str(tmp_path / "pings"),
        "STUB_SSH_ARGS": str(tmp_path / "ssh-args"),
        "STUB_ORIGIN_ENV": str(tmp_path / "origin-env"),
        "STUB_SLEEPS": str(tmp_path / "sleeps"),
        "STUB_LOCK": str(records / ".run.lock"),
        "STUB_LOCK_STATE": str(tmp_path / "lock-state"),
        "MIN_INTERVAL": "600",
    }
    if deployed is not None:
        env["STUB_DEPLOYED"] = deployed
    if cdn is not None:
        suite = tmp_path / "suite"
        (suite / ".venv" / "bin").mkdir(parents=True)
        (suite / ".venv" / "bin" / "python").write_text(CDN_PYTHON_STUB)
        (suite / ".venv" / "bin" / "python").chmod(0o755)
        shutil.copy(VANTAGE_DIFF, suite / "vantage_diff.py")
        if leftover is not None:
            (suite / "report").mkdir()
            (suite / "report" / "pentest-report.json").write_text(json.dumps(leftover))
        cdn_report = ""
        if cdn is not ...:
            cdn_report = str(tmp_path / "cdn-report.json")
            Path(cdn_report).write_text(json.dumps(cdn))
        # The header lives in the config file, as on the monitoring host: a
        # sourced file sets shell variables, not exported ones, so it reaches
        # the suite only if the runner passes it on.
        config = tmp_path / "config.env"
        config.write_text(
            "MASCOPE_PENTEST_HEADER_NAME=X-Assessment\n"
            f"MASCOPE_PENTEST_HEADER_VALUE={HEADER_VALUE}\n"
        )
        env.update(
            PENTEST_RUNNER_ENV=str(config),
            CDN_TARGET=CDN_TARGET,
            CDN_SUITE_DIR=str(suite),
            VANTAGE_GAP="17",
            STUB_CDN_REPORT=cdn_report,
            STUB_CDN_RC=str(cdn_rc),
            STUB_CDN_ENV=str(tmp_path / "cdn-env"),
        )
    if leftover is not None:
        (target / "report").mkdir()
        (target / "report" / "pentest-report.json").write_text(json.dumps(leftover))
    if release is not None:
        env["STUB_RELEASE"] = release
    env.update(extra_env or {})
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    (tmp_path / "stdout").write_text(result.stdout)
    summaries = list(records.glob("*-origin.summary"))
    assert len(summaries) <= 1, result.stderr
    pings_file = tmp_path / "pings"
    pings = pings_file.read_text().splitlines() if pings_file.exists() else []
    summary = summaries[0].read_text().strip() if summaries else None
    return result.returncode, summary, pings


def _tag(tag):
    return json.dumps({"tag_name": tag})


COUNTS = "findings=0 accepted=0 stale=0 verified=40 not_assessed=3 throttled=0"

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
    # The pattern is anchored at both ends: a prefixed build names no release.
    (
        "prefixed-build",
        "build-v1.7.9",
        _tag("v1.7.10"),
        0,
        0,
        "lag=unknown current=v1.7.10",
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
    assert summary == f"build={assessed} {COUNTS} {lag} trigger=schedule"
    # The heartbeat carries the same verdict, on the fail path when red.
    if code == 0:
        assert pings[-1] == f"{HC_URL} {summary}"
    else:
        assert pings[-1] == f"{HC_URL}/fail suite exit {code} | {summary}"


def test_an_unreadable_report_fails_the_run_and_appends_no_verdict(tmp_path):
    rc, summary, pings = _run(tmp_path, raw=b"{not json", release=_tag("v1.7.10"))
    assert rc == 1
    assert summary == "report unreadable trigger=schedule"
    assert pings[-1] == (
        f"{HC_URL}/fail suite exit 1 | report unreadable trigger=schedule"
    )


def test_an_empty_archive_fails_the_run(tmp_path):
    rc, summary, _ = _run(tmp_path, release=_tag("v1.7.2"))
    assert rc == 1
    assert summary == "report unreadable trigger=schedule"


def _rows(**outcomes):
    """Report sections from ``{control id: outcome}``."""
    section = {"failed": "findings", "passed": "controls_verified"}
    rows = {"findings": [], "controls_verified": [], "not_assessed": []}
    for control, outcome in outcomes.items():
        rows[section.get(outcome, "not_assessed")].append(
            {"id": control.replace("_", "-"), "title": control, "detail": ""}
        )
    return rows


ORIGIN_ROWS = _rows(HDR_01="passed", TLS_02="skipped", AUTHN_01="passed")
# The edge strips a header the origin sets, and answers the HTTPS redirect the
# origin never sees.
CDN_ROWS = _rows(HDR_01="failed", TLS_02="passed", AUTHN_01="passed")


def test_the_cdn_vantage_runs_beside_the_origin_and_the_two_are_compared(tmp_path):
    rc, origin, pings = _run(
        tmp_path,
        report=_report(rows=ORIGIN_ROWS),
        release=_tag("v1.7.2"),
        cdn=_report(rows=CDN_ROWS),
    )
    records = tmp_path / "records"
    assert rc == 0
    assert origin == f"build=v1.7.2 {COUNTS} lag=current trigger=schedule"
    [cdn_summary] = records.glob("*-cdn.summary")
    assert cdn_summary.read_text().strip() == origin
    assert len(list(records.glob("*-cdn.tar.gz"))) == 1
    assert len(list(records.glob("*-cdn.log"))) == 1

    # One timestamp holds both vantages and their comparison. (The dotfiles
    # are the triggers' state.) "2026-10-27T132500Z" is 18 characters.
    assert len({path.name[:18] for path in records.glob("2*")}) == 1
    assert len(list(records.iterdir())) == 8 + 3
    [comparison] = records.glob("*-vantage.json")
    verdicts = {
        d["id"]: (d["verdict"], d["layer"])
        for d in json.loads(comparison.read_text())["disagreements"]
    }
    assert verdicts == {
        "HDR-01": ("edge weakens", "edge"),
        "TLS-02": ("edge only", "edge"),
    }
    assert "| HDR-01 | passed | failed | edge weakens | edge |" in (
        next(records.glob("*-vantage.md")).read_text()
    )
    assert pings[-1] == (
        f"{HC_URL} origin: {origin} | cdn: {origin} | "
        "vantages: agree=1 disagree=2 edge=2 origin_only=0"
    )


def test_the_header_goes_to_the_local_cdn_run_and_never_to_the_target(tmp_path):
    _run(tmp_path, report=_report(), release=_tag("v1.7.2"), cdn=_report())
    cdn_env = (tmp_path / "cdn-env").read_text().splitlines()
    assert f"target={CDN_TARGET}" in cdn_env
    assert "vantage=cdn" in cdn_env
    assert "verify=true" in cdn_env
    assert f"header=X-Assessment:{HEADER_VALUE}" in cdn_env
    # Through the environment, not the command line.
    assert not any(HEADER_VALUE in line for line in cdn_env if line.startswith("args="))
    ssh_args = (tmp_path / "ssh-args").read_text()
    assert "MASCOPE_PENTEST_VANTAGE=origin" in ssh_args
    assert HEADER_VALUE not in ssh_args
    assert "X-Assessment" not in ssh_args


def test_the_current_release_goes_to_both_suites(tmp_path):
    # The report's copy of the lag needs the release the runner looked up, so
    # the lookup happens before either run.
    rc, summary, _ = _run(
        tmp_path, report=_report(), release=_tag("v1.7.10"), cdn=_report()
    )
    assert rc == 90
    assert summary.endswith("lag=behind current=v1.7.10 trigger=schedule")
    assert "MASCOPE_PENTEST_CURRENT_RELEASE='v1.7.10'" in (
        (tmp_path / "ssh-args").read_text()
    )
    assert (tmp_path / "origin-env").read_text().split() == ["release=v1.7.10"]
    assert "release=v1.7.10" in (tmp_path / "cdn-env").read_text().splitlines()


@pytest.mark.parametrize(
    "release",
    [
        None,
        "{not json",
        _tag("nightly"),
        # Anything that is not a release tag stays off the target's command
        # line, a quote that would end the assignment included.
        _tag("v1.7.10' && touch pwned && echo '"),
    ],
    ids=["unreachable", "malformed", "non-release", "injection"],
)
def test_no_current_release_goes_to_the_suites_without_a_release_tag(tmp_path, release):
    rc, summary, _ = _run(tmp_path, report=_report(), release=release, cdn=_report())
    assert rc == 0
    assert "lag=unknown" in summary
    ssh_args = (tmp_path / "ssh-args").read_text()
    assert "MASCOPE_PENTEST_CURRENT_RELEASE" not in ssh_args
    assert "pwned" not in ssh_args
    assert not (tmp_path / "target" / "pwned").exists()
    assert (tmp_path / "origin-env").read_text().split() == ["release=<unset>"]
    # Set empty, not left unset, so nothing inherited can stand in for it.
    assert "release=" in (tmp_path / "cdn-env").read_text().splitlines()


def test_a_release_in_the_environment_does_not_stand_in_for_the_lookup(tmp_path):
    rc, _, _ = _run(
        tmp_path,
        report=_report(),
        cdn=_report(),
        extra_env={"MASCOPE_PENTEST_CURRENT_RELEASE": "v1.7.2"},
    )
    # The CDN suite runs here and would inherit an exported value. (The
    # origin's would not: ssh carries no environment, only the stub does.)
    assert rc == 0
    assert "release=" in (tmp_path / "cdn-env").read_text().splitlines()


def test_a_failing_cdn_run_fails_the_run(tmp_path):
    rc, origin, pings = _run(
        tmp_path, report=_report(), release=_tag("v1.7.2"), cdn=_report(), cdn_rc=1
    )
    assert rc == 1
    assert pings[-1].startswith(f"{HC_URL}/fail suite exit 1 | origin: {origin}")


def test_an_origin_failure_keeps_its_code_over_the_cdn_run(tmp_path):
    rc, _, pings = _run(
        tmp_path,
        report=_report(build="v1.7.9"),
        release=_tag("v1.7.10"),
        cdn=_report(build="v1.7.9"),
        cdn_rc=1,
    )
    assert rc == 90
    assert pings[-1].startswith(f"{HC_URL}/fail suite exit 90 |")


def test_a_cdn_run_without_a_report_fails_and_is_not_compared(tmp_path):
    rc, _, pings = _run(tmp_path, report=_report(), release=_tag("v1.7.2"), cdn=...)
    records = tmp_path / "records"
    assert rc == 1
    assert next(records.glob("*-cdn.summary")).read_text().strip() == (
        "report unreadable trigger=schedule"
    )
    assert not list(records.glob("*-vantage.*"))
    assert pings[-1].endswith(
        "| cdn: report unreadable trigger=schedule | vantages: comparison unavailable"
    )


def test_an_aborted_run_does_not_archive_the_previous_report(tmp_path):
    # Both vantages abort as a WAF-refused run does (exit 4, no report) over a
    # good report an earlier run left behind. Archiving that one would date an
    # assessment that did not happen today, and compare it.
    rc, summary, pings = _run(
        tmp_path,
        release=_tag("v1.7.2"),
        suite_rc=4,
        cdn=...,
        cdn_rc=4,
        leftover=_report(build="v1.7.1"),
    )
    records = tmp_path / "records"
    assert rc == 4
    assert summary == "report unreadable trigger=schedule"
    assert next(records.glob("*-cdn.summary")).read_text().strip() == (
        "report unreadable trigger=schedule"
    )
    assert not list(records.glob("*-vantage.*"))
    assert pings[-1] == (
        f"{HC_URL}/fail suite exit 4 | origin: report unreadable trigger=schedule "
        "| cdn: report unreadable trigger=schedule | vantages: comparison unavailable"
    )


def test_without_a_cdn_target_only_the_origin_runs(tmp_path):
    rc, _, _ = _run(tmp_path, report=_report(), release=_tag("v1.7.2"))
    records = tmp_path / "records"
    assert rc == 0
    assert not list(records.glob("*-cdn.*"))
    assert not list(records.glob("*-vantage.*"))


def test_the_cdn_run_waits_out_the_gap_after_the_origin_run(tmp_path):
    _run(tmp_path, report=_report(), release=_tag("v1.7.2"), cdn=_report())
    assert (tmp_path / "sleeps").read_text().split() == ["17"]


def test_retention_prunes_every_vantage_and_the_comparison(tmp_path):
    old = [
        "2025-01-01T000000Z-origin.log",
        "2025-01-01T000000Z-cdn.tar.gz",
        "2025-01-01T000000Z-vantage.md",
    ]
    _run(
        tmp_path,
        report=_report(),
        release=_tag("v1.7.2"),
        cdn=_report(),
        old_records=[*old, "keep-me.txt"],
    )
    names = {path.name for path in (tmp_path / "records").iterdir()}
    assert not names & set(old)
    assert "keep-me.txt" in names


# --------------------------------------------------------------------------- #
# Throttling, as the summary carries it
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "throttled, says", [(2, "throttled=2"), (None, "throttled=?")], ids=["2", "old"]
)
def test_the_summary_counts_the_throttled_controls(tmp_path, throttled, says):
    rc, summary, pings = _run(
        tmp_path, report=_report(throttled=throttled), release=_tag("v1.7.2")
    )
    # A degraded run is told apart, not failed: a throttled control is not
    # assessed, and not-assessed never reddens a run.
    assert rc == 0
    assert f" not_assessed=3 {says} lag=current" in summary
    assert pings[-1] == f"{HC_URL} {summary}"


# --------------------------------------------------------------------------- #
# The two triggers
# --------------------------------------------------------------------------- #

DEPLOY = ("--on-deploy",)
DEPLOY_HC = "https://deploy-heartbeat.invalid/ping"


def _state(tmp_path, name):
    path = tmp_path / "records" / f".{name}"
    return path.read_text().strip() if path.exists() else None


def _suite_ran(tmp_path):
    return (tmp_path / "origin-env").exists()


def test_a_release_no_run_has_started_on_is_assessed_on_deploy(tmp_path):
    rc, summary, pings = _run(
        tmp_path,
        args=DEPLOY,
        report=_report(build="v1.7.3"),
        release=_tag("v1.7.3"),
        deployed=_deployed("v1.7.3"),
        state={"last-run-version": "v1.7.2", "last-run-end": int(time.time()) - 3600},
        extra_env={"DEPLOY_HC_URL": DEPLOY_HC},
    )
    assert rc == 0
    assert summary == f"build=v1.7.3 {COUNTS} lag=current trigger=deploy"
    # Its heartbeat is its own; the periodic one's is left to the clock.
    assert pings == [f"{DEPLOY_HC}/start ", f"{DEPLOY_HC} {summary}"]
    assert _state(tmp_path, "last-run-version") == "v1.7.3"
    # Same records, same retention as a periodic run.
    names = {path.name[18:] for path in (tmp_path / "records").glob("2*")}
    assert names == {"-origin.log", "-origin.tar.gz", "-origin.summary"}


def test_a_deploy_run_without_its_own_heartbeat_pings_the_periodic_one(tmp_path):
    rc, summary, pings = _run(
        tmp_path, args=DEPLOY, report=_report(), release=_tag("v1.7.2"), suite_rc=1
    )
    # A deploy run that finds a broken control has to page someone.
    assert rc == 1
    assert pings[-1] == f"{HC_URL}/fail suite exit 1 | {summary}"


def test_a_release_already_assessed_is_not_assessed_again(tmp_path):
    rc, summary, pings = _run(
        tmp_path,
        args=DEPLOY,
        report=_report(),
        state={"last-run-version": "v1.7.2"},
    )
    assert (rc, summary, pings) == (0, None, [])
    assert not _suite_ran(tmp_path)
    # Nothing ran, so nothing moved the interval either.
    assert _state(tmp_path, "last-run-end") is None


@pytest.mark.parametrize(
    "answer",
    [
        None,
        "{not json",
        _deployed("unknown"),
        json.dumps({"data": {"version": 7}}),
        json.dumps({"version": "v1.7.3"}),
        # The target's answer, held to the shape of a version: nothing it says
        # goes into a file name, a command line or the heartbeat as it is.
        _deployed("v1.7.3' && touch pwned && echo '"),
        _deployed("v1.7.3 build=v9.9.9"),
        _deployed("v" + "1" * 200),
    ],
    ids=[
        "unreachable",
        "malformed",
        "unknown",
        "not-a-string",
        "no-envelope",
        "injection",
        "spaces",
        "oversized",
    ],
)
def test_a_deploy_watch_that_cannot_read_the_release_runs_nothing(tmp_path, answer):
    # A release on record, so an empty answer is not "the same release".
    rc, summary, pings = _run(
        tmp_path,
        args=DEPLOY,
        report=_report(),
        deployed=answer,
        state={"last-run-version": "v1.7.2"},
    )
    assert (rc, summary, pings) == (0, None, [])
    assert not _suite_ran(tmp_path)
    assert _state(tmp_path, "last-run-version") == "v1.7.2"
    assert "cannot read the deployed release" in (tmp_path / "stdout").read_text()
    assert not list(tmp_path.rglob("pwned"))


def test_a_deploy_run_waits_out_the_interval_after_the_last_run(tmp_path):
    rc, summary, pings = _run(
        tmp_path,
        args=DEPLOY,
        report=_report(build="v1.7.3"),
        deployed=_deployed("v1.7.3"),
        state={"last-run-version": "v1.7.2", "last-run-end": int(time.time()) - 60},
    )
    assert (rc, summary, pings) == (0, None, [])
    assert "deferred: the last run ended" in (tmp_path / "stdout").read_text()
    # Deferred before it asked the target anything: the next tick asks.
    assert not (tmp_path / "ssh-args").exists()
    assert _state(tmp_path, "last-run-version") == "v1.7.2"


def test_a_deploy_run_after_the_interval_goes_ahead(tmp_path):
    rc, summary, _ = _run(
        tmp_path,
        args=DEPLOY,
        report=_report(build="v1.7.3"),
        release=_tag("v1.7.3"),
        deployed=_deployed("v1.7.3"),
        state={"last-run-version": "v1.7.2", "last-run-end": int(time.time()) - 601},
    )
    assert rc == 0
    assert summary.endswith("trigger=deploy")


def _hold_the_lock(tmp_path):
    records = tmp_path / "records"
    records.mkdir()
    holder = open(records / ".run.lock", "a")  # noqa: SIM115 - held past this call
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return holder


def test_a_deploy_run_does_not_start_inside_a_run(tmp_path):
    holder = _hold_the_lock(tmp_path)
    try:
        rc, summary, pings = _run(tmp_path, args=DEPLOY, report=_report())
    finally:
        holder.close()
    assert (rc, summary, pings) == (0, None, [])
    assert "deferred: a run is in progress" in (tmp_path / "stdout").read_text()
    assert not (tmp_path / "ssh-args").exists()


def test_a_periodic_run_waits_for_the_lock_and_fails_loudly_past_it(tmp_path):
    holder = _hold_the_lock(tmp_path)
    try:
        rc, summary, pings = _run(
            tmp_path, report=_report(), extra_env={"LOCK_WAIT": "1"}
        )
    finally:
        holder.close()
    assert rc == 75
    assert summary is None
    assert not _suite_ran(tmp_path)
    # Its heartbeat says why, so a missed assessment is not a silent one.
    assert pings == [f"{HC_URL}/fail no run: another held the lock for 1s"]


def test_every_run_holds_the_lock_while_the_suite_runs(tmp_path):
    _run(tmp_path, report=_report(), release=_tag("v1.7.2"))
    assert (tmp_path / "lock-state").read_text().strip() == "held"
    end = int(_state(tmp_path, "last-run-end"))
    assert abs(end - time.time()) < 60


def test_a_periodic_run_inside_the_interval_waits_it_out_and_runs(tmp_path):
    rc, summary, pings = _run(
        tmp_path,
        report=_report(),
        release=_tag("v1.7.2"),
        state={"last-run-end": int(time.time()) - 60},
    )
    assert rc == 0
    assert summary.endswith("trigger=schedule")
    [wait] = (tmp_path / "sleeps").read_text().split()
    assert 530 <= int(wait) <= 540
    # Its heartbeat starts when the run does, not when it began waiting.
    assert pings == [f"{HC_URL}/start ", f"{HC_URL} {summary}"]


def test_a_last_run_that_ended_in_the_future_costs_one_interval_at_most(tmp_path):
    # The clock stepped back after the last run ended.
    _run(
        tmp_path,
        report=_report(),
        release=_tag("v1.7.2"),
        state={"last-run-end": int(time.time()) + 86400},
    )
    assert (tmp_path / "sleeps").read_text().split() == ["600"]


def test_a_periodic_run_assesses_a_release_already_assessed(tmp_path):
    # The clock asks whether the ground shifted, which a release cannot answer.
    rc, summary, _ = _run(
        tmp_path,
        report=_report(),
        release=_tag("v1.7.2"),
        state={"last-run-version": "v1.7.2"},
    )
    assert rc == 0
    assert summary == f"build=v1.7.2 {COUNTS} lag=current trigger=schedule"


def test_a_periodic_run_on_a_new_release_leaves_the_watch_nothing_to_do(tmp_path):
    _run(
        tmp_path,
        report=_report(build="v1.7.3"),
        release=_tag("v1.7.3"),
        deployed=_deployed("v1.7.3"),
        state={"last-run-version": "v1.7.2"},
    )
    assert _state(tmp_path, "last-run-version") == "v1.7.3"


def test_a_periodic_run_that_cannot_read_the_release_says_the_watch_is_blind(
    tmp_path,
):
    rc, summary, _ = _run(
        tmp_path,
        report=_report(),
        release=_tag("v1.7.2"),
        deployed=None,
        state={"last-run-version": "v1.7.2"},
    )
    assert rc == 0
    assert summary == (
        f"build=v1.7.2 {COUNTS} lag=current trigger=schedule deployed=unreadable"
    )
    assert _state(tmp_path, "last-run-version") == "v1.7.2"


def test_an_unknown_argument_is_refused(tmp_path):
    rc, summary, pings = _run(tmp_path, args=("--on-deploy-now",), report=_report())
    assert (rc, summary, pings) == (2, None, [])
    assert not _suite_ran(tmp_path)
