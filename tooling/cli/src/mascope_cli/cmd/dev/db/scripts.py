"""
Development database script runner.

Discovers and executes maintenance scripts from
`mascope_backend.db.scripts.*` with automatic pre-execution backup.

Scripts are data-manipulation entry points — see `mascope_backend.db.admin`
for the distinction from Alembic schema migrations.
"""

import importlib
import importlib.util
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from mascope_cli.pg import (
    check_prerequisites,
    dirs,
    pg_dump,
    skip_backup_log_level,
)
from mascope_cli.runtime import runtime


dev_db_scripts_app = typer.Typer()

_MODE = "dev"
_SCRIPTS_MODULE = "mascope_backend.db.scripts"


def _discover_scripts() -> dict[str, str]:
    """
    Discover available scripts from mascope_backend.db.scripts.

    Scans the package directory for .py files (excluding __init__)
    that expose a main() callable.

    :return: Mapping of CLI name to dotted module path.
    :rtype: dict[str, str]
    """
    spec = importlib.util.find_spec(_SCRIPTS_MODULE)
    if spec is None or spec.submodule_search_locations is None:
        return {}

    scripts_dir = Path(next(iter(spec.submodule_search_locations)))
    result = {}

    for path in sorted(scripts_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        module_path = f"{_SCRIPTS_MODULE}.{path.stem}"
        try:
            mod = importlib.import_module(module_path)
            if callable(getattr(mod, "main", None)):
                cli_name = path.stem
                result[cli_name] = module_path
        except Exception as e:
            # Broken import - skip the script (don't crash list/run), but leave
            # a breadcrumb so it doesn't silently vanish from `db script list`.
            runtime.logger.debug(f"Skipping script module '{module_path}': {e}")

    return result


@dev_db_scripts_app.callback()
def main() -> None:
    """
    Run data maintenance scripts against the development database.

    Scripts manipulate existing data — they do not change the schema.
    For schema changes, use `mascope dev migrate upgrade`.

    A backup is always taken before execution.
    """


@dev_db_scripts_app.command("list")
def list_scripts() -> None:
    """List available maintenance scripts."""
    scripts = _discover_scripts()
    if not scripts:
        runtime.logger.warning("No scripts found in mascope_backend.db.scripts")
        return
    runtime.logger.info("Available scripts:")
    for name, module in scripts.items():
        runtime.logger.info(f"  {name}")


@dev_db_scripts_app.command("run")
def run_script(
    script: Annotated[
        str,
        typer.Argument(help="Script name. Run 'list' to see available scripts."),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm execution against dev."),
    ] = False,
    skip_backup: Annotated[
        bool,
        typer.Option(
            "--skip-backup",
            "-S",
            help="Skip the pre-execution backup. Use for large databases "
            "where pg_dump is prohibitively slow. NO BACKUP IS TAKEN.",
        ),
    ] = False,
) -> None:
    """
    Run a maintenance script against the development database.

    Takes an automatic pre-execution backup before running, unless
    `--skip-backup` is passed.

    Some scripts accept configuration via environment variables.
    For example, to pass MIN_DATETIME:

    \b
    Linux / macOS:
        MIN_DATETIME=2025-06-01T00:00:00 mascope dev db script run <script>
    Windows PowerShell:
        $env:MIN_DATETIME="2025-06-01T00:00:00"; mascope dev db script run <script>

    \b
    Examples:
        mascope dev db script run populate_none_instrument_function_ids
        mascope dev db script run sanitize_match_isotope_non_finite
    """
    if not check_prerequisites(_MODE):
        return

    scripts = _discover_scripts()

    if script not in scripts:
        runtime.logger.error(
            f"Unknown script '{script}'. Run 'mascope dev db script list'."
        )
        raise typer.Exit(1)

    if not yes:
        typer.confirm(
            f"Run '{script}' against dev '{runtime.env.name}'?",
            abort=True,
        )

    db_cfg = runtime.full_config.backend.database

    # --- Backup ---
    if skip_backup:
        # WARNING interactively, INFO under --yes: see skip_backup_log_level.
        runtime.logger.log(
            skip_backup_log_level(yes),
            "Skipping pre-script backup (--skip-backup). "
            "No restore point will exist if this script corrupts data.",
        )
        if not yes:
            typer.confirm(
                f"Run '{script}' against dev '{runtime.env.name}' WITHOUT a backup?",
                abort=True,
            )
    else:
        try:
            container = db_cfg.get_postgres_container_name(_MODE)
            database = db_cfg.get_postgres_database_name(runtime.env.name)
            target_dir, mount = dirs(False, _MODE)
            path = pg_dump(
                container,
                db_cfg.user,
                database,
                target_dir,
                mount,
                label=f"pre-{script}",
            )
            runtime.logger.success(f"Pre-script backup: {path.name}")
        except RuntimeError as e:
            runtime.logger.error(f"Backup failed — aborting: {e}")
            raise typer.Exit(1)

    # --- Execute ---
    module = scripts[script]
    runtime.logger.info(f"Running: {module}")

    result = subprocess.run(
        ["uv", "run", "python", "-m", module],
        check=False,
    )

    if result.returncode != 0:
        runtime.logger.error(f"Script exited with code {result.returncode}")
        raise typer.Exit(result.returncode)

    runtime.logger.success("Script completed")
