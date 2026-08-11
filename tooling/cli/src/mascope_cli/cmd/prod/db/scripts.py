"""
Production database script runner.

Discovers and executes maintenance scripts from
`mascope_backend.db.scripts.*` inside the production backend container,
with automatic pre-execution backup.

Scripts are data-manipulation entry points — see `mascope_backend.db.admin`
for the distinction from Alembic schema migrations.
"""

import importlib
import importlib.util
import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from mascope_cli.pg import check_prerequisites, dirs, pg_dump
from mascope_cli.runtime import runtime


prod_db_scripts_app = typer.Typer()

_MODE = "prod"
_SCRIPTS_MODULE = "mascope_backend.db.scripts"

# Candidate paths to the mascope uv-tool Python inside the backend container.
# The install location depends on how the image was built:
#   - /opt/uv/tools: current Dockerfile (UV_TOOL_DIR=/opt/uv/tools), also used by
#     db-init.sh, demo-init.sh and the reproducibility test.
#   - /root/.local/share/uv/tools: legacy images built before UV_TOOL_DIR was set.
# The interpreter is resolved at runtime (see _resolve_container_python) instead
# of hardcoding one, so the runner works regardless of which image is deployed.
_PYTHON_CANDIDATES = [
    "/opt/uv/tools/mascope/bin/python",
    "/root/.local/share/uv/tools/mascope/bin/python",
]

# Environment variables forwarded from the host into the backend container
# when running scripts via `docker exec -e`. A script env var missing from
# this list is silently unset inside the container - the documented
# MASCOPE_PRUNE_DRY_RUN=1 recipe would delete for real - so every variable a
# script reads must be listed here.
_FORWARDED_ENV_VARS = [
    "MIN_DATETIME",
    "UTC_OFFSET_HOURS",
    "ALLOW_MATCHED_LOSS",
    "DRY_RUN",
    "BATCH_SIZE",
    # prune_peak_assignment_runs
    "MASCOPE_PRUNE_DRY_RUN",
    "MASCOPE_PRUNE_KEEP_PER_SAMPLE",
    "MASCOPE_PRUNE_KEEP_FAILED_HOURS",
    "MASCOPE_PRUNE_KEEP_RUNNING_HOURS",
]


def _discover_scripts() -> dict[str, str]:
    """
    Discover available scripts from mascope_backend.db.scripts.

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
            # Broken import - skip the script, but leave a breadcrumb so it
            # doesn't silently vanish from `db script list`.
            runtime.logger.debug(f"Skipping script module '{module_path}': {e}")

    return result


def _resolve_container_python(container: str) -> str | None:
    """
    Resolve the mascope uv-tool Python inside the backend container.

    Probes the known uv-tool locations (which differ between current and legacy
    images) and falls back to any ``python``/``python3`` on PATH that can import
    ``mascope_backend``. Returns the resolved interpreter path, or ``None`` if
    none is found (e.g. the container is not the mascope backend, or is not
    running).

    :param container: Backend container name.
    :type container: str
    :return: Path to a usable interpreter inside the container, or None.
    :rtype: str | None
    """
    # A single shell probe: first existing tool Python wins; else the first
    # PATH python that can import the package. Prints the chosen path and exits 0.
    candidates = " ".join(f'"{p}"' for p in _PYTHON_CANDIDATES)
    probe = (
        f"for p in {candidates}; do "
        '  if [ -x "$p" ]; then echo "$p"; exit 0; fi; '
        "done; "
        "for p in python python3; do "
        '  if "$p" -c "import mascope_backend" >/dev/null 2>&1; then '
        '    command -v "$p"; exit 0; '
        "  fi; "
        "done; "
        "exit 1"
    )
    result = subprocess.run(
        ["docker", "exec", container, "sh", "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1] if result.returncode == 0 and lines else None


@prod_db_scripts_app.callback()
def main() -> None:
    """
    Run data maintenance scripts against the production database.

    Scripts manipulate existing data — they do not change the schema.
    For schema changes, the db-init container runs Alembic on startup.

    A backup is always taken before execution.
    """


@prod_db_scripts_app.command("list")
def list_scripts() -> None:
    """List available maintenance scripts."""
    scripts = _discover_scripts()
    if not scripts:
        runtime.logger.warning("No scripts found in mascope_backend.db.scripts")
        return
    runtime.logger.info("Available scripts:")
    for name, module in scripts.items():
        runtime.logger.info(f"  {name}")


@prod_db_scripts_app.command("run")
def run_script(
    script: Annotated[
        str,
        typer.Argument(help="Script name. Run 'list' to see available scripts."),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Confirm execution against prod."),
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
    Run a maintenance script inside the production backend container.

    Takes an automatic pre-execution backup before running, unless
    `--skip-backup` is passed.

    Some scripts accept configuration via environment variables.
    For example, to pass MIN_DATETIME:

    \b
    Linux / macOS:
        MIN_DATETIME=2025-06-01T00:00:00 mascope prod db script run <script>
    Windows PowerShell:
        $env:MIN_DATETIME="2025-06-01T00:00:00"; mascope prod db script run <script>

    \b
    Examples:
        mascope prod db script run populate_none_instrument_function_ids --yes
    """
    if not check_prerequisites(_MODE):
        return

    scripts = _discover_scripts()

    if script not in scripts:
        runtime.logger.error(
            f"Unknown script '{script}'. Run 'mascope prod db script list'."
        )
        raise typer.Exit(1)

    if not yes:
        typer.confirm(
            f"Run '{script}' against prod '{runtime.env.name}'?",
            abort=True,
        )

    db_cfg = runtime.full_config.backend.database

    # --- Backup ---
    if skip_backup:
        runtime.logger.warning(
            "Skipping pre-script backup (--skip-backup). "
            "No restore point will exist if this script corrupts data."
        )
        if not yes:
            typer.confirm(
                f"Run '{script}' against prod '{runtime.env.name}' WITHOUT a backup?",
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

    # --- Execute inside backend container ---
    module = scripts[script]
    backend_container = runtime.full_config.backend.get_backend_container_name(_MODE)

    container_python = _resolve_container_python(backend_container)
    if container_python is None:
        runtime.logger.error(
            f"Could not find a mascope Python in container '{backend_container}'. "
            "Is the backend container running, and built from the mascope image? "
            f"(looked for {', '.join(_PYTHON_CANDIDATES)} and python/python3 on PATH)"
        )
        raise typer.Exit(1)

    runtime.logger.info(
        f"Running in '{backend_container}' ({container_python}): {module}"
    )

    # Forward selected host env vars into the container
    env_args: list[str] = []
    for var in _FORWARDED_ENV_VARS:
        val = os.environ.get(var)
        if val is not None:
            env_args += ["-e", f"{var}={val}"]

    result = subprocess.run(
        [
            "docker",
            "exec",
            *env_args,
            backend_container,
            container_python,
            "-m",
            module,
        ],
        check=False,
    )

    if result.returncode != 0:
        runtime.logger.error(f"Script exited with code {result.returncode}")
        raise typer.Exit(result.returncode)

    runtime.logger.success("Script completed")
