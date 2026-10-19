"""
Maintenance script to repair acquisition times that were guessed as UTC.

A file uploaded without a reported timezone has its wall clock read in the
converter's own zone, which is UTC in the production image, so at a site in
another zone such a file was stored with a zero offset: its ``datetime_utc``
is the instrument PC's wall clock labelled UTC, hours off. Any upload that
reports no zone does this - the web app before it reported the browser's
zone (GitHub issue #2385), SDK scripts, File Agents older than zone
reporting - so the rows keep arriving until a site's clients all report one.
This script repairs the Orbitrap rows stored that way, reading each wall
clock in the zone the operator names. See
``mascope_backend.db.admin.fix_guessed_datetime_utc`` for what is selected,
what is written, what is deliberately left alone, and the evidence a dry run
gives about the zone named.

A dry run is the default: it reports what would change and writes nothing.
Set MASCOPE_FIX_GUESSED_UTC_APPLY=1 to write. An apply writes a ledger of
every row it changed (old and new value) under the environment's
``repairs/`` folder and says where.

Required:
    MASCOPE_FIX_GUESSED_UTC_ZONE         the IANA zone the instrument PC's
                                         clock is set to, e.g. Europe/Vienna
                                         (not where the site is; a PC set to
                                         UTC needs no repair)
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

The variables are read by their full names below, one literal each, so that
the CLI's guard (``test_every_env_var_the_db_scripts_read_is_forwarded``)
can see them: ``mascope prod db script run`` forwards into the backend
container only the names it knows.

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


def _blank_to_none(value: str) -> str | None:
    """A setting as read, or None when unset or blank."""
    value = value.strip()
    return value or None


def _iso_or_none(name: str, value: str) -> datetime | None:
    """An ISO instant from a setting, or None when unset."""
    raw = _blank_to_none(value)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as error:
        raise ValueError(f"{name}='{raw}' is not an ISO 8601 datetime") from error


async def run() -> None:
    """Initialize the database and run the repair as the environment asks."""
    zone = _blank_to_none(os.environ.get("MASCOPE_FIX_GUESSED_UTC_ZONE", ""))
    if zone is None:
        raise ValueError(
            "MASCOPE_FIX_GUESSED_UTC_ZONE is required: the IANA zone the "
            "instrument PC's clock is set to, e.g. Europe/Vienna"
        )
    instrument = _blank_to_none(
        os.environ.get("MASCOPE_FIX_GUESSED_UTC_INSTRUMENT", "")
    )
    created_min = _iso_or_none(
        "MASCOPE_FIX_GUESSED_UTC_CREATED_MIN",
        os.environ.get("MASCOPE_FIX_GUESSED_UTC_CREATED_MIN", ""),
    )
    created_max = _iso_or_none(
        "MASCOPE_FIX_GUESSED_UTC_CREATED_MAX",
        os.environ.get("MASCOPE_FIX_GUESSED_UTC_CREATED_MAX", ""),
    )
    apply = os.environ.get("MASCOPE_FIX_GUESSED_UTC_APPLY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    await configure_database_engine()
    result = await fix_guessed_datetime_utc(
        zone=zone,
        instrument=instrument,
        created_min=created_min,
        created_max=created_max,
        dry_run=not apply,
    )

    runtime.logger.info("=" * 80)
    runtime.logger.info(
        "FIX GUESSED DATETIME_UTC " + ("DRY RUN" if result["dry_run"] else "COMPLETE")
    )
    runtime.logger.info(f"Zone                     : {result['zone']}")
    runtime.logger.info(f"Orbitrap rows guessed as UTC: {result['candidates']}")
    runtime.logger.info(f"Already right in that zone  : {result['unchanged']}")
    runtime.logger.info(f"In a repeated/skipped hour  : {result['ambiguous']}")
    runtime.logger.info(
        "End after own registration  : "
        f"{result['impossible_before']} as stored, "
        f"{result['impossible_after']} after the repair "
        f"(of {result['with_created']} with a registration time)"
    )
    runtime.logger.info(f"Updated                     : {result['updated']}")
    runtime.logger.info(f"Instrument functions moved  : {result['functions_updated']}")
    runtime.logger.info(
        f"Left alone                  : {result['left_alone']} "
        "(guessed in a real zone, or repaired by an earlier run)"
    )
    if result["outside_window"]:
        runtime.logger.info(
            f"Outside the window          : {result['outside_window']} "
            "(no registration time, so no window can hold them)"
        )
    if result["ledger"]:
        runtime.logger.info(f"Ledger                      : {result['ledger']}")
    if result["dry_run"] and result["candidates"] > result["unchanged"]:
        runtime.logger.info(
            "Nothing was written. Set MASCOPE_FIX_GUESSED_UTC_APPLY=1 to apply "
            "the repair."
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
