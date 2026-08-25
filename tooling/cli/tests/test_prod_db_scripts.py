"""
Tests for the production db script runner.

Everything the runner needs lives in the backend container: the interpreter
path differs between image builds (``/opt/uv/tools`` on current images,
``/root/.local/share/uv/tools`` on legacy ones), and the scripts themselves
are only guaranteed to exist there - the standalone operator CLI ships without
``mascope_backend``. So the runner probes the container at runtime for both,
and these tests drive it with ``subprocess.run`` stubbed to answer as the
container would.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from loguru import logger
from typer.testing import CliRunner

from mascope_cli.cmd.prod.db import scripts


runner = CliRunner()

# Guarded the same way as test_systemd_units.py: repo-root tooling/systemd is
# not present in every checkout or packaged layout, and a missing directory
# should skip rather than fail with an unrelated FileNotFoundError.
SYSTEMD_DIR = Path(__file__).resolve().parents[3] / "tooling" / "systemd"

CONTAINER = "mascope_prod_backend"
CONTAINER_PYTHON = "/opt/uv/tools/mascope/bin/python"


def _fake_run(returncode: int, stdout: str):
    """Return a subprocess.run stand-in yielding a fixed CompletedProcess."""

    def _run(cmd, capture_output=False, text=False, check=False):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")

    return _run


class FakeContainer:
    """
    Stand in for ``subprocess.run`` against the backend container.

    Answers the runner's three ``docker exec`` calls the way the container
    would: the ``sh -c`` interpreter probe, the ``python -c`` script listing,
    and the ``python -m`` script run. Records every command so a test can
    assert on what would have reached docker.
    """

    def __init__(self, *, python=CONTAINER_PYTHON, names=(), run_exit=0):
        self.python = python
        self.names = list(names)
        self.run_exit = run_exit
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output=False, text=False, check=False):  # noqa: ARG002
        self.calls.append(list(cmd))
        if cmd[:2] != ["docker", "exec"]:
            raise AssertionError(f"unexpected command: {cmd}")
        if "sh" in cmd:
            if self.python is None:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, self.python + "\n", "")
        if "-c" in cmd:
            assert cmd[cmd.index("-c") - 1] == self.python
            return subprocess.CompletedProcess(
                cmd, 0, "".join(f"{n}\n" for n in self.names), ""
            )
        if "-m" in cmd:
            return subprocess.CompletedProcess(cmd, self.run_exit, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    @property
    def executed_modules(self) -> list[str]:
        return [cmd[cmd.index("-m") + 1] for cmd in self.calls if "-m" in cmd]


@pytest.fixture
def no_host_backend(monkeypatch):
    """
    Make the host look like an operator install: no ``mascope_backend``.

    ``None`` in ``sys.modules`` is the import system's "known missing" marker,
    so ``import mascope_backend`` - and ``find_spec`` on anything beneath it -
    raises ModuleNotFoundError exactly as on a host where the package was never
    installed. Cached submodules are dropped too, or ``find_spec`` would hand
    back the cached package without consulting the parent.
    """
    for name in list(sys.modules):
        if name == "mascope_backend" or name.startswith("mascope_backend."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "mascope_backend", None)


@pytest.fixture
def stack_up(monkeypatch):
    """Pretend Docker and the database config are in place."""
    monkeypatch.setattr(scripts, "check_prerequisites", lambda _mode: True)


@pytest.fixture
def no_backup(monkeypatch):
    """Fail the test if the runner reaches for pg_dump."""

    def _refuse(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("pg_dump must not run")

    monkeypatch.setattr(scripts, "pg_dump", _refuse)


@pytest.fixture
def log_lines():
    """
    Collect the runner's log messages.

    The CLI's terminal sink is bound to the real stdout at configure time, so
    CliRunner's captured output never sees loguru records; a sink of our own
    does.
    """
    lines: list[str] = []
    handler = logger.add(
        lambda message: lines.append(message.record["message"]), level="DEBUG"
    )
    yield lines
    logger.remove(handler)


# --- discovery ------------------------------------------------------------


def test_host_discovery_is_empty_without_the_backend_package(no_host_backend):
    # The original crash: find_spec imports the parent package first, so on an
    # operator install it raised ModuleNotFoundError out of `list` and `run`
    # instead of returning None.
    assert scripts._discover_host_scripts() == {}


def test_container_discovery_asks_the_container_python(monkeypatch):
    container = FakeContainer(names=["prune_peak_assignment_runs", "seed_demo"])
    monkeypatch.setattr(scripts.subprocess, "run", container)

    found = scripts._discover_container_scripts(CONTAINER, CONTAINER_PYTHON)

    assert found == {
        "prune_peak_assignment_runs": (
            "mascope_backend.db.scripts.prune_peak_assignment_runs"
        ),
        "seed_demo": "mascope_backend.db.scripts.seed_demo",
    }
    (cmd,) = container.calls
    assert cmd[:3] == ["docker", "exec", CONTAINER]
    assert cmd[3:5] == [CONTAINER_PYTHON, "-c"]


def test_container_discovery_reports_a_failed_probe_as_unknown(monkeypatch):
    # None, not {}: the caller must be able to tell "could not ask" from "an
    # empty package", because only the former warrants the host fallback.
    monkeypatch.setattr(scripts.subprocess, "run", _fake_run(1, ""))
    assert scripts._discover_container_scripts(CONTAINER, CONTAINER_PYTHON) is None


def test_container_discovery_ignores_noise_on_stdout(monkeypatch):
    # Whatever else ends up on stdout, only names that can follow `-m` as a
    # module path are offered.
    monkeypatch.setattr(
        scripts.subprocess,
        "run",
        _fake_run(0, "\nseed_demo\nnot a module\n  \n"),
    )
    found = scripts._discover_container_scripts(CONTAINER, CONTAINER_PYTHON)
    assert list(found) == ["seed_demo"]


def test_discovery_prefers_the_container_over_the_host(monkeypatch):
    monkeypatch.setattr(
        scripts.subprocess, "run", FakeContainer(names=["from_container"])
    )
    monkeypatch.setattr(
        scripts, "_discover_host_scripts", lambda: {"from_host": "x.from_host"}
    )
    assert list(scripts._discover_scripts(CONTAINER, CONTAINER_PYTHON)) == [
        "from_container"
    ]


def test_discovery_falls_back_to_the_host_without_a_container_python(monkeypatch):
    monkeypatch.setattr(
        scripts, "_discover_host_scripts", lambda: {"from_host": "x.from_host"}
    )
    assert list(scripts._discover_scripts(CONTAINER, None)) == ["from_host"]


def test_the_listing_snippet_lists_the_package_by_file(tmp_path):
    # The snippet runs inside the container, where no other test reaches, so
    # run it here against a synthetic package laid out like the real one: a
    # regular mascope_backend.db with a namespace scripts/ directory (no
    # __init__.py). Modules are listed; a subpackage, a private helper and
    # __pycache__ are not.
    pkg = tmp_path / "mascope_backend"
    (pkg / "db" / "scripts" / "helpers").mkdir(parents=True)
    (pkg / "db" / "scripts" / "__pycache__").mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "db" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "db" / "scripts" / "helpers" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    for name in ("prune_peak_assignment_runs", "require_password_change", "_shared"):
        (pkg / "db" / "scripts" / f"{name}.py").write_text(
            "def main():\n    pass\n", encoding="utf-8"
        )
    (pkg / "db" / "scripts" / "__pycache__" / "stale.cpython-312.pyc").write_bytes(b"")

    # PYTHONPATH first so the synthetic package shadows the workspace one.
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", scripts._LIST_SCRIPTS_SNIPPET],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [
        "prune_peak_assignment_runs",
        "require_password_change",
    ]


# --- list / run on an operator install --------------------------------------


def test_list_shows_the_container_scripts_without_a_host_package(
    no_host_backend, monkeypatch, log_lines
):
    container = FakeContainer(
        names=["prune_peak_assignment_runs", "require_password_change"]
    )
    monkeypatch.setattr(scripts.subprocess, "run", container)

    result = runner.invoke(scripts.prod_db_scripts_app, ["list"])

    assert result.exit_code == 0, result.output
    assert "  prune_peak_assignment_runs" in log_lines
    assert "  require_password_change" in log_lines
    # Probe, then listing - both inside the backend container.
    assert [cmd[2] for cmd in container.calls] == [CONTAINER, CONTAINER]


def test_list_fails_clearly_when_the_stack_is_down_on_an_operator_install(
    no_host_backend, monkeypatch, log_lines
):
    monkeypatch.setattr(scripts.subprocess, "run", FakeContainer(python=None))

    result = runner.invoke(scripts.prod_db_scripts_app, ["list"])

    assert result.exit_code == 1
    assert any(CONTAINER in line and "must be running" in line for line in log_lines), (
        log_lines
    )


def test_list_falls_back_to_the_host_install_when_the_stack_is_down(
    monkeypatch, log_lines
):
    # A monorepo install still has something to show - flagged as such, since
    # the host's copy need not match the deployed image.
    monkeypatch.setattr(scripts.subprocess, "run", FakeContainer(python=None))
    monkeypatch.setattr(
        scripts, "_discover_host_scripts", lambda: {"seed_demo": "x.seed_demo"}
    )

    result = runner.invoke(scripts.prod_db_scripts_app, ["list"])

    assert result.exit_code == 0, result.output
    assert "  seed_demo" in log_lines
    assert any("not running" in line for line in log_lines), log_lines


def test_run_executes_a_container_script_without_a_host_package(
    no_host_backend, stack_up, no_backup, monkeypatch
):
    # The nightly mascope-assignment-prune unit's exact invocation, on a host
    # with only the operator CLI installed - previously a ModuleNotFoundError
    # on every timer firing.
    container = FakeContainer(names=["prune_peak_assignment_runs"])
    monkeypatch.setattr(scripts.subprocess, "run", container)
    monkeypatch.setenv("MASCOPE_PRUNE_DRY_RUN", "1")

    result = runner.invoke(
        scripts.prod_db_scripts_app,
        ["run", "prune_peak_assignment_runs", "--yes", "--skip-backup"],
    )

    assert result.exit_code == 0, result.output
    assert container.executed_modules == [
        "mascope_backend.db.scripts.prune_peak_assignment_runs"
    ]
    run_cmd = container.calls[-1]
    assert run_cmd[: run_cmd.index(CONTAINER)] == [
        "docker",
        "exec",
        "-e",
        "MASCOPE_PRUNE_DRY_RUN=1",
    ]
    assert run_cmd[run_cmd.index(CONTAINER) + 1] == CONTAINER_PYTHON


def test_run_refuses_an_unknown_script_before_backing_up(
    no_host_backend, stack_up, no_backup, monkeypatch
):
    container = FakeContainer(names=["prune_peak_assignment_runs"])
    monkeypatch.setattr(scripts.subprocess, "run", container)

    result = runner.invoke(
        scripts.prod_db_scripts_app, ["run", "prune_peak_assignmnet_runs", "--yes"]
    )

    assert result.exit_code == 1
    assert container.executed_modules == []


def test_run_fails_before_backing_up_when_the_stack_is_down(
    no_host_backend, stack_up, no_backup, monkeypatch, log_lines
):
    # Previously the backup was taken first and the run failed afterwards.
    container = FakeContainer(python=None)
    monkeypatch.setattr(scripts.subprocess, "run", container)

    result = runner.invoke(
        scripts.prod_db_scripts_app, ["run", "prune_peak_assignment_runs", "--yes"]
    )

    assert result.exit_code == 1
    assert container.executed_modules == []
    assert any("must be running" in line for line in log_lines), log_lines


def test_run_propagates_the_script_exit_code(
    no_host_backend, stack_up, no_backup, monkeypatch
):
    container = FakeContainer(names=["prune_peak_assignment_runs"], run_exit=3)
    monkeypatch.setattr(scripts.subprocess, "run", container)

    result = runner.invoke(
        scripts.prod_db_scripts_app,
        ["run", "prune_peak_assignment_runs", "--yes", "--skip-backup"],
    )

    assert result.exit_code == 3


# --- interpreter resolution -------------------------------------------------


def test_resolves_first_existing_tool_python(monkeypatch):
    # The probe echoes the first candidate that exists in the container.
    monkeypatch.setattr(
        scripts.subprocess,
        "run",
        _fake_run(0, "/opt/uv/tools/mascope/bin/python\n"),
    )
    assert (
        scripts._resolve_container_python("mascope_prod_backend")
        == "/opt/uv/tools/mascope/bin/python"
    )


def test_resolves_path_fallback_interpreter(monkeypatch):
    # When no tool path exists, the probe falls back to a PATH python that can
    # import mascope_backend and prints its resolved location.
    monkeypatch.setattr(
        scripts.subprocess, "run", _fake_run(0, "/usr/local/bin/python\n")
    )
    assert (
        scripts._resolve_container_python("mascope_prod_backend")
        == "/usr/local/bin/python"
    )


def test_returns_none_when_no_interpreter_found(monkeypatch):
    # Probe exits non-zero (nothing found) -> None, so the caller errors clearly.
    monkeypatch.setattr(scripts.subprocess, "run", _fake_run(1, ""))
    assert scripts._resolve_container_python("mascope_prod_backend") is None


def test_returns_none_without_a_docker_binary(monkeypatch):
    # `list` probes before any prerequisite check, so a host without docker
    # must read as "container unreachable", not a traceback.
    def _no_docker(*args, **kwargs):  # noqa: ARG001
        raise FileNotFoundError("docker")

    monkeypatch.setattr(scripts.subprocess, "run", _no_docker)
    assert scripts._resolve_container_python("mascope_prod_backend") is None


def test_uses_the_current_dockerfile_path_first():
    # Guard the ordering: current images (UV_TOOL_DIR=/opt/uv/tools) must be
    # preferred over the legacy /root location.
    assert scripts._PYTHON_CANDIDATES[0] == "/opt/uv/tools/mascope/bin/python"
    assert (
        "/root/.local/share/uv/tools/mascope/bin/python" in scripts._PYTHON_CANDIDATES
    )


def test_every_env_var_the_db_scripts_read_is_forwarded():
    # The prod runner only passes allowlisted env vars through `docker exec -e`;
    # a var missing from the list is silently unset inside the container. For
    # the prune script that silence is dangerous in both directions: the
    # documented MASCOPE_PRUNE_DRY_RUN=1 recipe would delete for real, and a
    # retention knob an operator sets in /etc/mascope/prune.env would have no
    # effect at all while appearing to.
    #
    # Derived from the scripts rather than hand-listed. The hand-listed version
    # of this test stayed green through two knobs being added without the
    # allowlist following them, because a list that has to be edited alongside
    # the thing it guards is not a guard - it is a second copy.
    #
    # Every SCREAMING_CASE name, not only the MASCOPE_-prefixed ones: five of
    # the thirteen forwarded vars (DRY_RUN, BATCH_SIZE, MIN_DATETIME,
    # UTC_OFFSET_HOURS, ALLOW_MATCHED_LOSS) carry no prefix, so a prefix-only
    # pattern is blind to the majority spelling of exactly what it guards.
    # Anchoring on the env-reading calls keeps that from over-matching: no
    # script reads an unrelated variable (PATH, HOME) through them.
    script_dir = (
        Path(__file__).resolve().parents[3]
        / "server"
        / "backend"
        / "src"
        / "mascope_backend"
        / "db"
        / "scripts"
    )
    if not script_dir.is_dir():
        pytest.skip("backend sources not present in this checkout")

    read_by_scripts = set()
    for source in script_dir.glob("*.py"):
        read_by_scripts.update(
            re.findall(
                r'(?:_int_env|_bool_env|os\.environ\.get|os\.getenv)\(\s*"([A-Z][A-Z0-9_]*)"',
                source.read_text(encoding="utf-8"),
            )
        )

    assert read_by_scripts, "found no env reads; the pattern above has gone stale"
    missing = sorted(read_by_scripts - set(scripts._FORWARDED_ENV_VARS))
    assert not missing, (
        f"{missing} are read by a db script but not forwarded into the "
        "container, so an operator setting them gets silence"
    )


def test_password_change_env_vars_are_forwarded_into_the_container():
    # Same trap as above, with the same shape of consequence: an unforwarded
    # MASCOPE_REQUIRE_PASSWORD_CHANGE_DRY_RUN arrives unset inside the
    # container, so the documented dry run would require a password change on
    # every account for real. The email list narrows which accounts the undo
    # releases; unset, it would release all of them.
    for var in (
        "MASCOPE_REQUIRE_PASSWORD_CHANGE_DRY_RUN",
        "MASCOPE_CLEAR_PASSWORD_CHANGE_EMAILS",
    ):
        assert var in scripts._FORWARDED_ENV_VARS


def test_skip_backup_warns_an_interactive_operator():
    # A human at the terminal can still abort at the confirmation prompt that
    # prints right after, so the notice is a real warning.
    assert scripts.skip_backup_log_level(False) == "WARNING"


def test_skip_backup_stays_below_warning_when_unattended():
    # Under --yes there is nobody to react, and --skip-backup is a settled
    # configuration choice rather than a moment of risk: the nightly
    # mascope-assignment-prune unit passes it on every firing by design. At
    # WARNING it reached error monitoring, minting an event per server per
    # night that no operator could ever act on.
    assert scripts.skip_backup_log_level(True) == "INFO"


@pytest.mark.skipif(
    not SYSTEMD_DIR.is_dir(), reason="repo-root tooling/systemd not available"
)
def test_the_prune_unit_runs_unattended():
    # The level choice above only keeps the timer quiet because the unit
    # passes --yes alongside --skip-backup. If it ever drops --yes, the
    # nightly noise returns - and the run would block on a prompt no one
    # answers.
    unit = (SYSTEMD_DIR / "mascope-assignment-prune.service").read_text(
        encoding="utf-8"
    )
    exec_start = next(
        line for line in unit.splitlines() if line.startswith("ExecStart=")
    )
    assert "--skip-backup" in exec_start
    assert "--yes" in exec_start
