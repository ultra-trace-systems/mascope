"""
Maintenance script to learn method bindings from files already routed.

A ``method_binding`` row records which chemistry an acquisition method has
been seen running, so that a later file of the same method can route without
a filename token (``docs/dev/ingest_routing_and_splitting.md``, section 5.3).
Rows are normally learned as files arrive, which means a server that installs
the feature knows nothing until its next few weeks of uploads. It already has
the evidence: every ACQUISITION sample item says which mode a file of a known
method and polarity bound to.

This reads that history and writes the bindings it implies. Idempotent: a key
already present is folded into rather than duplicated, so a second run after
more ingest adds only what is new.

**It reads each file's ``.props``**, because the signature class comes from
the scan-stream census and nothing else may be substituted for it. A binding
built from a different source of signature would key the same method twice
and split its history, which is worse than a missing binding. Files whose
props cannot be read fall back to the polarity and mass range in the
database, exactly as ingest does for a file that carries no census.

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

from mascope_backend.api.controllers.sample.files.process.status import (
    read_scan_streams,
)
from mascope_backend.db import MethodBinding, async_session, configure_database_engine
from mascope_backend.db.id import gen_id
from mascope_backend.method_keys import (
    binding_digest,
    chemistry_key,
    method_key,
    signature_class,
)
from mascope_backend.runtime import runtime


#: Files whose props are read at once. The read is a small local JSON load,
#: so the limit is there to bound open descriptors rather than for speed.
_PROPS_CONCURRENCY = 16

#: Files per database page.
_PAGE = 2000

#: Keys listed individually in the report.
_PREVIEW_LIMIT = 20


async def _files() -> list[dict]:
    """Every file with an ACQUISITION item bound to a mode, oldest first.

    Oldest first so that ``first_seen`` and ``last_seen`` mean what they say,
    and so that the chemistry a key is recorded under is the one it was seen
    with first - the same order live learning would have seen them in.

    One row per (file, mode): a dual-polarity file contributes two, which is
    one observation per polarity, as ingest records.

    :return: One dict per (file, mode), in acquisition order.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT DISTINCT
                    sf.filename         AS filename,
                    sf.instrument       AS instrument,
                    sf.method_file      AS method_file,
                    sf."range"          AS mz_range,
                    sf.datetime_utc     AS datetime_utc,
                    im.ionization_mode_id           AS mode_id,
                    im.ionization_mode_polarity     AS polarity,
                    im.ionization_mechanism_ids     AS mechanism_ids
                FROM sample_item si
                JOIN sample_batch sb
                  ON sb.sample_batch_id = si.sample_batch_id
                JOIN sample_file sf
                  ON sf.sample_file_id = si.sample_file_id
                JOIN ionization_mode im
                  ON im.ionization_mode_id = si.ionization_mode_id
                WHERE sb.sample_batch_type = 'ACQUISITION'
                  AND si.ionization_mode_id IS NOT NULL
                ORDER BY sf.datetime_utc, sf.filename
            """)
        )
        return [dict(row) for row in result.mappings().all()]


async def _census(filenames: list[str]) -> dict[str, list[dict]]:
    """The scan-stream census of each file, read a bounded number at a time."""
    gate = asyncio.Semaphore(_PROPS_CONCURRENCY)

    async def one(name: str) -> tuple[str, list[dict]]:
        async with gate:
            return name, await read_scan_streams(name)

    return dict(await asyncio.gather(*(one(name) for name in filenames)))


def _fold(observations: list[dict], census: dict[str, list[dict]]) -> dict[str, dict]:
    """Turn the history into one record per binding key.

    The same rules live learning applies: the chemistry first seen is the one
    the row points at, a second chemistry marks the key ambiguous and counts a
    disagreement, and the row is never repointed by one.

    :param observations: Rows from :func:`_files`, oldest first.
    :param census: Each file's scan streams, by filename.
    :return: One record per binding key.
    :rtype: dict[str, dict]
    """
    records: dict[str, dict] = {}
    for row in observations:
        key = method_key(row["method_file"])
        signature = signature_class(
            census.get(row["filename"]), row["polarity"], row["mz_range"]
        )
        digest = binding_digest(row["instrument"], key, signature)
        chemistry = chemistry_key(row["mechanism_ids"])
        seen = row["datetime_utc"] or datetime.now(timezone.utc)

        record = records.get(digest)
        if record is None:
            records[digest] = {
                "binding_key": digest,
                "instrument": row["instrument"],
                "method_key": key,
                "signature_class": signature,
                "ionization_mode_id": row["mode_id"],
                "chemistry_keys": [chemistry],
                "state": "learned",
                "source": "history",
                "first_seen": seen,
                "last_seen": seen,
                "n_streams": 1,
                "n_disagreements": 0,
            }
            continue

        record["last_seen"] = max(record["last_seen"], seen)
        record["n_streams"] += 1
        if chemistry not in record["chemistry_keys"]:
            record["chemistry_keys"].append(chemistry)
            record["n_disagreements"] += 1
            record["state"] = "ambiguous"
    return records


async def _existing(digests: list[str]) -> set[str]:
    """Binding keys already in the table."""
    if not digests:
        return set()
    found: set[str] = set()
    async with async_session() as session:
        for start in range(0, len(digests), _PAGE):
            chunk = digests[start : start + _PAGE]
            rows = await session.execute(
                select(MethodBinding.binding_key).where(
                    MethodBinding.binding_key.in_(chunk)
                )
            )
            found.update(rows.scalars().all())
    return found


async def run() -> None:
    """Read the routing history and write the bindings it implies."""
    dry_run = os.environ.get("DRY_RUN") == "1"
    configure_database_engine()

    observations = await _files()
    if not observations:
        runtime.logger.info(
            "No ACQUISITION sample items are bound to an ionization mode, so "
            "there is no routing history to learn from"
        )
        return
    runtime.logger.info(
        f"Reading the scan-stream census of "
        f"{len({row['filename'] for row in observations})} files"
    )
    census = await _census(sorted({row["filename"] for row in observations}))

    records = _fold(observations, census)
    present = await _existing(list(records))
    new = [record for digest, record in records.items() if digest not in present]
    ambiguous = sum(1 for record in new if record["state"] == "ambiguous")

    runtime.logger.info(
        f"{len(observations)} observations over {len(records)} method keys: "
        f"{len(new)} to write, {len(records) - len(new)} already recorded, "
        f"{ambiguous} of the new ones ambiguous (seen with more than one "
        "chemistry, so they will not route)"
    )
    for record in new[:_PREVIEW_LIMIT]:
        runtime.logger.info(
            f"  {record['instrument']} "
            f"{record['method_key'] or '(no method name)'} "
            f"[{record['signature_class']}] -> {record['state']}, "
            f"{record['n_streams']} observations"
        )
    if len(new) > _PREVIEW_LIMIT:
        runtime.logger.info(f"  ... and {len(new) - _PREVIEW_LIMIT} more")

    if dry_run:
        runtime.logger.info("DRY_RUN=1, nothing written")
        return
    if not new:
        return

    async with async_session() as session:
        session.add_all(
            [MethodBinding(method_binding_id=gen_id(), **record) for record in new]
        )
        await session.commit()
    runtime.logger.info(f"Wrote {len(new)} method bindings")


def main() -> None:
    """Entry point for ``mascope db script run``."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
