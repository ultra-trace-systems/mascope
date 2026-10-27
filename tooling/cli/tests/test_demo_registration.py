"""
Tests for which `mascope demo` subcommands an install registers.

Seeding (the bare command with `--rebuild` / `--fresh`), `snapshot` and
`verify` drive the source tree, so a wheel install, where
``source_checkout()`` finds nothing, gets only `fetch` and `info`, and the bare
command points at the demo compose file instead of failing on an import.
"""

import pytest
import typer
from loguru import logger

from mascope_cli.cmd.demo import main as demo_main
from mascope_cli.runtime import runtime


def _command_names(app: typer.Typer) -> list[str]:
    return [info.name or info.callback.__name__ for info in app.registered_commands]


def _forbidden(what: str):
    def _fail(*_args, **_kwargs):
        raise AssertionError(f"{what} ran on a wheel install")

    return _fail


@pytest.fixture
def wheel_app(monkeypatch) -> typer.Typer:
    """
    The demo group as a wheel install builds it.

    The checkout flow is fenced off: a wheel has no source tree to seed from,
    so the bare command must not create the `demo` env in the operator's
    runtime home nor reach the dev imports (psycopg2) the seeding needs.
    """
    monkeypatch.setattr(demo_main, "source_checkout", lambda: None)
    monkeypatch.setattr(demo_main, "_ensure_demo_env", _forbidden("the demo env"))
    monkeypatch.setattr(demo_main, "_run", _forbidden("the checkout demo flow"))
    return demo_main.make_demo_app()


@pytest.fixture
def logs():
    """Log messages, captured after configuring (which removes earlier sinks)."""
    runtime.reload_config()
    lines: list[str] = []
    handler = logger.add(
        lambda message: lines.append(message.record["message"]), level="DEBUG"
    )
    yield lines
    logger.remove(handler)


def test_checkout_registers_every_subcommand():
    assert _command_names(demo_main.demo_app) == [
        "fetch",
        "verify",
        "snapshot",
        "info",
    ]


def _option_names(app: typer.Typer) -> set[str]:
    # The Click command itself, not its help text: CI renders help with
    # colour codes that split an option name in two.
    command = typer.main.get_command(app)
    return {opt for param in command.params for opt in param.opts}


@pytest.mark.parametrize("option", ["--rebuild", "--fresh", "--no-launch"])
def test_checkout_bare_command_seeds_the_demo(option):
    assert option in _option_names(demo_main.demo_app)


def test_wheel_registers_only_fetch_and_info(wheel_app):
    assert _command_names(wheel_app) == ["fetch", "info"]


@pytest.mark.parametrize("command", ["verify", "snapshot"])
def test_wheel_rejects_source_tree_subcommands(cli_runner, wheel_app, command):
    result = cli_runner.invoke(wheel_app, [command, "--help"])
    assert result.exit_code == 2  # a usage error: no such command


@pytest.mark.parametrize("option", ["--rebuild", "--fresh", "--no-launch"])
def test_wheel_bare_command_has_no_seeding_options(cli_runner, wheel_app, option):
    assert option not in _option_names(wheel_app)
    result = cli_runner.invoke(wheel_app, [option])
    assert result.exit_code == 2  # a usage error: no such option


def test_wheel_bare_command_points_at_the_compose_demo(
    cli_runner, wheel_app, mascope_home, logs
):
    result = cli_runner.invoke(wheel_app, [])

    # A clean exit with advice, not the AssertionError of a fenced-off call.
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    text = "\n".join(logs)
    assert "docker compose -f docker-compose.demo.yaml up -d" in text
    assert str(mascope_home) in text


def test_wheel_info_still_works(cli_runner, wheel_app, logs):
    result = cli_runner.invoke(wheel_app, ["info"])
    assert result.exit_code == 0, result.output
    assert any(line.startswith("Bundle:") for line in logs)
