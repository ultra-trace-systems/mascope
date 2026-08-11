"""
Ingest reference data into the active Mascope database, from inside the backend.

The ``mascope reference sync`` CLI command is a developer / monorepo command: it
pulls the chemistry dependencies (via ``mascope_reference`` -> ``mascope_tools``)
that are deliberately kept out of the lightweight operator CLI. A deployed
backend image, however, already ships those dependencies, so in production
reference data is loaded by running this script **inside the backend container**:

    docker compose exec backend \\
        python -m mascope_backend.db.scripts.reference_sync \\
        custom /data/my_list.csv --name my-list --version 2024

The file path is resolved inside the container, so mount your dump into the
backend service first (any path the container can read). ``source`` is a
registered adapter name (``custom`` for hand-authored lists - see
``docs/dev/reference_data_authoring.md``).

This is the same versioned, idempotent-by-replacement ingest the CLI runs; it
just executes where the chemistry dependencies live.
"""

import argparse
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from mascope_backend.db.secrets import postgres_password
from mascope_backend.runtime import runtime
from mascope_reference import get_adapter, ingest


def _sync_engine():
    """Build a synchronous engine for the backend's configured database."""
    db_cfg = runtime.config.database
    url = db_cfg.get_postgres_url_sync(
        password=postgres_password, env_name=runtime.env.name
    )
    return create_engine(url, poolclass=NullPool)


def main() -> None:
    """Entry point: ``python -m mascope_backend.db.scripts.reference_sync``."""
    parser = argparse.ArgumentParser(
        prog="reference_sync",
        description="Ingest reference data into the active Mascope database.",
    )
    parser.add_argument(
        "source", help="Registered source name, e.g. 'pubchem' or 'custom'."
    )
    parser.add_argument(
        "file", type=Path, help="Path to the dump, resolved inside the container."
    )
    parser.add_argument(
        "--version", required=True, help="Version tag for this load (reproducibility)."
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Provenance name override, e.g. for a 'custom' list (--name my-list).",
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete prior (now inactive) loads of this source after success.",
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="Ingest without activating (does not replace the current version).",
    )
    args = parser.parse_args()

    try:
        adapter = get_adapter(args.source)
    except KeyError as error:
        raise SystemExit(str(error))
    if not args.file.exists():
        raise SystemExit(f"Reference dump not found: {args.file}")

    engine = _sync_engine()
    runtime.logger.info(
        f"Ingesting '{args.name or args.source}' (version '{args.version}') "
        f"from {args.file.name}..."
    )

    def _report_progress(n: int) -> None:
        # A named function, not a lambda: loguru colorizes the record's function
        # name, and a "<lambda>" name is parsed as an (invalid) color tag.
        runtime.logger.info(f"  ...{n:,} records ingested")

    try:
        result = ingest(
            engine,
            adapter,
            args.file,
            args.version,
            source_name=args.name,
            batch_size=args.batch_size,
            activate=not args.stage,
            prune=args.prune,
            progress=_report_progress,
        )
    finally:
        engine.dispose()

    runtime.logger.success(
        f"Ingested {result.ingested:,} records from '{result.source}' "
        f"(version '{result.version}', source_id={result.reference_source_id}, "
        f"{result.skipped:,} skipped)."
    )


if __name__ == "__main__":
    main()
