"""
Tests for `mascope test run` command construction.

The command only assembles pytest/npm invocations and delegates to
`lib.run`; asserting on the assembled command strings covers the
component/module routing table without running any actual suite.
"""

import os

import pytest

from mascope_cli.checkout import source_checkout
from mascope_cli.cmd import lib
from mascope_cli.main import app


class _FakeCompleted:
    """Stand-in for the CompletedProcess `lib.run` returns."""

    def __init__(self, returncode: int = 0):
        self.returncode = returncode


class _Calls(list):
    """Recorded lib.run calls, plus the codes to answer them with.

    Append to `returncodes` to hand out an exit code per call, in order;
    anything past the end answers 0.
    """

    returncodes: list[int]


@pytest.fixture
def recorded_runs(monkeypatch):
    """Capture lib.run calls issued by the test command."""
    calls = _Calls()
    calls.returncodes = []

    def fake_run(command, cwd=None, **kwargs):
        calls.append({"command": command, "cwd": cwd})
        codes = calls.returncodes
        code = codes[len(calls) - 1] if len(calls) <= len(codes) else 0
        return _FakeCompleted(code)

    monkeypatch.setattr(lib, "run", fake_run)
    return calls


def _commands(calls):
    return [c["command"] for c in calls]


def test_default_runs_backend_and_libraries(cli_runner, recorded_runs):
    result = cli_runner.invoke(app, ["test", "run"])

    assert result.exit_code == 0
    commands = _commands(recorded_runs)
    assert "pytest server/backend/tests/" in commands
    assert "pytest libraries/" in commands


def test_backend_module_scopes_the_path(cli_runner, recorded_runs):
    result = cli_runner.invoke(app, ["test", "run", "-m", "unit"])

    assert result.exit_code == 0
    assert _commands(recorded_runs) == ["pytest server/backend/tests/unit/"]


def test_library_module_routes_to_libraries(cli_runner, recorded_runs):
    result = cli_runner.invoke(app, ["test", "run", "-m", "sdk"])

    assert result.exit_code == 0
    assert _commands(recorded_runs) == [
        "pytest libraries/sdk/",
        "pytest libraries/sdk/src --doctest-modules",
    ]


def test_verbose_flag_is_forwarded(cli_runner, recorded_runs):
    result = cli_runner.invoke(app, ["test", "run", "backend", "-v"])

    assert result.exit_code == 0
    assert _commands(recorded_runs) == ["pytest server/backend/tests/ -v"]


def test_frontend_runs_vitest_in_frontend_dir(cli_runner, recorded_runs):
    result = cli_runner.invoke(app, ["test", "run", "frontend"])

    assert result.exit_code == 0
    npm = "npm.cmd" if os.name == "nt" else "npm"
    assert _commands(recorded_runs) == [f"{npm} run test:unit"]
    assert recorded_runs[0]["cwd"] == os.path.join("server", "frontend")


# ============= Library doctests and working directory =============


def test_library_doctests_run_over_src_not_the_tests_tree(cli_runner, recorded_runs):
    """Doctests are a second pass over source trees only.

    Collecting them alongside the tests directories used to abort the whole
    run: every library ships its own tests/conftest.py, and the second one
    collected is an import file mismatch under pytest's default import mode.
    """
    result = cli_runner.invoke(app, ["test", "run", "libraries"])

    assert result.exit_code == 0
    commands = _commands(recorded_runs)
    assert commands[0] == "pytest libraries/"
    assert "--doctest-modules" not in commands[0]
    assert commands[1].startswith("pytest libraries/")
    assert commands[1].endswith("--doctest-modules")
    assert "/src" in commands[1]
    assert "libraries/tools/tests" not in commands[1]


def test_tests_run_from_the_source_checkout(cli_runner, recorded_runs):
    """Not from MASCOPE_PATH, which normally points at another checkout.

    lib.run defaults a subprocess to the runtime home. A worktree carries its
    own code, so collecting there while importing the worktree's installed
    packages fails on import file mismatch - or silently tests the wrong tree.
    """
    result = cli_runner.invoke(app, ["test", "run", "libraries"])

    assert result.exit_code == 0
    root = source_checkout()
    assert root is not None
    for call in recorded_runs:
        assert call["cwd"] == str(root)
    assert str(root) != os.environ["MASCOPE_PATH"]


def test_a_failing_suite_fails_the_command(cli_runner, recorded_runs):
    """The command used to report success whatever pytest answered."""
    recorded_runs.returncodes.append(1)

    result = cli_runner.invoke(app, ["test", "run", "libraries"])

    assert result.exit_code == 1


def test_a_library_without_doctests_is_not_a_failure(cli_runner, recorded_runs):
    """pytest answers 5 for "no tests collected"; that is not a failure here."""
    recorded_runs.returncodes.extend([0, 5])

    result = cli_runner.invoke(app, ["test", "run", "libraries", "-m", "sdk"])

    assert result.exit_code == 0
