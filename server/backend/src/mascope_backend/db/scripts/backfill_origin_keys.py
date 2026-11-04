"""
Give the copies made before the origin key one, where their window says which
part of the acquisition record they are.

A batch refreshed from its filter tells what it already holds by its copies'
origin keys, and copies made before the key have none. This gives one to every
copy whose window is still an acquisition sample's of its file and polarity,
and counts the copies it leaves without: those with an edited window, and
those whose file and polarity have no acquisition sample. Offered, not run on
upgrade: a copy without a key is what every copy was before.

Usage:
    DRY_RUN=1 mascope dev db script run backfill_origin_keys   # count only
    mascope dev db script run backfill_origin_keys
    DRY_RUN=1 mascope prod db script run backfill_origin_keys
    mascope prod db script run backfill_origin_keys

Environment toggles:
- DRY_RUN=1        : count what it would set and skip, write nothing
- BATCH_SIZE=<n>   : copies written per committed page (default 5000)

Date: 2026-11-04
"""

import asyncio
import os

from mascope_backend.db import configure_database_engine
from mascope_backend.db.admin.sample_item.backfill_origin_keys import (
    DEFAULT_BATCH_SIZE,
    backfill_origin_keys,
)
from mascope_backend.runtime import runtime


async def run_backfill() -> None:
    """Initialize the database and give the copies their keys, logging a summary.

    Honors environment toggles:
    - DRY_RUN=1      : count only, make no changes
    - BATCH_SIZE=<n> : copies per committed page
    """
    await configure_database_engine()

    dry_run = os.environ.get("DRY_RUN") == "1"
    try:
        batch_size = int(os.environ.get("BATCH_SIZE", DEFAULT_BATCH_SIZE))
    except ValueError:
        runtime.logger.error("BATCH_SIZE must be a positive integer.")
        raise

    result = await backfill_origin_keys(dry_run=dry_run, batch_size=batch_size)

    runtime.logger.info("=" * 80)
    runtime.logger.info("BACKFILL ORIGIN KEYS COMPLETE")
    runtime.logger.info(f"Status: {result['status']}")
    runtime.logger.info(result["message"])
    runtime.logger.info(
        f"Would set={result['set']}, set={result['updated']}, "
        f"skipped: edited window={result['edited']}, "
        f"no acquisition sample={result['no_source']}"
    )
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the backfill script."""
    try:
        asyncio.run(run_backfill())
    except KeyboardInterrupt:
        runtime.logger.info("Backfill cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Origin key backfill failed")
        raise


if __name__ == "__main__":
    main()
