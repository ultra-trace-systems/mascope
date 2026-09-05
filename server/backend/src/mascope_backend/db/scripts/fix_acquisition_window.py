"""
Maintenance script to widen ACQUISITION sample-item windows that stop short of
the acquisition they describe.

``t0``/``t1`` used to be derived from the TIC, which reports MS1 scans only. On
an acquisition whose MS2 scans are recorded as their own block -- a manual MS2
run records MS1 first and the fragmentation afterwards, rather than
interleaving them the way data-dependent acquisition does -- the window ended
before the first MS2 scan, and every endpoint that selects within ``[t0, t1]``
found no MS2 data at all. Ingestion now spans every scan type; this repairs the
rows written before that.

Candidates are found in SQL by ``sample_item.t1 < sample_file.length``, the
window ending before the file does, and each candidate's window is then
recomputed from the file itself. Only ACQUISITION items are touched: any other
sample item is a deliberately chosen slice of the file, and widening it would
silently change what the user asked for.

Set DRY_RUN=1 to report what would change without writing.

Usage:
    mascope dev db script run fix_acquisition_window
    mascope prod db script run fix_acquisition_window

Date: 2026-09-05
"""

import asyncio
import os

from sqlalchemy import text

import mascope_signal.compute as m_compute
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.runtime import runtime


# Rows whose window is short by less than this are left alone: a scan time is a
# float that has been through a filestore round trip, and an acquisition missing
# a fraction of a second at the end has nothing to recover.
_TOLERANCE_S = 1e-6

# Maximum number of repaired rows to log individually.
_PREVIEW_LIMIT = 20


async def _candidates() -> list[dict]:
    """ACQUISITION items whose window ends before their file does.

    :return: One dict per candidate with its ids, filename, polarity, window.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT si.sample_item_id,
                       si.polarity,
                       si.t0,
                       si.t1,
                       sf.filename,
                       sf.length
                FROM sample_item si
                JOIN sample_file sf ON sf.sample_file_id = si.sample_file_id
                WHERE si.sample_item_type = 'ACQUISITION'
                  AND si.t1 IS NOT NULL
                  AND sf.length IS NOT NULL
                  AND si.t1 < sf.length - :tol
                ORDER BY sf.datetime
            """),
            {"tol": _TOLERANCE_S},
        )
        return [dict(row._mapping) for row in result]


def _recompute(candidate: dict) -> tuple[float, float] | None:
    """Read the true acquisition window for one candidate off its file.

    A file that has since been removed from the filestore, or that the reader
    cannot open, is reported and skipped -- a repair script must not fail a
    whole run over one unreadable acquisition.

    :param candidate: Row from :func:`_candidates`.
    :return: ``(t0, t1)`` in seconds, or None when the file cannot be read.
    :rtype: tuple[float, float] | None
    """
    try:
        return m_compute.get_acquisition_window(
            base_filename=candidate["filename"],
            polarity=candidate["polarity"],
        )
    except Exception as exc:  # noqa: BLE001
        runtime.logger.warning(
            f"  Cannot read {candidate['filename']} "
            f"(polarity {candidate['polarity']}): {exc}"
        )
        return None


async def _apply(updates: list[dict]) -> int:
    """Write the recomputed windows back.

    :param updates: ``{"sample_item_id", "t0", "t1"}`` per row to update.
    :return: Number of rows updated.
    :rtype: int
    """
    if not updates:
        return 0
    async with async_session() as session:
        result = await session.execute(
            text("""
                UPDATE sample_item
                SET t0 = :t0,
                    t1 = :t1,
                    sample_item_utc_modified = NOW() AT TIME ZONE 'UTC'
                WHERE sample_item_id = :sample_item_id
            """),
            updates,
        )
        await session.commit()
        # executemany reports -1 for rowcount on some drivers, so fall back to
        # the number of statements sent rather than logging a nonsense count.
        return (
            result.rowcount if result.rowcount and result.rowcount > 0 else len(updates)
        )


async def run() -> None:
    """Find, recompute and repair short ACQUISITION windows."""
    await configure_database_engine()
    dry_run = os.environ.get("DRY_RUN") == "1"

    candidates = await _candidates()
    if not candidates:
        runtime.logger.info("No ACQUISITION windows end before their file does.")
        return

    runtime.logger.info(f"Candidates whose window ends early: {len(candidates)}")

    updates: list[dict] = []
    unchanged = 0
    unreadable = 0
    for candidate in candidates:
        window = await asyncio.to_thread(_recompute, candidate)
        if window is None:
            unreadable += 1
            continue
        t0, t1 = window
        if (
            t1 - candidate["t1"] <= _TOLERANCE_S
            and candidate["t0"] - t0 <= _TOLERANCE_S
        ):
            # A window can legitimately stop before the file ends -- the other
            # polarity of a dual-polarity acquisition runs on after this one.
            unchanged += 1
            continue
        if len(updates) < _PREVIEW_LIMIT:
            runtime.logger.info(
                f"  {candidate['filename']} ({candidate['polarity']}): "
                f"[{candidate['t0']:.3f}, {candidate['t1']:.3f}] -> "
                f"[{t0:.3f}, {t1:.3f}] s"
            )
        updates.append(
            {"sample_item_id": candidate["sample_item_id"], "t0": t0, "t1": t1}
        )

    if len(updates) > _PREVIEW_LIMIT:
        runtime.logger.info(f"  ... and {len(updates) - _PREVIEW_LIMIT} more")

    updated = 0
    if dry_run:
        runtime.logger.info(f"DRY_RUN=1: {len(updates)} rows would be updated.")
    else:
        updated = await _apply(updates)

    runtime.logger.info("=" * 80)
    runtime.logger.info("FIX ACQUISITION WINDOW COMPLETE")
    runtime.logger.info(
        f"Candidates: {len(candidates)}, updated: {updated}, "
        f"already correct: {unchanged}, unreadable: {unreadable}"
    )
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the acquisition-window fix script."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Script failed")
        raise


if __name__ == "__main__":
    main()
