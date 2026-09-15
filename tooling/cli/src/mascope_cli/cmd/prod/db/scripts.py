"""
Production database script runner.

Discovers and executes maintenance scripts from
`mascope_backend.db.scripts.*` inside the production backend container,
with automatic pre-execution backup.

Discovery happens inside the container as well. The standalone operator CLI
(`uv tool install mascope-cli`) ships without `mascope_backend`, so there is
nothing to discover on the host - and even on a monorepo install the
container's copy is the one the script will run in, so it is the one that
should be listed. The host install is consulted only when the container
cannot be asked.

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

from mascope_cli.pg import (
    check_prerequisites,
    dirs,
    pg_dump,
    skip_backup_log_level,
)
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
    "MASCOPE_PRUNE_KEEP_PER_SAMPLE_TOTAL",
    "MASCOPE_PRUNE_KEEP_FAILED_HOURS",
    "MASCOPE_PRUNE_KEEP_RUNNING_HOURS",
    "MASCOPE_PRUNE_KEEP_IMPORTING_HOURS",
    # require_password_change
    "MASCOPE_REQUIRE_PASSWORD_CHANGE_DRY_RUN",
    # clear_password_change_requirement
    "MASCOPE_CLEAR_PASSWORD_CHANGE_EMAILS",
]

# Runs inside the backend container (`<container_python> -c ...`) and prints
# one script name per line. It lists the package by file instead of importing
# each module, so the probe has no import side effects and cannot be broken
# by one script's imports. Every module in the package is an entry point by
# convention (`python -m` with a main()); subpackages and `_private` helpers
# are not scripts.
_LIST_SCRIPTS_SNIPPET = "\n".join(
    [
        "import pkgutil",
        f"import {_SCRIPTS_MODULE} as scripts",
        "for module in pkgutil.iter_modules(scripts.__path__):",
        "    if not module.ispkg and not module.name.startswith('_'):",
        "        print(module.name)",
    ]
)


def _discover_container_scripts(
    container: str, container_python: str
) -> dict[str, str] | None:
    """
    List the maintenance scripts shipped in the backend container.

    :param container: Backend container name.
    :type container: str
    :param container_python: Interpreter inside the container, as resolved by
        :func:`_resolve_container_python`.
    :type container_python: str
    :return: Mapping of CLI name to dotted module path, or ``None`` when the
        container could not be asked (the exec failed, or the package is not
        importable there) - distinct from a package with no scripts in it.
    :rtype: dict[str, str] | None
    """
    result = subprocess.run(
        ["docker", "exec", container, container_python, "-c", _LIST_SCRIPTS_SNIPPET],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        runtime.logger.warning(
            f"Could not list scripts inside '{container}' "
            f"(exit {result.returncode}): {detail[-1] if detail else 'no output'}"
        )
        return None

    names = [line.strip() for line in result.stdout.splitlines()]
    return {name: f"{_SCRIPTS_MODULE}.{name}" for name in names if name.isidentifier()}


def _discover_host_scripts() -> dict[str, str]:
    """
    Discover scripts from the host's own ``mascope_backend`` install.

    Only a monorepo install has the package; the standalone operator CLI does
    not, and gets an empty mapping here. Scans the package directory for .py
    files (excluding __init__) that expose a main() callable.

    :return: Mapping of CLI name to dotted module path.
    :rtype: dict[str, str]
    """
    try:
        spec = importlib.util.find_spec(_SCRIPTS_MODULE)
    except ModuleNotFoundError:
        # find_spec imports the parent package first, so a host without
        # mascope_backend raises here rather than returning None.
        return {}
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


def _discover_scripts(container: str, container_python: str | None) -> dict[str, str]:
    """
    Discover the available scripts, asking the backend container first.

    :param container: Backend container name.
    :type container: str
    :param container_python: Interpreter inside the container, or ``None``
        when none could be resolved (the container is down), in which case the
        host install is consulted instead.
    :type container_python: str | None
    :return: Mapping of CLI name to dotted module path; empty when neither
        the container nor the host could provide one.
    :rtype: dict[str, str]
    """
    if container_python is not None:
        found = _discover_container_scripts(container, container_python)
        if found is not None:
            return found
        runtime.logger.warning("Falling back to the host install's scripts.")
    return _discover_host_scripts()


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
    try:
        result = subprocess.run(
            ["docker", "exec", container, "sh", "-c", probe],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        # No docker binary on this host: same outcome as a stopped container.
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[-1] if result.returncode == 0 and lines else None


def _no_container_python_message(container: str) -> str:
    """Explain a failed interpreter lookup, and what to do about it."""
    return (
        f"Could not find a mascope Python in container '{container}'. "
        "Scripts are discovered and run inside the backend container, so the "
        "stack must be running (`mascope prod up --detach`) and the container "
        "must be built from the mascope image "
        f"(looked for {', '.join(_PYTHON_CANDIDATES)} and python/python3 on PATH)."
    )


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
    """List the maintenance scripts shipped in the backend container."""
    backend_container = runtime.full_config.backend.get_backend_container_name(_MODE)
    container_python = _resolve_container_python(backend_container)
    scripts = _discover_scripts(backend_container, container_python)

    if not scripts:
        if container_python is None:
            runtime.logger.error(_no_container_python_message(backend_container))
        else:
            runtime.logger.error(
                f"No scripts found in {_SCRIPTS_MODULE} "
                f"(container '{backend_container}')"
            )
        raise typer.Exit(1)

    if container_python is None:
        runtime.logger.warning(
            f"Backend container '{backend_container}' is not running; listing "
            "the host install's scripts instead, which may not match the "
            "deployed image."
        )
    runtime.logger.info("Available scripts:")
    for name in scripts:
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

    backend_container = runtime.full_config.backend.get_backend_container_name(_MODE)

    # The script can only run inside the container, so a stopped stack fails
    # here - before a backup is taken for a run that cannot happen.
    container_python = _resolve_container_python(backend_container)
    if container_python is None:
        runtime.logger.error(_no_container_python_message(backend_container))
        raise typer.Exit(1)

    scripts = _discover_scripts(backend_container, container_python)

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
        # WARNING interactively, INFO under --yes: see skip_backup_log_level.
        runtime.logger.log(
            skip_backup_log_level(yes),
            "Skipping pre-script backup (--skip-backup). "
            "No restore point will exist if this script corrupts data.",
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
