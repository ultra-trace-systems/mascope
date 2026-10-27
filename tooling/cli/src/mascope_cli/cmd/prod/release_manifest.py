"""
Release manifest generation and loading.

A release manifest is a small JSON document published alongside a release's
container images. It records the Alembic head baked into that release, so the
update preflight (and, later, an automated updater) can tell whether moving to
the release will run a database migration - the downtime-bearing case - without
having to spawn a throwaway container to read the head out of the image.

The manifest is deliberately minimal and forward-compatible: consumers check
``schema_version`` and read the fields they understand. Generation reads the
Alembic script directory only (no database, no Docker), so it runs in CI at
build time where the checkout is the source of truth for the head.
"""

import datetime
import json
from pathlib import Path
from typing import Optional


# NOTE: alembic is imported lazily inside read_alembic_head(), not at module
# level. Only a source checkout installs it (the root pyproject.toml), while this
# module is pulled in by the always-loaded `prod` command group. A top-level
# import would break `import mascope_cli` on a standalone operator install that
# has no alembic. Only manifest *generation* (a release-build step) needs it.

# Bump when the manifest shape changes incompatibly. Consumers reject a
# schema_version they do not recognise rather than misread newer fields.
SCHEMA_VERSION = 1

# Conventional filename for the published manifest artifact (e.g. a GitHub
# Release asset). Kept here so producer and consumer agree on one name.
MANIFEST_FILENAME = "mascope-manifest.json"


class ManifestError(RuntimeError):
    """Raised when a manifest cannot be generated or a loaded one is invalid."""


def read_alembic_head(backend_path: Path) -> str:
    """
    Read the single Alembic head revision from a backend checkout.

    Reads the migration scripts only (via Alembic's ScriptDirectory) - no
    database connection - so it is safe to run at build time.

    :param backend_path: The backend package directory holding ``alembic.ini``.
    :raises ManifestError: if the head cannot be resolved or the scripts have
        multiple heads (an unreleasable state that must be merged first).
    """
    # Imported here, not at module top: alembic is a dev/CI-only dependency (see
    # the note above). Manifest generation only runs where it is installed.
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
    except ModuleNotFoundError as e:
        raise ManifestError(
            "Manifest generation requires alembic, which a source checkout "
            "installs; it runs at release build time, not on an operator install."
        ) from e

    ini = backend_path / "alembic.ini"
    if not ini.exists():
        raise ManifestError(f"alembic.ini not found under '{backend_path}'")

    script = ScriptDirectory.from_config(Config(str(ini)))
    heads = script.get_heads()
    if not heads:
        raise ManifestError("No Alembic revisions found")
    if len(heads) > 1:
        raise ManifestError(
            f"Multiple Alembic heads {heads} - merge them before releasing"
        )
    return heads[0]


def build_manifest(app_version: str, backend_path: Path) -> dict:
    """
    Assemble the release manifest for ``app_version`` from a backend checkout.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "app_version": app_version,
        "alembic_head": read_alembic_head(backend_path),
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def load_manifest(path: Path) -> dict:
    """
    Load and validate a manifest from a local file.

    :raises ManifestError: if the file is missing, not valid JSON, of an
        unrecognised ``schema_version``, or missing required fields.
    """
    if not path.exists():
        raise ManifestError(f"Manifest file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ManifestError(f"Could not read manifest '{path}': {e}") from e

    if not isinstance(data, dict):
        raise ManifestError("Manifest must be a JSON object")

    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ManifestError(
            f"Unsupported manifest schema_version {version!r} "
            f"(this CLI understands {SCHEMA_VERSION})"
        )

    for field in ("app_version", "alembic_head"):
        if not data.get(field):
            raise ManifestError(f"Manifest is missing required field '{field}'")

    return data


def write_manifest(manifest: dict, output: Optional[Path]) -> str:
    """
    Serialise ``manifest`` as pretty JSON, writing it to ``output`` when given.

    :return: The JSON text (also written to the file when ``output`` is set).
    """
    text = json.dumps(manifest, indent=2, sort_keys=True)
    if output is not None:
        output.write_text(text + "\n", encoding="utf-8")
    return text
