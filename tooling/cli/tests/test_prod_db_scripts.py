"""
Tests for the production db script runner's container-Python resolution.

The interpreter path differs between image builds (``/opt/uv/tools`` on current
images, ``/root/.local/share/uv/tools`` on legacy ones), so the runner probes
the container at runtime instead of hardcoding a path.
"""

import re
import subprocess
from pathlib import Path

import pytest

from mascope_cli.cmd.prod.db import scripts


def _fake_run(returncode: int, stdout: str):
    """Return a subprocess.run stand-in yielding a fixed CompletedProcess."""

    def _run(cmd, capture_output=False, text=False, check=False):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")

    return _run


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
                r'(?:_int_env|_bool_env|os\.environ\.get|os\.getenv)\(\s*"(MASCOPE_[A-Z0-9_]+)"',
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
