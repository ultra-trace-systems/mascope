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
from mascope_reference.schema import reference_compound, reference_source


reference_app = typer.Typer()
console = Console()


def _license_gate() -> list[str] | None:
    """The deployment's Stage A reference-licence allowlist, or None if unset.

    Backend configuration, not CLI configuration, so it is read off the full
    config rather than ``runtime.config``. A checkout with no ``[backend]``
    section at all yields None, same as an unset allowlist.

    :return: The configured allowlist, or None for no gating.
    """
    backend = getattr(runtime.full_config, "backend", None)
    return getattr(backend, "reference_licenses", None)


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
    allowed = _license_gate()
    for name in available_sources():
        adapter = get_adapter(name)
        # Flagged here as well as in `status` so the gate is visible *before* a
        # multi-hour ingest of a source peak assignment would then decline to
        # match. Only the adapter's DEFAULT licence is known at this point, and
        # the gate matches per record: a `custom` list whose rows carry their
        # own licence is judged row by row, which `status` reports once it is
        # loaded. Hence "records that keep it" rather than "this source".
        blocked = allowed is not None and adapter.license not in allowed
        gated = (
            "  [red](outside this deployment's licence gate - peak assignment "
            "would not match records that keep this licence)[/red]"
            if blocked
            else ""
        )
        console.print(f"[bold]{name}[/bold]  (license: {adapter.license}){gated}")


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


def _record_licenses(conn, source_ids: list[int]) -> dict[int, list[str]]:
    """The distinct per-record licences held by each of the given sources.

    The gate matches ``reference_compound.license``, not the source's declared
    licence, and the two can differ: the ``custom`` adapter honours a per-row
    ``license`` column. Reporting the source licence alone would therefore claim
    a source is matched when half its records are not.

    Only called when a gate is configured, and only for the active sources: it
    is an aggregate over the compound table, and a deployment that has not
    opted into gating should not pay for a scan it has nothing to learn from.

    :param conn: Open synchronous connection.
    :param source_ids: Reference source ids to roll up.
    :return: Source id -> sorted distinct record licences.
    """
    rows = conn.execute(
        select(
            reference_compound.c.reference_source_id,
            reference_compound.c.license,
        )
        .where(reference_compound.c.reference_source_id.in_(source_ids))
        .group_by(
            reference_compound.c.reference_source_id,
            reference_compound.c.license,
        )
    ).all()
    grouped: dict[int, list[str]] = {}
    for source_id, license_name in rows:
        grouped.setdefault(source_id, []).append(license_name or "")
    return {source_id: sorted(names) for source_id, names in grouped.items()}


def _print_license_gate(
    rows, record_licenses: dict[int, list[str]], allowed: list[str] | None
) -> None:
    """Report which active sources peak assignment is allowed to match.

    The licence set is otherwise invisible: a narrowed gate shrinks what Stage A
    can find with nothing in the UI to say so, which is exactly why it is
    reported here (and recorded on every run's config).

    :param rows: The source rows already fetched for the status table.
    :param record_licenses: Source id -> distinct record licences, for the
        active sources; empty when no gate is configured.
    :param allowed: The configured allowlist, or None for no gating.
    """
    if allowed is None:
        # The toml section name is escaped: Rich reads square brackets as
        # console markup and would swallow '[backend]' entirely, leaving the
        # operator an instruction that omits where the setting goes.
        console.print(
            "\n[bold]Stage A licence gate:[/bold] not configured - peak "
            "assignment matches every active source.\n"
            "Restrict it with \\[backend] reference_licenses in the env config "
            "toml (see docs/maintaining.md)."
        )
        return

    console.print(
        Text.assemble(
            ("\nStage A licence gate: ", "bold"),
            Text(", ".join(allowed)),
        )
    )
    active = [row for row in rows if row.is_active]
    if not active:
        console.print("No active sources to match against.")
        return

    permitted = set(allowed)
    table = Table(title="Peak assignment (Stage A) reference matching")
    table.add_column("Source")
    table.add_column("Version")
    table.add_column("Stage A")
    table.add_column("Allowed record licences")
    table.add_column("Blocked record licences")
    for row in active:
        found = record_licenses.get(row.reference_source_id, [])
        ok = [name for name in found if name in permitted]
        blocked = [name for name in found if name not in permitted]
        if not found:
            verdict = "[dim]no records[/dim]"
        elif not blocked:
            verdict = "[green]matched[/green]"
        elif not ok:
            verdict = "[red]NOT matched[/red]"
        else:
            verdict = "[yellow]partly matched[/yellow]"
        table.add_row(
            Text(row.name),
            Text(row.version),
            verdict,
            Text(", ".join(ok)),
            Text(", ".join(blocked)),
        )
    console.print(table)


@reference_app.command()
def status() -> None:
    """Show ingested sources, their versions, and what Stage A may match."""
    allowed = _license_gate()
    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    reference_source.c.reference_source_id,
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
            active_ids = [row.reference_source_id for row in rows if row.is_active]
            record_licenses = (
                _record_licenses(conn, active_ids)
                if allowed is not None and active_ids
                else {}
            )
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
    for row in rows:
        # Wrapped in Text because these come from the database: Rich would parse
        # square brackets in them as console markup and swallow them, so a source
        # named with --name riva2019[hom] would be reported as 'riva2019'.
        table.add_row(
            Text(row.name),
            Text(row.version),
            Text(row.license or ""),
            f"{row.record_count:,}",
            "[green]yes[/green]" if row.is_active else "no",
            Text(str(row.ingested_at)),
        )
    console.print(table)
    _print_license_gate(rows, record_licenses, allowed)
