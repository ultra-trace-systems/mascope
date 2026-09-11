"""
Load the reference lists Mascope ships into the active database, from inside the
backend.

The deployment counterpart of ``mascope reference seed``: a server has no
developer CLI, but its backend image ships the lists and the chemistry
dependencies, so an operator runs this inside the backend container:

    docker compose exec backend python -m mascope_backend.db.scripts.reference_seed
    docker compose exec backend python -m mascope_backend.db.scripts.reference_seed --list

Nothing runs it unasked - loading the lists is an operator's choice, because
every active reference formula is one Stage A of peak assignment matches peaks
against. Each list becomes its own versioned source, the same ingest
``reference_sync`` runs, and a list whose version is already active is left
alone, so running it twice changes nothing.
"""

import argparse

from mascope_backend.db.scripts.reference_sync import _sync_engine
from mascope_backend.runtime import runtime
from mascope_reference.peaklist import admitted_species
from mascope_reference.seed import catalogue, seed, select_lists


def main() -> None:
    """Entry point: ``python -m mascope_backend.db.scripts.reference_seed``."""
    parser = argparse.ArgumentParser(
        prog="reference_seed",
        description="Load the reference lists Mascope ships into the active database.",
    )
    parser.add_argument(
        "names",
        nargs="*",
        help="Ids of the lists to load. Default: every list that loads by default.",
    )
    parser.add_argument(
        "--all",
        dest="include_optional",
        action="store_true",
        help="Also load the lists that do not load by default (the radical lists).",
    )
    parser.add_argument(
        "--list",
        dest="show",
        action="store_true",
        help="Show the shipped lists and exit, without touching the database.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete a list's earlier loads once its new version is in.",
    )
    args = parser.parse_args()

    lists = catalogue()
    if args.show:
        for peak_list in lists:
            default = "loads by default" if peak_list.load_by_default else "opt-in"
            species = sum(1 for _ in admitted_species(peak_list))
            runtime.logger.info(
                f"{peak_list.id}  version {peak_list.data_version}, "
                f"{peak_list.license}, {species:,} species, {default} - "
                f"{peak_list.label}"
            )
        return
    try:
        chosen = select_lists(
            lists, args.names or None, include_optional=args.include_optional
        )
    except KeyError as error:
        raise SystemExit(error.args[0]) from None

    engine = _sync_engine()
    try:
        outcomes = seed(
            engine, names=[peak_list.id for peak_list in chosen], prune=args.prune
        )
    finally:
        engine.dispose()
    for outcome in outcomes:
        if outcome.loaded:
            runtime.logger.success(
                f"Loaded '{outcome.list_id}' version '{outcome.version}' "
                f"({outcome.ingested:,} records)."
            )
        else:
            runtime.logger.info(
                f"'{outcome.list_id}' version '{outcome.version}' is already "
                "active - nothing to load."
            )


if __name__ == "__main__":
    main()
