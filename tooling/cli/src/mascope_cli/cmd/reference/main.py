"""
`mascope reference` - ingest and inspect mirrored public chemistry databases.

Fetches happen out of band; this command runs a source's ETL adapter over a
downloaded dump and upserts the normalized records into the ``reference_*``
tables as a versioned load, mirroring how the demo dataset is built and keeping
ingestion off the request path. Each load is versioned for reproducibility and
becomes the active version of its source.

Each source row also records how its compounds may be matched - its window, its
radical allowance and its polarity (``mascope_reference.scope``). A database
mirror loads at the mirror window and a list loads unbounded; ``sync``'s
``--elements``, ``--max-carbon``, ``--max-mass``, ``--allow-radicals`` and
``--polarity`` override either. It records the publications the source comes
from as well: a list file's own references, or the one ``sync``'s
``--citation`` with ``--doi`` or ``--isbn`` names.

``sync`` loads the MS/MS spectral libraries (``massbank``, ``mona``, ``gnps``)
the same way, as versioned loads of spectra rather than compounds. They have
no window: Stage A never matches a spectrum, so the window flags are refused
for them.
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
from mascope_reference import (
    available_sources,
    available_spectrum_sources,
    get_adapter,
    get_spectrum_adapter,
    ingest,
    ingest_spectra,
    is_spectrum_source,
)
from mascope_reference.ingest import (
    DEFAULT_BATCH_SIZE,
    EmptyIngest,
    SourceKindConflict,
    deactivate,
)
from mascope_reference.peaklist import (
    admitted_species,
    citation_text,
    given_citations,
)
from mascope_reference.schema import (
    COMPOUND_KIND,
    SPECTRUM_KIND,
    reference_compound,
    reference_source,
)
from mascope_reference.scope import (
    BOTH_POLARITIES,
    UNBOUNDED_TOKEN,
    SourceScope,
    scope_of,
)
from mascope_reference.seed import catalogue, select_lists
from mascope_reference.seed import seed as seed_lists


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
    """List the reference sources with a registered ETL adapter.

    Each with the licence its records default to and the window a load of it
    writes on its source row: the mirror window for a database, unbounded for a
    list, where ``sync``'s window flags can say otherwise.
    """
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
        console.print(
            f"[bold]{name}[/bold]  (license: {adapter.license}; window: "
            f"{adapter.known_window.describe()}){gated}"
        )
    # Not under the gate above: it is Stage A's, and Stage A reads no spectrum.
    console.print(
        "\n[bold]MS/MS spectral libraries[/bold] - spectra, not compounds: not "
        "matched by Stage A, each spectrum keeps the licence its record states"
    )
    for name in available_spectrum_sources():
        adapter = get_spectrum_adapter(name)
        console.print(
            f"[bold]{name}[/bold]  (license: per record; {adapter.license} where "
            "a record states none)"
        )


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
            help=(
                "Path to the downloaded source dump (.sdf/.csv/.xml, optionally "
                ".gz; for a spectral library .msp, .mgf or a MassBank release "
                ".tar.gz)."
            ),
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
    elements: Annotated[
        Optional[str],
        typer.Option(
            "--elements",
            help=(
                "Elements a formula of this source may carry, comma-separated "
                f"(C,H,N,O,S,Si), or '{UNBOUNDED_TOKEN}'. Default: the adapter's "
                "window (see 'sources')."
            ),
        ),
    ] = None,
    max_carbon: Annotated[
        Optional[str],
        typer.Option(
            "--max-carbon",
            help=f"Largest carbon count, or '{UNBOUNDED_TOKEN}'.",
        ),
    ] = None,
    max_mass: Annotated[
        Optional[str],
        typer.Option(
            "--max-mass",
            help=f"Largest monoisotopic mass in Da, or '{UNBOUNDED_TOKEN}'.",
        ),
    ] = None,
    allow_radicals: Annotated[
        Optional[bool],
        typer.Option(
            "--allow-radicals/--no-allow-radicals",
            help=(
                "Whether this source's odd-electron formulas may be matched. "
                "Default: not for a database or a CSV; a list file's own header "
                "decides for it."
            ),
        ),
    ] = None,
    polarity: Annotated[
        Optional[str],
        typer.Option(
            "--polarity",
            help=(
                "The polarity this source's compounds are detected in: positive, "
                f"negative or '{BOTH_POLARITIES}'. Default: both for a database or a "
                "CSV; a list file's own header decides for it."
            ),
        ),
    ] = None,
    citation: Annotated[
        Optional[str],
        typer.Option(
            "--citation",
            help=(
                "The publication this source comes from, as a reader would cite "
                "it; give its --doi or --isbn too. Recorded on the source row in "
                "place of what a list file's header cites. Default: a list "
                "file's references; nothing for a database or a CSV."
            ),
        ),
    ] = None,
    doi: Annotated[
        Optional[str],
        typer.Option("--doi", help="The DOI of the --citation publication."),
    ] = None,
    isbn: Annotated[
        Optional[str],
        typer.Option("--isbn", help="The ISBN of the --citation publication."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Do not ask for confirmation (for non-interactive use).",
        ),
    ] = False,
) -> None:
    """Ingest a source dump as a new versioned load.

    A spectral library (massbank, mona, gnps) loads its MS/MS spectra; the
    window, radical and polarity flags are a compound source's and are refused
    for it.
    """
    try:
        citations = given_citations(citation, doi, isbn)
    except ValueError as error:
        # Refused before the prompt: a citation that cannot be followed would
        # otherwise be found out only once the load is serving.
        runtime.logger.error(str(error))
        raise typer.Exit(1) from None
    if is_spectrum_source(source):
        flags = {
            "--elements": elements,
            "--max-carbon": max_carbon,
            "--max-mass": max_mass,
            "--allow-radicals": allow_radicals,
            "--polarity": polarity,
        }
        given = [flag for flag, value in flags.items() if value is not None]
        if given:
            # Refused before the prompt: a flag that does nothing would read as
            # if it had bounded something.
            runtime.logger.error(
                f"{', '.join(given)} set how a compound source is matched; "
                f"'{source}' is a spectral library, which Stage A never matches."
            )
            raise typer.Exit(1)
        _confirm_sync(name or source, prune=prune, stage=stage, yes=yes)
        _sync_spectra(
            source,
            path,
            version,
            name=name,
            batch_size=batch_size,
            prune=prune,
            stage=stage,
            citations=citations,
        )
        return

    try:
        adapter = get_adapter(source)
    except KeyError as error:
        runtime.logger.error(str(error))
        raise typer.Exit(1) from None
    try:
        scope = scope_of(adapter, path).overridden(
            elements=elements,
            max_carbon=max_carbon,
            max_mass=max_mass,
            allow_radicals=allow_radicals,
            polarity=polarity,
        )
    except ValueError as error:
        # Refused before the prompt, like an unknown source: a bound that cannot
        # be read would otherwise be discovered after a multi-hour load.
        runtime.logger.error(str(error))
        raise typer.Exit(1) from None

    _confirm_sync(name or source, prune=prune, stage=stage, yes=yes)

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
            scope=scope,
            citations=citations,
        )
    except (EmptyIngest, SourceKindConflict) as error:
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
    runtime.logger.info(f"Matched as: {result.scope.describe()}.")
    for reference in result.citations:
        runtime.logger.info(f"Cites: {citation_text(reference)}")
    if stage:
        runtime.logger.info(
            f"Load staged (inactive). Expose it with: mascope reference activate "
            f"{result.source} --version {result.version}"
        )


def _confirm_sync(target: str, *, prune: bool, stage: bool, yes: bool) -> None:
    """Ask before a sync replaces what is serving, unless staged or told not to.

    An activating sync replaces whatever is currently serving annotations, and
    --prune destroys the load it replaces. Staging changes nothing visible, so
    it needs no confirmation.
    """
    if stage or yes:
        return
    consequence = (
        f"replace the active version of '{target}' and DELETE its prior loads"
        if prune
        else f"replace the active version of '{target}'"
    )
    typer.confirm(f"This will {consequence}. Continue?", abort=True)


def _skipped_text(skipped: dict[str, int]) -> str:
    """'3 skipped: 2 not MS2, 1 predicted', or '0 skipped'."""
    total = sum(skipped.values())
    if not total:
        return "0 skipped"
    reasons = ", ".join(f"{count:,} {reason}" for reason, count in skipped.items())
    return f"{total:,} skipped: {reasons}"


def _sync_spectra(
    source: str,
    path: Path,
    version: str,
    *,
    name: str | None,
    batch_size: int,
    prune: bool,
    stage: bool,
    citations: list[dict] | None,
) -> None:
    """Load a spectral library's dump, as `sync` does a compound source's."""
    adapter = get_spectrum_adapter(source)
    engine = _sync_engine()
    runtime.logger.info(
        f"Ingesting spectral library '{name or source}' (version '{version}') "
        f"from {path.name}..."
    )

    def _progress(count: int) -> None:
        runtime.logger.info(f"  ...{count:,} spectra ingested")

    try:
        result = ingest_spectra(
            engine,
            adapter,
            path,
            version,
            source_name=name,
            batch_size=batch_size,
            activate=not stage,
            prune=prune,
            progress=_progress,
            citations=citations or (),
        )
    except (EmptyIngest, SourceKindConflict) as error:
        runtime.logger.error(str(error))
        raise typer.Exit(1) from None
    finally:
        engine.dispose()

    runtime.logger.success(
        f"Ingested {result.ingested:,} spectra from '{result.source}' "
        f"(version '{result.version}', source_id={result.reference_source_id}, "
        f"{_skipped_text(result.skipped)})."
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


@reference_app.command("deactivate")
def deactivate_source(
    source: Annotated[
        str,
        typer.Argument(help="Provenance name of the source, as shown by 'status'."),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Do not ask for confirmation."),
    ] = False,
) -> None:
    """Take a source's active load out, leaving no version of it active.

    Its compounds stay in the database, inactive: annotation and peak assignment
    stop reading them, 'reference activate' brings a version back, and 'reference
    seed' loads a shipped list again.
    """
    if not yes:
        typer.confirm(
            f"This will deactivate '{source}': annotation and peak assignment "
            "will no longer read it. Continue?",
            abort=True,
        )

    engine = _sync_engine()
    try:
        taken = deactivate(engine, source)
    finally:
        engine.dispose()
    if taken is None:
        runtime.logger.error(
            f"'{source}' has no active load. Run 'mascope reference status' to "
            "see what is loaded."
        )
        raise typer.Exit(1)
    runtime.logger.success(
        f"Deactivated '{taken.source}' version '{taken.version}' "
        f"({taken.record_count:,} records). Bring it back with: mascope reference "
        f"activate {taken.source} --version {taken.version}"
    )


def _print_catalogue(lists) -> None:
    """Show the shipped reference lists: what each is and whether it loads by
    default. Reads the package, never the database."""
    table = Table(title="Reference lists shipped with Mascope")
    table.add_column("List")
    table.add_column("Version")
    table.add_column("Licence")
    table.add_column("Species", justify="right")
    table.add_column("Loads by default")
    table.add_column("Label")
    for peak_list in lists:
        table.add_row(
            Text(peak_list.id),
            Text(peak_list.data_version or ""),
            Text(peak_list.license or ""),
            f"{sum(1 for _ in admitted_species(peak_list)):,}",
            "yes" if peak_list.load_by_default else "no",
            Text(peak_list.label or ""),
        )
    console.print(table)


@reference_app.command()
def seed(
    names: Annotated[
        Optional[list[str]],
        typer.Argument(
            help=(
                "Ids of the lists to load (see --list). Default: every list "
                "that loads by default."
            ),
        ),
    ] = None,
    include_optional: Annotated[
        bool,
        typer.Option(
            "--all",
            help=(
                "Also load the lists that do not load by default: the radical "
                "lists, whose species compete with closed-shell molecules for "
                "the same peaks."
            ),
        ),
    ] = False,
    show: Annotated[
        bool,
        typer.Option(
            "--list", help="Show the shipped lists and exit, without the database."
        ),
    ] = False,
    prune: Annotated[
        bool,
        typer.Option(
            "--prune",
            help="Delete a list's earlier loads once its new version is in.",
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
    """Load the reference lists Mascope ships, each as its own source.

    Each list becomes the active version of a source named by its id, through
    the same ingest `sync` runs. A list whose version is already active is left
    alone, so seeding twice changes nothing.
    """
    lists = catalogue()
    if show:
        _print_catalogue(lists)
        return
    try:
        chosen = select_lists(lists, names, include_optional=include_optional)
    except KeyError as error:
        runtime.logger.error(error.args[0])
        raise typer.Exit(1) from None

    # Loading activates each list's source, replacing an earlier version of it,
    # and --prune deletes what it replaces - gated like `sync`, before any
    # database work.
    if not yes:
        ids = ", ".join(peak_list.id for peak_list in chosen)
        consequence = (
            "and DELETE their earlier loads"
            if prune
            else "replacing any earlier version of each"
        )
        typer.confirm(
            f"This will load {len(chosen)} reference list(s) ({ids}) as active "
            f"sources, {consequence}. Continue?",
            abort=True,
        )

    engine = _sync_engine()
    try:
        outcomes = seed_lists(
            engine, names=[peak_list.id for peak_list in chosen], prune=prune
        )
    finally:
        engine.dispose()
    for outcome in outcomes:
        if not outcome.loaded:
            refreshed = (
                "; its row was brought up to date with what the list says of its "
                "window, radical allowance, polarity and citations"
                if outcome.refreshed
                else ""
            )
            runtime.logger.info(
                f"'{outcome.list_id}' version '{outcome.version}' is already "
                f"active - nothing to load{refreshed}."
            )
            continue
        held = (
            f"; {outcome.held_back:,} radicals held back" if outcome.held_back else ""
        )
        runtime.logger.success(
            f"Loaded '{outcome.list_id}' version '{outcome.version}' "
            f"({outcome.ingested:,} records{held})."
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


def _registered_licenses() -> dict[str, list[str]]:
    """Licence tag -> the registered sources that carry it by default.

    Read off the adapter registry rather than written down here, so a source
    added later cannot leave the vocabulary out of date.

    :return: Tag -> source names, ordered by tag.
    """
    tags: dict[str, list[str]] = {}
    for name in available_sources():
        tags.setdefault(get_adapter(name).license, []).append(name)
    return dict(sorted(tags.items()))


def _print_known_licenses(allowed: list[str] | None) -> None:
    """List every licence tag in use, marking the ones a gate leaves out.

    The gate is an exact string match on the tag a record carries, so an
    allowlist that omits one drops those records with nothing in a result to
    say why. Listing the whole vocabulary - not only the sources that happen to
    be loaded here - is what stops an operator declining one source and
    silently losing another they never considered.

    :param allowed: The configured allowlist, or None for no gating.
    """
    table = Table(title="Reference licence tags (matched as exact strings)")
    table.add_column("Licence tag")
    table.add_column("Default for")
    table.add_column("Stage A")
    for tag, names in _registered_licenses().items():
        matched = allowed is None or tag in allowed
        table.add_row(
            Text(tag),
            Text(", ".join(names)),
            "[green]matched[/green]" if matched else "[red]NOT matched[/red]",
        )
    console.print(table)
    console.print(
        "If you set an allowlist, any tag you leave out is dropped silently, "
        "and a source added by a later release brings a tag your list will not "
        "name - so re-check it after an upgrade. 'custom' is the tag on every "
        "hand-authored row that carries no licence of its own."
    )


def _is_spectral(row) -> bool:
    """Whether a status row is a spectral library's load.

    A row from before sources had a kind is a compound source's.
    """
    return (row.kind or COMPOUND_KIND) == SPECTRUM_KIND


def _scope_text(row) -> str:
    """How a status row's compounds may be matched, for the table."""
    if _is_spectral(row):
        return "spectra; not matched by Stage A"
    try:
        return SourceScope.from_row(row).describe()
    except (TypeError, ValueError):
        # A row the scope cannot read is reported, not allowed to hide the table.
        return "unreadable"


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
                    reference_source.c.known_window,
                    reference_source.c.allow_radicals,
                    reference_source.c.polarity,
                    reference_source.c.kind,
                ).order_by(
                    reference_source.c.name, reference_source.c.ingested_at.desc()
                )
            ).all()
            # The gate is Stage A's, and Stage A reads compounds only.
            active_ids = [
                row.reference_source_id
                for row in rows
                if row.is_active and not _is_spectral(row)
            ]
            record_licenses = (
                _record_licenses(conn, active_ids)
                if allowed is not None and active_ids
                else {}
            )
    finally:
        engine.dispose()

    if not rows:
        runtime.logger.info("No reference sources ingested yet.")
        # Printed before the early return as well: an operator with an empty
        # mirror is exactly the one about to write `reference_licenses` for the
        # first time, and the vocabulary is what they need to write it.
        _print_known_licenses(allowed)
        return

    table = Table(title="Reference sources")
    table.add_column("Source")
    table.add_column("Version")
    table.add_column("License")
    table.add_column("Records", justify="right")
    table.add_column("Active")
    table.add_column("Ingested (UTC)")
    table.add_column("Matched as")
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
            Text(_scope_text(row)),
        )
    console.print(table)
    _print_license_gate(
        [row for row in rows if not _is_spectral(row)], record_licenses, allowed
    )
    _print_known_licenses(allowed)
