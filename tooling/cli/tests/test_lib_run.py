"""
Tests for `mascope_cli.cmd.lib.run` — the subprocess wrapper every CLI
command shells out through.

The env-merging contract matters: compose invocations rely on caller-supplied
variables (DB name, version tag) winning over inherited ones, while
process-level vars (MASCOPE_LOGLEVEL etc.) must still reach the child.
"""

import shlex
import subprocess
import sys

from mascope_cli.cmd import lib
from mascope_cli.runtime import runtime


def _capture_run(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(lib.subprocess, "run", fake_run)
    return captured


def test_inherited_env_reaches_subprocess(monkeypatch):
    captured = _capture_run(monkeypatch)
    monkeypatch.setenv("INHERITED_VAR", "from-parent")

    lib.run("echo hi", env_vars={"EXTRA_VAR": "added"})

    assert captured["env"]["INHERITED_VAR"] == "from-parent"
    assert captured["env"]["EXTRA_VAR"] == "added"


def test_caller_env_vars_win_over_inherited(monkeypatch):
    captured = _capture_run(monkeypatch)
    monkeypatch.setenv("CLASH", "inherited")

    lib.run("echo hi", env_vars={"CLASH": "caller"})

    assert captured["env"]["CLASH"] == "caller"


def test_command_is_shlex_split_not_shell(monkeypatch):
    captured = _capture_run(monkeypatch)

    lib.run("docker compose --file 'a path' up")

    assert captured["args"] == ["docker", "compose", "--file", "a path", "up"]


def test_cwd_defaults_to_runtime_path(monkeypatch):
    captured = _capture_run(monkeypatch)

    lib.run("echo hi")

    assert captured["cwd"] == runtime.path()


def test_explicit_cwd_is_honored(monkeypatch, tmp_path):
    captured = _capture_run(monkeypatch)

    lib.run("echo hi", cwd=str(tmp_path))

    assert captured["cwd"] == str(tmp_path)


def test_returncode_passes_through_real_subprocess(mascope_home):
    # One real subprocess to keep the wrapper honest end to end; git is
    # already required by parse_version, so it is a safe dependency here.
    assert lib.run("git --version").returncode == 0
    assert lib.run("git frobnicate-not-a-command").returncode != 0


def test_child_streams_stay_apart(mascope_home, capfd):
    # A passthrough keeps the child's stderr (its log) off the CLI's stdout,
    # so redirecting a compose exec's stdout to a file saves only its output.
    script = "import sys; print('out-line'); print('err-line', file=sys.stderr)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"

    assert lib.run(command).returncode == 0

    captured = capfd.readouterr()
    assert captured.out == "out-line\n"
    assert captured.err == "err-line\n"
