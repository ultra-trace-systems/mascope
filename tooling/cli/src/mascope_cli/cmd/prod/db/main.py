"""
Production database management commands.

Provides commands to manage the production PostgreSQL database: status,
logs, psql shell, create, drop, and restore. Backup management is delegated
to the `backup` subgroup — see `backup.py`.

The production container does NOT expose a port to the host. All database
operations go through `docker exec` (no psycopg2 direct connections).

Required compose bind mounts on the postgres service:
    ${MASCOPE_PATH}/.runtime/database/backups/prod:/backups
    ${MASCOPE_PATH}/.runtime/database/transfer:/transfer
"""

import subprocess
from typing import Annotated, Optional

import typer

from mascope_cli.cmd.prod.db.backup import backup_app
from mascope_cli.cmd.prod.db.scripts import prod_db_scripts_app
from mascope_cli.pg import (
    READONLY_ROLE,
    check_prerequisites,
    dirs,
    drop_database,
    grant_readonly_role,
    is_container_running,
    is_database_ready,
    is_server_ready,
    list_dumps,
    pg_restore,
    validate_env,
)
from mascope_cli.pg import (
    create_database as admin_create_database,
)
from mascope_cli.runtime import runtime


prod_db_app = typer.Typer()
prod_db_app.add_typer(backup_app, name="backup")
prod_db_app.add_typer(prod_db_scripts_app, name="script")

_MODE = "prod"


# --- Prod-specific helpers (docker exec path — port not exposed) ---


def _create_database_if_missing(container: str, user: str, database: str) -> bool:
    """
    Create a database if it does not already exist.

    Idempotency check via `psql -lqt` through `docker exec` — no direct
    port connection required. Wraps :func:`mascope_cli.pg.admin.create_database`
    which is NOT idempotent on its own.

    :param container: PostgreSQL container name.
    :type container: str
    :param user: PostgreSQL user with `CREATEDB` privilege.
    :type user: str
    :param database: Name of the database to create.
    :type database: str
    :return: `True` if the database already existed or was created successfully.
    :rtype: bool
    """
    result = subprocess.run(
        ["docker", "exec", container, "psql", "-U", user, "-lqt"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if database in result.stdout:
        runtime.logger.debug(f"Database '{database}' already exists")
        return True

    try:
        admin_create_database(container, user, database)
        runtime.logger.success(f"Database '{database}' created")
        return True
    except RuntimeError as e:
        runtime.logger.error(str(e))
        return False


def _grant_readonly_role(container: str, user: str, database: str) -> None:
    """
    Give the fleet's read-only role its grants in a new or restored database.

    The database's data is in place by now, so a failure here says so and
    exits non-zero rather than reporting the restore as failed.

    :param container: PostgreSQL container name.
    :type container: str
    :param user: The app's PostgreSQL role.
    :type user: str
    :param database: The database just created or restored.
    :type database: str
    """
    try:
        if grant_readonly_role(container, user, database):
            runtime.logger.info(
                f"Granted the read-only role '{READONLY_ROLE}' read access "
                f"to '{database}'"
            )
    except RuntimeError as e:
        runtime.logger.error(
            f"'{database}' is in place, but the read-only role has no access "
            f"to it: run the fleet role postgres_readonly again.\n{e}"
        )
        raise typer.Exit(1)


# --- Commands ---


@prod_db_app.callback()
def main() -> None:
    """
    PostgreSQL database management for the production environment.
    """


@prod_db_app.command()
def status() -> None:
    """
    Show PostgreSQL container status and configuration.

    Displays current configuration from .mascope.toml, connection pool
    settings, and live container status.
    """
    if not check_prerequisites(_MODE):
        return

    db_cfg = runtime.full_config.backend.database

    runtime.logger.info("\n=== Database Configuration ===")
    runtime.logger.info(f"User: {db_cfg.user}")
    runtime.logger.info(
        f"DB name: {db_cfg.get_postgres_database_name(runtime.env.name)}"
    )
    runtime.logger.info(f"Container: {db_cfg.get_postgres_container_name(_MODE)}")

    runtime.logger.info("=== Connection Pool ===")
    runtime.logger.info(f"Pool size: {db_cfg.pool_size}")
    runtime.logger.info(f"Max overflow: {db_cfg.max_overflow}")
    runtime.logger.info(f"Pool timeout: {db_cfg.pool_timeout}s")
    runtime.logger.info(f"Server max_connections: {db_cfg.max_connections}")

    runtime.logger.info("=== Status ===")
    if not is_server_ready(_MODE):
        runtime.logger.warning("Container not running")
        runtime.logger.info("Run 'mascope prod up' to start")
        return

    if is_database_ready(_MODE, runtime.env.name):
        runtime.logger.success("Env-specific database is ready")
    else:
        runtime.logger.warning("Env-specific database does not exist")
        runtime.logger.info("Run 'mascope prod db create' to create it")


@prod_db_app.command()
def logs(
    follow: Annotated[
        bool,
        typer.Option("--follow", "-f", help="Follow log output."),
    ] = False,
    tail: Annotated[
        int,
        typer.Option("--tail", "-n", help="Number of lines to show from end of logs."),
    ] = 100,
) -> None:
    """
    Show PostgreSQL container logs.

    \b
    Examples:
        mascope prod db logs
        mascope prod db logs --follow
        mascope prod db logs --tail 50
    """
    if not is_container_running(_MODE):
        runtime.logger.warning("Container not running — run 'mascope prod up' to start")
        return

    container = runtime.full_config.backend.database.get_postgres_container_name(
        mode=_MODE
    )
    cmd = ["docker", "logs"]
    if follow:
        cmd.append("-f")
    cmd.extend(["--tail", str(tail), container])

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        runtime.logger.warning(f"Container '{container}' not found")
    except KeyboardInterrupt:
        runtime.logger.success("\nStopped following logs")


@prod_db_app.command()
def cli(
    postgres: Annotated[
        bool,
        typer.Option(
            "--postgres",
            "-p",
            help="Connect to the administrative 'postgres' database.",
        ),
    ] = False,
) -> None:
    """
    Open a psql shell inside the production PostgreSQL container.

    Connects to the environment-specific database if ready, falls back to
    the default `postgres` database otherwise.

    \b
    Useful psql commands:
        \\l                          list all databases
        \\c mascope_default          connect to specific database
        \\dt                         list tables in current database
        \\d+ table_name              describe table structure
        \\du                         list users
        SELECT version();           PostgreSQL version
        \\q                          quit
    """
    if not is_container_running(_MODE):
        runtime.logger.warning("Container not running — run 'mascope prod up' to start")
        return

    db_cfg = runtime.full_config.backend.database
    target_db = "postgres"
    if not postgres and is_database_ready(_MODE, runtime.env.name):
        target_db = db_cfg.get_postgres_database_name(runtime.env.name)
    elif not postgres:
        runtime.logger.warning(
            f"Env-specific database not ready, connecting to '{target_db}'"
        )

    runtime.logger.info(
        f"Opening psql (container: {db_cfg.get_postgres_container_name(_MODE)}, db: {target_db})"
    )
    runtime.logger.info("Type '\\q' or Ctrl+D to exit")

    try:
        subprocess.run(
            [
                "docker",
                "exec",
                "-it",
                db_cfg.get_postgres_container_name(mode=_MODE),
                "psql",
                "-h",
                "localhost",
                "-U",
                db_cfg.user,
                "-d",
                target_db,
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        runtime.logger.error("Failed to open psql shell")
    except KeyboardInterrupt:
        runtime.logger.success("\nClosed psql shell")


@prod_db_app.command()
def create(
    env: Annotated[
        Optional[str],
        typer.Option(
            "--env",
            "-e",
            help="Environment to create database for. Defaults to active.",
        ),
    ] = None,
) -> None:
    """
    Create the environment's database if it doesn't exist.

    Idempotent — safe to run multiple times.
    Uses `docker exec` (prod port is not exposed).

    \b
    Examples:
        mascope prod db create
        mascope prod db create --env tof1
    """
    if not check_prerequisites(_MODE):
        return
    if not is_server_ready(_MODE):
        runtime.logger.error("PostgreSQL not running — run 'mascope prod up' first")
        raise typer.Exit(1)

    db_cfg = runtime.full_config.backend.database
    target_env = env or runtime.env.name

    if not validate_env(target_env):
        runtime.logger.error(
            f"Environment '{target_env}' not found.\n"
            f"Available: {', '.join(e['name'] for e in runtime.env.list)}"
        )
        raise typer.Exit(1)

    container = db_cfg.get_postgres_container_name(mode=_MODE)
    database = db_cfg.get_postgres_database_name(target_env)

    if not _create_database_if_missing(container, db_cfg.user, database):
        runtime.logger.error(f"Failed to create database '{database}'")
        raise typer.Exit(1)

    runtime.logger.success(f"Database '{database}' is ready")
    _grant_readonly_role(container, db_cfg.user, database)


@prod_db_app.command()
def drop(
    env: Annotated[
        Optional[str],
        typer.Option("--env", "-e", help="Environment to drop. Defaults to active."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm destructive operation."),
    ] = False,
) -> None:
    """
    Drop an environment's database, terminating all active connections first.

    \b
    Examples:
        mascope prod db drop --env tof1 --yes
    """
    if not check_prerequisites(_MODE):
        return
    if not is_server_ready(_MODE):
        runtime.logger.error("PostgreSQL not running — run 'mascope prod up' first")
        raise typer.Exit(1)

    db_cfg = runtime.full_config.backend.database
    target_env = env or runtime.env.name

    if not validate_env(target_env):
        runtime.logger.error(
            f"Environment '{target_env}' not found.\n"
            f"Available: {', '.join(e['name'] for e in runtime.env.list)}"
        )
        raise typer.Exit(1)

    container = db_cfg.get_postgres_container_name(mode=_MODE)
    database = db_cfg.get_postgres_database_name(target_env)

    if not yes:
        typer.confirm(
            f"Drop database '{database}'? All data will be permanently deleted.",
            abort=True,
        )

    try:
        runtime.logger.info(f"Terminating connections and dropping '{database}'...")
        drop_database(container, db_cfg.user, database)
    except RuntimeError as e:
        runtime.logger.error(str(e))
        raise typer.Exit(1)

    runtime.logger.success(f"Database '{database}' dropped")


@prod_db_app.command()
def restore(
    dump_file: Annotated[
        Optional[str],
        typer.Argument(
            help=(
                "Dump filename (basename only). "
                "Omit to use the latest available dump for the target environment."
            ),
        ),
    ] = None,
    env: Annotated[
        Optional[str],
        typer.Option(
            "--env",
            "-e",
            help="Environment to restore into. Defaults to active.",
        ),
    ] = None,
    transfer: Annotated[
        bool,
        typer.Option(
            "--transfer",
            "-t",
            help="Read dump from transfer directory instead of backups.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm destructive operation. Required."),
    ] = False,
) -> None:
    """
    Restore an environment's database from a backup dump file.

    Drops and recreates the target database, then restores from the dump.
    All current data in the target is replaced.

    `--yes` is required. No interactive fallback in production.

    \b
    Examples:
        mascope prod db restore --env tof1 --yes
        mascope prod db restore mascope_tof1_20250101_040000.dump --yes
        mascope prod db restore --transfer --env tof1 --yes
    """
    if not check_prerequisites(_MODE):
        return
    if not is_server_ready(_MODE):
        runtime.logger.error("PostgreSQL not running — run 'mascope prod up' first")
        raise typer.Exit(1)

    db_cfg = runtime.full_config.backend.database
    target_env = env or runtime.env.name

    if not validate_env(target_env):
        runtime.logger.error(
            f"Target environment '{target_env}' not found.\n"
            f"Available: {', '.join(e['name'] for e in runtime.env.list)}"
        )
        raise typer.Exit(1)

    container = db_cfg.get_postgres_container_name(mode=_MODE)
    database = db_cfg.get_postgres_database_name(target_env)
    source_dir, mount = dirs(transfer, _MODE)

    if dump_file:
        resolved = source_dir / dump_file
        if not resolved.exists():
            runtime.logger.error(f"Dump file not found: {resolved}")
            raise typer.Exit(1)
    else:
        available = list_dumps(source_dir, db_name_filter=database)
        if not available:
            runtime.logger.error(f"No backups found for '{database}' in {source_dir}")
            raise typer.Exit(1)
        resolved = available[0]
        runtime.logger.info(f"Latest dump: {resolved.name}")

    if not yes:
        typer.confirm(
            f"Drop and restore '{database}' from '{resolved.name}'?\n"
            f"All current data will be replaced.",
            abort=True,
        )

    try:
        runtime.logger.info(f"Terminating connections and dropping '{database}'...")
        drop_database(container, db_cfg.user, database)

        runtime.logger.info(f"Creating empty '{database}'...")
        if not _create_database_if_missing(container, db_cfg.user, database):
            raise RuntimeError("Failed to recreate database after drop")

        runtime.logger.info(f"Restoring from '{resolved.name}'...")
        pg_restore(container, db_cfg.user, database, resolved, mount)

    except (RuntimeError, FileNotFoundError) as e:
        runtime.logger.error(str(e))
        raise typer.Exit(1)

    runtime.logger.success(
        f"Database '{database}' restored from '{resolved.name}' successfully"
    )
    _grant_readonly_role(container, db_cfg.user, database)
