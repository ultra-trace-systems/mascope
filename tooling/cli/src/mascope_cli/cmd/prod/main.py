"""
Production environment management commands.

Provides commands to manage Mascope production services via docker compose.
Wraps the most common compose operations as explicit subcommands and exposes
a `docker` escape hatch for arbitrary compose passthrough.

Common operations:
    mascope prod up
    mascope prod up --build
    mascope prod down
    mascope prod ps
    mascope prod build
    mascope prod logs --follow
    mascope prod restart postgres
    mascope prod docker exec -it postgres bash

Database management:
    mascope prod db status
    mascope prod db backup
    mascope prod db restore --yes
"""

import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Annotated, Optional

import typer

from mascope_cli.checkout import backend_path
from mascope_cli.cmd import lib
from mascope_cli.cmd.prod import auto_update, cli_drift, preflight, release_manifest
from mascope_cli.cmd.prod import doctor as prod_doctor
from mascope_cli.cmd.prod.db import prod_db_app
from mascope_cli.pg.utils import check_data_dirs, is_container_running
from mascope_cli.runtime import runtime
from mascope_runtime import Runtime


_MODE = "prod"

# Published production image repositories, matching the `image:` fields in
# docker-compose.yaml. The target release tag is appended to form the full
# reference the update preflight pulls and inspects.
_BACKEND_IMAGE = "ghcr.io/ultra-trace-systems/mascope/backend"
_FRONTEND_IMAGE = "ghcr.io/ultra-trace-systems/mascope/frontend"

# Repository whose releases the unattended updater tracks. Overridable via
# MASCOPE_UPDATE_REPO for forks/mirrors.
_DEFAULT_UPDATE_REPO = "ultra-trace-systems/mascope"

prod_app = typer.Typer()
prod_app.add_typer(prod_db_app, name="db")


#  --- Callback — runs before every prod subcommand ---


@prod_app.callback()
def main() -> None:
    """
    Manage the Mascope production environment.

    Wraps the most common `docker compose` operations as named subcommands.
    For anything not covered, use `mascope prod docker <args>` to pass
    arbitrary arguments directly to `docker compose`.

    \b
    Compose commands (run `mascope prod <cmd> --help` for details):
        mascope prod up
        mascope prod up --build
        mascope prod down
        mascope prod ps
        mascope prod build
        mascope prod logs --follow backend
        mascope prod restart postgres
        mascope prod docker exec -it postgres bash

    \b
    Database management (run `mascope prod db --help` for details):
        mascope prod db status
        mascope prod db backup
        mascope prod db restore --yes
    """
    # Override mode without writing to state.json — prevents state.json from a
    # previous dev/prod run from contaminating this invocation's config.
    runtime.state.override("mode", _MODE)
    runtime.reload_config()
    runtime.logger.info(
        f'Running at env "{runtime.env.name}" in {runtime.state.mode} mode'
    )


#  --- Internal helpers ---


def _compose_path() -> str:
    """
    Path of the production compose file under MASCOPE_PATH.

    :return: Absolute path to docker-compose.yaml.
    :rtype: str
    """
    return os.path.join(os.environ["MASCOPE_PATH"], "docker-compose.yaml")


def _deploy_version() -> str:
    """
    Resolve the image tag for pulling/running published production images.

    Published prod images exist only for master (``latest``) and release tags
    (``vX.Y.Z``) - never for the branch-derived dev build id - so this ignores
    the checked-out branch entirely: an explicit ``MASCOPE_VERSION`` pin wins;
    otherwise a semver tag at HEAD selects that release; otherwise ``latest``
    (the rolling master build).

    :return: The image tag to deploy.
    :rtype: str
    """
    if os.environ.get("_MASCOPE_VERSION_PINNED") == "1":
        return os.environ["MASCOPE_VERSION"]
    # Resolve git in MASCOPE_PATH - the deployment checkout - rather than the
    # process's working directory, which is not the checkout when the CLI is
    # invoked from elsewhere (systemd starts units in "/"). Off the checkout
    # git resolves nothing, and the fallback below would quietly swap the
    # pinned release for `latest` on every boot.
    #
    # Git only, not resolve_version: the CLI's own package version is a
    # calver (e.g. v2026.7.7) in a different series from the app's release
    # image tags (vX.Y.Z), so it must never be used as a deploy tag. A
    # pip-installed CLI without a pin deploys `latest`.
    version = runtime.parse_version(cwd=os.environ.get("MASCOPE_PATH"))
    if re.fullmatch(r"v\d+\.\d+\.\d+", version):
        return version
    if version == "unknown-version":
        # Git resolved nothing at all (no checkout, or a directory that is not
        # a repository). Deploying `latest` is still the only safe published
        # tag, but it silently changes release channel - and a `latest` build
        # can carry migrations the pinned release has not seen - so say so.
        runtime.logger.warning(
            f"No git checkout found at '{os.environ.get('MASCOPE_PATH')}' - "
            "cannot tell which release this deployment runs, falling back to "
            "the rolling 'latest' image tag. Pin the release explicitly with "
            "MASCOPE_VERSION=vX.Y.Z, or deploy from a checkout at the release "
            "tag."
        )
    return "latest"


def _align_checkout(target: str, mascope_path: Optional[str]) -> None:
    """
    Move the deployment checkout to the release tag that is now running.

    A boot redeploys whatever the checkout selects (the systemd unit runs
    from it), so after an update the checkout must name the running release -
    left behind, a reboot before the next update quietly redeploys the
    previous one, and `mascope prod doctor` reports the gap as DRIFT the
    whole time.

    Best-effort by design: only a clean checkout is moved (`git checkout`
    without force, so local modifications are never discarded), a tag the
    checkout has not fetched yet is fetched from `origin` first, and any
    failure downgrades to a warning naming the manual step - the stack is
    already healthy on the new release, and alignment must not turn that
    success into an error.

    :param target: The image tag just deployed. Only a release tag
        (``vX.Y.Z``) aligns; ``latest`` tracks master and has no tag.
    :param mascope_path: The deployment checkout, or None when unset.
    """
    if not mascope_path or not re.fullmatch(r"v\d+\.\d+\.\d+", target):
        return

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", mascope_path, *args],
            capture_output=True,
            text=True,
            timeout=120,
        )

    try:
        head = git("rev-parse", "HEAD")
        if head.returncode != 0:
            return  # not a git checkout - nothing to align
        tag = git("rev-parse", "--verify", f"refs/tags/{target}^{{commit}}")
        if tag.returncode != 0:
            # Routine under --auto: it learns of new releases from the GitHub
            # releases API, so the tag is often newer than the last fetch.
            fetch = git("fetch", "--quiet", "--no-tags", "origin", "tag", target)
            if fetch.returncode != 0:
                raise RuntimeError(fetch.stderr.strip() or "git fetch failed")
            tag = git("rev-parse", "--verify", f"refs/tags/{target}^{{commit}}")
            if tag.returncode != 0:
                raise RuntimeError(f"tag '{target}' not found after fetching")
        if head.stdout.strip() == tag.stdout.strip():
            return  # the checkout already names the running release
        checkout = git("checkout", "--quiet", target)
        if checkout.returncode != 0:
            raise RuntimeError(checkout.stderr.strip() or "git checkout failed")
        runtime.logger.info(f"Moved the deployment checkout to {target}.")
    except Exception as e:
        runtime.logger.warning(
            f"The stack runs {target} but the deployment checkout could not "
            f"be moved to it ({e}). Until it is, a reboot redeploys the "
            f"previous release and doctor reports DRIFT - run "
            f"'git checkout {target}' in {mascope_path} to align."
        )


def _compose_env(building: bool = False) -> dict[str, str]:
    """
    Build the environment variable dict injected into every docker compose call.

    Resolves all runtime config, container names, and build arguments required
    by the production compose file. Must be called after `runtime.reload_config()`
    so that prod-mode config is active.

    :return: Mapping of environment variable names to their resolved values.
    :rtype: dict[str, str]
    """
    db_cfg = runtime.full_config.backend.database
    backend_cfg = runtime.full_config.backend
    file_converter_cfg = runtime.full_config.file_converter
    frontend_cfg = runtime.full_config.frontend
    redis_cfg = runtime.full_config.backend.redis

    db_name = db_cfg.get_postgres_database_name(env_name=runtime.env.name)

    # Instantiated with log=False to avoid duplicate log configuration.
    # mode=_MODE passed explicitly so this temporary instance uses prod config
    # without touching state.json.
    frontend_runtime = Runtime("frontend", mode=_MODE, log=False)
    mascope_runtime = frontend_runtime.module.to_json()

    if platform.system() != "Windows":
        # On Unix, inherit OS timezone
        timezone = "/".join(time.tzname)
    else:
        # Windows uses a different timezone system than Linux/macOS; converting
        # from the Windows format proved difficult, and the app is deployed on
        # Linux anyway, so Etc/UTC is a safe default.
        timezone = "Etc/UTC"

    return dict(
        MASCOPE_ENV=runtime.env.name,
        MASCOPE_PATH=os.environ["MASCOPE_PATH"],
        MASCOPE_RUNTIME=mascope_runtime,
        MASCOPE_FILESTORE=runtime.meta.filestore,
        MASCOPE_TIMEZONE=timezone,
        # Selects which image tag to pull/build (the compose `image:` field).
        # When building, use the current HEAD's version (branch-derived or
        # pinned) so the local build is tagged and displayed accordingly; when
        # pulling/running, use the deploy version (pin, release tag, or `latest`)
        # so a stray branch checkout never asks for an unpublished image tag.
        MASCOPE_VERSION=(
            os.environ["MASCOPE_VERSION"] if building else _deploy_version()
        ),
        # Forwarded explicitly so compose variable interpolation is always
        # satisfied — empty string when --log-level was not passed, which
        # compose treats as "no override" for the container environment.
        MASCOPE_LOGLEVEL=os.environ.get("MASCOPE_LOGLEVEL", ""),
        # --- Db settings ---
        MASCOPE_DB_NAME=db_name,
        MASCOPE_DB_USER=db_cfg.user,
        MASCOPE_DB_CONTAINER_NAME=db_cfg.get_postgres_container_name(mode=_MODE),
        MASCOPE_DB_SHM_SIZE=db_cfg.shm_size,
        MASCOPE_DB_MAX_CONNECTIONS=str(db_cfg.max_connections),
        MASCOPE_DB_SHARED_BUFFERS=db_cfg.shared_buffers,
        MASCOPE_DB_EFFECTIVE_CACHE_SIZE=db_cfg.effective_cache_size,
        MASCOPE_DB_WORK_MEM=db_cfg.work_mem,
        MASCOPE_DB_MAINTENANCE_WORK_MEM=db_cfg.maintenance_work_mem,
        MASCOPE_DB_AUTOVACUUM_WORK_MEM=db_cfg.autovacuum_work_mem,
        MASCOPE_DB_WAL_BUFFERS=db_cfg.wal_buffers,
        MASCOPE_DB_MIN_WAL_SIZE=db_cfg.min_wal_size,
        MASCOPE_DB_MAX_WAL_SIZE=db_cfg.max_wal_size,
        MASCOPE_DB_CHECKPOINT_COMPLETION_TARGET=str(
            db_cfg.checkpoint_completion_target
        ),
        MASCOPE_DB_WAL_COMPRESSION=db_cfg.wal_compression,
        MASCOPE_DB_EFFECTIVE_IO_CONCURRENCY=str(db_cfg.effective_io_concurrency),
        MASCOPE_DB_RANDOM_PAGE_COST=str(db_cfg.random_page_cost),
        MASCOPE_DB_DEFAULT_STATISTICS_TARGET=str(db_cfg.default_statistics_target),
        MASCOPE_DB_JIT=db_cfg.jit,
        MASCOPE_DB_AUTOVACUUM_MAX_WORKERS=str(db_cfg.autovacuum_max_workers),
        # pg_dump --compress for the db_init pre-migration dump
        MASCOPE_DUMP_COMPRESSION=db_cfg.dump_compression,
        # --- Container names ---
        MASCOPE_REDIS_CONTAINER_NAME=redis_cfg.get_redis_container_name(mode=_MODE),
        MASCOPE_BACKEND_CONTAINER_NAME=backend_cfg.get_backend_container_name(
            mode=_MODE
        ),
        MASCOPE_FILE_CONVERTER_CONTAINER_NAME=file_converter_cfg.get_file_converter_container_name(
            mode=_MODE
        ),
        MASCOPE_FRONTEND_CONTAINER_NAME=frontend_cfg.get_frontend_container_name(
            mode=_MODE
        ),
    )


def _run_compose(args: list[str], building: bool = False) -> None:
    """
    Invoke `docker compose` against the production compose file.

    Builds the full environment variable dict and delegates to `lib.run`.
    Mode override and config reload are handled in the callback — by the
    time any command calls this, config is already prod-scoped.

    :param args: docker compose subcommand and arguments,
                 e.g. `["up", "--detach"]` or `["logs", "--follow", "backend"]`.
    :type args: list[str]
    :param building: Whether this invocation builds images (vs. pulling/running).
                     Selects the current HEAD's version instead of the deploy
                     version for the compose `image:` tag.
    :type building: bool
    :raises typer.Exit: With docker compose's exit code when it fails, so
                        callers (CI in particular) can rely on the CLI's exit
                        status instead of scraping logs.
    """
    env_vars = _compose_env(building)
    command = f"docker compose --file '{_compose_path()}' {' '.join(args)}"

    runtime.logger.info(
        f"Database: {env_vars['MASCOPE_DB_NAME']}."
        f" Timezone: {env_vars['MASCOPE_TIMEZONE']}."
        f" Command: {command}"
    )

    result = lib.run(command=command, env_vars=env_vars)
    if result.returncode != 0:
        runtime.logger.error(
            f"docker compose exited with code {result.returncode} (command: {command})"
        )
        raise typer.Exit(result.returncode)


def _abort_if_low_disk(*, auto: bool) -> None:
    """
    Refuse to pull update images when the docker image store is low on space.

    A pull that fills the disk can wedge Postgres and take the whole stack down,
    so stop before touching anything. Under ``--auto`` the shortfall is written
    to the updater's status log (its audit trail, surfaced by the same tooling
    an operator already watches) and exits ``AUTO_ERROR``; interactively it
    exits 1. Never returns when disk is low - it raises ``typer.Exit``.
    """
    message = auto_update.disk_precheck()
    if message is None:
        return
    runtime.logger.error(message)
    if auto:
        auto_update.record_status(
            os.environ["MASCOPE_PATH"], f"Update aborted (low disk): {message}"
        )
        raise typer.Exit(auto_update.AUTO_ERROR)
    raise typer.Exit(1)


def _prune_images() -> None:
    """
    Remove unused images after a successful deploy.

    An update pulls new backend/frontend images and leaves the previous
    release's images behind - still tagged, no longer referenced by any
    container - so over many (especially unattended) updates they silently
    accumulate gigabytes. ``docker image prune -af`` drops every image no
    existing container references; the running stack's images are referenced and
    kept, and a rollback re-pulls (the documented flow already does, guarded by
    the disk precheck). Best-effort: a prune failure must never fail an
    otherwise healthy update, so it is logged and swallowed.
    """
    try:
        result = subprocess.run(
            ["docker", "image", "prune", "-a", "-f"],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        runtime.logger.warning(f"Skipped image prune (non-fatal): {exc}")
        return
    if result.returncode != 0:
        runtime.logger.warning(
            "Skipped image prune (non-fatal): "
            f"{result.stderr.strip() or 'docker image prune failed'}"
        )
        return
    # docker prints a trailing 'Total reclaimed space: <n>' summary line.
    summary = next(
        (
            line.strip()
            for line in reversed(result.stdout.splitlines())
            if "reclaimed" in line.lower()
        ),
        "",
    )
    runtime.logger.success(f"Pruned unused images. {summary}".rstrip())


def _warn_if_cli_stale(*, auto: bool) -> None:
    """
    Warn (never fix) when the running CLI has drifted behind the checkout.

    ``prod update`` refreshes the images but not the ``mascope`` tool itself, so
    after the operator moves the checkout forward the running CLI keeps executing
    the code it was last installed from - hiding new commands and fixes until the
    tool is reinstalled. This surfaces that with the exact reinstall command
    rather than replacing the binary in place: the update may be running *from*
    the stale tool, and ``uv tool install --force --reinstall`` rebuilds native
    extensions (minutes) and overwrites the files backing the running process, so
    a self-reinstall mid-run is risky - worse under ``--auto`` (unattended,
    systemd oneshot), where a failed reinstall could break the tool the update
    timer itself depends on. The operator runs the command when ready.

    Under ``--auto`` the warning is also written to the updater's status log (the
    audit trail an operator already watches). Best-effort: any failure while
    checking must never fail an otherwise-healthy update, so it is swallowed.
    """
    try:
        result = cli_drift.detect(os.environ["MASCOPE_PATH"])
    except OSError as exc:
        runtime.logger.warning(f"Skipped CLI staleness check (non-fatal): {exc}")
        return
    if result is None or not result.drifted:
        return

    message = (
        "The installed 'mascope' CLI is OLDER than this checkout - 'prod update' "
        "refreshed the images but not the CLI itself. New CLI commands and fixes "
        "in the checkout take effect only after you reinstall the tool:\n\n"
        f"    {result.reinstall}\n"
    )
    runtime.logger.warning(message)
    if auto:
        auto_update.record_status(
            os.environ["MASCOPE_PATH"],
            "Installed CLI is stale after update - reinstall the tool: "
            f"{result.reinstall}",
        )


# --- Commands ---


@prod_app.command()
def up(
    rebuild: Annotated[
        bool,
        typer.Option("--build", help="Build images before starting containers."),
    ] = False,
    detach: Annotated[
        bool,
        typer.Option("--detach", "-d", help="Stream container logs after starting."),
    ] = False,
) -> None:
    """
    Start production containers.

    Streams logs to terminal by default (foreground). Pass --detach to run
    in the background and return the terminal immediately.

    \b
    Examples:
        mascope prod up
        mascope prod up --build
        mascope prod up --detach
        mascope prod up --build --detach
    """
    # Check database bind-mount dirs before starting containers
    check_data_dirs(_MODE)
    args = ["up"]
    if rebuild:
        args.append("--build")
    if detach:
        args.append("--detach")
    # --build compiles the current HEAD (branch-derived version); a plain `up`
    # runs the deploy version (pin, release tag, or latest).
    _run_compose(args, building=rebuild)


@prod_app.command()
def down() -> None:
    """
    Stop and remove production containers.

    Runs `docker compose down`. Does not remove volumes or images.

    \b
    Examples:
        mascope prod down
    """
    _run_compose(["down"])


@prod_app.command()
def ps() -> None:
    """
    Show production container status.

    Runs `docker compose ps`.

    \b
    Examples:
        mascope prod ps
    """
    _run_compose(["ps"])


@prod_app.command()
def build() -> None:
    """
    Build production container images.

    Runs `docker compose build`. Use `mascope prod up --build` to build
    and start in one step.

    \b
    Examples:
        mascope prod build
    """
    _run_compose(["build"], building=True)


@prod_app.command()
def manifest(
    version: Annotated[
        Optional[str],
        typer.Option(
            "--version",
            help="Version to record in the manifest. Defaults to the resolved "
            "deploy version.",
        ),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output",
            "-o",
            help="Write the manifest JSON to this file instead of stdout.",
        ),
    ] = None,
) -> None:
    """
    Generate the release manifest for this checkout.

    Records the Alembic head baked into the current source tree so a deployed
    stack can classify a pending update (`mascope prod update --check
    --manifest ...`) without inspecting the release image. Intended to run at
    release build time; the produced JSON is published alongside the release
    images (e.g. as a GitHub Release asset).

    \b
    Examples:
        mascope prod manifest
        mascope prod manifest --version v1.3.0 --output mascope-manifest.json
    """
    app_version = version or _deploy_version()
    # The Alembic head is read from the checkout being released - the same
    # source-vs-runtime-home distinction `mascope dev migrate` makes. In a
    # release build and on a deployed server these coincide; from a worktree
    # they do not, and MASCOPE_PATH would stamp the wrong revision.
    try:
        data = release_manifest.build_manifest(app_version, backend_path())
    except release_manifest.ManifestError as e:
        runtime.logger.error(str(e))
        raise typer.Exit(1)

    text = release_manifest.write_manifest(data, output)
    if output is not None:
        runtime.logger.success(f"Wrote manifest for '{app_version}' to {output}")
    else:
        typer.echo(text)


def _report_plan(plan: preflight.UpdatePlan) -> None:
    """Print a human-readable summary of an update preflight classification."""
    runtime.logger.info(f"Target release:   {plan.target}")
    runtime.logger.info(f"Target revision:  {plan.target_revision}")
    runtime.logger.info(
        f"Current revision: {plan.current_revision or '(none - not yet migrated)'}"
    )
    if plan.classification == "up-to-date":
        runtime.logger.success("Up to date - no update available.")
    elif plan.classification == "fast-update":
        runtime.logger.warning(
            "Fast update available - new images, no database migration "
            "(near-zero downtime)."
        )
    else:  # migration-update
        runtime.logger.warning(
            "Migration update available - a database migration will run on "
            "startup; schedule a maintenance window."
        )


def _preflight(
    target: str, *, pull: bool, as_json: bool, manifest: Optional[Path] = None
) -> None:
    """
    Classify the pending update to ``target`` and exit with its code.

    Requires the Postgres container to be running, since the applied database
    revision is read from it. When a release ``manifest`` is given, the target
    Alembic head (and version label) come from it rather than from inspecting
    the image. Never returns normally — it always raises ``typer.Exit`` with the
    classification's exit code (or the error code when the update cannot be
    classified).
    """
    if not is_container_running(_MODE):
        runtime.logger.error(
            "Postgres container is not running - start the stack with "
            "'mascope prod up' before checking (the applied database revision "
            "is read from the running container)."
        )
        raise typer.Exit(preflight.ERROR_EXIT_CODE)

    target_head: Optional[str] = None
    if manifest is not None:
        try:
            data = release_manifest.load_manifest(manifest)
        except release_manifest.ManifestError as e:
            runtime.logger.error(str(e))
            raise typer.Exit(preflight.ERROR_EXIT_CODE)
        target = data["app_version"]
        target_head = data["alembic_head"]

    db_cfg = runtime.full_config.backend.database
    backend_cfg = runtime.full_config.backend
    frontend_cfg = runtime.full_config.frontend

    try:
        plan = preflight.build_plan(
            target=target,
            backend_image=f"{_BACKEND_IMAGE}:{target}",
            backend_container=backend_cfg.get_backend_container_name(mode=_MODE),
            frontend_image=f"{_FRONTEND_IMAGE}:{target}",
            frontend_container=frontend_cfg.get_frontend_container_name(mode=_MODE),
            pg_container=db_cfg.get_postgres_container_name(mode=_MODE),
            db_user=db_cfg.user,
            db_name=db_cfg.get_postgres_database_name(env_name=runtime.env.name),
            pull=pull,
            target_head=target_head,
        )
    except preflight.PreflightError as e:
        runtime.logger.error(str(e))
        raise typer.Exit(preflight.ERROR_EXIT_CODE)

    if as_json:
        typer.echo(json.dumps(plan.to_dict()))
    else:
        _report_plan(plan)
    raise typer.Exit(plan.exit_code)


def _apply_update(
    target: str, backend_container: str, mascope_path: str, *, kind: str
) -> None:
    """
    Apply an update to ``target`` and verify health.

    Pins the deploy to ``target``, restarts the stack, and waits for the
    backend to report healthy. For a migration update the db_init service takes
    the pre-migration dump and runs the migration on startup. On success clears
    the pending record; on failure it alerts and stops - it never rolls back
    automatically. Raises ``typer.Exit`` with the appropriate AUTO_* code.

    :param kind: "fast" or "migration" - for log/status wording only.
    """
    os.environ["MASCOPE_VERSION"] = target
    os.environ["_MASCOPE_VERSION_PINNED"] = "1"

    check_data_dirs(_MODE)
    runtime.logger.info(f"Applying {kind} update to '{target}'")
    _run_compose(["pull"])
    _run_compose(["up", "--detach"])

    if auto_update.wait_healthy(backend_container):
        auto_update.clear_pending(mascope_path)
        _prune_images()  # reclaim the superseded release's images
        message = f"{kind.capitalize()} update to {target} applied; backend healthy."
        runtime.logger.success(message)
        auto_update.record_status(mascope_path, message)
        # Keep the checkout naming the release that runs, so a reboot
        # between timer windows redeploys this release, not the previous one.
        _align_checkout(target, mascope_path)
        _run_compose(["ps"])
        # Images moved; the unattended tool cannot safely reinstall itself, so
        # record + log if the CLI now trails the checkout for the operator.
        _warn_if_cli_stale(auto=True)
        raise typer.Exit(auto_update.AUTO_OK)

    message = (
        f"{kind.capitalize()} update to {target} applied but the backend did not "
        "become healthy - manual intervention needed (no automatic rollback)."
    )
    runtime.logger.error(message)
    auto_update.record_status(mascope_path, message)
    raise typer.Exit(auto_update.AUTO_ERROR)


def _manage_pending(*, confirm: bool, snooze: Optional[int]) -> None:
    """
    Confirm or snooze the recorded pending migration update, then exit.

    Never returns normally - always raises ``typer.Exit``.
    """
    mascope_path = os.environ["MASCOPE_PATH"]

    if snooze is not None:
        if snooze <= 0:
            runtime.logger.error("--snooze days must be a positive integer.")
            raise typer.Exit(1)
        pending = auto_update.snooze_pending(mascope_path, snooze)
        if pending is None:
            runtime.logger.warning("No pending migration update to snooze.")
            raise typer.Exit(0)
        message = (
            f"Snoozed migration update {pending.version} until {pending.snooze_until}."
        )
        runtime.logger.success(message)
        auto_update.record_status(mascope_path, message)
        raise typer.Exit(0)

    pending = auto_update.confirm_pending(mascope_path)
    if pending is None:
        runtime.logger.warning("No pending migration update to confirm.")
        raise typer.Exit(0)
    message = (
        f"Confirmed migration update {pending.version}; it will apply at the next "
        "maintenance window."
    )
    runtime.logger.success(message)
    auto_update.record_status(mascope_path, message)
    raise typer.Exit(0)


def _auto(*, pull: bool) -> None:
    """
    Unattended update: resolve the newest release, classify it, and act.

    - up-to-date: nothing to do.
    - fast update: apply inside the maintenance window (health-checked); outside
      the window, do nothing and retry on the next tick.
    - migration update: record it as pending; apply it inside the window once
      the grace period elapses or it has been confirmed (and is not snoozed);
      otherwise notify and wait.

    Never returns normally - always raises ``typer.Exit`` with an AUTO_* code.
    """
    mascope_path = os.environ["MASCOPE_PATH"]

    try:
        window = auto_update.parse_window(os.environ.get("MASCOPE_UPDATE_WINDOW"))
    except ValueError as e:
        runtime.logger.error(str(e))
        raise typer.Exit(auto_update.AUTO_ERROR)

    if not is_container_running(_MODE):
        runtime.logger.error(
            "Postgres container is not running - the applied database revision "
            "is read from it. Start the stack with 'mascope prod up'."
        )
        raise typer.Exit(auto_update.AUTO_ERROR)

    repo = os.environ.get("MASCOPE_UPDATE_REPO", _DEFAULT_UPDATE_REPO)
    target = auto_update.latest_release_tag(repo)
    if target is None:
        runtime.logger.error(
            f"Could not determine the latest release of '{repo}' - check network "
            "and read access to the repository releases."
        )
        raise typer.Exit(auto_update.AUTO_ERROR)

    # Prefer the release manifest's Alembic head; fall back to image inspection.
    target_head: Optional[str] = None
    manifest_path = auto_update.download_manifest(
        repo, target, auto_update.update_dir(mascope_path) / "manifests"
    )
    if manifest_path is not None:
        try:
            target_head = release_manifest.load_manifest(manifest_path)["alembic_head"]
        except release_manifest.ManifestError as e:
            runtime.logger.warning(f"Ignoring invalid release manifest: {e}")

    db_cfg = runtime.full_config.backend.database
    backend_cfg = runtime.full_config.backend
    frontend_cfg = runtime.full_config.frontend
    backend_container = backend_cfg.get_backend_container_name(mode=_MODE)

    # Classifying the update pulls the target images; guard the disk first.
    _abort_if_low_disk(auto=True)

    try:
        plan = preflight.build_plan(
            target=target,
            backend_image=f"{_BACKEND_IMAGE}:{target}",
            backend_container=backend_container,
            frontend_image=f"{_FRONTEND_IMAGE}:{target}",
            frontend_container=frontend_cfg.get_frontend_container_name(mode=_MODE),
            pg_container=db_cfg.get_postgres_container_name(mode=_MODE),
            db_user=db_cfg.user,
            db_name=db_cfg.get_postgres_database_name(env_name=runtime.env.name),
            pull=pull,
            target_head=target_head,
        )
    except preflight.PreflightError as e:
        runtime.logger.error(str(e))
        raise typer.Exit(auto_update.AUTO_ERROR)

    if plan.classification == "up-to-date":
        auto_update.clear_pending(mascope_path)
        runtime.logger.success(f"Already up to date ({target}).")
        raise typer.Exit(auto_update.AUTO_OK)

    if plan.classification == "migration-update":
        pending = auto_update.record_pending(mascope_path, target, plan.target_revision)
        try:
            grace_days = int(os.environ.get("MASCOPE_UPDATE_GRACE_DAYS", "7"))
        except ValueError:
            runtime.logger.error(
                "MASCOPE_UPDATE_GRACE_DAYS must be an integer number of days."
            )
            raise typer.Exit(auto_update.AUTO_ERROR)

        if auto_update.should_apply_migration(
            pending, auto_update._now(), grace_days, window
        ):
            reason = (
                "confirmed" if pending.confirmed else f"grace of {grace_days}d elapsed"
            )
            runtime.logger.info(
                f"Applying migration update {target} ({reason}, in window)."
            )
            _apply_update(target, backend_container, mascope_path, kind="migration")

        message = (
            f"Migration update {target} available (first seen "
            f"{pending.first_seen_at}); not applied yet - waiting for the "
            "maintenance window, grace period, or an explicit confirm."
        )
        runtime.logger.warning(message)
        auto_update.record_status(mascope_path, message)
        raise typer.Exit(auto_update.AUTO_MIGRATION_PENDING)

    # fast-update
    if not auto_update.in_window(auto_update._now(), window):
        runtime.logger.info(
            f"Fast update {target} available; waiting for the maintenance window "
            f"({os.environ.get('MASCOPE_UPDATE_WINDOW')})."
        )
        raise typer.Exit(auto_update.AUTO_OK)

    _apply_update(target, backend_container, mascope_path, kind="fast")


@prod_app.command()
def update(
    version: Annotated[
        Optional[str],
        typer.Option(
            "--version",
            help="Release to update to: vX.Y.Z or 'latest'. Defaults to the "
            "MASCOPE_VERSION pin, or 'latest'.",
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Classify the pending update without applying it, then exit. "
            "Exit codes: 0 up-to-date, 10 fast update, 20 migration update, "
            "2 error.",
        ),
    ] = False,
    pull: Annotated[
        bool,
        typer.Option(
            "--pull/--no-pull",
            help="With --check, pull the target images before classifying. "
            "--no-pull compares only images already present locally.",
        ),
    ] = True,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="With --check, print the classification as JSON."),
    ] = False,
    manifest: Annotated[
        Optional[Path],
        typer.Option(
            "--manifest",
            help="With --check, read the target Alembic head from a release "
            "manifest file instead of inspecting the image.",
            exists=True,
            dir_okay=False,
        ),
    ] = None,
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            help="Unattended update (for a timer): resolve the latest release and "
            "apply a fast update inside the maintenance window "
            "(MASCOPE_UPDATE_WINDOW, e.g. '2-5'), or record and notify a "
            "migration update without applying it. Exit codes: 0 handled, "
            "30 migration pending, 2 error.",
        ),
    ] = False,
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm",
            help="Confirm the pending migration update so --auto applies it at the "
            "next maintenance window without waiting out the grace period.",
        ),
    ] = False,
    snooze: Annotated[
        Optional[int],
        typer.Option(
            "--snooze",
            help="Postpone the pending migration update by this many days, then exit.",
        ),
    ] = None,
) -> None:
    """
    Update the production stack to a newer release.

    Pulls the target release images and restarts the stack with them
    (`docker compose pull` followed by `up --detach`), then shows container
    status. Database migrations run automatically on startup — the db_init
    service takes a pre-migration dump into the backups directory first.
    Containers whose image did not change are left running, and a failed
    pull aborts before the running stack is touched.

    Pass --check to classify the pending update as up-to-date, a fast update
    (new images, no migration, near-zero downtime), or a migration update (a
    database migration will run and cause downtime) without applying anything.
    This is the signal to decide whether a maintenance window is needed.

    Pass --auto for an unattended, timer-driven update: it resolves the latest
    release itself (ignoring --version), applies fast updates inside the
    maintenance window, and only records + notifies migration updates.

    \b
    Examples:
        mascope prod update                        # follow the latest release
        mascope prod update --version v1.2.0       # move to a specific release
        MASCOPE_VERSION=v1.2.0 mascope prod update # same, via env pin
        mascope prod update --check                # classify without applying
        mascope prod update --check --json         # machine-readable preflight
        mascope prod update --auto                 # unattended (for a timer)
        mascope prod update --confirm              # approve the pending migration
        mascope prod update --snooze 7             # postpone it 7 days
    """
    if confirm or snooze is not None:
        # Standalone management of the recorded pending migration update.
        _manage_pending(confirm=confirm, snooze=snooze)

    if auto:
        # Standalone unattended path - resolves its own target and exits.
        _auto(pull=pull)

    if version is not None:
        if version != "latest" and not re.fullmatch(r"v\d+\.\d+\.\d+", version):
            runtime.logger.error(
                f"Invalid release '{version}' - expected vX.Y.Z or 'latest'. "
                "For other image tags, pin via the MASCOPE_VERSION env var."
            )
            raise typer.Exit(1)
        # Same effect as an env pin: _deploy_version honors it for both the
        # pull and the restart.
        os.environ["MASCOPE_VERSION"] = version
        os.environ["_MASCOPE_VERSION_PINNED"] = "1"

    target = _deploy_version()

    if check:
        # Never returns - raises typer.Exit with the classification's code.
        _preflight(target, pull=pull, as_json=as_json, manifest=manifest)

    check_data_dirs(_MODE)
    _abort_if_low_disk(auto=False)
    runtime.logger.info(f"Updating the production stack to '{target}'")
    _run_compose(["pull"])
    _run_compose(["up", "--detach"])
    _prune_images()  # reclaim the superseded release's images
    _run_compose(["ps"])
    runtime.logger.success(f"Production stack updated to '{target}'")
    # Keep the checkout naming the release that runs, so a reboot redeploys
    # this release and doctor stays clean (no-op when already checked out).
    _align_checkout(target, os.environ.get("MASCOPE_PATH"))
    # Last, so it is the final thing the operator sees: the images moved but the
    # CLI tool did not - warn if it now trails the checkout.
    _warn_if_cli_stale(auto=False)


@prod_app.command()
def logs(
    follow: Annotated[
        bool,
        typer.Option("--follow", "-f", help="Follow log output."),
    ] = False,
    tail: Annotated[
        int,
        typer.Option("--tail", "-n", help="Number of lines to show from end of logs."),
    ] = 100,
    service: Annotated[
        Optional[str],
        typer.Argument(help="Service name to filter logs (e.g. postgres, backend)."),
    ] = None,
) -> None:
    """
    Show production container logs.

    Runs `docker compose logs`. Optionally filter by service name and follow
    output in real time.

    \b
    Examples:
        mascope prod logs
        mascope prod logs --follow
        mascope prod logs --follow backend
        mascope prod logs --tail 50 postgres
    """
    args = ["logs", "--tail", str(tail)]
    if follow:
        args.append("--follow")
    if service:
        args.append(service)
    _run_compose(args)


@prod_app.command()
def restart(
    service: Annotated[
        Optional[str],
        typer.Argument(
            help="Service name to restart. Restarts all services if omitted."
        ),
    ] = None,
) -> None:
    """
    Restart production containers.

    Runs `docker compose restart`, optionally scoped to a single service.

    \b
    Examples:
        mascope prod restart
        mascope prod restart postgres
        mascope prod restart backend
    """
    args = ["restart"]
    if service:
        args.append(service)
    _run_compose(args)


@prod_app.command()
def doctor(
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit the report as JSON instead of text."),
    ] = False,
) -> None:
    """
    One-glance operational status of the deployment.

    Gathers container health, the deployed version, free disk on the state and
    docker filesystems, the recorded pending update, local backup freshness,
    and the docker image footprint. Read-only and network-free - safe to run
    anytime or to poll.

    Exits 0 when everything looks healthy, 1 when a container is not running,
    the running images do not match the release this deployment would deploy,
    or a filesystem is below the free-space floor (MASCOPE_UPDATE_MIN_FREE_GB),
    so it can double as a monitoring probe.

    \b
    Examples:
        mascope prod doctor
        mascope prod doctor --json
    """
    db_cfg = runtime.full_config.backend.database
    backend_cfg = runtime.full_config.backend
    file_converter_cfg = runtime.full_config.file_converter
    frontend_cfg = runtime.full_config.frontend
    redis_cfg = runtime.full_config.backend.redis

    mascope_path = os.environ["MASCOPE_PATH"]
    container_specs = [
        ("backend", backend_cfg.get_backend_container_name(mode=_MODE)),
        ("frontend", frontend_cfg.get_frontend_container_name(mode=_MODE)),
        ("postgres", db_cfg.get_postgres_container_name(mode=_MODE)),
        ("redis", redis_cfg.get_redis_container_name(mode=_MODE)),
        (
            "file_converter",
            file_converter_cfg.get_file_converter_container_name(mode=_MODE),
        ),
    ]
    # Only the containers built from a Mascope image carry the release tag;
    # postgres and redis are upstream images on their own versions.
    versioned_specs = [
        (label, name)
        for label, name in container_specs
        if label in ("backend", "frontend", "file_converter")
    ]
    disk_specs = [
        ("state", Path(mascope_path) / ".runtime"),
        ("docker", auto_update.docker_root()),
    ]

    report = prod_doctor.build_report(
        mascope_path=mascope_path,
        container_specs=container_specs,
        versioned_specs=versioned_specs,
        expected_version=_deploy_version(),
        disk_specs=disk_specs,
        backups_dir=db_cfg.get_backups_dir(mode=_MODE),
        min_free_gb=auto_update.min_free_gb(),
    )

    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        typer.echo(prod_doctor.format_text(report))
    raise typer.Exit(0 if report.ok else 1)


@prod_app.command(
    name="docker",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def docker_passthrough(ctx: typer.Context) -> None:
    """
    Pass arbitrary arguments directly to docker compose.

    Escape hatch for compose operations not covered by the explicit subcommands.
    All arguments after `docker` are forwarded verbatim to
    `docker compose --file <compose_path>`, with production environment
    variables injected.

    \b
    Examples:
        mascope prod docker exec -it postgres bash
        mascope prod docker pull
        mascope prod docker config
        mascope prod docker top backend
    """
    if not ctx.args:
        runtime.logger.error(
            "No arguments provided — usage: mascope prod docker <compose-args>"
        )
        raise typer.Exit(1)
    _run_compose(ctx.args)
