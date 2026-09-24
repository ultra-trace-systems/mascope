"""
Maintenance script to learn method bindings from files already routed.

A ``method_binding`` row records which chemistry an acquisition method has
been seen running, so that a later file of the same method can route without
a filename token (``docs/dev/ingest_routing_and_splitting.md``, section 5.3).
Rows are normally learned as files arrive, which means a server that installs
the feature knows nothing until its next few weeks of uploads. It already has
the evidence: every ACQUISITION sample item auto-processing made says which
mode a file of a known method and polarity bound to.

This reads that history and folds it into the table. **It folds rather than
skips**: ``shadow`` is the default, so live learning starts creating rows the
moment the release lands, and the busiest methods have a row - built from the
last few files - before anyone runs this. Skipping those would leave exactly
the keys that matter with no history, and a key whose past holds two
chemistries would read `learned` on the handful seen live. Running it twice
is safe, and a second run after more ingest adds only what is new.

**It reads the scan-stream census from each file's ``.props``**, for the
instruments whose reader records one, because the signature class comes from
the census and nothing else may be substituted for it. A file of such an
instrument that carries no census - anything converted before the census
existed - is *skipped and counted*, not guessed at: a guessed class would key
the same method apart from the census later files carry and split its history
between the two.

Set DRY_RUN=1 to report what would be written without writing.

Usage:
    mascope dev db script run backfill_method_bindings
    mascope prod db script run backfill_method_bindings

Date: 2026-09-24
"""

import asyncio
import os
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mascope_backend.api.controllers.sample.files.process.bindings import (
    method_binding_mode,
)
from mascope_backend.api.controllers.sample.files.process.status import (
    read_scan_streams,
)
from mascope_backend.db import MethodBinding, async_session, configure_database_engine
from mascope_backend.db.id import gen_id
from mascope_backend.method_keys import (
    CENSUS_BEARING_INSTRUMENT_TYPES,
    METHOD_KEY_COLUMN,
    SIGNATURE_CLASS_COLUMN,
    binding_digest,
    chemistry_key,
    clipped,
    method_key,
    signature_class,
)
from mascope_backend.runtime import runtime


#: Files whose props are read at once. The read is a small local JSON load,
#: so the limit is there to bound open descriptors rather than for speed.
_PROPS_CONCURRENCY = 16

#: Rows fetched per query, for the history walk and for the lookups.
_PAGE = 5000

#: Keys listed individually in the report.
_PREVIEW_LIMIT = 20

#: Every (file, mode) auto-processing produced, oldest first.
#:
#: The item filter is ``process.service._pipeline_item()`` spelled in SQL: an
#: ACQUISITION item, in an ACQUISITION batch, of an ACQUISITION dataset, in a
#: system workspace. None of those says it alone - a person can make an
#: ACQUISITION-typed item in a batch of their own, and a dataset a person
#: creates inside an instrument's workspace is in a system workspace too - and
#: live learning only ever sees the pipeline's own bindings, so reading any
#: wider would teach this table things ingest never would.
#:
#: No DISTINCT: two of the selected columns are `json`, which has no equality
#: operator in Postgres, so the query would be refused outright. A file with
#: several items under one mode - a windowed acquisition - is de-duplicated in
#: :func:`_history_pages` instead, by comparing each row with the previous
#: one: the two share a filename and a timestamp, so this order puts them
#: next to each other.
_HISTORY_SQL = """
    SELECT
        sf.filename                     AS filename,
        sf.instrument                   AS instrument,
        sf.instrument_type              AS instrument_type,
        sf.method_file                  AS method_file,
        sf.datetime_utc                 AS datetime_utc,
        im.ionization_mode_id           AS mode_id,
        im.ionization_mode_polarity     AS polarity,
        im.ionization_mechanism_ids     AS mechanism_ids
    FROM sample_item si
    JOIN sample_batch sb ON sb.sample_batch_id = si.sample_batch_id
    JOIN dataset d       ON d.dataset_id = sb.dataset_id
    JOIN workspace w     ON w.workspace_id = d.workspace_id
    JOIN sample_file sf  ON sf.sample_file_id = si.sample_file_id
    JOIN ionization_mode im ON im.ionization_mode_id = si.ionization_mode_id
    WHERE si.sample_item_type = 'ACQUISITION'
      AND sb.sample_batch_type = 'ACQUISITION'
      AND d.dataset_type = 'ACQUISITION'
      AND w.is_system IS TRUE
      AND si.ionization_mode_id IS NOT NULL
    ORDER BY sf.datetime_utc, sf.filename, im.ionization_mode_id
    LIMIT :limit OFFSET :offset
"""


async def _history_pages():
    """The routing history in pages, oldest first, one entry per (file, mode).

    Oldest first so that ``first_seen`` means what it says, and so that the
    chemistry a key is recorded under is the one it was seen with first - the
    order live learning would have seen them in.

    A page at a time, because the largest production server has hundreds of
    thousands of these and only the folded keys - a few hundred - need to be
    held. The de-duplication is a comparison with the previous row rather than
    a set of every identity seen, for the same reason: two items of one file
    under one mode, which a windowed acquisition produces, share a filename
    and a timestamp and so are adjacent in this order.

    :return: Pages of dicts, each page in acquisition order.
    """
    offset = 0
    async with async_session() as session:
        while True:
            page = [
                dict(row)
                for row in (
                    await session.execute(
                        text(_HISTORY_SQL), {"limit": _PAGE, "offset": offset}
                    )
                ).mappings()
            ]
            if page:
                yield page
            if len(page) < _PAGE:
                return
            offset += _PAGE


async def _census(filenames: list[str]) -> dict[str, list[dict]]:
    """The scan-stream census of each file, read a bounded number at a time."""
    gate = asyncio.Semaphore(_PROPS_CONCURRENCY)

    async def one(name: str) -> tuple[str, list[dict]]:
        async with gate:
            return name, await read_scan_streams(name)

    return dict(await asyncio.gather(*(one(name) for name in filenames)))


def _fold(
    observations: list[dict],
    census: dict[str, list[dict]],
    records: dict[str, dict],
    tally: dict[str, int],
    previous: tuple[str, str] | None,
) -> tuple[str, str] | None:
    """Fold one page of history into the records built so far.

    The rules live learning applies: the chemistry first seen is the one the
    row points at, a second chemistry marks the key ambiguous and counts a
    disagreement, and the row is never repointed by one.

    :param observations: One page from :func:`_history_pages`, oldest first.
    :param census: The scan streams of that page's files, by filename.
    :param records: The records so far, added to in place.
    :param tally: Running counts, added to in place.
    :param previous: The last identity of the page before, so that the
        de-duplication carries across a page boundary.
    :return: The last identity of this page.
    :rtype: tuple[str, str] | None
    """
    for row in observations:
        identity = (row["filename"], row["mode_id"])
        if identity == previous:
            continue
        if previous is None or previous[0] != row["filename"]:
            tally["files"] += 1
        previous = identity
        tally["observations"] += 1

        signature = signature_class(
            census.get(row["filename"]), row["polarity"], row["instrument_type"]
        )
        if signature is None:
            tally["unknown"] += 1
            continue
        key = method_key(row["method_file"])
        digest = binding_digest(row["instrument"], key, signature)
        chemistry = chemistry_key(row["mechanism_ids"])
        seen = row["datetime_utc"] or datetime.now(timezone.utc)

        record = records.get(digest)
        if record is None:
            records[digest] = {
                "binding_key": digest,
                "instrument": row["instrument"],
                "method_key": clipped(key, METHOD_KEY_COLUMN),
                "signature_class": clipped(signature, SIGNATURE_CLASS_COLUMN),
                "ionization_mode_id": row["mode_id"],
                "chemistry_keys": [chemistry],
                "state": "learned",
                "source": "history",
                "first_seen": seen,
                "last_seen": seen,
                "n_streams": 1,
                "n_disagreements": 0,
                "last_chemistry_key": chemistry,
            }
            continue

        record["first_seen"] = min(record["first_seen"], seen)
        record["last_seen"] = max(record["last_seen"], seen)
        record["n_streams"] += 1
        record["last_chemistry_key"] = chemistry
        if chemistry not in record["chemistry_keys"]:
            record["chemistry_keys"].append(chemistry)
            record["n_disagreements"] += 1
            record["state"] = "ambiguous"
    return previous


async def _apply(records: dict[str, dict]) -> dict[str, int]:
    """Fold the records into the table, creating or merging each row.

    Row by row under ``FOR UPDATE``, so that a live ingest creating one of
    these keys in between cannot make the whole write roll back on the unique
    constraint - which one ``add_all`` of every new row would.

    :param records: The records from :func:`_fold`.
    :return: Counts of the rows created and merged, and of the keys a merge
        turned ambiguous.
    :rtype: dict[str, int]
    """
    counts = {"created": 0, "merged": 0, "turned_ambiguous": 0}
    async with async_session() as session:
        for digest in sorted(records):
            record = records[digest]
            created = (
                await session.execute(
                    pg_insert(MethodBinding)
                    .values(method_binding_id=gen_id(), **record)
                    .on_conflict_do_nothing(constraint="uq_method_binding_key")
                    .returning(MethodBinding.method_binding_id)
                )
            ).scalar_one_or_none()
            if created is not None:
                counts["created"] += 1
                continue

            row = (
                await session.execute(
                    select(MethodBinding)
                    .where(MethodBinding.binding_key == digest)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                continue
            if _merge(row, record):
                counts["turned_ambiguous"] += 1
            counts["merged"] += 1
        await session.commit()
    return counts


def _merge(row: MethodBinding, record: dict) -> bool:
    """Fold a record into a row live learning already made.

    The row keeps pointing where it does - live learning's first chemistry
    stays the routing one - and gains the history's chemistries, counts and
    span.

    :return: True when this merge turned the key ambiguous.
    :rtype: bool
    """
    known = list(row.chemistry_keys or [])
    added = [c for c in record["chemistry_keys"] if c not in known]
    was_ambiguous = len(known) > 1
    if added:
        row.chemistry_keys = known + added
        row.n_disagreements = (row.n_disagreements or 0) + len(added)
    if len(row.chemistry_keys or []) > 1:
        row.state = "ambiguous"
    row.n_streams = (row.n_streams or 0) + record["n_streams"]
    row.first_seen = min(row.first_seen, record["first_seen"])
    row.last_seen = max(row.last_seen, record["last_seen"])
    return bool(added) and not was_ambiguous and len(row.chemistry_keys or []) > 1


async def backfill_method_bindings() -> None:
    """Read the routing history and fold the bindings it implies into the table.

    Separate from :func:`run` so that it can be exercised against a test
    database: :func:`run` configures the engine, which would point this at
    the deployment's own.
    """
    dry_run = os.environ.get("DRY_RUN") == "1"

    mode = method_binding_mode()
    if mode == "off":
        runtime.logger.warning(
            "backend.method_binding is 'off', so this deployment records no "
            "method bindings and nothing was written. Set it to 'shadow' "
            "first if you meant to backfill."
        )
        return

    records: dict[str, dict] = {}
    tally = {"observations": 0, "files": 0, "unknown": 0}
    previous: tuple[str, str] | None = None
    async for page in _history_pages():
        # Only the instruments whose reader records a census: a TofDaq h5
        # never carries one, and its signature class is its polarity, so
        # there is nothing to read off the filestore for it.
        wanted = sorted(
            {
                row["filename"]
                for row in page
                if row["instrument_type"] in CENSUS_BEARING_INSTRUMENT_TYPES
            }
        )
        previous = _fold(page, await _census(wanted), records, tally, previous)

    if not tally["observations"]:
        runtime.logger.info(
            "Auto-processing has made no ACQUISITION sample items bound to an "
            "ionization mode, so there is no routing history to learn from"
        )
        return

    runtime.logger.info(
        f"{tally['observations']} observations over {tally['files']} files "
        f"gave {len(records)} method keys"
    )
    if tally["unknown"]:
        runtime.logger.warning(
            f"{tally['unknown']} observations were skipped: their files were "
            "converted before the scan-stream census existed, so what they "
            "measured is not known and a guess would split their methods' "
            "history"
        )
    for record in sorted(records.values(), key=lambda r: -r["n_streams"])[
        :_PREVIEW_LIMIT
    ]:
        runtime.logger.info(
            f"  {record['instrument']} "
            f"{record['method_key'] or '(no method name)'} "
            f"[{record['signature_class']}] -> {record['state']}, "
            f"{record['n_streams']} observations"
        )
    if len(records) > _PREVIEW_LIMIT:
        runtime.logger.info(f"  ... and {len(records) - _PREVIEW_LIMIT} more keys")

    if dry_run:
        ambiguous = sum(1 for r in records.values() if r["state"] == "ambiguous")
        runtime.logger.info(
            f"DRY_RUN=1: {len(records)} keys would be folded in, {ambiguous} of "
            "them ambiguous (seen with more than one chemistry, so they will "
            "not route). Nothing written."
        )
        return

    counts = await _apply(records)
    runtime.logger.info(
        f"Method bindings: {counts['created']} created, {counts['merged']} "
        f"merged into rows already learned, {counts['turned_ambiguous']} of "
        "those turned ambiguous by their history"
    )


async def run() -> None:
    """Initialise the database and fold in the bindings."""
    await configure_database_engine()
    await backfill_method_bindings()


def main() -> None:
    """Entry point for ``mascope db script run``."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
