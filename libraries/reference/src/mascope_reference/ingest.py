"""Versioned ingestion of a source dump into the reference mirror.

Each ingest is one versioned load: it records a new ``reference_source`` row,
streams the adapter's records through :func:`normalize.finalize`, and bulk
inserts them as ``reference_compound`` rows pointing at that source row. The
new load becomes the active version of its source and any prior active load of
the same source is deactivated, so queries see exactly one version per source
while older loads stay on disk for reproducibility (``prune=True`` drops them).

Runs against a synchronous SQLAlchemy engine - it is driven by the CLI, off the
request path, and streams so a multi-gigabyte dump never lands in memory.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Callable

from sqlalchemy import delete, insert, update
from sqlalchemy.engine import Engine

from mascope_reference.adapters.base import Adapter
from mascope_reference.normalize import finalize
from mascope_reference.record import ReferenceRecord
from mascope_reference.schema import (
    COMPOUND_INSERT_COLUMNS,
    FORMULA_LENGTH,
    INCHIKEY_LENGTH,
    LICENSE_LENGTH,
    SOURCE_NATIVE_ID_LENGTH,
    reference_compound,
    reference_source,
)


DEFAULT_BATCH_SIZE = 5000


class EmptyIngest(RuntimeError):
    """An activating load produced no usable records, so it was not applied.

    Raised instead of committing, because the alternative - an active source with
    zero compounds - is indistinguishable from a source nobody has loaded yet,
    and silently removes every annotation the previous version provided.
    """


@dataclass
class IngestResult:
    """Outcome of one ingest load."""

    source: str
    version: str
    reference_source_id: int
    ingested: int
    skipped: int


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
) -> Iterator[tuple[dict | None, bool]]:
    """Yield (insert_dict, skipped) for each raw record from the adapter."""
    for raw in adapter.parse(path):
        record = finalize(raw)
        if record is None:
            # Formula had no parseable elements - no usable annotation key.
            yield None, True
            continue
        row = _compound_row(source_id, record)
        if row is None:
            yield None, True
            continue
        yield row, False


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
    :return: An :class:`IngestResult` summarizing the load.
    :raises FileNotFoundError: If ``path`` does not exist.
    :raises EmptyIngest: If an activating load yielded no usable records. The
        transaction is rolled back, so the previous active version survives.
    """
    if not path.exists():
        raise FileNotFoundError(f"Reference dump not found: {path}")

    name = source_name or adapter.name
    ingested = 0
    skipped = 0
    with engine.begin() as conn:
        # The load starts inactive and is promoted only once rows are in. Doing it
        # the other way round - deactivating the previous version up front - means
        # a dump that yields nothing (a misnamed file, or a header the adapter
        # cannot see) replaces a good mirror with an empty one and reports
        # success. Ordering it this way makes a failed load a no-op instead.
        source_id = conn.execute(
            insert(reference_source)
            .values(
                name=name,
                version=version,
                license=adapter.license,
                record_count=0,
                is_active=False,
                ingested_at=datetime.now(timezone.utc),
            )
            .returning(reference_source.c.reference_source_id)
        ).scalar_one()

        batch: list[dict] = []
        for row, was_skipped in _finalized_rows(adapter, path, source_id):
            if was_skipped:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= batch_size:
                conn.execute(insert(reference_compound), batch)
                ingested += len(batch)
                batch = []
                if progress is not None:
                    progress(ingested)
        if batch:
            conn.execute(insert(reference_compound), batch)
            ingested += len(batch)
            if progress is not None:
                progress(ingested)

        if activate and ingested == 0:
            raise EmptyIngest(
                f"Source '{name}' version '{version}' yielded no usable records "
                f"from {path} ({skipped} skipped); the active version was left "
                "in place. Check the file's format and encoding."
            )

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
            _prune_inactive(conn, name, keep_source_id=source_id)

    return IngestResult(
        source=name,
        version=version,
        reference_source_id=source_id,
        ingested=ingested,
        skipped=skipped,
    )


def _prune_inactive(conn, source_name: str, keep_source_id: int) -> None:
    """Delete all but the given load for a source (compounds then source rows)."""
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
    conn.execute(
        delete(reference_compound).where(
            reference_compound.c.reference_source_id.in_(stale)
        )
    )
    conn.execute(
        delete(reference_source).where(
            reference_source.c.reference_source_id.in_(stale)
        )
    )
