"""
Runtime environment management commands.

Provides commands to list, activate, and sync Mascope runtime environments.
Environments are named directories under `{MASCOPE_PATH}/.runtime/env/`,
each containing all state required to run Mascope services (filestore,
streaming folders).
"""

from contextlib import nullcontext
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mascope_cli.cmd.env._create import (
    create_env_local,
    create_env_remote,
    validate_env_name,
)
from mascope_cli.cmd.env._filter import parse_option_date
from mascope_cli.cmd.env._paths import (
    env_exists_local,
    env_exists_remote,
    parse_address,
)
from mascope_cli.cmd.env._ssh import SshMux
from mascope_cli.cmd.env._sync import sync_db, sync_filestore
from mascope_cli.runtime import runtime
from mascope_runtime import Runtime


env_app = typer.Typer()


@env_app.callback()
def main():
    """
    Manage your mascope runtime environments.

    Runtime envs are folders in the `.runtime/env` directory under your
    `MASCOPE_PATH`. They contain all the state needed to run Mascope apps
    and/or services, e.g. file stores or file streaming folders.
    """


# --- Internal helpers ---


def _complete_env() -> list[str]:
    """
    Return available environment names for shell autocompletion.

    :return: List of environment names from the `.runtime/env/` directory.
    :rtype: list[str]
    """
    return [e["name"] for e in runtime.env.list]


# --- Commands ---


@env_app.command(name="list")
def list_envs():
    """
    List available envs.

    Displays name, description, path, and status for each environment.
    The active environment is marked with `*`.
    """
    table = Table()
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Description", style="green")
    table.add_column("Path", style="magenta", no_wrap=True)
    table.add_column("Status", style="cyan", no_wrap=True)
    for env in runtime.env.list:
        env_runtime = Runtime("cli", env=env["name"])
        is_selected = env["name"] == runtime.state.env
        default = "default" if env["name"] == "default" else None
        active = "active" if is_selected else None
        status = default or active
        selected = "*" if is_selected else ""
        table.add_row(
            env["name"] + selected,
            env_runtime.meta.description or "n/a",
            env["path"],
            status,
        )
    console = Console()
    console.print(table)


@env_app.command()
def use(
    env: Annotated[
        str,
        typer.Argument(
            help="The environment to activate.",
            autocompletion=_complete_env,
        ),
    ],
) -> None:
    """
    Activate an env so that it is used in all subsequent commands.

    Writes the selected environment name to `state.json`. Persists across
    CLI invocations until changed again.

    \b
    Examples:
        mascope env use tof1
        mascope env use default
    """
    if env in [e["name"] for e in runtime.env.list]:
        runtime.state.env = env
        runtime.logger.info(f"Mascope env set to '{env}'")
    else:
        runtime.logger.error(f"No env named '{env}' was found")


@env_app.command()
def create(
    name: Annotated[
        str,
        typer.Argument(
            metavar="NAME",
            help="Name of the environment to create. No spaces or path separators.",
        ),
    ],
) -> None:
    """
    Create a new local runtime environment.

    Creates the environment directory at `.runtime/env/{NAME}/` under
    `MASCOPE_PATH`. Raises an error if the environment already exists.

    The new environment will appear in `mascope env list` and can be
    activated with `mascope env use NAME`.

    \b
    Examples:
        mascope env create tof1
        mascope env create test-env-2
    """
    try:
        validate_env_name(name)
    except ValueError as e:
        runtime.logger.error(str(e))
        raise typer.Exit(1)

    if env_exists_local(name):
        runtime.logger.error(
            f"Environment '{name}' already exists. "
            "Use 'mascope env list' to see available environments."
        )
        raise typer.Exit(1)

    try:
        create_env_local(name)
    except (ValueError, FileExistsError, OSError) as e:
        runtime.logger.error(f"Failed to create environment '{name}': {e}")
        raise typer.Exit(1)

    runtime.logger.success(f"Environment '{name}' created.")


@env_app.command()
def sync(
    source_env: Annotated[
        str,
        typer.Argument(
            metavar="SOURCE_ENV",
            help=(
                "Source environment. Local name (e.g. `tof1`) or remote "
                "address in `USER@HOST:ENV` format (e.g. `user@203.0.113.10:tof1`)."
            ),
        ),
    ],
    source_mode: Annotated[
        str,
        typer.Argument(
            metavar="SOURCE_MODE",
            help="Mode of the source PostgreSQL server: `dev` or `prod`.",
        ),
    ],
    target_env: Annotated[
        str,
        typer.Argument(
            metavar="TARGET_ENV",
            help=(
                "Target environment. Local name (e.g. `tof1`) or remote "
                "address in `USER@HOST:ENV` format."
            ),
        ),
    ],
    target_mode: Annotated[
        str,
        typer.Argument(
            metavar="TARGET_MODE",
            help="Mode of the target PostgreSQL server: `dev` or `prod`.",
        ),
    ],
    skip_filestore: Annotated[
        bool,
        typer.Option(
            "--skip-filestore",
            "-sf",
            help="Skip synchronizing the filestore.",
        ),
    ] = False,
    skip_db: Annotated[
        bool,
        typer.Option(
            "--skip-db",
            "-sd",
            help="Skip synchronizing the PostgreSQL database.",
        ),
    ] = False,
    from_date: Annotated[
        str | None,
        typer.Option(
            "--from",
            help=(
                "Only sync filestore data acquired on or after this date "
                "(YYYY-MM-DD, inclusive). Filters on the acquisition-date "
                "directory in the filestore, not on file modification time. "
                "Does not filter the database."
            ),
        ),
    ] = None,
    to_date: Annotated[
        str | None,
        typer.Option(
            "--to",
            help=(
                "Only sync filestore data acquired on or before this date "
                "(YYYY-MM-DD, inclusive). Same filter as --from."
            ),
        ),
    ] = None,
    chown: Annotated[
        bool,
        typer.Option(
            "--chown",
            help=(
                "After syncing the filestore, chown the target env directory "
                "to its existing owner so Mascope can use the new files. "
                "Requires passwordless sudo on the receiving machine; falls "
                "back to printing the command when that is unavailable."
            ),
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompts (for creating missing target env).",
        ),
    ] = False,
):
    """
    Sync a runtime env from source to target.

    Both source and target accept a local environment name or a remote address
    in `USER@HOST:ENV` format. Both require an explicit mode (`dev` or `prod`)
    to identify which PostgreSQL server to use — no defaults are applied.

    By default, both the filestore (via rsync) and the database (via
    pg_dump/pg_restore through a staging transfer directory) are synced.
    Use `--skip-filestore` or `--skip-db` to opt out of either.

    `--from` / `--to` narrow the filestore transfer to samples acquired inside
    a date window. They filter on the acquisition-date directories in the
    filestore, never on the database — a filtered filestore plus a full
    database leaves the target holding sample rows whose files were not
    transferred, so add `--skip-db` if that is not what you want.

    rsync does not carry ownership across, so files land owned by whoever runs
    the receiving side. When that differs from the owner of the target env
    directory the exact `sudo chown` to run is printed; `--chown` attempts it
    for you where passwordless sudo is available.

    If the target environment directory does not exist, you will be prompted
    to create it. Pass `--yes` to create it automatically without prompting.

    The database sync always stages through `.runtime/database/transfer/`.
    On success, the transfer dump is deleted and 7-day retention pruning runs.
    On failure, the dump is preserved for manual recovery.

    For remote topologies, a single SSH ControlMaster connection is opened
    before any remote operations begin — password or passphrase is prompted
    at most once for the entire sync regardless of how many SSH/scp operations
    are required (existence check, env creation, dump, transfer, restore,
    cleanup, filestore rsync).

    Remote → remote sync is not supported — run from one of the machines.

    \b
    Examples:
        mascope env sync user@203.0.113.10:tof1 prod tof1 dev
        mascope env sync tof1 prod tof1 dev
        mascope env sync tof1 dev test-env dev
        mascope env sync tof1 prod user@203.0.113.10:tof1 prod
        mascope env sync user@203.0.113.10:tof1 prod tof1 dev --skip-filestore
        mascope env sync tof1 dev test-env dev --skip-db
        mascope env sync user@203.0.113.10:tof1 prod tof1 dev --from 2026-01-01
        mascope env sync user@203.0.113.10:tof1 prod tof1 dev --from 2026-01-01 --to 2026-03-31 --skip-db
    """
    if source_mode not in ("dev", "prod"):
        runtime.logger.error(
            f"Invalid SOURCE_MODE '{source_mode}' — must be 'dev' or 'prod'."
        )
        raise typer.Exit(1)

    if target_mode not in ("dev", "prod"):
        runtime.logger.error(
            f"Invalid TARGET_MODE '{target_mode}' — must be 'dev' or 'prod'."
        )
        raise typer.Exit(1)

    parsed_from = parsed_to = None
    try:
        if from_date:
            parsed_from = parse_option_date(from_date, "--from")
        if to_date:
            parsed_to = parse_option_date(to_date, "--to")
    except ValueError as e:
        runtime.logger.error(str(e))
        raise typer.Exit(1)

    if parsed_from and parsed_to and parsed_from > parsed_to:
        runtime.logger.error(f"--from ({from_date}) is later than --to ({to_date}).")
        raise typer.Exit(1)

    if parsed_from or parsed_to:
        if skip_filestore:
            runtime.logger.warning(
                "--from/--to filter the filestore only, and --skip-filestore "
                "was given — the date range has no effect."
            )
        elif not skip_db:
            runtime.logger.warning(
                "--from/--to filter the filestore only — the database is "
                "synced in full, so the target will hold sample rows whose "
                "files were not transferred. Add --skip-db if that is not "
                "what you want."
            )

    # Resolve remote address before opening the mux so we know which host
    # to connect to. At most one of source/target can be remote.
    source_remote, _ = parse_address(source_env)
    target_remote, target_env_name = parse_address(target_env)
    remote = source_remote or target_remote

    # Open the SSH ControlMaster connection before any remote operations so
    # the passphrase/password is prompted at most once — covers existence
    # check, env creation, dump, transfer, restore, cleanup, and filestore
    # rsync. For local→local sync no SSH is involved and nullcontext([])
    # is used instead.
    ctx = SshMux(remote) if remote else nullcontext([])

    with ctx as ctl:
        # --- target env existence check ---
        if target_remote is not None:
            target_missing = not env_exists_remote(target_remote, target_env_name, ctl)
        else:
            target_missing = not env_exists_local(target_env_name)

        if target_missing:
            location = target_remote if target_remote else "local"
            if not yes:
                confirmed = typer.confirm(
                    f"Target environment '{target_env_name}' does not exist on "
                    f"{location}. Create it?"
                )
                if not confirmed:
                    runtime.logger.error(
                        "Sync aborted — target environment does not exist."
                    )
                    raise typer.Exit(1)

            try:
                if target_remote is not None:
                    create_env_remote(target_remote, target_env_name, ctl)
                else:
                    create_env_local(target_env_name)
            except (ValueError, FileExistsError, RuntimeError, OSError) as e:
                # The raised messages already state what failed ("Failed to
                # create environment '<name>' ..."), so no extra prefix here.
                runtime.logger.error(str(e))
                raise typer.Exit(1)

        # --- Sync ---
        errors: list[str] = []

        if not skip_db:
            runtime.logger.info("--- Database sync ---")
            try:
                sync_db(
                    source_env,
                    source_mode,
                    target_env,
                    target_mode,
                    control_args=ctl,
                )
            except (ValueError, RuntimeError) as e:
                # The raised messages already say what failed ("scp failed
                # pulling ...", "Filestore sync failed ..."); a "Database sync
                # failed:" prefix would double the wording. The closing
                # "Sync completed with errors in: ..." line names the stage.
                runtime.logger.error(str(e))
                errors.append("database")

        if not skip_filestore:
            runtime.logger.info("--- Filestore sync ---")
            try:
                sync_filestore(
                    source_env,
                    target_env,
                    control_args=ctl,
                    from_date=parsed_from,
                    to_date=parsed_to,
                    chown=chown,
                )
            except Exception as e:
                runtime.logger.error(str(e))
                errors.append("filestore")

    if errors:
        runtime.logger.error(f"Sync completed with errors in: {', '.join(errors)}")
        raise typer.Exit(1)

    runtime.logger.success(
        f"Sync complete: {source_env} ({source_mode}) → {target_env} ({target_mode})"
    )
