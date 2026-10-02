"""
Seed the demo runtime env from a cached bundle's database snapshot.

This is the "instant" path: restore the pre-built ``pg_dump`` into the
``mascope_demo`` database and copy the bundle's filestore tree into the demo
env so the backend can serve the stored files. No instrument pipeline runs.

The slower ``--rebuild`` path (ingest raw through the real file-converter) lives
in ``_rebuild.py`` and is what the reproducibility test drives.
"""

import json
import os
import shutil
from pathlib import Path

from mascope_cli.cmd.demo import bundles
from mascope_cli.pg import dirs, drop_database, pg_restore
from mascope_cli.runtime import DEPLOYMENT_FILE, runtime


# The demo always uses a dedicated env so it never touches a developer's work.
DEMO_ENV = "demo"
_MODE = "dev"


def env_dir(env: str = DEMO_ENV) -> Path:
    """Return the runtime directory for an env (may not exist yet)."""
    return Path(os.environ["MASCOPE_PATH"]) / ".runtime" / "env" / env


def copy_filestore(src: Path, dest: Path) -> None:
    """
    Copy a filestore tree into a bundle or out of one, without its deployment id.

    The deployment file (``deployment.json``) names the deployment whose
    backend generated it. A bundle carries data from the deployment that built
    it to every stack seeded from it, so the file stays where it was: copied
    in, every demo stack would export under the builder's name.

    :param src: The filestore to copy.
    :param dest: Where to copy it; must not exist yet.
    """
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(DEPLOYMENT_FILE))


def _resolve(version: str | None, source_dir: "Path | None") -> tuple[Path, dict]:
    """Resolve the bundle root and load its manifest (published cache or local)."""
    if source_dir:
        root = Path(source_dir)
        manifest_path = root / bundles.MANIFEST_NAME
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"No {bundles.MANIFEST_NAME} in {root}. A snapshot restore needs a "
                "built bundle; for a raw-only local directory use 'mascope demo "
                "--rebuild --local <dir>'."
            )
        return root, json.loads(manifest_path.read_text(encoding="utf-8"))
    return bundles.bundle_dir(version), bundles.load_manifest(version)


def has_block(
    block: str, version: str | None = None, source_dir: "Path | None" = None
) -> bool:
    """
    Report whether a manifest block (``"seed"`` or ``"snapshot"``) is available.

    Returns ``False`` (rather than raising) when there is no manifest - e.g. a
    raw-only ``--local`` directory - so callers can fall back gracefully.

    :param block: Manifest key, ``"seed"`` or ``"snapshot"``.
    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override.
    :return: ``True`` if the block's dump exists on disk.
    """
    try:
        root, manifest = _resolve(version, source_dir)
    except FileNotFoundError:
        return False
    dump_rel = manifest.get(block, {}).get("dump")
    return bool(dump_rel) and (root / dump_rel).is_file()


def _restore_block(
    block: str, version: str | None = None, source_dir: "Path | None" = None
) -> None:
    """
    Restore a manifest dump block into the ``mascope_demo`` database.

    Stages the dump into the dev backups directory (bind-mounted into the
    PostgreSQL container), then drops/recreates the demo database and restores
    into it. Assumes the dev PostgreSQL container is running and the demo env
    exists.

    :param block: Manifest key, ``"seed"`` (reference data) or ``"snapshot"``
                  (full state).
    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory override.
    :raises FileNotFoundError: If the bundle or the block's dump is missing.
    :raises RuntimeError: If the restore fails.
    """
    # Lazy import: keeps `mascope demo fetch` from pulling the dev.db graph.
    from mascope_cli.cmd.dev.db import create_database

    root, manifest = _resolve(version, source_dir)
    dump_rel = manifest.get(block, {}).get("dump")
    if not dump_rel:
        raise FileNotFoundError(
            f"Bundle manifest has no '{block}' dump. Build it with "
            f"'mascope demo snapshot --{block if block == 'seed' else 'update'}'."
        )

    src_dump = root / dump_rel
    if not src_dump.is_file():
        raise FileNotFoundError(f"{block} dump not found: {src_dump}")

    db_cfg = runtime.full_config.backend.database
    container = db_cfg.get_postgres_container_name(mode=_MODE)
    database = db_cfg.get_postgres_database_name(DEMO_ENV)

    # Stage the dump where pg_restore expects it (bind-mounted backups dir).
    backups_dir, mount = dirs(transfer=False, mode=_MODE)
    backups_dir.mkdir(parents=True, exist_ok=True)
    staged = backups_dir / src_dump.name
    shutil.copy2(src_dump, staged)

    try:
        runtime.logger.info(f"Dropping and recreating '{database}'...")
        drop_database(container, db_cfg.user, database)
        if not create_database(DEMO_ENV):
            raise RuntimeError(f"Failed to recreate database '{database}'")

        runtime.logger.info(f"Restoring {block} into '{database}'...")
        pg_restore(container, db_cfg.user, database, staged, mount)
    finally:
        staged.unlink(missing_ok=True)

    runtime.logger.success(f"Demo database '{database}' restored from {block}")


def restore_snapshot(
    version: str | None = None, source_dir: "Path | None" = None
) -> None:
    """Restore the full ``snapshot`` dump (instant path). See :func:`_restore_block`."""
    _restore_block("snapshot", version, source_dir)


def restore_seed(version: str | None = None, source_dir: "Path | None" = None) -> None:
    """
    Restore the reference ``seed`` dump (ionization modes, instrument config,
    calibration/diagnostic collections, demo user) before a rebuild ingests raw.
    See :func:`_restore_block`.
    """
    _restore_block("seed", version, source_dir)


def restore_filestore(
    version: str | None = None, source_dir: "Path | None" = None
) -> None:
    """
    Copy the bundle's filestore tree into the demo env's filestore directory.

    Mirrors ``snapshot/filestore/`` from the bundle into
    ``.runtime/env/demo/filestore/`` so the restored database rows resolve to
    real files on disk. Existing demo filestore contents are replaced, except
    the env's own deployment id, which a re-seed keeps (see
    :func:`copy_filestore`).

    :param version: Bundle version tag. Defaults to the registry default.
    :param source_dir: Local bundle directory to use instead of the published cache.
    """
    root, manifest = _resolve(version, source_dir)
    snapshot = manifest.get("snapshot", {})
    filestore_rel = snapshot.get("filestore", "snapshot/filestore")
    src = root / filestore_rel

    if not src.is_dir():
        runtime.logger.warning(
            f"Bundle has no filestore tree at {src}; skipping filestore restore"
        )
        return

    dest = env_dir() / "filestore"
    own_id = dest / DEPLOYMENT_FILE
    kept = own_id.read_bytes() if own_id.is_file() else None
    if dest.exists():
        shutil.rmtree(dest)
    copy_filestore(src, dest)
    if kept is not None:
        own_id.write_bytes(kept)
    runtime.logger.success(f"Demo filestore restored to {dest}")
