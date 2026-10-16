"""
Maintenance script to repair acquisition times that were guessed as UTC.

A file uploaded without a reported timezone has its wall clock read in the
converter's own zone, which is UTC in the production image, so at a site in
another zone such a file was stored with a zero offset: its ``datetime_utc``
is the instrument PC's wall clock labelled UTC, hours off. The web app now
reports the browser's zone (GitHub issue #2385); this script repairs the rows
stored before that, reading each wall clock in the zone the operator names.
See ``mascope_backend.db.admin.fix_guessed_datetime_utc`` for what is
selected, what is written, and what is deliberately left alone.

A dry run is the default: it reports what would change and writes nothing.
Set MASCOPE_FIX_GUESSED_UTC_APPLY=1 to write.

Required:
    MASCOPE_FIX_GUESSED_UTC_ZONE         the IANA zone of the instrument PC,
                                         e.g. Europe/Vienna
Optional:
    MASCOPE_FIX_GUESSED_UTC_INSTRUMENT   only this instrument's files
    MASCOPE_FIX_GUESSED_UTC_CREATED_MIN  only files registered at or after
                                         this ISO instant (UTC when naive)
    MASCOPE_FIX_GUESSED_UTC_CREATED_MAX  only files registered before it
    MASCOPE_FIX_GUESSED_UTC_APPLY        1 to write; anything else dry-runs

Usage:
    # report what would change
    MASCOPE_FIX_GUESSED_UTC_ZONE=Europe/Vienna \\
        mascope prod db script run fix_guessed_datetime_utc --skip-backup

    # repair one instrument's files
    MASCOPE_FIX_GUESSED_UTC_ZONE=Europe/Vienna \\
    MASCOPE_FIX_GUESSED_UTC_INSTRUMENT=orbi-1 \\
    MASCOPE_FIX_GUESSED_UTC_APPLY=1 \\
        mascope prod db script run fix_guessed_datetime_utc

    # Windows PowerShell (dev):
    $env:MASCOPE_FIX_GUESSED_UTC_ZONE="Europe/Vienna"; mascope dev db script run fix_guessed_datetime_utc

Date: 2026-10-16
"""

import asyncio
import os
from datetime import datetime

from mascope_backend.db import configure_database_engine
from mascope_backend.db.admin.fix_guessed_datetime_utc import (
    fix_guessed_datetime_utc,
)
from mascope_backend.runtime import runtime


_PREFIX = "MASCOPE_FIX_GUESSED_UTC_"


def _env(name: str) -> str | None:
    """A setting from the environment, or None when unset or blank."""
    value = os.environ.get(_PREFIX + name, "").strip()
    return value or None


def _env_datetime(name: str) -> datetime | None:
    """An ISO instant from the environment, or None when unset."""
    raw = _env(name)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as error:
        raise ValueError(
            f"{_PREFIX}{name}='{raw}' is not an ISO 8601 datetime"
        ) from error


async def run() -> None:
    """Initialize the database and run the repair as the environment asks."""
    zone = _env("ZONE")
    if zone is None:
        raise ValueError(
            f"{_PREFIX}ZONE is required: the IANA zone the instrument PC's "
            "clock was in, e.g. Europe/Vienna"
        )
    apply = (_env("APPLY") or "").lower() in {"1", "true", "yes", "on"}

    await configure_database_engine()
    result = await fix_guessed_datetime_utc(
        zone=zone,
        instrument=_env("INSTRUMENT"),
        created_min=_env_datetime("CREATED_MIN"),
        created_max=_env_datetime("CREATED_MAX"),
        dry_run=not apply,
    )

    runtime.logger.info("=" * 80)
    runtime.logger.info(
        "FIX GUESSED DATETIME_UTC " + ("DRY RUN" if result["dry_run"] else "COMPLETE")
    )
    runtime.logger.info(f"Zone                 : {result['zone']}")
    runtime.logger.info(f"Guessed as UTC       : {result['candidates']}")
    runtime.logger.info(f"Already right        : {result['unchanged']}")
    runtime.logger.info(f"Repeated/skipped hour: {result['ambiguous']}")
    runtime.logger.info(f"Updated              : {result['updated']}")
    runtime.logger.info(f"Guessed elsewhere    : {result['left_alone']} (left alone)")
    if result["dry_run"] and result["candidates"] > result["unchanged"]:
        runtime.logger.info(
            f"Nothing was written. Set {_PREFIX}APPLY=1 to apply the repair."
        )
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the guessed datetime_utc repair."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Script failed")
        raise


if __name__ == "__main__":
    main()
