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
registered adapter name (``custom`` for hand-authored lists - see the user guide
``docs/user/guides/reference-lists.md``).

This is the same versioned, idempotent-by-replacement ingest the CLI runs; it
just executes where the chemistry dependencies live. The source row records how
the compounds may be matched - a database at the mirror window, a list
unbounded - and ``--elements``, ``--max-carbon``, ``--max-mass``,
``--allow-radicals`` and ``--polarity`` override it, as they do for the CLI.
It records the publications the source comes from too - a list file's own
references - and ``--citation`` with ``--doi`` or ``--isbn`` names one in their
place.

An MS/MS spectral library loads the same way, its spectra rather than
compounds, with no window to override:

    docker compose exec backend \\
        python -m mascope_backend.db.scripts.reference_sync \\
        massbank /data/MassBank-data-2026.03.tar.gz --version 2026.03
"""

import argparse
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from mascope_backend.db.secrets import postgres_password
from mascope_backend.runtime import runtime
from mascope_reference import (
    get_adapter,
    get_spectrum_adapter,
    ingest,
    ingest_spectra,
    is_spectrum_source,
)
from mascope_reference.ingest import EmptyIngest, SourceKindConflict
from mascope_reference.peaklist import citation_text, given_citations
from mascope_reference.scope import UNBOUNDED_TOKEN, scope_of


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
        "source",
        help="Registered source name, e.g. 'pubchem', 'custom' or a spectral "
        "library: 'massbank', 'mona', 'gnps'.",
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
    parser.add_argument(
        "--elements",
        help=(
            "Elements a formula of this source may carry, comma-separated "
            f"(C,H,N,O,S,Si), or '{UNBOUNDED_TOKEN}'. Default: the adapter's window."
        ),
    )
    parser.add_argument(
        "--max-carbon", help=f"Largest carbon count, or '{UNBOUNDED_TOKEN}'."
    )
    parser.add_argument(
        "--max-mass", help=f"Largest monoisotopic mass in Da, or '{UNBOUNDED_TOKEN}'."
    )
    parser.add_argument(
        "--allow-radicals",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Whether this source's odd-electron formulas may be matched. Default: "
            "not for a database or a CSV; a list file's own header decides for it."
        ),
    )
    parser.add_argument(
        "--polarity",
        choices=["positive", "negative", "both"],
        help=(
            "The polarity this source's compounds are detected in. Default: both "
            "for a database or a CSV; a list file's own header decides for it."
        ),
    )
    parser.add_argument(
        "--citation",
        help=(
            "The publication this source comes from, as a reader would cite it; "
            "give its --doi or --isbn too. Recorded in place of what a list "
            "file's header cites."
        ),
    )
    parser.add_argument("--doi", help="The DOI of the --citation publication.")
    parser.add_argument("--isbn", help="The ISBN of the --citation publication.")
    args = parser.parse_args()

    try:
        citations = given_citations(args.citation, args.doi, args.isbn)
    except ValueError as error:
        raise SystemExit(str(error))

    if is_spectrum_source(args.source):
        _sync_spectra(args, citations)
        return

    try:
        adapter = get_adapter(args.source)
    except KeyError as error:
        raise SystemExit(str(error))
    if not args.file.exists():
        raise SystemExit(f"Reference dump not found: {args.file}")
    try:
        scope = scope_of(adapter, args.file).overridden(
            elements=args.elements,
            max_carbon=args.max_carbon,
            max_mass=args.max_mass,
            allow_radicals=args.allow_radicals,
            polarity=args.polarity,
        )
    except ValueError as error:
        raise SystemExit(str(error))

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
            scope=scope,
            citations=citations,
        )
    except SourceKindConflict as error:
        raise SystemExit(str(error))
    finally:
        engine.dispose()

    runtime.logger.success(
        f"Ingested {result.ingested:,} records from '{result.source}' "
        f"(version '{result.version}', source_id={result.reference_source_id}, "
        f"{result.skipped:,} skipped)."
    )
    runtime.logger.info(f"Matched as: {result.scope.describe()}.")
    for reference in result.citations:
        runtime.logger.info(f"Cites: {citation_text(reference)}")


def _sync_spectra(args: argparse.Namespace, citations: list[dict] | None) -> None:
    """Load a spectral library's dump, as ``main`` does a compound source's."""
    window_flags = {
        "--elements": args.elements,
        "--max-carbon": args.max_carbon,
        "--max-mass": args.max_mass,
        "--allow-radicals": args.allow_radicals,
        "--polarity": args.polarity,
    }
    given = [flag for flag, value in window_flags.items() if value is not None]
    if given:
        raise SystemExit(
            f"{', '.join(given)} set how a compound source is matched; "
            f"'{args.source}' is a spectral library, which Stage A never matches."
        )
    if not args.file.exists():
        raise SystemExit(f"Reference dump not found: {args.file}")
    adapter = get_spectrum_adapter(args.source)

    engine = _sync_engine()
    runtime.logger.info(
        f"Ingesting spectral library '{args.name or args.source}' "
        f"(version '{args.version}') from {args.file.name}..."
    )

    def _report_progress(n: int) -> None:
        # A named function: see main().
        runtime.logger.info(f"  ...{n:,} spectra ingested")

    try:
        result = ingest_spectra(
            engine,
            adapter,
            args.file,
            args.version,
            source_name=args.name,
            batch_size=args.batch_size,
            activate=not args.stage,
            prune=args.prune,
            progress=_report_progress,
            citations=citations or (),
        )
    except (EmptyIngest, SourceKindConflict) as error:
        raise SystemExit(str(error))
    finally:
        engine.dispose()

    reasons = ", ".join(f"{n:,} {reason}" for reason, n in result.skipped.items())
    runtime.logger.success(
        f"Ingested {result.ingested:,} spectra from '{result.source}' "
        f"(version '{result.version}', source_id={result.reference_source_id}, "
        f"{result.skipped_total:,} skipped{': ' + reasons if reasons else ''})."
    )


if __name__ == "__main__":
    main()
