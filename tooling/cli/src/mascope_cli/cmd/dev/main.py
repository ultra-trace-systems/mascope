"""
Development environment management commands.

Provides commands to run, monitor, and manage Mascope development services.
"""

import base64
import json
import os
import platform
from pathlib import Path
from typing import Annotated, List, Optional

import typer

from mascope_cli import config_layers
from mascope_cli.cmd import lib
from mascope_cli.cmd.dev.db import (
    create_database,
    dev_db_app,
    wait_for_server,
)
from mascope_cli.cmd.dev.docker import (
    check_and_start_docker,
    dev_docker_app,
)
from mascope_cli.cmd.dev.migrate import (
    check_pending_migrations,
    dev_migrate_app,
    run_migrations,
)
from mascope_cli.cmd.dev.redis import dev_redis_app, wait_for_redis
from mascope_cli.cmd.dev.tools import dev_tools_app
from mascope_cli.pg.utils import check_data_dirs
from mascope_cli.runtime import runtime
from mascope_runtime import Runtime


dev_app = typer.Typer()

concurrently = "concurrently.cmd" if platform.system() == "Windows" else "concurrently"


_MODE = "dev"


def _dev_compose_path() -> Path:
    """Path of the dev compose file under MASCOPE_PATH."""
    return Path(os.environ["MASCOPE_PATH"]) / "docker-compose.dev.yaml"


def _source_root() -> str:
    """Repo root of the running CLI source.

    ``mascope dev run`` launches the app (backend, frontend, ...) from here so
    it runs the checkout you invoked ``mascope`` from - e.g. a git worktree -
    rather than ``MASCOPE_PATH``. ``MASCOPE_PATH`` only locates runtime state
    (the database, secrets, ``.runtime``), which is shared across checkouts, so
    the two must not be conflated: without this, running from a worktree would
    execute the main repo's source, because editable installs resolve imports
    from the directory the subprocess starts in, not the one you cd'd into.

    Derived from the installed ``mascope_cli`` package location, falling back to
    the current directory when that does not resolve to a repo (e.g. a
    non-editable install).
    """
    from mascope_cli.checkout import source_checkout

    root = source_checkout()
    if root:
        return str(root)
    return os.getcwd()


@dev_app.callback()
def main():
    """
    Manage your development environment
    """
    runtime.state.override("mode", _MODE)
    runtime.reload_config()
    runtime.logger.info(
        f'Running at env "{runtime.env.name}" in {runtime.state.mode} mode'
    )


# Add subcommands
dev_app.add_typer(dev_docker_app, name="docker")
dev_app.add_typer(dev_migrate_app, name="migrate")
dev_app.add_typer(dev_db_app, name="db")
dev_app.add_typer(dev_redis_app, name="redis")
dev_app.add_typer(dev_tools_app, name="tools")


def _resolve_modules(module_names: List[str]) -> List[dict]:
    """
    Resolve module names or tags to actual module definitions.

    :param module_names: List of module names or a single tag
    :return: List of resolved module dictionaries
    """
    # Select modules by name
    resolved = [mod for mod in runtime.modules if mod["name"] in module_names]

    # Use tags if no modules selected by name
    if not resolved:
        [tag] = module_names
        resolved = [mod for mod in runtime.modules if tag in mod["tags"]]

    return resolved


def _activate_instance() -> None:
    """
    Resolve this worktree's instance and switch the runtime onto it.

    Allocates (or reuses) the worktree's slot, ensures its env directory
    exists, publishes the derived ports via env vars (honoured by the runtime
    config and inherited by the backend/frontend subprocesses), and points the
    runtime at the instance env. Env selection goes through ``MASCOPE_ENV`` /
    ``use_env`` — never persisted state — so concurrent worktrees sharing one
    ``MASCOPE_PATH`` do not clobber each other.
    """
    from mascope_cli.instance import provision, resolve_or_allocate

    inst = resolve_or_allocate()
    provision(inst)

    # Set port overrides before use_env: its config reload reads them.
    os.environ["MASCOPE_API_PORT"] = str(inst.api_port)
    os.environ["MASCOPE_FRONTEND_PORT"] = str(inst.frontend_port)
    runtime.use_env(inst.env)

    runtime.logger.info(
        f"Instance slot {inst.slot}: env '{inst.env}', "
        f"backend :{inst.api_port}, frontend :{inst.frontend_port}"
    )


def _run_dev_compose(args: list[str]):
    """
    Execute docker-compose command for dev environment.

    :param args: Docker compose arguments (e.g., ['up', '-d'])
    """
    db_cfg = runtime.full_config.backend.database
    redis_cfg = runtime.full_config.backend.redis

    lib.run(
        command=f"docker compose --file '{_dev_compose_path()}' {' '.join(args)}",
        env_vars={
            "MASCOPE_ENV": runtime.env.name,
            "MASCOPE_PATH": os.environ["MASCOPE_PATH"],
            # --- Db settings ---
            "MASCOPE_DB_CONTAINER_NAME": db_cfg.get_postgres_container_name(mode=_MODE),
            "MASCOPE_DB_PORT": str(db_cfg.port),
            "MASCOPE_DB_USER": db_cfg.user,
            "MASCOPE_DB_SHM_SIZE": db_cfg.shm_size,
            "MASCOPE_DB_MAX_CONNECTIONS": str(db_cfg.max_connections),
            "MASCOPE_DB_SHARED_BUFFERS": db_cfg.shared_buffers,
            "MASCOPE_DB_EFFECTIVE_CACHE_SIZE": db_cfg.effective_cache_size,
            "MASCOPE_DB_WORK_MEM": db_cfg.work_mem,
            "MASCOPE_DB_MAINTENANCE_WORK_MEM": db_cfg.maintenance_work_mem,
            "MASCOPE_DB_AUTOVACUUM_WORK_MEM": db_cfg.autovacuum_work_mem,
            "MASCOPE_DB_WAL_BUFFERS": db_cfg.wal_buffers,
            "MASCOPE_DB_MIN_WAL_SIZE": db_cfg.min_wal_size,
            "MASCOPE_DB_MAX_WAL_SIZE": db_cfg.max_wal_size,
            "MASCOPE_DB_CHECKPOINT_COMPLETION_TARGET": str(
                db_cfg.checkpoint_completion_target
            ),
            "MASCOPE_DB_WAL_COMPRESSION": db_cfg.wal_compression,
            "MASCOPE_DB_EFFECTIVE_IO_CONCURRENCY": str(db_cfg.effective_io_concurrency),
            "MASCOPE_DB_RANDOM_PAGE_COST": str(db_cfg.random_page_cost),
            "MASCOPE_DB_DEFAULT_STATISTICS_TARGET": str(
                db_cfg.default_statistics_target
            ),
            "MASCOPE_DB_JIT": db_cfg.jit,
            "MASCOPE_DB_AUTOVACUUM_MAX_WORKERS": str(db_cfg.autovacuum_max_workers),
            # --- Redis settings ---
            "MASCOPE_REDIS_CONTAINER_NAME": redis_cfg.get_redis_container_name(
                mode=_MODE
            ),
            "MASCOPE_REDIS_PORT": str(redis_cfg.port),
        },
    )


def _run_application(
    modules: List[dict],
    host: bool = False,
    lab: bool = False,
    reload: bool = False,
):
    """
    Internal helper to run application services.

    :param modules: List of resolved modules to run
    :param host: Whether to expose to network
    :param lab: Whether to include jupyter lab
    :param reload: Whether to use Windows reload mode
    """
    selected = modules.copy()

    if lab:
        selected.append({"name": "lab", "run": "uv run jupyter lab"})

    # Set config env var
    frontend_runtime = Runtime("frontend", log=False)
    os.environ["MASCOPE_ENV"] = runtime.env.name
    os.environ["MASCOPE_RUNTIME"] = json.dumps(
        {
            "mode": frontend_runtime.mode,
            "env": frontend_runtime.env.name,
            "meta": frontend_runtime.meta.model_dump(),
            "config": frontend_runtime.config.model_dump(),
            "version": os.environ["MASCOPE_VERSION"],
        }
    )

    # If --host set, expose dev server to network
    if host:
        os.environ["MASCOPE_DEVHOST"] = "HOST"

    # Launch the app from the CLI's source checkout, not MASCOPE_PATH, so
    # running from a worktree runs that worktree's code (see _source_root).
    source_root = _source_root()

    # Build module runner
    def run_module(mod):
        """Run a module with optional Windows reload mode."""
        if reload and mod["name"] == "backend":
            # Helper to pass env vars
            def pass_envvar(var):
                value = os.environ.get(var)
                return (
                    f"[Environment]::SetEnvironmentVariable('{var}', '{value}')"
                    if value
                    else None
                )

            pass_envvars = " && ".join(
                [
                    pass_envvar(var)
                    for var in [
                        "MASCOPE_LOGLEVEL",
                        "MASCOPE_LOGGREP",
                        "MASCOPE_ENV",  # runtime env
                        "MASCOPE_DEVHOST",  # host option
                    ]
                    if pass_envvar(var)
                ]
            )
            # construct the command
            cmd = f"{pass_envvars} && {mod['run']}"
            # complex commands are best encoded to avoid needing escape chars
            base64_cmd = base64.b64encode(bytearray(cmd, "utf-16-le")).decode()
            # open a new tab in the current windows terminal, started in the
            # source checkout, and run
            return (
                f'"wt --window 0 -d \\"{source_root}\\" '
                f'pwsh -noExit -EncodedCommand {base64_cmd}"'
            )
        else:
            # default behavior
            return f'"{mod["run"]}"'

    # Run concurrently
    names = f"--names {','.join(mod['name'] for mod in selected)}"
    cmds = f"{' '.join([run_module(mod) for mod in selected])}"

    runtime.logger.info(f"Starting: {', '.join(mod['name'] for mod in selected)}")
    # cwd=source_root so the spawned `uv run mascope-backend` / frontend resolve
    # against the invoked checkout rather than MASCOPE_PATH.
    lib.run(f"{concurrently} --raw {names} {cmds}", cwd=source_root)


@dev_app.command()
def up(
    detach: Annotated[
        bool,
        typer.Option("--detach", "-d", help="Run containers in background"),
    ] = True,
    build: Annotated[
        bool,
        typer.Option("--build", help="Rebuild images before starting"),
    ] = False,
):
    """
    Start development dependencies (PostgreSQL, Redis).

    Does NOT run migrations or application.
    Use 'mascope dev run' for automatic workflow.
    """
    check_and_start_docker()

    # Prepare data directories before starting containers
    check_data_dirs(_MODE)

    args = ["up"]
    if detach:
        args.append("-d")
    if build:
        args.append("--build")

    runtime.logger.info("Starting development dependencies...")
    _run_dev_compose(args)

    if detach:
        runtime.logger.success("Development dependencies started")
        runtime.logger.info("Run 'mascope dev run' to start the application")


@dev_app.command()
def down(
    volumes: Annotated[
        bool,
        typer.Option("--volumes", "-v", help="Remove named volumes"),
    ] = False,
):
    """
    Stop and remove development containers.

    Data in bind mounts (PostgreSQL) is preserved.
    """
    check_and_start_docker()

    args = ["down"]
    if volumes:
        args.append("-v")

    _run_dev_compose(args)
    runtime.logger.success("Development dependencies stopped")


@dev_app.command()
def run(
    modules: Annotated[
        Optional[List[str]],
        typer.Argument(
            help=(
                "List of modules or module groups to run; see "
                "`mascope modules --runnable` to see runnable modules"
            ),
            show_default="backend frontend",
        ),
    ] = None,
    host: Annotated[
        bool,
        typer.Option(
            "--host",
            "-h",
            help="Expose the backend and frontend dev servers to the network",
        ),
    ] = False,
    lab: Annotated[
        bool,
        typer.Option(
            "--lab",
            "-l",
            help="Spawn the jupyter lab server",
        ),
    ] = False,
    reload: Annotated[
        bool,
        typer.Option(
            "--reload",
            "-r",
            help="Spawn the backend in a seperate terminal tab to enable HMR in Windows",
        ),
    ] = False,
    skip_migrations: Annotated[
        bool,
        typer.Option(
            "--skip-migrations",
            "-s",
            help="Skip database migrations",
        ),
    ] = False,
    instance: Annotated[
        bool,
        typer.Option(
            "--instance",
            "-i",
            help=(
                "Run an isolated per-worktree instance: derive a dedicated env "
                "and app ports from this checkout so several worktrees can run "
                "at once against the shared Postgres/Redis. Also enabled by "
                "setting MASCOPE_INSTANCE=1."
            ),
        ),
    ] = False,
):
    """
    \b
    Run application services in development environment:
    - Checks Docker is running
    - Starts dependencies (PostgreSQL, Redis)
    - Runs migrations (if backend + PostgreSQL configured and not skipped)
    - Starts application

    Pass modules to run as arguments. You can also use
    module group tags to run multiple services at once.

    Run `mascope groups` to discover the full list of runtime groups.

    \b
    Examples:
        mascope dev run                    # Backend + frontend (default)
        mascope dev run backend            # Backend only
        mascope dev run file-converter     # Explicit services
        mascope dev run file               # Module group tag

    \b
    Manual control:
        mascope dev up                     # Dependencies only
        mascope dev migrate upgrade        # Migrations manually
    """
    selected_modules = modules or ["backend", "frontend", "file-converter"]

    # --- Resolve module names/tags to actual modules ---
    resolved_modules = _resolve_modules(selected_modules)

    if not resolved_modules:
        runtime.logger.error(
            f"No configured modules found for: {', '.join(selected_modules)}"
        )
        raise typer.Exit(1)

    # --- per-worktree instance (isolated env + app ports on shared infra) ---
    if instance or os.environ.get("MASCOPE_INSTANCE"):
        _activate_instance()

    # --- Check if backend is selected (for migration logic) ---
    backend_selected = any(mod["name"] == "backend" for mod in resolved_modules)

    # --- check Docker ---
    check_and_start_docker()

    # --- check dependencies running ---
    check_data_dirs(_MODE)
    _run_dev_compose(["up", "-d"])

    # --- wait for services ---
    if not wait_for_redis(max_wait=30):
        runtime.logger.error("Redis failed to start")
        raise typer.Exit(1)

    if not wait_for_server(max_wait=30):
        runtime.logger.error("PostgreSQL server failed to start")
        raise typer.Exit(1)

    # --- migrations (if backend selected + PostgreSQL) ---
    if backend_selected:
        runtime.logger.info("Checking database...")
        if not create_database():
            runtime.logger.error("Failed to create database")
            raise typer.Exit(1)

        runtime.logger.info("Checking migrations...")
        if check_pending_migrations():
            runtime.logger.info("Pending migrations detected")
            if not skip_migrations:
                runtime.logger.info("Applying migrations...")
                if not run_migrations():
                    runtime.logger.error("Failed to apply migrations")
                    raise typer.Exit(1)
            else:
                runtime.logger.warning("Skipping migrations as requested")
        else:
            runtime.logger.success("Database up to date")

    # --- run application ---
    _run_application(
        modules=resolved_modules,
        host=host,
        lab=lab,
        reload=reload,
    )


@dev_app.command()
def logs(
    follow: Annotated[
        bool,
        typer.Option("--follow", "-f", help="Follow log output"),
    ] = False,
    service: Annotated[
        Optional[str],
        typer.Argument(help="Service name (postgres, redis, db-migrate)"),
    ] = None,
):
    """Show logs from development containers."""
    args = ["logs"]
    if follow:
        args.append("-f")
    if service:
        args.append(service)

    _run_dev_compose(args)


@dev_app.command()
def ps():
    """List running development containers."""
    check_and_start_docker()
    _run_dev_compose(["ps"])


@dev_app.command()
def restart(
    service: Annotated[
        Optional[str],
        typer.Argument(help="Service to restart (postgres, redis)"),
    ] = None,
):
    """Restart development services."""
    args = ["restart"]
    if service:
        args.append(service)

    _run_dev_compose(args)


@dev_app.command(name="config")
def config_report(
    show_all: Annotated[
        bool,
        typer.Option("--all", help="Print every setting, not only the overrides."),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit the report as JSON instead of text."),
    ] = False,
) -> None:
    """
    Show which config settings this env runs differently from the defaults.

    Reads the three config layers as the backend does (base.mascope.toml,
    dev.mascope.toml, then the env's own dev.mascope.toml) and prints every
    setting whose value differs from the config model's default, with the
    layer that set it. Credentials are never printed. Set MASCOPE_ENV to
    report another env.

    \b
    Examples:
        mascope dev config
        mascope dev config --all
        MASCOPE_ENV=testbed mascope dev config --json
    """
    report = config_layers.collect(
        {
            "base": runtime.path("base.mascope.toml"),
            "mode": runtime.path(f"{_MODE}.mascope.toml"),
            "env": runtime.env.path(f"./{_MODE}.mascope.toml"),
        }
    )
    if as_json:
        typer.echo(config_layers.format_json(report, show_all=show_all))
        return
    heading = config_layers.title(runtime.env.name, _MODE, show_all=show_all)
    typer.echo(config_layers.format_text(report, show_all=show_all, title=heading))


@dev_app.command()
def shell(
    vi: Annotated[
        Optional[bool],
        typer.Option(
            "--vi",
            "-v",
            help="Use with vim keybindings",
        ),
    ] = False,
    asyncio: Annotated[
        Optional[bool],
        typer.Option(
            "--async",
            "-a",
            help="Run with asyncio loop to allow awaiting",
        ),
    ] = False,
):
    """
    Drop into a ptpython shell with access to the Mascope venv
    """
    lib.run("clear")
    lib.run(
        f"uv run --with ptpython ptpython --dark-bg {'--vi' if vi else ''} {'--asyncio' if asyncio else ''}"
    )
