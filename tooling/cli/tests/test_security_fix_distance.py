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
    as ``(path, body)``, and answered with no HTTP at all while ``garbled``.
    """

    answers: dict = {}
    pings: list = []
    agents: list = []
    garbled = False

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
        if self.garbled:
            self.wfile.write(b"not http\r\n\r\n")
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def fleet():
    handler = type(
        "FleetHandler",
        (Fleet,),
        {"answers": {}, "pings": [], "agents": [], "garbled": False},
    )
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


def _run(tmp_path, fleet, upstream, answers, urls=None, python=None, **check):
    """One run of the check, with ``answers`` as the deployments' versions."""
    fleet.answers.clear()
    fleet.answers.update(answers)
    fleet.pings.clear()
    config = _config(tmp_path, fleet, upstream, answers, urls, **check)
    return subprocess.run(
        [python or sys.executable, "-I", str(SCRIPT), "--config", str(config)],
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
    # A hotfix lands as the release flow lands it: a PR into master may leave
    # no fragment, so it is compiled away on the branch, and the PR merges
    # with a merge commit. That merge matches master's previous commit on
    # every fragment path, which is what hides the fix from a plain log.
    upstream.git("switch", "-q", "-c", "hotfix", "master")
    fix = upstream.fix("mas-1-hotfix")
    upstream.commit(
        "docs(release): prep v1.0.1",
        {"CHANGELOG.md": "## [1.0.1]\n"},
        remove=["changelog.d/mas-1-hotfix.security.md"],
    )
    upstream.git("switch", "-q", "master")
    upstream.git("merge", "-q", "--no-ff", "-m", "Merge hotfix v1.0.1", "hotfix")
    upstream.release("v1.0.1")
    upstream.git("switch", "-q", "develop")

    result = _run(
        tmp_path, fleet, upstream, {"server-a": "v1.0.0", "server-b": "v1.0.1"}
    )

    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        f"behind: server-a runs v1.0.0, missing {fix[:9]} mas-1-hotfix"
        " (merged 2026-10-03, released in v1.0.1)"
    ]


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


# -- dependency updates marked [security] ------------------------------------


def _uv_lock(packages: dict, root: list, dev: list = (), docs: list = ()) -> str:
    """A ``uv.lock`` in uv's own layout.

    ``packages`` maps a name to ``(version, dependencies, extras)``, a
    dependency written ``"name"``, ``"name[extra]"`` or ``"name==1.0[extra]"``
    (the entry uv writes for a package with several versions in the lock,
    its ``source`` table before the extra); ``root``, ``dev`` and ``docs``
    are the root project's dependencies and two of its groups.
    """

    def entries(deps):
        lines = []
        for dep in deps:
            name, _, extra = dep.partition("[")
            name, _, pinned = name.partition("==")
            if pinned:
                lines.append(
                    f'    {{ name = "{name}", version = "{pinned}", source = {{ '
                    'registry = "https://pypi.org/simple" }, '
                    f'extra = ["{extra[:-1]}"], marker = "sys_platform == \'linux\'" }},'
                )
            elif extra:
                lines.append(f'    {{ name = "{name}", extra = ["{extra[:-1]}"] }},')
            else:
                lines.append(
                    f'    {{ name = "{name}", marker = "sys_platform != \'win32\'" }},'
                )
        return lines

    out = ["version = 1", "revision = 3", 'requires-python = "==3.12.*"', ""]
    out += ["[manifest]", 'members = ["mascope"]', ""]
    out += ["[[package]]", 'name = "mascope"', 'version = "0.0.0"']
    out += ['source = { editable = "." }', "dependencies = ["]
    out += entries(root) + ["]", "", "[package.dev-dependencies]", "dev = ["]
    out += entries(dev) + ["]", "docs = ["] + entries(docs) + ["]", ""]
    for name, (version, deps, extras) in sorted(packages.items()):
        out += ["[[package]]", f'name = "{name}"', f'version = "{version}"']
        out += ['source = { registry = "https://pypi.org/simple" }']
        if deps:
            out += ["dependencies = ["] + entries(deps) + ["]"]
        out += ["wheels = [", f'    {{ url = "https://example.invalid/{name}.whl" }},']
        out += ["]", ""]
        if extras:
            out += ["[package.optional-dependencies]"]
            for extra, extra_deps in extras.items():
                out += [f"{extra} = ["] + entries(extra_deps) + ["]"]
            out += [""]
        out += ["[package.metadata]", f'requires-dist = [{{ name = "{name}" }}]', ""]
    return "\n".join(out)


#: The root ships fastapi, which pulls starlette, uvicorn with its
#: ``standard`` extra, and gunicorn through a forked entry with its ``gevent``
#: extra; the docs group ships mkdocs-material and its pymdown-extensions; the
#: dev group alone reaches pytest and its pluggy.
UV_PACKAGES = {
    "fastapi": ("1.0", ["starlette"], {}),
    "starlette": ("1.0", [], {}),
    "uvicorn": ("1.0", [], {"standard": ["httptools"], "unused": ["watchfiles"]}),
    "httptools": ("1.0", [], {}),
    "watchfiles": ("1.0", [], {}),
    "gunicorn": ("1.0", [], {"gevent": ["gevent"]}),
    "gevent": ("1.0", [], {}),
    "mkdocs-material": ("1.0", ["pymdown-extensions"], {}),
    "pymdown-extensions": ("1.0", [], {}),
    "pytest": ("1.0", ["pluggy"], {}),
    "pluggy": ("1.0", [], {}),
}
UV_ROOT = ["fastapi", "uvicorn[standard]", "gunicorn==1.0[gevent]"]
UV_DOCS = ["mkdocs-material"]


def _uv_bump(upstream, message, path="uv.lock", **versions):
    packages = {
        name: (versions.get(name, version), deps, extras)
        for name, (version, deps, extras) in UV_PACKAGES.items()
    }
    lock = _uv_lock(packages, UV_ROOT, dev=["pytest"], docs=UV_DOCS)
    return upstream.commit(message, {path: lock})


def _npm_lock(packages: dict) -> str:
    """A ``package-lock.json``, v3: ``packages`` maps a name to (version, dev)."""
    entries = {"": {"name": "mascope", "version": "2.0"}}
    for name, (version, dev) in packages.items():
        entries[f"node_modules/{name}"] = {
            "version": version,
            **({"dev": True} if dev else {}),
        }
    return json.dumps({"name": "mascope", "lockfileVersion": 3, "packages": entries})


NPM = "server/frontend/package-lock.json"


@pytest.fixture
def locked(upstream):
    """Upstream with both lockfiles, released as v1.0.0."""
    _uv_bump(upstream, "chore: lock")
    upstream.commit(
        "chore: npm lock",
        {NPM: _npm_lock({"axios": ("1.0", False), "vite": ("1.0", True)})},
    )
    upstream.release("v1.0.0")
    return upstream


def _fixes(fleet):
    """The ``fixes=`` count of the run's last ping."""
    return int(_final_ping(fleet)[1].split()[0].removeprefix("fixes="))


def test_a_security_update_of_a_shipped_python_dependency_reports(
    tmp_path, fleet, locked
):
    fix = _uv_bump(
        locked,
        "chore(deps): update dependency fastapi to v1.1 [security]",
        fastapi="1.1",
    )

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [
        f"behind: server-a runs v1.0.0, missing {fix[:9]} deps-fastapi"
        " (merged 2026-10-05, not released)"
    ]
    assert _final_ping(fleet) == (
        "/ping/fail",
        "fixes=1 unreleased=1 deployments=1 behind=1 unreadable=0",
    )


@pytest.mark.parametrize(
    "package",
    [
        # Reached through fastapi's dependencies, through the extra the root
        # asks uvicorn for, through the extra of a forked entry, and through
        # the docs group the frontend image builds and serves.
        "starlette",
        "httptools",
        "gevent",
        "mkdocs-material",
        "pymdown-extensions",
    ],
)
def test_a_dependency_the_root_reaches_ships(tmp_path, fleet, locked, package):
    _uv_bump(
        locked,
        f"chore(deps): update dependency {package} [security]",
        **{package: "2.0"},
    )

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    assert f"deps-{package} (merged" in result.stdout


@pytest.mark.parametrize(
    "package",
    [
        # The dev group's own dependency, the one it pulls in, and an extra no
        # shipped package asks for.
        "pytest",
        "pluggy",
        "watchfiles",
    ],
)
def test_a_security_update_of_development_tooling_alone_is_quiet(
    tmp_path, fleet, locked, package
):
    _uv_bump(
        locked,
        f"chore(deps): update dependency {package} [security]",
        **{package: "2.0"},
    )

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 0, result.stdout
    assert _fixes(fleet) == 0


def test_a_security_update_of_a_production_npm_package_reports(tmp_path, fleet, locked):
    locked.commit(
        "chore(deps): update dependency axios to v1.1 [security]",
        {
            NPM: _npm_lock(
                {
                    "axios": ("1.1", False),
                    "@agent/base": ("1.0", False),
                    "vite": ("1.0", True),
                }
            )
        },
    )

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    # Named after the package the subject names, not the first in order.
    assert "deps-axios-and-1-more (merged" in result.stdout


def test_a_version_1_npm_lock_keeps_its_dev_marks(tmp_path, fleet, upstream):
    # A lock of version 1 nests a package under the one that needs it.
    def v1(redirects, esbuild):
        return json.dumps(
            {
                "lockfileVersion": 1,
                "dependencies": {
                    "axios": {
                        "version": "1.0",
                        "dependencies": {"follow-redirects": {"version": redirects}},
                    },
                    "vite": {
                        "version": "1.0",
                        "dev": True,
                        "dependencies": {"esbuild": {"version": esbuild, "dev": True}},
                    },
                },
            }
        )

    upstream.commit("chore: npm lock", {NPM: v1("1.0", "1.0")})
    upstream.release("v1.0.0")
    upstream.commit("chore(deps): update esbuild [security]", {NPM: v1("1.0", "1.1")})
    upstream.commit(
        "chore(deps): update follow-redirects [security]", {NPM: v1("1.1", "1.1")}
    )

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert "deps-follow-redirects (merged" in result.stdout
    assert _fixes(fleet) == 1


def test_a_security_update_of_an_npm_dev_dependency_is_quiet(tmp_path, fleet, locked):
    locked.commit(
        "chore(deps): update dependency vite to v1.1 [security]",
        {NPM: _npm_lock({"axios": ("1.0", False), "vite": ("1.1", True)})},
    )

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 0, result.stdout
    assert _fixes(fleet) == 0


def test_a_shipped_package_a_security_update_takes_out_reports(tmp_path, fleet, locked):
    locked.commit(
        "chore(deps): drop the vulnerable proxy [security]",
        {NPM: _npm_lock({"vite": ("1.0", True)})},
    )

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    assert "deps-axios (merged" in result.stdout


def test_a_bump_without_the_security_mark_is_no_fix(tmp_path, fleet, locked):
    _uv_bump(locked, "chore(deps): update dependency fastapi to v1.1", fastapi="1.1")

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 0, result.stdout
    assert _fixes(fleet) == 0


@pytest.mark.parametrize(
    "subject",
    [
        "docs: explain the [security] mark",
        "chore(deps): update fastapi [security] and pin it",
    ],
)
def test_a_security_mark_not_at_the_end_is_no_fix(tmp_path, fleet, locked, subject):
    _uv_bump(locked, subject, fastapi="1.1")

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 0, result.stdout
    assert _fixes(fleet) == 0


def test_a_lockfile_no_deployment_ships_is_not_read(tmp_path, fleet, locked):
    _uv_bump(
        locked,
        "chore(deps): update dependency fastapi to v1.1 [security]",
        path="agents/file/uv.lock",
        fastapi="1.1",
    )

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 0, result.stdout
    assert _fixes(fleet) == 0


def test_a_lockfile_it_cannot_read_counts_as_a_fix(tmp_path, fleet, locked):
    locked.commit("chore(deps): update everything [SECURITY]", {"uv.lock": "garbled\n"})

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    assert "deps-unreadable-uv.lock (merged" in result.stdout


def test_a_security_update_released_is_quiet_on_the_release(tmp_path, fleet, locked):
    _uv_bump(
        locked, "chore(deps): update dependency fastapi [security] (#12)", fastapi="1.1"
    )
    locked.release("v1.1.0")

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.1.0", "server-b": "v1.0.0"})

    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("behind: server-b runs v1.0.0, missing ")
    assert lines[0].endswith("deps-fastapi (merged 2026-10-05, released in v1.1.0)")


def test_a_fragment_and_a_security_mark_on_one_commit_are_one_fix(
    tmp_path, fleet, locked
):
    packages = {
        n: (("1.1" if n == "fastapi" else v), d, e)
        for n, (v, d, e) in UV_PACKAGES.items()
    }
    locked.commit(
        "fix(deps): fastapi 1.1 [security]",
        {
            "uv.lock": _uv_lock(packages, UV_ROOT, dev=["pytest"], docs=UV_DOCS),
            "changelog.d/mas-9-fastapi.security.md": "- **Fastapi.**\n",
        },
    )
    locked.fix("mas-10-cookie-scope")

    result = _run(tmp_path, fleet, locked, {"server-a": "v1.0.0"})

    assert result.returncode == 1
    assert "mas-9-fastapi (merged" in result.stdout
    assert "mas-10-cookie-scope (merged" in result.stdout
    assert "deps-" not in result.stdout
    assert _fixes(fleet) == 2


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
        (b"[" * 4000, "answered no version"),
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


def test_a_body_nested_past_the_recursion_limit_ends_no_run(tmp_path, fleet, upstream):
    # python3.10, the oldest the host may run, decodes JSON recursively and
    # gives up within MAX_ANSWER with a RecursionError; a newer one may decode
    # it as a plain error, so only that interpreter shows the difference.
    python = shutil.which("python3.10") or pytest.skip("no python3.10 on PATH")
    upstream.fix("mas-1-token-expiry")
    upstream.release("v1.0.0")

    result = _run(
        tmp_path,
        fleet,
        upstream,
        {"server-a": b"[" * 4000, "server-b": "v1.0.0"},
        python=python,
    )

    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == ["unreadable: server-a answered no version"]
    assert _final_ping(fleet)[0] == "/ping/fail"


def test_a_deployment_url_urllib_cannot_open_is_a_finding(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")

    result = _run(
        tmp_path,
        fleet,
        upstream,
        {"server-a": "v1.0.0", "server-b": "v1.0.0"},
        urls={"server-a": "http://[server-a"},
    )

    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines() == [
        "unreadable: server-a has a URL that cannot be opened"
    ]


# -- a heartbeat that misbehaves ends no run ---------------------------------


def test_a_heartbeat_that_answers_no_http_ends_no_run(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    upstream.fix("mas-1-token-expiry")
    fleet.garbled = True

    result = _run(tmp_path, fleet, upstream, {"server-a": "v1.0.0"})

    assert result.returncode == 1, result.stderr
    assert "mas-1-token-expiry" in result.stdout
    # Each ping is tried three times, the /start before the check included.
    assert [path for path, _ in fleet.pings] == ["/ping/start"] * 3 + ["/ping/fail"] * 3


def test_a_heartbeat_url_urllib_cannot_open_ends_no_run(tmp_path, fleet, upstream):
    upstream.release("v1.0.0")
    upstream.fix("mas-1-token-expiry")
    fleet.answers.update({"server-a": "v1.0.0"})
    config = _config(tmp_path, fleet, upstream, ["server-a"])
    config.write_text(
        config.read_text().replace(
            f"heartbeat = {fleet.url}/ping", "heartbeat = http://[hc"
        )
    )

    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--config", str(config)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **GIT_ENV, "no_proxy": "*"},
    )

    assert result.returncode == 1, result.stderr
    assert "mas-1-token-expiry" in result.stdout
    assert "Traceback" not in result.stderr


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
