"""Versioned ingestion of a source dump into the reference mirror.

Each ingest is one versioned load: it records a new ``reference_source`` row,
streams the adapter's records through :func:`normalize.finalize`, and bulk
inserts them as ``reference_compound`` rows pointing at that source row. The
new load becomes the active version of its source and any prior active load of
the same source is deactivated, so queries see exactly one version per source
while older loads stay on disk for reproducibility (``prune=True`` drops them).
:func:`deactivate` takes a source's active load out without a replacement.

The source row also records how the load's compounds may be matched - its
window, its radical allowance and its polarity (:mod:`mascope_reference.scope`) -
as the adapter says of the dump unless the caller says otherwise, and the
publications the source comes from: a list file's own references, or the ones
the caller gives.

:func:`ingest_spectra` loads a spectral library the same way, into
``reference_spectrum``: a versioned load on its own source row, promoted only
once spectra are in. The row records its kind, and a name holds loads of one
kind only, so a spectral load can never take the place of a compound source's
active version, nor the other way round.

Runs against a synchronous SQLAlchemy engine - it is driven by the CLI, off the
request path, and streams so a multi-gigabyte dump never lands in memory.
"""

from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Callable

from sqlalchemy import Table, delete, func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from mascope_reference.adapters.base import Adapter, SpectrumAdapter
from mascope_reference.normalize import finalize
from mascope_reference.record import ReferenceRecord
from mascope_reference.schema import (
    COMPOUND_INSERT_COLUMNS,
    COMPOUND_KIND,
    FORMULA_LENGTH,
    INCHIKEY_LENGTH,
    LICENSE_LENGTH,
    SOURCE_NATIVE_ID_LENGTH,
    SPECTRUM_INSERT_COLUMNS,
    SPECTRUM_KIND,
    reference_compound,
    reference_source,
    reference_spectrum,
)
from mascope_reference.scope import SourceScope, scope_of
from mascope_reference.spectrum import (
    SkippedSpectrum,
    SpectrumRecord,
    finalize_spectrum,
)


DEFAULT_BATCH_SIZE = 5000


class EmptyIngest(RuntimeError):
    """An activating load produced no usable records, so it was not applied.

    Raised instead of committing, because the alternative - an active source with
    zero compounds - is indistinguishable from a source nobody has loaded yet,
    and silently removes every annotation the previous version provided.
    """


class SourceKindConflict(RuntimeError):
    """A load named after a source whose loads hold the other kind of data.

    Raised before anything is written. Activating a load deactivates every other
    load of its name, so a spectral library loaded under a compound source's
    name (``--name hmdb`` for MoNA's HMDB library) would take that source out of
    annotation and Stage A, and a list loaded under a library's name would take
    the library's spectra out of the read path.
    """


@dataclass
class IngestResult:
    """Outcome of one ingest load."""

    source: str
    version: str
    reference_source_id: int
    ingested: int
    skipped: int
    #: What the source row records about how the compounds may be matched.
    scope: SourceScope
    #: The publications the source row records it comes from.
    citations: tuple[dict, ...] = ()


@dataclass
class SpectrumIngestResult:
    """Outcome of one spectral library load."""

    source: str
    version: str
    reference_source_id: int
    ingested: int
    #: Why the records left out were: reason -> count.
    skipped: dict[str, int]

    @property
    def skipped_total(self) -> int:
        """How many records of the dump were left out."""
        return sum(self.skipped.values())


@dataclass(frozen=True)
class DeactivatedLoad:
    """A load :func:`deactivate` took out."""

    source: str
    version: str
    record_count: int


def _fit_native_id(value: str) -> str:
    """Fit a source-native id into its column without losing distinctness.

    Adapters fall back to the compound name when a dump has no id column, and
    systematic names (lipids especially) run well past the column width. Plain
    truncation would collide two different compounds onto one id, so the head is
    kept for readability and a digest of the whole value guarantees the result
    still distinguishes them.
    """
    if len(value) <= SOURCE_NATIVE_ID_LENGTH:
        return value
    digest = sha1(value.encode("utf-8")).hexdigest()[:12]
    return f"{value[: SOURCE_NATIVE_ID_LENGTH - len(digest) - 1]}-{digest}"


def _fit_inchikey(value: str | None) -> str | None:
    """Normalize an InChIKey, or drop it when it is not one.

    Several sources publish the field prefixed (``InChIKey=BSYNRY...``), which is
    36 characters against a 27-character column. Anything that is not a standard
    key after stripping that prefix is dropped rather than truncated: the key is
    the identity that de-duplication collapses on, so a mangled one would merge
    two unrelated compounds, while a missing one only costs a merge.
    """
    if not value:
        return None
    candidate = value.strip()
    if candidate.upper().startswith("INCHIKEY="):
        candidate = candidate[len("INCHIKEY=") :].strip()
    return candidate if len(candidate) <= INCHIKEY_LENGTH else None


def _compound_row(source_id: int, record: ReferenceRecord) -> dict | None:
    """Build a ``reference_compound`` insert dict from a finalized record.

    Returns ``None`` when the record cannot be stored as-is. Every bounded column
    is fitted here rather than left to the database: Postgres answers an over-long
    value with StringDataRightTruncation, which aborts the statement and, because
    a load is one transaction, discards hours of completed work over a single bad
    cell. One row is a cheaper thing to lose than the load.
    """
    record_columns = set(COMPOUND_INSERT_COLUMNS) - {"reference_source_id"}
    data = record.model_dump(include=record_columns)
    if len(data.get("formula") or "") > FORMULA_LENGTH:
        # canonical_formula already rejects non-formulas, so this is unreachable
        # for real data; truncating would silently change the annotation key.
        return None
    data["source_native_id"] = _fit_native_id(str(data.get("source_native_id") or ""))
    data["inchikey"] = _fit_inchikey(data.get("inchikey"))
    # A licence is a label, so the head of it still identifies the terms.
    license_value = data.get("license")
    if license_value and len(license_value) > LICENSE_LENGTH:
        data["license"] = license_value[:LICENSE_LENGTH]
    data["reference_source_id"] = source_id
    return data


def _finalized_rows(
    adapter: Adapter, path: Path, source_id: int
) -> Iterator[dict | None]:
    """Yield an insert dict for each raw record from the adapter, None if skipped."""
    for raw in adapter.parse(path):
        record = finalize(raw)
        # A formula with no parseable elements has no usable annotation key.
        yield None if record is None else _compound_row(source_id, record)


def _spectrum_row(source_id: int, spectrum: SpectrumRecord) -> dict:
    """Build a ``reference_spectrum`` insert dict from a finalized spectrum.

    The bounded columns are fitted here, as a compound's are: a load is one
    transaction, and one over-long cell would abort it.
    """
    data = spectrum.model_dump(
        include=set(SPECTRUM_INSERT_COLUMNS) - {"reference_source_id", "peaks"}
    )
    data["source_native_id"] = _fit_native_id(spectrum.source_native_id)
    if len(data.get("formula") or "") > FORMULA_LENGTH:
        data["formula"] = None
    data["license"] = (data.get("license") or "")[:LICENSE_LENGTH]
    data["peaks"] = [[mz, intensity] for mz, intensity in spectrum.peaks]
    data["reference_source_id"] = source_id
    return data


def _citations_of(adapter, path: Path) -> tuple[dict, ...]:
    """The publications a load of this dump records, before any the caller gives.

    An adapter whose dump names them - a list file's header does - reads them
    with a ``citations(path)`` method of its own; any other cites none.
    """
    read = getattr(adapter, "citations", None)
    return () if read is None else tuple(read(path))


def _refuse_other_kind(conn: Connection, name: str, kind: str) -> None:
    """Raise SourceKindConflict when ``name`` already holds the other kind.

    A row written before sources had a kind, or by a schema that has none, is a
    compound source.
    """
    other = conn.execute(
        select(func.coalesce(reference_source.c.kind, COMPOUND_KIND))
        .where(reference_source.c.name == name)
        .where(func.coalesce(reference_source.c.kind, COMPOUND_KIND) != kind)
        .limit(1)
    ).scalar()
    if other is not None:
        held = "compounds" if other == COMPOUND_KIND else "spectra"
        loading = "compounds" if kind == COMPOUND_KIND else "spectra"
        raise SourceKindConflict(
            f"'{name}' holds {held}, and a load of {loading} under that name would "
            f"deactivate them. Load it under a name of its own (--name)."
        )


def _begin_load(
    conn: Connection, *, name: str, version: str, license: str, kind: str, **values
) -> int:
    """Insert a load's source row, inactive and empty, and return its id.

    The load starts inactive and is promoted only once rows are in. Doing it the
    other way round - deactivating the previous version up front - means a dump
    that yields nothing (a misnamed file, or a header the adapter cannot see)
    replaces a good mirror with an empty one and reports success. Ordering it
    this way makes a failed load a no-op instead.
    """
    _refuse_other_kind(conn, name, kind)
    return conn.execute(
        insert(reference_source)
        .values(
            name=name,
            version=version,
            license=license,
            record_count=0,
            is_active=False,
            ingested_at=datetime.now(timezone.utc),
            kind=kind,
            **values,
        )
        .returning(reference_source.c.reference_source_id)
    ).scalar_one()


def _insert_rows(
    conn: Connection,
    target: Table,
    rows: Iterable[dict | None],
    batch_size: int,
    progress: Callable[[int], None] | None,
) -> tuple[int, int]:
    """Bulk insert the rows, None standing for a skipped record.

    :return: (inserted, skipped).
    """
    inserted = 0
    skipped = 0
    batch: list[dict] = []
    for row in rows:
        if row is None:
            skipped += 1
            continue
        batch.append(row)
        if len(batch) >= batch_size:
            conn.execute(insert(target), batch)
            inserted += len(batch)
            batch = []
            if progress is not None:
                progress(inserted)
    if batch:
        conn.execute(insert(target), batch)
        inserted += len(batch)
        if progress is not None:
            progress(inserted)
    return inserted, skipped


def _finish_load(
    conn: Connection,
    name: str,
    source_id: int,
    ingested: int,
    *,
    activate: bool,
    prune: bool,
    target: Table,
) -> None:
    """Record the load's count and, when activating, promote it."""
    conn.execute(
        update(reference_source)
        .where(reference_source.c.reference_source_id == source_id)
        .values(record_count=ingested, is_active=activate)
    )
    if activate:
        # Only one active version per source at a time. Deactivating the
        # others here, after this load is known to be good, is what makes a
        # bad load harmless.
        conn.execute(
            update(reference_source)
            .where(reference_source.c.name == name)
            .where(reference_source.c.reference_source_id != source_id)
            .where(reference_source.c.is_active.is_(True))
            .values(is_active=False)
        )
    if prune and activate:
        _prune_inactive(conn, name, keep_source_id=source_id, target=target)


def ingest(
    engine: Engine,
    adapter: Adapter,
    path: Path,
    version: str,
    *,
    source_name: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    activate: bool = True,
    prune: bool = False,
    progress: Callable[[int], None] | None = None,
    scope: SourceScope | None = None,
    citations: Sequence[dict] | None = None,
) -> IngestResult:
    """Ingest one source dump as a new versioned load.

    :param engine: Synchronous SQLAlchemy engine for the target database.
    :param adapter: Source adapter resolved from the registry.
    :param path: Path to the downloaded dump.
    :param version: Version tag for this load (release date/tag), for
        reproducibility and provenance.
    :param source_name: Provenance name for this load, overriding the adapter's.
        Lets several loads of a generic adapter (e.g. ``custom``) coexist as
        distinct sources instead of replacing one another. Defaults to
        ``adapter.name``.
    :param batch_size: Rows per bulk insert.
    :param activate: Mark this load active and deactivate prior loads of the
        same source. Set ``False`` to stage a load without exposing it.
    :param prune: After a successful activated load, delete prior (now
        inactive) loads of the same source and their compounds.
    :param progress: Optional callback invoked with the running inserted count
        after each batch.
    :param scope: What the source row records about how the compounds may be
        matched. Defaults to what the adapter says of the dump
        (:func:`mascope_reference.scope.scope_of`): the mirror window for a
        database, unbounded for a list.
    :param citations: The publications the source row records it comes from,
        each a reference as :func:`mascope_reference.peaklist.recorded_reference`
        keeps one. Defaults to what the dump names: a list file's references,
        none for anything else.
    :return: An :class:`IngestResult` summarizing the load.
    :raises FileNotFoundError: If ``path`` does not exist.
    :raises EmptyIngest: If an activating load yielded no usable records. The
        transaction is rolled back, so the previous active version survives.
    :raises SourceKindConflict: If ``source_name`` holds spectra.
    """
    if not path.exists():
        raise FileNotFoundError(f"Reference dump not found: {path}")

    name = source_name or adapter.name
    if scope is None:
        scope = scope_of(adapter, path)
    citations = _citations_of(adapter, path) if citations is None else tuple(citations)
    with engine.begin() as conn:
        source_id = _begin_load(
            conn,
            name=name,
            version=version,
            license=adapter.license,
            kind=COMPOUND_KIND,
            citations=list(citations),
            **scope.row_values(),
        )
        ingested, skipped = _insert_rows(
            conn,
            reference_compound,
            _finalized_rows(adapter, path, source_id),
            batch_size,
            progress,
        )
        if activate and ingested == 0:
            raise EmptyIngest(
                f"Source '{name}' version '{version}' yielded no usable records "
                f"from {path} ({skipped} skipped); the active version was left "
                "in place. Check the file's format and encoding."
            )
        _finish_load(
            conn,
            name,
            source_id,
            ingested,
            activate=activate,
            prune=prune,
            target=reference_compound,
        )

    return IngestResult(
        source=name,
        version=version,
        reference_source_id=source_id,
        ingested=ingested,
        skipped=skipped,
        scope=scope,
        citations=citations,
    )


def _spectrum_rows(
    adapter: SpectrumAdapter, path: Path, source_id: int, skipped: Counter
) -> Iterator[dict | None]:
    """Yield an insert dict per admitted spectrum, None per record left out.

    Counts each left-out record under its reason in ``skipped``.
    """
    for parsed in adapter.parse(path):
        spectrum = (
            parsed if isinstance(parsed, SkippedSpectrum) else finalize_spectrum(parsed)
        )
        if isinstance(spectrum, SkippedSpectrum):
            skipped[spectrum.reason] += 1
            yield None
            continue
        yield _spectrum_row(source_id, spectrum)


def ingest_spectra(
    engine: Engine,
    adapter: SpectrumAdapter,
    path: Path,
    version: str,
    *,
    source_name: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    activate: bool = True,
    prune: bool = False,
    progress: Callable[[int], None] | None = None,
    citations: Sequence[dict] = (),
) -> SpectrumIngestResult:
    """Ingest one spectral library dump as a new versioned load.

    The same versioned load :func:`ingest` makes of a compound source: a new
    source row, its spectra in, then promoted to the active version of its name,
    with the earlier versions kept unless ``prune``. Each spectrum keeps its own
    licence, which the read path filters on.

    :param engine: Synchronous SQLAlchemy engine for the target database.
    :param adapter: The library's adapter.
    :param path: Path to the downloaded dump.
    :param version: Version tag for this load (the library's release).
    :param source_name: Provenance name for this load, overriding the adapter's,
        so two exports of one library (MoNA's positive and negative modes) can
        be loaded side by side.
    :param batch_size: Rows per bulk insert.
    :param activate: Mark this load active and deactivate prior loads of the
        same name. ``False`` stages it.
    :param prune: After a successful activated load, delete the name's earlier
        loads and their spectra.
    :param progress: Optional callback with the running inserted count.
    :param citations: The publications the source row records the library
        comes from, as :func:`ingest` takes them; none by default.
    :return: A :class:`SpectrumIngestResult`, with why records were left out.
    :raises FileNotFoundError: If ``path`` does not exist.
    :raises EmptyIngest: If an activating load admitted no spectrum. Nothing is
        written, so the previous active version survives.
    :raises SourceKindConflict: If the name holds compounds.
    """
    if not path.exists():
        raise FileNotFoundError(f"Reference dump not found: {path}")

    name = source_name or adapter.name
    skipped: Counter = Counter()
    with engine.begin() as conn:
        source_id = _begin_load(
            conn,
            name=name,
            version=version,
            license=adapter.license,
            kind=SPECTRUM_KIND,
            citations=list(citations),
        )
        ingested, _ = _insert_rows(
            conn,
            reference_spectrum,
            _spectrum_rows(adapter, path, source_id, skipped),
            batch_size,
            progress,
        )
        if activate and ingested == 0:
            reasons = ", ".join(
                f"{count} {reason}" for reason, count in skipped.most_common()
            )
            raise EmptyIngest(
                f"Library '{name}' version '{version}' yielded no usable spectra "
                f"from {path} ({reasons or 'no records read'}); the active version "
                "was left in place. Check that the file is this library's dump."
            )
        _finish_load(
            conn,
            name,
            source_id,
            ingested,
            activate=activate,
            prune=prune,
            target=reference_spectrum,
        )

    return SpectrumIngestResult(
        source=name,
        version=version,
        reference_source_id=source_id,
        ingested=ingested,
        skipped=dict(skipped.most_common()),
    )


def deactivate(engine: Engine, source_name: str) -> DeactivatedLoad | None:
    """Take a source's active load out, leaving no version of it active.

    Nothing is deleted: the load and its compounds stay, inactive, so
    ``mascope reference activate`` brings the version back and a re-seed of a
    shipped list finds nothing active and loads it again.

    :param engine: Synchronous engine for the target database.
    :param source_name: The source's provenance name.
    :return: The load taken out, or None when the source had no active load.
    """
    with engine.begin() as conn:
        active = conn.execute(
            select(
                reference_source.c.reference_source_id,
                reference_source.c.version,
                reference_source.c.record_count,
            )
            .where(reference_source.c.name == source_name)
            .where(reference_source.c.is_active.is_(True))
            .order_by(reference_source.c.reference_source_id.desc())
        ).all()
        if not active:
            return None
        conn.execute(
            update(reference_source)
            .where(
                reference_source.c.reference_source_id.in_(
                    [row.reference_source_id for row in active]
                )
            )
            .values(is_active=False)
        )
    # One version is active at a time; were there ever more, the newest is the
    # one a person would name.
    newest = active[0]
    return DeactivatedLoad(
        source=source_name,
        version=newest.version,
        record_count=newest.record_count or 0,
    )


def _prune_inactive(
    conn, source_name: str, keep_source_id: int, target: Table = reference_compound
) -> None:
    """Delete all but the given load for a source (its rows, then source rows).

    :param target: The table the source's loads hold rows in: compounds, or
        spectra for a spectral library. A name holds one kind.
    """
    stale = (
        conn.execute(
            reference_source.select()
            .with_only_columns(reference_source.c.reference_source_id)
            .where(reference_source.c.name == source_name)
            .where(reference_source.c.reference_source_id != keep_source_id)
        )
        .scalars()
        .all()
    )
    if not stale:
        return
    conn.execute(delete(target).where(target.c.reference_source_id.in_(stale)))
    conn.execute(
        delete(reference_source).where(
            reference_source.c.reference_source_id.in_(stale)
        )
    )
