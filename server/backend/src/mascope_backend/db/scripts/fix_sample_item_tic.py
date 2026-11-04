"""
Maintenance script to recompute the TIC of every sample item as the mean TIC
per scan.

``sample_item.tic`` used to be the sum of the TIC of every scan an item
covers, so it grew with the item's length, while the item's spectrum is
averaged over the same scans. A new item now stores the mean
(``_sample_item_tic`` in the sample items controller), but the TIC is
computed only when an item is created without one, and a copied item carries
the stored value: every row written before the change keeps the sum until
this rewrites it.

Each value is read again from the item's file with the rule a new item gets,
the function itself and not a copy of it. Dividing the stored sum by a scan
count would not give the same number: the reader leaves out an outlying
first scan, and a stitched polarity reads each stream's own scans.

The rule reads a whole polarity of a file, whatever window an item spans, so
it runs once per file and polarity and writes the result to every item of
that pair, copies in other workspaces included. Items with no TIC are left
alone: ingestion fills them in. Each file's rows are committed as they are
read, so an interrupted run keeps what it wrote; running it again reads every
file again and writes the same values.

Set DRY_RUN=1 to report what would change without writing.

Usage:
    DRY_RUN=1 mascope dev db script run fix_sample_item_tic
    mascope dev db script run fix_sample_item_tic
    mascope prod db script run fix_sample_item_tic

Date: 2026-11-03
"""

import asyncio
import math
import os

from sqlalchemy import text

from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    _sample_item_tic,
)
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.runtime import runtime


# Maximum number of changed files to log individually.
_PREVIEW_LIMIT = 20


async def _targets() -> list[dict]:
    """Each file and polarity whose items store a TIC.

    :return: One dict per pair: ``filename``, ``polarity``, the number of
        ``items`` and the range of their stored TIC.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT sf.filename,
                       si.polarity,
                       COUNT(*) AS items,
                       MIN(si.tic) AS stored_min,
                       MAX(si.tic) AS stored_max
                FROM sample_item si
                JOIN sample_file sf ON sf.sample_file_id = si.sample_file_id
                WHERE si.tic IS NOT NULL
                GROUP BY sf.filename, si.polarity
                ORDER BY sf.filename, si.polarity
            """)
        )
        return [dict(row._mapping) for row in result]


async def _recompute(target: dict) -> float | None:
    """The mean TIC per scan of one file and polarity, read off the file.

    A file that has since been removed from the filestore, or that the reader
    cannot open, is reported and skipped - a repair script must not fail a
    whole run over one unreadable file.

    :param target: Row from :func:`_targets`.
    :return: The TIC, or None when the file cannot be read.
    :rtype: float | None
    """
    try:
        tic = await _sample_item_tic(
            filename=target["filename"], polarity=target["polarity"]
        )
    except Exception as exc:  # noqa: BLE001
        # INFO per file: run() raises one summary WARNING for the lot, so a
        # server missing many raw files does not report one event per file.
        runtime.logger.info(
            f"  Cannot read {target['filename']} (polarity {target['polarity']}): {exc}"
        )
        return None
    if not math.isfinite(tic):
        runtime.logger.info(
            f"  {target['filename']} (polarity {target['polarity']}): "
            f"the TIC reads as {tic}"
        )
        return None
    return tic


async def _apply(target: dict, tic: float) -> int:
    """Write one file and polarity's TIC to all its items.

    :param target: Row from :func:`_targets`.
    :param tic: The recomputed TIC.
    :return: Number of rows updated.
    :rtype: int
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                UPDATE sample_item si
                SET tic = :tic,
                    sample_item_utc_modified = NOW()
                FROM sample_file sf
                WHERE sf.sample_file_id = si.sample_file_id
                  AND sf.filename = :filename
                  AND si.polarity IS NOT DISTINCT FROM :polarity
                  AND si.tic IS NOT NULL
            """),
            {
                "tic": tic,
                "filename": target["filename"],
                "polarity": target["polarity"],
            },
        )
        await session.commit()
        return result.rowcount


async def run() -> None:
    """Recompute and write the TIC of every sample item that stores one."""
    await configure_database_engine()
    dry_run = os.environ.get("DRY_RUN") == "1"

    targets = await _targets()
    if not targets:
        runtime.logger.info("No sample item stores a TIC.")
        return

    runtime.logger.info(f"Files and polarities to read: {len(targets)}")

    updated = 0
    items = 0
    unreadable = 0
    for done, target in enumerate(targets):
        tic = await _recompute(target)
        if tic is None:
            unreadable += 1
            continue
        if done < _PREVIEW_LIMIT:
            runtime.logger.info(
                f"  {target['filename']} ({target['polarity']}), "
                f"{target['items']} items: {target['stored_max']:.6g} -> {tic:.6g}"
            )
        items += target["items"]
        if not dry_run:
            updated += await _apply(target, tic)

    if len(targets) > _PREVIEW_LIMIT:
        runtime.logger.info(f"  ... and {len(targets) - _PREVIEW_LIMIT} more")

    runtime.logger.info("=" * 80)
    runtime.logger.info("FIX SAMPLE ITEM TIC COMPLETE")
    if dry_run:
        runtime.logger.info(f"DRY_RUN=1: {items} items would be updated.")
    runtime.logger.info(
        f"Files and polarities: {len(targets)}, items updated: {updated}, "
        f"unreadable: {unreadable}"
    )
    runtime.logger.info("=" * 80)
    if unreadable:
        runtime.logger.warning(
            f"Sample item TIC fix could not read {unreadable} files; "
            "see the INFO lines above"
        )


def main() -> None:
    """Entry point for the sample item TIC fix script."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Script failed")
        raise


if __name__ == "__main__":
    main()
