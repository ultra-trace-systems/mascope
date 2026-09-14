"""
Take a reference source's active load out of the active database, from inside the
backend.

The deployment counterpart of ``mascope reference deactivate``: a server has no
developer CLI, so an operator runs this inside the backend container:

    docker compose exec backend python -m mascope_backend.db.scripts.reference_deactivate cyclic-siloxanes

Annotation and peak assignment stop reading the source at once. Nothing is
deleted - the load stays, inactive - so ``reference_seed`` loads a shipped list
again and ``reference_sync`` loads a new version of any other source.
"""

import argparse

from mascope_backend.db.scripts.reference_sync import _sync_engine
from mascope_backend.runtime import runtime
from mascope_reference.ingest import deactivate


def main() -> None:
    """Entry point: ``python -m mascope_backend.db.scripts.reference_deactivate``."""
    parser = argparse.ArgumentParser(
        prog="reference_deactivate",
        description="Take a reference source's active load out.",
    )
    parser.add_argument("source", help="The source's name, e.g. a list's id.")
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Do not ask for confirmation (for non-interactive use).",
    )
    args = parser.parse_args()

    if not args.yes:
        answer = input(
            f"This will deactivate '{args.source}': annotation and peak assignment "
            "will no longer read it. Continue? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            raise SystemExit("Aborted.")

    engine = _sync_engine()
    try:
        taken = deactivate(engine, args.source)
    finally:
        engine.dispose()
    if taken is None:
        raise SystemExit(f"'{args.source}' has no active load - nothing to deactivate.")
    runtime.logger.success(
        f"Deactivated '{taken.source}' version '{taken.version}' "
        f"({taken.record_count:,} records)."
    )


if __name__ == "__main__":
    main()
