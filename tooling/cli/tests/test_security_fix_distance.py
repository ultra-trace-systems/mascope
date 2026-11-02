"""
Tests for tooling/monitoring/security-fix-distance.py, the scheduled check on
the monitoring host that a merged security fix runs on every deployment.

The script runs as it is, under python3, against the two things it reads: a
git repository standing in for GitHub (a real one, with fragments, release
tags and branches, fetched into the check's own clone as it would be) and a
local HTTP server standing in for the deployments' version routes and for the
heartbeat, which records every ping.

The check has to fail both ways before it can be trusted: a marked fix no
release holds reports, and a fleet that runs every fix stays quiet. The other
cases are the ones where it could pass by not looking: a version it cannot
place, a fragment it does not see (moved in from another section, deleted at
release), a repository it cannot fetch, a configuration with nothing in it.
"""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tooling" / "monitoring" / "security-fix-distance.py"

pytestmark = [
    pytest.mark.skipif(not SCRIPT.is_file(), reason="the check script is absent"),
    pytest.mark.skipif(not shutil.which("git"), reason="the check needs git"),
]

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


class Fleet(BaseHTTPRequestHandler):
    """The deployments' version routes and the heartbeat, on one server.

    ``GET /<alias>/api/version/public`` answers what ``answers[alias]`` holds:
    a version string (wrapped as the route wraps it), an int (that HTTP
    status), or bytes (sent as they are). Every ``POST`` is a ping, recorded
    as ``(path, body)``.
    """

    answers: dict = {}
    pings: list = []
    agents: list = []

    def do_GET(self):  # noqa: N802 - the stdlib's name
        self.agents.append(self.headers.get("User-Agent"))
        alias, _, route = self.path.lstrip("/").partition("/")
        answer = self.answers.get(alias, 404)
        if route != "api/version/public":
            answer = 404
        if isinstance(answer, int):
            self.send_response(answer)
            self.end_headers()
            return
        if isinstance(answer, str):
            answer = json.dumps({"message": "", "data": {"version": answer}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(answer)

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.pings.append((self.path, body.decode()))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def fleet():
    handler = type("FleetHandler", (Fleet,), {"answers": {}, "pings": [], "agents": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler.url = f"http://127.0.0.1:{server.server_port}"
    yield handler
    server.shutdown()
    server.server_close()


class Upstream:
    """The repository the check fetches from, with develop and master."""

    def __init__(self, path: Path):
        self.path = path
        self.day = 0
        path.mkdir()
        self.git("init", "-q", "-b", "develop")
        self.commit("chore: start", {"README.md": "mascope\n"})
        self.git("branch", "master")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **GIT_ENV, **self._dates()},
        ).stdout.strip()

    def _dates(self) -> dict:
        date = f"2026-10-{1 + self.day:02d}T12:00:00+00:00"
        return {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}

    def commit(self, message: str, files: dict, remove=()) -> str:
        self.day += 1
        for name, text in files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            self.git("add", name)
        for name in remove:
            self.git("rm", "-q", name)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def fix(self, branch: str, section: str = "security") -> str:
        """A merged fix with its fragment, as a PR lands it."""
        return self.commit(
            f"fix: {branch}",
            {
                f"src/{branch}.py": "fixed = True\n",
                f"changelog.d/{branch}.{section}.md": f"- **{branch}.**\n",
            },
        )

    def release(self, tag: str) -> None:
        self.git("tag", tag)


@pytest.fixture
def upstream(tmp_path):
    return Upstream(tmp_path / "upstream")


def _config(tmp_path, fleet, upstream, deployments, urls=None, **check) -> Path:
    lines = [
        "[check]",
        f"repository = {tmp_path / 'clone.git'}",
        f"remote = {upstream.path}",
        f"heartbeat = {fleet.url}/ping",
        "timeout = 5",
    ]
    lines += [f"{key} = {value}" for key, value in check.items()]
    lines.append("[deployments]")
    urls = urls or {}
    lines += [
        f"{alias} = {urls.get(alias, f'{fleet.url}/{alias}')}" for alias in deployments
    ]
    path = tmp_path / "fix-distance.ini"
    path.write_text("\n".join(lines) + "\n")
    return path


def _run(tmp_path, fleet, upstream, answers, urls=None, **check):
    """One run of the check, with ``answers`` as the deployments' versions."""
    fleet.answers.clear()
    fleet.answers.update(answers)
    fleet.pings.clear()
    config = _config(tmp_path, fleet, upstream, answers, urls, **check)
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--config", str(config)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **GIT_ENV, "no_proxy": "*"},
    )


def _final_ping(fleet):
    """The ping that closed the run, after its /start."""
    assert fleet.pings[0] == ("/ping/start", "")
    return fleet.pings[-1]


# -- both ways --------------------------------------------------------------


def test_a_merged_fix_no_release_holds_reports_every_deployment(
    tmp_path, fleet, upstream
):
    upstream.release("v1.0.0")
    upstream.fix("mas-1-token-expiry")

    result = _run(
        tmp_path, fleet, upstream, {"server-a": "v1.0.0", "server-b": "v1.0.0"}
    )

    assert result.returncode == 1, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    for alias in ("server-a", "server-b"):
        assert any(
            line.startswith(f"behind: {alias} runs v1.0.0, missing ")
            and "mas-1-token-expiry (merged 2026-10-03, not released)" in line
            for line in lines
        ), lines
    assert _final_ping(fleet) == (
        "/ping/fail",
        "fixes=1 unreleased=1 deployments=2 behind=2 unreadable=0",
    )


def test_a_fleet_that_runs_every_fix_is_quiet(tmp_path, fleet, upstream):
    upstream.fix("mas-1-token-expiry")
    upstream.fix("mas-2-cookie-scope")
    upstream.release("v1.1.0")
    # A later change that is no security fix asks nothing of the fleet.
    upstream.fix("mas-3-faster-list", section="performance")

    result = _run(
        tmp_path, fleet, upstream, {"server-a": "v1.1.0", "server-b": "v1.1.0"}
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert _final_ping(fleet) == (
        "/ping",
        "fixes=2 unreleased=0 deployments=2 behind=0 unreadable=0",
    )


def test_only_the_deployment_behind_is_named_with_the_release_it_needs(
    tmp_path, fleet, upstream
):
    upstream.release("v1.0.0")
    fix = upstream.fix("mas-1-token-expiry")
    upstream.release("v1.1.0")

    result = _run(
        tmp_path, fleet, upstream, {"server-a": "v1.1.0", "server-b": "v1.0.0"}
    )

    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        f"behind: server-b runs v1.0.0, missing {fix[:9]} mas-1-token-expiry"
        " (merged 2026-10-03, released in v1.1.0)"
    ]
    assert _final_ping(fleet)[0] == "/ping/fail"


def test_the_heartbeat_carries_counts_and_no_names(tmp_path, fleet, upstream):
    upstream.fix("mas-1-token-expiry")

    _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    for _, body in fleet.pings:
        assert "server-a" not in body
        assert "mas-1" not in body


def test_the_check_names_itself_to_the_edge(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")

    _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    # The name the README tells an operator to let through an edge rule.
    assert fleet.agents == ["mascope-fix-distance"]


def test_each_run_fetches_so_a_fix_merged_since_the_last_one_reports(
    tmp_path, fleet, upstream
):
    upstream.fix("mas-1-token-expiry")
    upstream.release("v1.0.0")
    assert _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"}).returncode == 0

    upstream.fix("mas-2-cookie-scope")
    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    assert "mas-2-cookie-scope" in result.stdout
    assert "mas-1-token-expiry" not in result.stdout


# -- which fixes it sees -----------------------------------------------------


def test_a_fragment_moved_into_the_security_section_marks_its_commit(
    tmp_path, fleet, upstream
):
    upstream.fix("mas-1-token-expiry", section="fixed")
    upstream.release("v1.0.0")
    upstream.git(
        "mv",
        "changelog.d/mas-1-token-expiry.fixed.md",
        "changelog.d/mas-1-token-expiry.security.md",
    )
    upstream.commit("docs: file the fix under security", {})

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    assert "mas-1-token-expiry (merged 2026-10-04, not released)" in result.stdout


def test_a_security_fragment_renamed_is_still_one_fix(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    upstream.fix("mas-1-token-expiry")
    upstream.git(
        "mv",
        "changelog.d/mas-1-token-expiry.security.md",
        "changelog.d/mas-1-session-expiry.security.md",
    )
    upstream.commit("docs: rename the fragment", {})

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert result.stdout.splitlines() == [
        line for line in result.stdout.splitlines() if "mas-1-token-expiry" in line
    ]
    assert _final_ping(fleet)[1].startswith("fixes=1 ")


def test_a_fragment_the_release_compiled_away_still_marks_its_fix(
    tmp_path, fleet, upstream
):
    upstream.release("v1.0.0")
    upstream.fix("mas-1-token-expiry")
    # Cutting a release compiles the fragments into CHANGELOG.md and deletes them.
    upstream.commit(
        "docs(release): prep v1.1.0",
        {"CHANGELOG.md": "## [1.1.0]\n"},
        remove=["changelog.d/mas-1-token-expiry.security.md"],
    )
    upstream.release("v1.1.0")

    result = _run(
        tmp_path, fleet, upstream, {"server-a": "v1.0.0", "server-b": "v1.1.0"}
    )

    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("behind: server-a runs v1.0.0, missing ")
    assert lines[0].endswith(
        "mas-1-token-expiry (merged 2026-10-03, released in v1.1.0)"
    )


def test_a_fix_merged_into_master_alone_counts(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    upstream.git("switch", "-q", "master")
    upstream.fix("mas-1-hotfix")
    upstream.git("switch", "-q", "develop")

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    assert "mas-1-hotfix" in result.stdout


def test_a_dated_build_tag_is_no_release_of_the_fix(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    fix = upstream.fix("mas-1-token-expiry")
    upstream.release(f"v2026.10.02-{fix[:7]}")

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert "not released" in result.stdout
    assert _final_ping(fleet)[1].startswith("fixes=1 unreleased=1 ")


def test_a_candidate_has_the_fixes_its_tag_contains_and_no_later_ones(
    tmp_path, fleet, upstream
):
    upstream.fix("mas-1-token-expiry")
    upstream.release("v1.1.0-rc.1")
    upstream.fix("mas-2-cookie-scope")
    upstream.release("v1.1.0")

    result = _run(
        tmp_path, fleet, upstream, {"server-a": "v1.1.0-rc.1", "server-b": "v1.1.0"}
    )

    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("behind: server-a runs v1.1.0-rc.1, missing ")
    assert "mas-2-cookie-scope" in lines[0]


def test_a_fix_names_the_first_release_that_holds_it(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    upstream.fix("mas-1-token-expiry")
    upstream.release("v1.1.0-rc.1")
    upstream.release("v1.1.0")
    upstream.commit("chore: later", {"later.txt": "x\n"})
    upstream.release("v1.2.0")

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert "released in v1.1.0-rc.1)" in result.stdout


def test_a_fragment_name_it_cannot_print_still_marks_a_fix(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    upstream.commit(
        "fix: oddly named",
        {"changelog.d/$(touch x) fix.security.md": "- **Fixed.**\n"},
    )

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    assert "(fragment name withheld) (merged" in result.stdout
    assert "touch" not in result.stdout


def test_a_release_tag_moved_or_deleted_upstream_is_followed(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    upstream.release("v1.1.0")
    upstream.fix("mas-1-token-expiry")
    first = _run(tmp_path, fleet, upstream, {"server-a": "v1.1.0"})
    assert "missing" in first.stdout

    # The tag moved onto the fix: the clone follows it.
    upstream.git("tag", "-f", "v1.1.0")
    assert _run(tmp_path, fleet, upstream, {"server-a": "v1.1.0"}).returncode == 0

    # The tag went: what the deployment names is no release any more.
    upstream.git("tag", "-d", "v1.1.0")
    gone = _run(tmp_path, fleet, upstream, {"server-a": "v1.1.0"})
    assert gone.stdout.splitlines() == [
        "unreadable: server-a runs v1.1.0, a tag the repository does not have"
    ]


# -- a version it cannot place is a finding ----------------------------------


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        ("unknown", "runs 'unknown', which names no release"),
        ("latest", "runs 'latest', which names no release"),
        ("v2026.10.02-abcdef0", "runs v2026.10.02-abcdef0, which is not a release"),
        ("v9.9.9", "runs v9.9.9, a tag the repository does not have"),
        ("v1.0.0; rm -rf /", "answered no version"),
        (503, "answered HTTP 503"),
        (b"<html>maintenance</html>", "answered no version"),
        (b'{"data": {}}', "answered no version"),
        (b'{"data": {"version": 7}}', "answered no version"),
    ],
)
def test_a_version_that_names_no_release_of_the_repository_is_a_finding(
    tmp_path, fleet, upstream, answer, why
):
    upstream.fix("mas-1-token-expiry")
    upstream.release("v1.0.0")

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0", "server-b": answer})

    assert result.returncode == 1
    assert result.stdout.splitlines() == [f"unreadable: server-b {why}"]
    assert _final_ping(fleet) == (
        "/ping/fail",
        "fixes=1 unreleased=0 deployments=2 behind=0 unreadable=1",
    )


def test_a_deployment_that_does_not_answer_is_a_finding(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    # A port nothing listens on: bound, then closed.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead = sock.getsockname()[1]

    result = _run(
        tmp_path,
        fleet,
        upstream,
        {"server-a": "v1.0.0"},
        urls={"server-a": f"http://127.0.0.1:{dead}"},
    )

    assert result.returncode == 1
    assert result.stdout.startswith("unreadable: server-a no answer (")
    assert _final_ping(fleet)[0] == "/ping/fail"


# -- a check that cannot look fails ------------------------------------------


def test_a_repository_it_cannot_fetch_fails_the_heartbeat(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")

    result = _run(
        tmp_path,
        fleet,
        upstream,
        {"server-a": "v1.0.0"},
        branches="develop no-such-branch",
    )

    assert result.returncode == 3
    assert result.stdout == ""
    assert "fetch failed" in result.stderr
    assert _final_ping(fleet) == ("/ping/fail", "fetch failed: the check is blind")


def test_a_configuration_without_deployments_is_refused(tmp_path, fleet, upstream):
    result = _run(tmp_path, fleet, upstream, {})

    assert result.returncode == 2
    assert "no deployments" in result.stderr
    assert fleet.pings == []


def test_a_configuration_without_a_heartbeat_is_refused(tmp_path, fleet, upstream):
    config = _config(tmp_path, fleet, upstream, ["server-a"])
    config.write_text(
        "\n".join(
            line
            for line in config.read_text().splitlines()
            if not line.startswith("heartbeat")
        )
    )

    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--config", str(config)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "no heartbeat" in result.stderr


def test_a_missing_configuration_is_refused(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--config", str(tmp_path / "none.ini")],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "cannot read" in result.stderr


# -- kept in step -------------------------------------------------------------


def test_the_release_pattern_is_the_runtimes():
    from mascope_runtime import RELEASE_TAG_PATTERN

    spec = importlib.util.spec_from_file_location("security_fix_distance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclasses look their module up while it loads.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]

    assert module.RELEASE_TAG.pattern == RELEASE_TAG_PATTERN.pattern
