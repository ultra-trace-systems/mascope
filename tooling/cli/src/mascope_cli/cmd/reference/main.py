"""
`mascope reference` - ingest and inspect mirrored public chemistry databases.

Fetches happen out of band; this command runs a source's ETL adapter over a
downloaded dump and upserts the normalized records into the ``reference_*``
tables as a versioned load, mirroring how the demo dataset is built and keeping
ingestion off the request path. Each load is versioned for reproducibility and
becomes the active version of its source.
"""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text
from sqlalchemy import create_engine, select, update
from sqlalchemy.pool import NullPool

from mascope_cli.runtime import runtime
from mascope_reference import available_sources, get_adapter, ingest
from mascope_reference.ingest import DEFAULT_BATCH_SIZE, EmptyIngest
from mascope_reference.schema import reference_source


reference_app = typer.Typer()
console = Console()


def _sync_engine():
    """Build a synchronous engine for the active runtime env's database.

    Mirrors ``mascope dev migrate`` - resolves the sync Postgres URL from the
    runtime config and the ``POSTGRES_PASSWORD_FILE`` secret.

    :raises typer.Exit: If the database is not configured.
    """
    db_cfg = runtime.full_config.backend.database
    if not db_cfg:
        runtime.logger.error("Database not configured in .mascope.toml")
        raise typer.Exit(1)
    password = runtime.secret("POSTGRES_PASSWORD_FILE", "postgres_password.txt")
    url = db_cfg.get_postgres_url_sync(password=password, env_name=runtime.env.name)
    return create_engine(url, poolclass=NullPool)


@reference_app.callback()
def main():
    """Ingest and inspect mirrored public chemistry databases."""


@reference_app.command("sources")
def sources() -> None:
    """List the reference sources with a registered ETL adapter."""
    for name in available_sources():
        adapter = get_adapter(name)
        console.print(f"[bold]{name}[/bold]  (license: {adapter.license})")


@reference_app.command()
def sync(
    source: Annotated[
        str,
        typer.Argument(help="Registered source name, e.g. 'pubchem' (see 'sources')."),
    ],
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Path to the downloaded source dump (.sdf/.csv/.xml, optionally .gz).",
        ),
    ],
    version: Annotated[
        str,
        typer.Option(
            "--version",
            "-v",
            help="Version tag for this load (release date/tag), for reproducibility.",
        ),
    ],
    name: Annotated[
        Optional[str],
        typer.Option(
            "--name",
            "-n",
            help=(
                "Provenance name for this load, overriding the source name. Use "
                "with the 'custom' source so multiple hand-authored lists coexist "
                "as distinct sources (e.g. --name riva2019-hom)."
            ),
        ),
    ] = None,
    batch_size: Annotated[
        int,
        typer.Option("--batch-size", help="Rows per bulk insert."),
    ] = DEFAULT_BATCH_SIZE,
    prune: Annotated[
        bool,
        typer.Option(
            "--prune",
            help="Delete prior (now inactive) loads of this source after success.",
        ),
    ] = False,
    stage: Annotated[
        bool,
        typer.Option(
            "--stage",
            help=(
                "Ingest without activating (does not replace the current "
                "version). Expose it later with 'reference activate'."
            ),
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Do not ask for confirmation (for non-interactive use).",
        ),
    ] = False,
) -> None:
    """Ingest a source dump as a new versioned load."""
    try:
        adapter = get_adapter(source)
    except KeyError as error:
        runtime.logger.error(str(error))
        raise typer.Exit(1) from None

    # An activating sync replaces whatever is currently serving annotations, and
    # --prune destroys the load it replaces. Staging changes nothing visible, so
    # it needs no confirmation.
    if not stage and not yes:
        target = name or source
        consequence = (
            f"replace the active version of '{target}' and DELETE its prior loads"
            if prune
            else f"replace the active version of '{target}'"
        )
        typer.confirm(f"This will {consequence}. Continue?", abort=True)

    engine = _sync_engine()
    runtime.logger.info(
        f"Ingesting '{name or source}' (version '{version}') from {path.name}..."
    )

    def _progress(count: int) -> None:
        runtime.logger.info(f"  ...{count:,} records ingested")

    try:
        result = ingest(
            engine,
            adapter,
            path,
            version,
            source_name=name,
            batch_size=batch_size,
            activate=not stage,
            prune=prune,
            progress=_progress,
        )
    except EmptyIngest as error:
        # The mirror is untouched - report why rather than leaving the operator to
        # discover an empty source later.
        runtime.logger.error(str(error))
        raise typer.Exit(1) from None
    finally:
        engine.dispose()

    runtime.logger.success(
        f"Ingested {result.ingested:,} records from '{result.source}' "
        f"(version '{result.version}', source_id={result.reference_source_id}, "
        f"{result.skipped:,} skipped)."
    )
    if stage:
        runtime.logger.info(
            f"Load staged (inactive). Expose it with: mascope reference activate "
            f"{result.source} --version {result.version}"
        )


@reference_app.command()
def activate(
    source: Annotated[
        str,
        typer.Argument(help="Provenance name of the source, as shown by 'status'."),
    ],
    version: Annotated[
        str,
        typer.Option("--version", "-v", help="Version tag of the load to activate."),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Do not ask for confirmation."),
    ] = False,
) -> None:
    """Make a staged load the active version of its source.

    The counterpart to ``sync --stage``: without it a staged load can only be
    exposed by re-running the whole ingest without ``--stage``.
    """
    if not yes:
        typer.confirm(
            f"This will replace the active version of '{source}' with "
            f"'{version}'. Continue?",
            abort=True,
        )

    engine = _sync_engine()
    try:
        with engine.begin() as conn:
            target = conn.execute(
                select(
                    reference_source.c.reference_source_id,
                    reference_source.c.record_count,
                )
                .where(reference_source.c.name == source)
                .where(reference_source.c.version == version)
            ).first()
            if target is None:
                runtime.logger.error(
                    f"No load found for source '{source}' version '{version}'. "
                    "Run 'mascope reference status' to see what is loaded."
                )
                raise typer.Exit(1)
            source_id, record_count = target
            if not record_count:
                runtime.logger.error(
                    f"Load '{source}' version '{version}' has no records; "
                    "activating it would remove every annotation the current "
                    "version provides."
                )
                raise typer.Exit(1)
            conn.execute(
                update(reference_source)
                .where(reference_source.c.name == source)
                .where(reference_source.c.reference_source_id != source_id)
                .values(is_active=False)
            )
            conn.execute(
                update(reference_source)
                .where(reference_source.c.reference_source_id == source_id)
                .values(is_active=True)
            )
    finally:
        engine.dispose()

    runtime.logger.success(
        f"Activated '{source}' version '{version}' ({record_count:,} records)."
    )


@reference_app.command()
def status() -> None:
    """Show ingested sources and their versions from the database."""
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    reference_source.c.name,
                    reference_source.c.version,
                    reference_source.c.license,
                    reference_source.c.record_count,
                    reference_source.c.is_active,
                    reference_source.c.ingested_at,
                ).order_by(
                    reference_source.c.name, reference_source.c.ingested_at.desc()
                )
            ).all()
    finally:
        engine.dispose()

    if not rows:
        runtime.logger.info("No reference sources ingested yet.")
        return

    table = Table(title="Reference sources")
    table.add_column("Source")
    table.add_column("Version")
    table.add_column("License")
    table.add_column("Records", justify="right")
    table.add_column("Active")
    table.add_column("Ingested (UTC)")
    for name, version, lic, count, is_active, ingested_at in rows:
        # Wrapped in Text because these come from the database: Rich would parse
        # square brackets in them as console markup and swallow them, so a source
        # named with --name riva2019[hom] would be reported as 'riva2019'.
        table.add_row(
            Text(name),
            Text(version),
            Text(lic or ""),
            f"{count:,}",
            "[green]yes[/green]" if is_active else "no",
            Text(str(ingested_at)),
        )
    console.print(table)
