"""
Alembic database migration management for development.

Provides commands to check, apply, and manage PostgreSQL schema migrations
using Alembic.
"""

import subprocess
from pathlib import Path
from typing import Annotated

import typer
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from mascope_cli.checkout import backend_path
from mascope_cli.pg import dirs, pg_dump
from mascope_cli.runtime import runtime


dev_migrate_app = typer.Typer()

_PATH = "dev"


def _backend_path() -> Path:
    """Alembic working directory: the backend package of the invoked checkout.

    Resolved from the running CLI source (see ``checkout.backend_path``), not
    from ``MASCOPE_PATH`` - a worktree must migrate its database to its *own*
    head, and ``MASCOPE_PATH`` is the shared runtime home, not the source.
    """
    return backend_path()


def _check_prerequisites() -> bool:
    """
    Check if PostgreSQL is configured.

    :return: True if PostgreSQL configured
    """
    if not runtime.full_config.backend.database:
        runtime.logger.warning("Database not configured in .mascope.toml")
        return False

    return True


def _run_alembic(
    args: list[str], capture: bool = False, show_output: bool = False
) -> subprocess.CompletedProcess:
    """
    Run Alembic command in backend directory.

    :param args: Alembic command arguments (e.g., ['upgrade', 'head'])
    :param capture: If True, capture output for parsing
    :param show_output: If True, print captured output (only works with capture=True)
    :return: CompletedProcess result
    """
    result = subprocess.run(
        ["uv", "run", "alembic"] + args,
        cwd=_backend_path(),
        capture_output=capture,
        text=True,
        check=False,
    )

    # Print captured output if requested
    if show_output and capture:
        if result.stdout:
            typer.echo(result.stdout, nl=False)
        if result.stderr:
            typer.echo(result.stderr, nl=False)

    return result


def check_pending_migrations() -> bool:
    """
    Check if there are pending migrations using Alembic Script API.

    This avoids importing backend by using Alembic's script directory
    to read head revision, then direct DB query for current revision.

    :return: True if migrations need to be applied, False if current
    """
    try:
        # Get head revision from migration files
        alembic_cfg = Config(_backend_path() / "alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        head_rev = script.get_current_head()

        if not head_rev:
            return False  # No migrations defined

        # Get current revision from database
        db_cfg = runtime.full_config.backend.database

        postgres_password = runtime.secret(
            "POSTGRES_PASSWORD_FILE", "postgres_password.txt"
        )
        url = db_cfg.get_postgres_url_sync(
            password=postgres_password, env_name=runtime.env.name
        )

        engine = create_engine(url, poolclass=NullPool)
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                )
                current_rev = result.scalar()
        finally:
            engine.dispose()

        return current_rev != head_rev

    except Exception as e:
        runtime.logger.debug(f"Migration check error: {e}")
        return True  # Assume pending on error


def run_migrations(target: str = "head") -> bool:
    """
    Run migrations programmatically.

    :param target: Migration target revision
    :return: True if successful, False otherwise
    """
    if not _check_prerequisites():
        return False

    # Safety backup before applying migrations.
    # Failure is non-fatal — a missing backup should not block migration in dev.
    # In prod this is handled by db-init.sh before alembic runs.
    try:
        db_cfg = runtime.full_config.backend.database
        container = db_cfg.get_postgres_container_name(mode=_PATH)
        database = db_cfg.get_postgres_database_name(runtime.env.name)
        dump_dir, mount = dirs(transfer=False, mode=_PATH)
        path = pg_dump(
            container, db_cfg.user, database, dump_dir, mount, label="pre-migration"
        )
        runtime.logger.success(f"Pre-migration backup: {path.name}")
    except RuntimeError as e:
        runtime.logger.warning(f"Pre-migration backup failed: {e}")

    runtime.logger.info(f"Applying migrations to: {target}")

    result = _run_alembic(["upgrade", target], capture=True)
    if result.returncode == 0:
        runtime.logger.success("Migrations applied")
        return True
    else:
        runtime.logger.error("Migration failed: {}", result.stderr)
        return False


@dev_migrate_app.callback()
def main():
    """
    Manage Alembic database migrations
    """


@dev_migrate_app.command()
def status():
    """
    Show current migration status.

    Displays current database revision and latest available revision.
    """
    if not _check_prerequisites():
        return

    # Get current
    result = _run_alembic(["current"], capture=True)
    if result.returncode != 0:
        runtime.logger.error("Failed to check current migration status")
        return

    current_revision = result.stdout.strip()

    # Get head
    result = _run_alembic(["heads"], capture=True)
    if result.returncode != 0:
        runtime.logger.error("Failed to check head revision")
        return

    head_revision = result.stdout.strip()

    # Display summary
    runtime.logger.info(
        f"Current: {current_revision or '(empty - no migrations applied)'}"
    )
    runtime.logger.info(f"Head:    {head_revision or '(no migrations defined)'}")

    # Compare inline (avoid redundant calls)
    current_id = current_revision.split()[0] if current_revision else ""
    head_id = head_revision.split()[0] if head_revision else ""

    if not current_id or current_id != head_id:
        runtime.logger.warning("Migrations pending - run 'mascope dev migrate upgrade'")
    else:
        runtime.logger.success("Database is up to date")


@dev_migrate_app.command()
def check():
    """
    Check if migrations need to be applied (exit code indicates status).

    Exit codes:
        0 - Database is current
        1 - Migrations pending
        2 - Error checking status
    """
    if not _check_prerequisites():
        raise typer.Exit(2)

    if check_pending_migrations():
        runtime.logger.warning("Migrations pending")
        raise typer.Exit(1)
    else:
        runtime.logger.success("Database is current")
        raise typer.Exit(0)


@dev_migrate_app.command()
def upgrade(
    target: Annotated[
        str,
        typer.Argument(help="Target revision (use 'head' for latest)"),
    ] = "head",
):
    """
    Apply database migrations.

    Upgrades database schema to specified revision (default: latest).
    """
    if not _check_prerequisites():
        return

    if run_migrations(target):
        runtime.logger.success("Migrations applied successfully")
    else:
        runtime.logger.error("Migration failed")
        raise typer.Exit(1)


@dev_migrate_app.command()
def downgrade(
    target: Annotated[
        str,
        typer.Argument(
            help="Target revision ('-1' for previous, 'base' for empty DB)",
            metavar="TARGET",
        ),
    ],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Skip confirmation prompt"),
    ] = False,
):
    """
    Rollback database migrations.

    WARNING: This can cause data loss. Use with caution.

    Examples:
        mascope dev migrate downgrade -- -1           # Rollback one migration
        mascope dev migrate downgrade b2fafb2c        # Rollback to specific revision
        mascope dev migrate downgrade base            # Rollback all migrations
    """
    if not _check_prerequisites():
        return

    if not force:
        if not typer.confirm(
            f"Rollback to revision '{target}'? This may cause data loss."
        ):
            runtime.logger.info("Cancelled")
            return

    runtime.logger.info(f"Rolling back to: {target}")
    result = _run_alembic(["downgrade", target], capture=True)

    if result.returncode == 0:
        runtime.logger.success("Rollback completed")
    else:
        runtime.logger.error("Rollback failed: {}", result.stderr)
        raise typer.Exit(1)


@dev_migrate_app.command()
def history(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Show detailed history"),
    ] = False,
):
    """
    Show migration history.
    """
    if not _check_prerequisites():
        return

    args = ["history"]
    if verbose:
        args.append("--verbose")

    _run_alembic(args)


@dev_migrate_app.command()
def revision(
    message: Annotated[
        str,
        typer.Option("--message", "-m", help="Migration description"),
    ],
    autogenerate: Annotated[
        bool,
        typer.Option("--autogenerate", help="Detect schema changes automatically"),
    ] = True,
):
    """
    Create a new migration revision.

    Uses autogenerate by default to detect model changes.
    """
    if not _check_prerequisites():
        return

    args = ["revision"]
    if autogenerate:
        args.append("--autogenerate")
    args.extend(["-m", message])

    runtime.logger.info("Generating migration...")
    result = _run_alembic(args)

    if result.returncode == 0:
        runtime.logger.success("Migration created")
    else:
        runtime.logger.error("Failed to create migration: {}", result.stderr)
        raise typer.Exit(1)


@dev_migrate_app.command()
def heads():
    """
    Show current head revision(s).
    """
    if not _check_prerequisites():
        return

    _run_alembic(["heads"])


@dev_migrate_app.command()
def current():
    """
    Show current database revision.
    """
    if not _check_prerequisites():
        return

    _run_alembic(["current", "--verbose"])
