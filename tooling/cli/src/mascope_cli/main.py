"""
Mascope CLI entry point.

Defines the top-level `mascope` command group, shared options (env override,
log level, log grep), and the utility commands `modules`, `groups`, and `path`.
All environment-specific functionality is delegated to sub-apps under
`mascope_cli.cmd`.
"""

import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from typing_extensions import Annotated

from mascope_cli import cmd
from mascope_cli.checkout import source_checkout
from mascope_cli.cmd.init import init
from mascope_cli.runtime import runtime
from mascope_cli.version import resolve_version


app = typer.Typer()

app.command(name="init")(init)

# Operator commands — available everywhere.
app.add_typer(cmd.env_app, name="env")
app.add_typer(cmd.demo_app, name="demo")
app.add_typer(cmd.prod_app, name="prod")
app.add_typer(cmd.logs_app, name="logs")
app.add_typer(cmd.cert_app, name="cert")
# Developer commands — they operate on the monorepo source tree (run
# services, migrations, test suites, reference-database ingestion), so they are
# only registered when the CLI runs from a checkout. A wheel install neither
# shows nor imports them, keeping their dependencies (the `dev` extra) out of
# the operator install.
if source_checkout():
    from mascope_cli.cmd.agent import agent_app
    from mascope_cli.cmd.backend import backend_app
    from mascope_cli.cmd.dev import dev_app
    from mascope_cli.cmd.fleet import fleet_app
    from mascope_cli.cmd.instance import instance_app
    from mascope_cli.cmd.reference import reference_app
    from mascope_cli.cmd.test import test_app

    app.add_typer(dev_app, name="dev")
    app.add_typer(backend_app, name="backend")
    app.add_typer(agent_app, name="agent")
    app.add_typer(instance_app, name="instance")
    app.add_typer(test_app, name="test")
    app.add_typer(fleet_app, name="fleet")
    app.add_typer(reference_app, name="reference")


@app.callback()
def main(
    ctx: typer.Context,
    env: Annotated[
        Optional[str],
        typer.Option(
            "--env",
            "-e",
            help="Override the active Mascope runtime env for the duration of the command",
        ),
    ] = None,
    log_level: Annotated[
        Optional[str],
        typer.Option(
            "--log-level", "-l", help="Set the log level shown in terminal logs"
        ),
    ] = None,
    log_grep: Annotated[
        Optional[str],
        typer.Option(
            "--log-grep",
            "-g",
            help="Highlight lines matching the grep pattern in the terminal logs",
        ),
    ] = None,
):
    """
    Mascope development CLI

    The `mascope` CLI offers a series of commands for managing development and
    production environments, as well as the database. A few top-level options
    are shared between dev and prod modes, but most functionality and options
    are delegated to the specific commands.

    \b
    Type `mascope <cmd> --help` to learn more about a specific command.
    """
    # `mascope init` creates the runtime home, so it must run before one
    # exists — skip the runtime-backed setup for it (the log options below
    # only touch env vars).
    needs_runtime = ctx.invoked_subcommand != "init"

    # Default the version from git, but let an explicitly-set MASCOPE_VERSION win
    # (e.g. pinning a release image for a prod deploy: MASCOPE_VERSION=v1.0.0).
    # Record whether it was pinned: a prod deploy honors a pin but otherwise
    # pulls `latest`/a release tag rather than the branch-derived build id (see
    # the prod _deploy_version helper).
    if os.environ.get("MASCOPE_VERSION"):
        os.environ["_MASCOPE_VERSION_PINNED"] = "1"
    else:
        os.environ["_MASCOPE_VERSION_PINNED"] = "0"
        if needs_runtime:
            os.environ["MASCOPE_VERSION"] = resolve_version(runtime)

    if needs_runtime:
        # override active env with CLI option (null if not provided)
        runtime.state.override("env", env)

    # Set log level for terminal output. Always sync the env var so docker
    # compose never sees a stale value from a previous invocation.
    if log_level:
        os.environ["MASCOPE_LOGLEVEL"] = log_level.upper()
    elif "MASCOPE_LOGLEVEL" in os.environ:
        del os.environ["MASCOPE_LOGLEVEL"]

    # Set grep highlight pattern for terminal logs
    if log_grep:
        os.environ["MASCOPE_LOGGREP"] = log_grep
    elif "MASCOPE_LOGGREP" in os.environ:
        del os.environ["MASCOPE_LOGGREP"]


@app.command()
def modules(
    runnable: Annotated[
        Optional[bool],
        typer.Option(help="Only list modules that can be run with `mascope dev run`"),
    ] = False,
):
    """
    List modules in the monorepo

    A 'module' may be a Python or NPM package or a service which is part of
    a package and can be independently run.

    Use --installable to list modules installed by 'mascope dev install' and
    --runnable to list modules that can be run by 'mascope dev run'.
    """

    table = Table()
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Color", style="magenta", no_wrap=True)
    table.add_column("Command", style="magenta", no_wrap=True)
    for mod in runtime.modules:
        if mod["run"] if runnable else True:
            table.add_row(mod["name"], mod["color"], mod["run"])
    console = Console()
    console.print(table)


@app.command()
def groups():
    """
    List runtime groups in the monorepo

    A 'group' is a set of runtime modules that can be
    launched together with `mascope dev run <group_name>`.
    """

    table = Table()
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Modules", style="magenta", no_wrap=True)
    for group in runtime.groups:
        table.add_row(
            group["name"], ", ".join([mod["name"] for mod in group["modules"]])
        )
    console = Console()
    console.print(table)


@app.command()
def path():
    """
    Prints your mascope path

    This information is stored in the MASCOPE_PATH enviroment variable
    and used by the CLI and application.
    """
    print(os.environ["MASCOPE_PATH"])
