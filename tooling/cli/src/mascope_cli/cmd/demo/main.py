"""
`mascope demo` - one-command local demo on a dedicated `demo` runtime env.

Brings up the dev stack, seeds the demo env from a published bundle (instant
database snapshot) or rebuilds it from raw through the real pipeline, then
launches the app. Always operates on the `demo` env in `dev` mode so it never
disturbs a developer's working environment.

See `docs/demo_dataset.md` for the full design.
"""

import os
from pathlib import Path
from typing import Annotated, Optional

import typer

from mascope_cli.cmd.demo import _fetch, _rebuild, _seed, bundles
from mascope_cli.cmd.demo import verify as verify_mod
from mascope_cli.cmd.demo._seed import DEMO_ENV, env_dir
from mascope_cli.cmd.env._create import create_env_local
from mascope_cli.cmd.env._paths import env_exists_local
from mascope_cli.runtime import runtime


_MODE = "dev"

# Standard runtime-env subdirectories the app expects to exist (relative paths
# in base.mascope.toml: filestore, file-converter source, temp, logs). The
# backend's startup (e.g. filestore GC) lists these, so they must exist.
_ENV_SUBDIRS = ("filestore", "filestreams", "temp", "logs", "agents")

demo_app = typer.Typer()


def _ensure_demo_env() -> None:
    """
    Create the `demo` runtime env and its standard subdirectories.

    ``create_env_local`` only makes the top-level env folder; the app expects
    the filestore/filestreams/temp/logs subdirs to exist on startup. Creating
    them here (idempotently) covers both a brand-new env and one left partial by
    an earlier interrupted run.
    """
    if not env_exists_local(DEMO_ENV):
        create_env_local(DEMO_ENV)
    for sub in _ENV_SUBDIRS:
        (env_dir() / sub).mkdir(parents=True, exist_ok=True)


@demo_app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help=(
                "Ingest from raw through the real pipeline instead of "
                "restoring the snapshot"
            ),
        ),
    ] = False,
    no_launch: Annotated[
        bool,
        typer.Option("--no-launch", help="Seed the demo env but do not start the app"),
    ] = False,
    bundle_version: Annotated[
        Optional[str],
        typer.Option("--bundle", "-b", help="Demo bundle version (default: latest)"),
    ] = None,
    force_fetch: Annotated[
        bool,
        typer.Option("--force-fetch", help="Re-download the bundle even if cached"),
    ] = False,
    local: Annotated[
        Optional[Path],
        typer.Option(
            "--local",
            help=(
                "Use a local bundle directory instead of fetching a published "
                "bundle. For --rebuild it needs a raw/ subdir; for the snapshot "
                "path it needs manifest.json + snapshot/."
            ),
        ),
    ] = None,
    fresh: Annotated[
        bool,
        typer.Option(
            "--fresh",
            help=(
                "Bring up a clean, empty demo env (no bundle) for authoring "
                "reference data, then capturing it with 'mascope demo snapshot "
                "--seed'. Offers to reset the demo database first."
            ),
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the --fresh database reset prompt."),
    ] = False,
) -> None:
    """
    Run the local Mascope demo.

    With no subcommand, fetches the demo bundle (if needed), seeds the `demo`
    env, and launches the app. Use `--rebuild` to exercise the full ingestion
    pipeline from the raw files, or `--fresh` to author reference data from
    scratch (no bundle).

    \b
    Examples:
        mascope demo  # fetch snapshot + launch
        mascope demo --fresh  # clean empty env to author reference data
        mascope demo --rebuild  # ingest from raw through the pipeline
        mascope demo --rebuild --local path/to/bundle  # rebuild from a local bundle
        mascope demo --no-launch  # seed only, do not launch
        mascope demo fetch  # download + checksum-verify the bundle
    """
    # Pin the demo env + dev mode for every demo subcommand. MASCOPE_ENV is
    # checked first by the runtime's env resolution, so setting it here (before
    # reload_config) makes the whole process AND every launched subprocess
    # resolve the demo env consistently - no `mascope env use demo` needed.
    _ensure_demo_env()
    os.environ["MASCOPE_ENV"] = DEMO_ENV
    runtime.state.override("env", DEMO_ENV)
    runtime.state.override("mode", _MODE)
    # RuntimeEnv.name is captured once at Runtime construction (import time), so
    # the CLI singleton still holds the import-time env ("default"). Refresh it
    # so config paths resolve to the demo env AND so `_run_application` exports
    # MASCOPE_ENV=demo (it sets it from `runtime.env.name`) to the launched
    # backend/frontend/file-converter subprocesses.
    runtime.env.name = DEMO_ENV
    runtime.reload_config()

    if ctx.invoked_subcommand is not None:
        return

    _run(
        rebuild=rebuild,
        no_launch=no_launch,
        version=bundle_version,
        force=force_fetch,
        local=local,
        fresh=fresh,
        yes=yes,
    )


def _run(
    rebuild: bool,
    no_launch: bool,
    version: Optional[str],
    force: bool,
    local: Optional[Path] = None,
    fresh: bool = False,
    yes: bool = False,
) -> None:
    """Bring up the stack, seed (or rebuild) the demo env, and launch the app."""
    # MASCOPE_ENV=demo is already set by the callback so the backend import below
    # and every launched subprocess target the mascope_demo database.

    # Imported lazily to avoid pulling the dev module graph for `fetch`/`verify`.
    from mascope_cli.cmd.dev.db import create_database, wait_for_server
    from mascope_cli.cmd.dev.docker import check_and_start_docker
    from mascope_cli.cmd.dev.main import (
        _resolve_modules,
        _run_application,
        _run_dev_compose,
    )
    from mascope_cli.cmd.dev.migrate import (
        check_pending_migrations,
        run_migrations,
    )
    from mascope_cli.cmd.dev.redis import wait_for_redis
    from mascope_cli.pg.utils import check_data_dirs

    # --- bundle present + verified (not needed for a fresh authoring env) ---
    if fresh:
        pass
    elif local is not None:
        local = local.expanduser().resolve()
        if (
            not (local / "raw").is_dir()
            and not (local / bundles.MANIFEST_NAME).is_file()
        ):
            runtime.logger.error(
                f"--local {local} has neither a raw/ directory "
                f"nor {bundles.MANIFEST_NAME}"
            )
            raise typer.Exit(1)
        runtime.logger.info(f"Using local bundle directory: {local}")
    else:
        try:
            _fetch.fetch(version, force=force)
        except (ValueError, RuntimeError, OSError) as e:
            # Ordinary download/verification failures get a clean one-liner,
            # not a traceback (matching the sibling dev/prod flows).
            runtime.logger.error(str(e))
            raise typer.Exit(1)

    # --- secrets ---
    _ensure_dev_secrets()

    # --- infrastructure ---
    check_and_start_docker()
    check_data_dirs(_MODE)
    _run_dev_compose(["up", "-d"])

    if not wait_for_redis(max_wait=30):
        runtime.logger.error("Redis failed to start")
        raise typer.Exit(1)
    if not wait_for_server(max_wait=30):
        runtime.logger.error("PostgreSQL failed to start")
        raise typer.Exit(1)

    # --- database ---
    if not create_database(DEMO_ENV):
        runtime.logger.error("Failed to create demo database")
        raise typer.Exit(1)

    if fresh:
        # Clean, empty env for authoring reference data - no bundle, no ingestion.
        _reset_demo_database(yes=yes)
        # The DB was just emptied, so apply the schema directly. Skipping the
        # pending-migration check avoids a confusing (harmless) "alembic_version
        # does not exist" log on the empty database.
        if not run_migrations():
            runtime.logger.error("Failed to apply migrations")
            raise typer.Exit(1)
    elif rebuild:
        # Restore the reference seed (ionization modes, instrument config,
        # calibration/diagnostic collections, demo user) so the pipeline can
        # calibrate + match, then ingest raw via the real upload endpoint.
        # Uploads are deferred to a background thread that waits for the backend.
        if _seed.has_block("seed", version, source_dir=local):
            try:
                _seed.restore_seed(version, source_dir=local)
            except (RuntimeError, OSError) as e:
                runtime.logger.error(str(e))
                raise typer.Exit(1)
        else:
            runtime.logger.warning(
                "No reference seed in bundle - rebuilding from an empty database. "
                "Without ionization modes + a calibration collection, processing "
                "skips calibration/matching. Build a seed with "
                "'mascope demo snapshot --seed' for a complete rebuild."
            )
        if check_pending_migrations() and not run_migrations():
            runtime.logger.error("Failed to apply migrations")
            raise typer.Exit(1)
        _rebuild.upload_raw_deferred(version, source_dir=local)
    else:
        # Restore snapshot, then upgrade to head in case the snapshot predates
        # the current schema.
        try:
            _seed.restore_snapshot(version, source_dir=local)
            _seed.restore_filestore(version, source_dir=local)
        except (RuntimeError, OSError) as e:
            runtime.logger.error(str(e))
            raise typer.Exit(1)
        if check_pending_migrations() and not run_migrations():
            runtime.logger.error("Failed to apply migrations after restore")
            raise typer.Exit(1)

    _seed_credentials()
    _seed_reference_demo()
    _print_access()

    if no_launch:
        runtime.logger.success("Demo env seeded. Start it later with 'mascope demo'.")
        return

    modules = _resolve_modules(["backend", "frontend", "file-converter"])
    _run_application(modules=modules)


def _reset_demo_database(yes: bool) -> None:
    """
    Offer to drop + recreate the demo database for a clean authoring env.

    A fresh, empty demo database is what `mascope demo snapshot --seed` should
    capture (reference data only, no leftover samples). If the database does not
    exist yet it is already clean and nothing happens. Otherwise the user is
    prompted before the destructive reset (skip with ``--yes``).

    :param yes: Skip the confirmation prompt and reset unconditionally.
    """
    from mascope_cli.cmd.dev.db import create_database
    from mascope_cli.pg import drop_database, is_database_ready

    db_cfg = runtime.full_config.backend.database
    container = db_cfg.get_postgres_container_name(mode=_MODE)
    database = db_cfg.get_postgres_database_name(DEMO_ENV)

    if not is_database_ready(_MODE, DEMO_ENV):
        return  # does not exist yet - already a clean slate

    if not (
        yes
        or typer.confirm(
            f"Reset demo database '{database}'? This drops all current demo data "
            "so you can author reference data from a clean slate.",
            default=True,
        )
    ):
        runtime.logger.info("Keeping existing demo database contents.")
        return

    runtime.logger.info(f"Resetting '{database}'...")
    drop_database(container, db_cfg.user, database)
    if not create_database(DEMO_ENV):
        runtime.logger.error(f"Failed to recreate '{database}'")
        raise typer.Exit(1)


def _ensure_dev_secrets() -> None:
    """
    Create the dev secrets the backend reads, if missing.

    Removes setup steps for newcomers. The dev stack reads these arbitrary-string
    secrets at startup/import:
    - ``postgres_password.txt`` - dev PostgreSQL password,
    - ``jwt_secret_key.txt`` - JWT signing key,
    - ``server_owner_secret_key.txt`` - first-owner registration key,
    - ``mfa_encryption_key.txt`` - encrypts stored TOTP seeds.

    Each is written with a random value only when absent, so existing secrets are
    never clobbered. The SSL cert secrets are not created - they are only needed
    for prod-mode nginx, not the dev demo. If the dev postgres data directory was
    already initialized with a different password, the user must reset it.
    """
    import secrets as _secrets

    secrets_dir = Path(runtime.path(".runtime", "secrets"))
    secrets_dir.mkdir(parents=True, exist_ok=True)

    for filename in (
        "postgres_password.txt",
        "jwt_secret_key.txt",
        "server_owner_secret_key.txt",
        "mfa_encryption_key.txt",
    ):
        path = secrets_dir / filename
        if path.exists():
            continue
        # token_hex is alphanumeric - safe for the postgres connection URL.
        path.write_text(_secrets.token_hex(32), encoding="utf-8")
        runtime.logger.warning(f"Created missing dev secret: {path}")

    runtime.logger.info(
        "If the dev postgres was previously initialized with a different "
        "password, stop it and delete .runtime/database/dev, then re-run."
    )


def _seed_credentials() -> None:
    """Seed the fixed demo user + SDK token into the demo database in-process."""
    import asyncio
    import os

    runtime.logger.info("Seeding demo credentials...")
    # Explicit opt-in required by the seed as a production safeguard; this is
    # the local demo flow, so enable it.
    os.environ["MASCOPE_ALLOW_DEMO_SEED"] = "1"
    try:
        # Lazy import: pulls the backend graph (needs MASCOPE_ENV set above and
        # the postgres secret, both present in the demo flow).
        from mascope_backend.db.scripts.seed_demo import seed_demo

        asyncio.run(seed_demo())
    except Exception as e:  # noqa: BLE001 - surface any seed failure to the user
        runtime.logger.error(f"Failed to seed demo credentials: {e}")
        raise typer.Exit(1)


def _seed_reference_demo() -> None:
    """Seed the small illustrative reference-compound set into the demo DB.

    Makes the peak-assignment identity annotation visible out of the box. Best
    effort: a failure here only warns, since the demo is fully usable without
    reference data.
    """
    import asyncio

    runtime.logger.info("Seeding demo reference compounds...")
    try:
        from mascope_backend.db.scripts.seed_reference_demo import seed_reference_demo

        asyncio.run(seed_reference_demo())
    except Exception as e:  # noqa: BLE001 - reference seed is optional for the demo
        runtime.logger.warning(f"Skipped demo reference seed: {e}")


def _print_access() -> None:
    """Log how to reach the running demo (URL + fixed demo credentials)."""
    # Mirrors the constants in
    # server/backend/src/mascope_backend/db/scripts/seed_demo.py.
    port = runtime.meta.api_port
    runtime.logger.success("Demo ready.")
    runtime.logger.info(f"  Web UI:    http://localhost:{port}")
    runtime.logger.info("  Login:     demo@mascope.app  /  mascope-demo")
    runtime.logger.info("  SDK token: mascope_demo_sdk_token  (MASCOPE_ACCESS_TOKEN)")


@demo_app.command()
def fetch(
    bundle_version: Annotated[
        Optional[str],
        typer.Option("--bundle", "-b", help="Demo bundle version (default: latest)"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-download even if a valid copy is cached"),
    ] = False,
) -> None:
    """
    Download and checksum-verify the demo bundle into the local cache.

    Idempotent: a cached, intact bundle is left untouched unless `--force`.
    """
    _fetch.fetch(bundle_version, force=force)


@demo_app.command()
def verify(
    actual: Annotated[
        Optional[Path],
        typer.Option(
            "--actual",
            help="Parquet of peaks to compare instead of the live demo database",
        ),
    ] = None,
    local: Annotated[
        Optional[Path],
        typer.Option(
            "--local",
            help="Verify against a local bundle directory instead of the cached one",
        ),
    ] = None,
    bundle_version: Annotated[
        Optional[str],
        typer.Option("--bundle", "-b", help="Demo bundle version (default: latest)"),
    ] = None,
) -> None:
    """
    Compare produced peaks against the bundle's golden outputs.

    The goldens come from the cached/registered bundle, or a local bundle
    directory with `--local <dir>` (e.g. while authoring before publishing).

    The 'actual' peaks default to a live export from the current demo database
    (run after `mascope demo --rebuild`); pass `--actual <parquet>` to compare a
    pre-exported file instead.
    """
    import json

    import pandas as pd

    # Resolve the goldens (manifest + expected/peaks.parquet) from a local
    # bundle dir (pre-publish) or the cached/registered bundle.
    if local is not None:
        local = local.expanduser().resolve()
        manifest = json.loads(
            (local / bundles.MANIFEST_NAME).read_text(encoding="utf-8")
        )
        bundle_root = local
    else:
        manifest = bundles.load_manifest(bundle_version)
        bundle_root = bundles.bundle_dir(bundle_version)

    expected_rel = manifest.get("expected", {}).get("peaks")
    if not expected_rel:
        runtime.logger.error("Bundle manifest has no expected/peaks goldens")
        raise typer.Exit(1)
    expected_path = bundle_root / expected_rel

    # Resolve the 'actual' peaks: an explicit parquet, or a live export from the
    # current (rebuilt) demo database via the shared golden-export query.
    if actual is not None:
        actual_df = pd.read_parquet(actual)
    else:
        from mascope_backend.db.scripts.export_goldens import get_golden_peaks

        rows = get_golden_peaks()
        if not rows:
            runtime.logger.error(
                "No matched peaks in the demo database to verify. "
                "Run 'mascope demo --rebuild' and confirm matching first."
            )
            raise typer.Exit(2)
        actual_df = pd.DataFrame(rows)

    problems = verify_mod.compare_peaks(
        expected=pd.read_parquet(expected_path),
        actual=actual_df,
        tolerances=manifest.get("tolerances"),
    )
    if problems:
        runtime.logger.error(f"Reproducibility check FAILED ({len(problems)} diffs):")
        for p in problems:
            runtime.logger.error(f"  - {p}")
        raise typer.Exit(1)
    runtime.logger.success("Reproducibility check passed - outputs match goldens")


@demo_app.command()
def snapshot(
    out: Annotated[
        Path,
        typer.Option("--out", help="Output bundle directory to (re)build"),
    ],
    raw: Annotated[
        Optional[Path],
        typer.Option(
            "--raw",
            help=(
                "Directory of de-identified raw files (source of truth). Required "
                "the first time; omit on later refreshes to reuse the raw files "
                "already in the bundle (skips re-copying + re-hashing them)."
            ),
        ),
    ] = None,
    seed: Annotated[
        bool,
        typer.Option(
            "--seed",
            help=(
                "Capture the reference seed dump (ionization modes, instrument "
                "config, calibration/diagnostic collections) from the demo DB - "
                "run BEFORE ingesting raw. Restored by 'mascope demo --rebuild'."
            ),
        ),
    ] = False,
    update: Annotated[
        bool,
        typer.Option(
            "--update",
            help=(
                "Capture the full snapshot/ (+ filestore) and expected/ goldens "
                "from the demo DB - run AFTER ingestion + matching. Restored by "
                "the instant 'mascope demo' path."
            ),
        ),
    ] = False,
) -> None:
    """
    MAINTAINER: (re)build a demo bundle from the raw source of truth.

    Writes manifest.json + the de-identification report, and (with --seed /
    --update) the derived database dumps. The two dumps are captured at different
    points in the authoring workflow and accumulate in the manifest. Pass --raw
    once to create the bundle; omit it afterwards and the existing raw files are
    reused (no re-copy/re-hash):

    \b
        # 1. first build - copy raw + author reference data, then capture seed:
        mascope demo snapshot --raw <dir> --out <bundle> --seed
        # 2. ingest raw + run matching, then refresh the snapshot (raw reused):
        mascope demo snapshot --out <bundle> --update

    See docs/demo_dataset.md for the full publish workflow.
    """
    from mascope_cli.cmd.demo import build_bundle

    try:
        build_bundle.build(out_dir=out, raw_dir=raw, update=update, seed=seed)
    except (RuntimeError, OSError) as e:
        # A rejected coverage check is a routine authoring outcome with its own
        # remediation text, and a missing --raw is a usage error; neither is
        # worth a traceback (matching the sibling flows above).
        runtime.logger.error(str(e))
        raise typer.Exit(1)


@demo_app.command()
def info(
    bundle_version: Annotated[
        Optional[str],
        typer.Option("--bundle", "-b", help="Demo bundle version (default: latest)"),
    ] = None,
) -> None:
    """Show the registered + cached status of a demo bundle."""
    bundle = bundles.get_bundle(bundle_version)
    runtime.logger.info(f"Bundle:   {bundle.version}")
    runtime.logger.info(f"URL:      {bundle.url or '(not published yet)'}")
    runtime.logger.info(f"DOI:      {bundle.doi or '(none)'}")
    runtime.logger.info(f"Cached:   {bundles.is_cached(bundle_version)}")
    if bundles.is_cached(bundle_version):
        problems = bundles.verify_manifest(bundle_version)
        status = "intact" if not problems else f"{len(problems)} issue(s)"
        runtime.logger.info(f"Integrity: {status}")
