"""
Tests for `mascope prod` version selection and compose-environment assembly.

`_deploy_version` decides which image tag a production deploy pulls — a wrong
answer either deploys stale code or asks GHCR for an unpublished tag. The
compose-env tests pin the variables the production compose file interpolates.

Complements test_prod_compose.py, which covers exit-code propagation of
`_run_compose` in isolation; here the same path is exercised end to end
through the Typer app with only `lib.run` stubbed.
"""

import importlib
import subprocess

import pytest

from mascope_cli.main import app
from mascope_cli.runtime import runtime


# The prod package re-exports a `main` function that shadows the module of
# the same name, so import the module explicitly.
prod_main = importlib.import_module("mascope_cli.cmd.prod.main")


# --- _deploy_version ---


def test_pinned_version_wins(monkeypatch):
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "1")
    monkeypatch.setenv("MASCOPE_VERSION", "v1.2.3")
    assert prod_main._deploy_version() == "v1.2.3"


def test_release_tag_at_head_deploys_that_release(monkeypatch):
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "0")
    monkeypatch.setattr(prod_main.runtime, "parse_version", lambda **kw: "v2.0.0")
    assert prod_main._deploy_version() == "v2.0.0"


def test_branch_build_deploys_latest(monkeypatch):
    # A stray branch checkout must never ask for an unpublished image tag.
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "0")
    monkeypatch.setattr(
        prod_main.runtime, "parse_version", lambda **kw: "feat-x-2026.01.01-abc1234"
    )
    assert prod_main._deploy_version() == "latest"


def test_pip_installed_cli_deploys_latest(monkeypatch):
    # Outside a checkout the CLI's own calver package version (v2026.x.y)
    # must not be mistaken for an app release image tag: deploy `latest`.
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "0")
    monkeypatch.setattr(
        prod_main.runtime, "parse_version", lambda **kw: "unknown-version"
    )
    monkeypatch.setattr("mascope_cli.version.metadata.version", lambda name: "2026.7.7")
    assert prod_main._deploy_version() == "latest"


def test_version_is_resolved_from_the_deployment_checkout(monkeypatch):
    # Not from the process cwd: systemd starts the boot service in "/", where
    # git would resolve nothing and every reboot would deploy `latest` over
    # the pinned release.
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "0")
    monkeypatch.setenv("MASCOPE_PATH", "/srv/mascope")
    seen = {}

    def fake_parse_version(cwd=None):
        seen["cwd"] = cwd
        return "v1.6.1"

    monkeypatch.setattr(prod_main.runtime, "parse_version", fake_parse_version)

    assert prod_main._deploy_version() == "v1.6.1"
    assert seen["cwd"] == "/srv/mascope"


def test_unresolvable_version_warns_before_falling_back(monkeypatch):
    # The silent fallback is what let a reboot swap release channels unnoticed.
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "0")
    monkeypatch.setattr(
        prod_main.runtime, "parse_version", lambda **kw: "unknown-version"
    )
    warnings = []
    monkeypatch.setattr(prod_main.runtime.logger, "warning", warnings.append)

    assert prod_main._deploy_version() == "latest"
    assert len(warnings) == 1 and "latest" in warnings[0]


def test_branch_checkout_does_not_warn(monkeypatch):
    # Git resolved fine, the checkout is simply not at a release - expected on
    # a master deployment, so it must not cry wolf.
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "0")
    monkeypatch.setattr(
        prod_main.runtime, "parse_version", lambda **kw: "2026.01.01-abc1234"
    )
    warnings = []
    monkeypatch.setattr(prod_main.runtime.logger, "warning", warnings.append)

    assert prod_main._deploy_version() == "latest"
    assert warnings == []


# --- _compose_env ---


@pytest.fixture
def prod_mode():
    """Mimic the prod callback: prod-scoped config without touching state."""
    runtime.state.override("mode", "prod")
    runtime.reload_config()
    yield
    # conftest's autouse fixture restores state and reloads dev config.


def test_compose_env_resolves_names_and_mounts(prod_mode, monkeypatch):
    monkeypatch.setenv("MASCOPE_VERSION", "test-build-1")

    env = prod_main._compose_env(building=True)

    assert env["MASCOPE_ENV"] == "default"
    assert env["MASCOPE_DB_NAME"] == "mascope_default"
    assert env["MASCOPE_DB_CONTAINER_NAME"] == "mascope_prod_postgres"
    assert env["MASCOPE_BACKEND_CONTAINER_NAME"] == "mascope_prod_backend"
    assert env["MASCOPE_FRONTEND_CONTAINER_NAME"] == "mascope_prod_frontend"
    # MASCOPE_RUNTIME is the JSON blob the frontend build bakes in.
    assert '"mode": "prod"' in env["MASCOPE_RUNTIME"]


def test_compose_env_build_uses_head_version(prod_mode, monkeypatch):
    monkeypatch.setenv("MASCOPE_VERSION", "feat-x-2026.01.01-abc1234")
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "0")

    env = prod_main._compose_env(building=True)

    assert env["MASCOPE_VERSION"] == "feat-x-2026.01.01-abc1234"


def test_compose_env_run_uses_deploy_version(prod_mode, monkeypatch):
    monkeypatch.setenv("MASCOPE_VERSION", "feat-x-2026.01.01-abc1234")
    monkeypatch.setenv("_MASCOPE_VERSION_PINNED", "0")
    monkeypatch.setattr(
        prod_main.runtime, "parse_version", lambda **kw: "feat-x-2026.01.01-abc1234"
    )

    env = prod_main._compose_env(building=False)

    assert env["MASCOPE_VERSION"] == "latest"


# --- end-to-end through the Typer app, lib.run stubbed ---


class _ComposeRecorder:
    """Records lib.run invocations and controls the stubbed exit code."""

    def __init__(self):
        self.calls = []
        self.returncode = 0


@pytest.fixture
def compose(monkeypatch):
    """Stub lib.run for prod commands and record every invocation."""
    recorder = _ComposeRecorder()

    def fake_run(command, env_vars=None, **kwargs):
        recorder.calls.append({"command": command, "env_vars": env_vars or {}})
        return subprocess.CompletedProcess([], recorder.returncode)

    monkeypatch.setattr(prod_main.lib, "run", fake_run)
    # `prod up` checks bind-mount dirs; harmless against the temp home, but
    # keep the compose command the only observable side effect.
    monkeypatch.setattr(prod_main, "check_data_dirs", lambda mode: None)
    return recorder


def test_prod_build_invokes_compose_build(cli_runner, compose):
    result = cli_runner.invoke(app, ["prod", "build"])

    assert result.exit_code == 0
    assert len(compose.calls) == 1
    command = compose.calls[0]["command"]
    assert command.startswith("docker compose --file")
    assert command.endswith("build")
    assert "docker-compose.yaml" in command
    assert compose.calls[0]["env_vars"]["MASCOPE_DB_NAME"] == "mascope_default"


def test_prod_up_detached_passes_flags(cli_runner, compose):
    result = cli_runner.invoke(app, ["prod", "up", "--detach"])

    assert result.exit_code == 0
    assert compose.calls[0]["command"].endswith("up --detach")


def test_compose_failure_becomes_cli_exit_code(cli_runner, compose):
    compose.returncode = 5

    result = cli_runner.invoke(app, ["prod", "ps"])

    assert result.exit_code == 5


def test_docker_passthrough_without_args_errors(cli_runner, compose):
    result = cli_runner.invoke(app, ["prod", "docker"])

    assert result.exit_code == 1
    assert compose.calls == []


# --- prod update ---


def test_update_pulls_then_restarts_then_reports(cli_runner, compose):
    result = cli_runner.invoke(app, ["prod", "update"])

    assert result.exit_code == 0
    steps = [c["command"].split("' ", 1)[1] for c in compose.calls]
    assert steps == ["pull", "up --detach", "ps"]


def test_update_version_pin_reaches_compose(cli_runner, compose):
    result = cli_runner.invoke(app, ["prod", "update", "--version", "v1.2.0"])

    assert result.exit_code == 0
    assert all(
        call["env_vars"]["MASCOPE_VERSION"] == "v1.2.0" for call in compose.calls
    )


def test_update_rejects_a_non_release_version(cli_runner, compose):
    # Missing the leading `v` — must fail before any compose call.
    result = cli_runner.invoke(app, ["prod", "update", "--version", "1.2.0"])

    assert result.exit_code == 1
    assert compose.calls == []


def test_update_failed_pull_never_touches_the_stack(cli_runner, compose):
    compose.returncode = 3

    result = cli_runner.invoke(app, ["prod", "update"])

    assert result.exit_code == 3
    # Only the pull ran; the running stack was not restarted.
    assert len(compose.calls) == 1
    assert compose.calls[0]["command"].endswith("pull")
