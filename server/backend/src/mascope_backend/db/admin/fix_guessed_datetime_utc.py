"""
Database administration operation: repair ``datetime_utc`` on sample files
whose UTC offset was guessed as zero while the instrument PC kept another zone.

A raw file records its acquisition time as the instrument PC's wall clock,
with no offset. The converter places it in UTC with the zone the upload
reported (``utc_offset_source = 'agent'``), else with its own zone
(``'guess'``). The production image runs on UTC, and until the web app
reported the browser's zone (GitHub issue #2385) a browser upload reported
none, so every such file at a site east or west of UTC was stored with an
offset of zero: ``datetime_utc`` is the wall clock read as UTC, wrong by the
site's offset at that instant, and the ingest of the file appears to precede
its acquisition.

Detection: ``utc_offset_source = 'guess'`` and ``acquisition_timezone IS
NULL`` and ``datetime_utc`` equal to ``datetime`` read as UTC (within a
second). A guessed row whose stored offset is *not* zero was guessed in a
real zone - a development server, or the Helsinki-era image - and is left
alone and counted, since it needs a different repair.

Fix: ``datetime_utc`` becomes the wall clock read in the zone the operator
names, resolved the way the converter resolves it (``fold=0``: the first pass
of a repeated hour, the pre-transition offset for a skipped one, see
``resolve_wall_time_offset`` in the converter), so the row ends up as it
would have had the upload reported that zone. Rows whose wall clock falls in
such an hour are reported.

The two source columns are left as they are. ``acquisition_timezone`` records
the zone the upload reported, and none was; ``utc_offset_source`` records
what decided the offset at conversion, and that was a guess. Recording the
repair there would need a value the column's contract does not have, which
is a call for the plan owner (noted on #2385).

Entry points:
- Async: ``fix_guessed_datetime_utc()``
- CLI: ``mascope dev db script run fix_guessed_datetime_utc`` (and ``prod``)
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from mascope_backend.db import async_session
from mascope_backend.runtime import runtime


#: Rows logged in full before anything is changed, so an operator can check
#: the correction on real data before running without ``dry_run``.
PREVIEW_ROWS = 20

#: Rows per UPDATE statement, so a large backlog does not become one request
#: with hundreds of thousands of parameters.
UPDATE_CHUNK = 1000

#: The rows this repair is for, within whatever scope the caller adds: guessed
#: with no reported zone, and stored with a zero offset.
_GUESSED_AS_UTC = """
    utc_offset_source = 'guess'
    AND acquisition_timezone IS NULL
    AND abs(extract(epoch from ((datetime AT TIME ZONE 'UTC') - datetime_utc))) < 1
"""

#: Guessed with no reported zone, but in a zone that was not UTC.
_GUESSED_ELSEWHERE = """
    utc_offset_source = 'guess'
    AND acquisition_timezone IS NULL
    AND abs(extract(epoch from ((datetime AT TIME ZONE 'UTC') - datetime_utc))) >= 1
"""


def wall_clock_as_utc(local_dt: datetime, zone: ZoneInfo) -> tuple[datetime, bool]:
    """Read a naive wall clock in ``zone`` as the converter would.

    ``fold=0`` in both directions: the first pass of a repeated hour, and for
    a skipped hour the offset the clock still had before the jump. This is
    the rule ``resolve_wall_time_offset`` in the converter applies, so the
    repaired value is the one the converter would have stored had the upload
    reported the zone.

    :param local_dt: The wall clock as the instrument recorded it, naive.
    :type local_dt: datetime
    :param zone: The zone to read it in.
    :type zone: ZoneInfo
    :return: The UTC instant, and whether the wall clock named two instants
        or none, so that the choice could have gone the other way.
    :rtype: tuple[datetime, bool]
    """
    first = local_dt.replace(tzinfo=zone, fold=0).astimezone(timezone.utc)
    second = local_dt.replace(tzinfo=zone, fold=1).astimezone(timezone.utc)
    return first, first != second


def _as_utc_bound(moment: datetime | None) -> datetime | None:
    """A scope bound as an aware UTC instant; a naive one is read as UTC."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=timezone.utc)


def _scope(
    instrument: str | None,
    created_min: datetime | None,
    created_max: datetime | None,
) -> tuple[str, dict]:
    """The caller's narrowing of the repair, as SQL conditions and their params."""
    clauses: list[str] = []
    params: dict = {}
    if instrument is not None:
        clauses.append("AND instrument = :instrument")
        params["instrument"] = instrument
    if created_min is not None:
        clauses.append("AND sample_file_utc_created >= :created_min")
        params["created_min"] = _as_utc_bound(created_min)
    if created_max is not None:
        clauses.append("AND sample_file_utc_created < :created_max")
        params["created_max"] = _as_utc_bound(created_max)
    return "\n".join(clauses), params


async def fix_guessed_datetime_utc(
    zone: str,
    instrument: str | None = None,
    created_min: datetime | None = None,
    created_max: datetime | None = None,
    dry_run: bool = True,
) -> dict:
    """Repair ``datetime_utc`` on rows that were guessed as UTC, in ``zone``.

    Selects the rows the module docstring describes, within the scope the
    other parameters narrow it to, computes each one's correct UTC instant in
    Python, and writes the ones that change. A row whose corrected value is
    the one already stored - a zone at UTC for that part of the year - is
    counted as unchanged and not written, which also makes a second run with
    the same zone a no-op. Rows guessed in a real zone (a non-zero stored
    offset) are never touched; their count is reported so nobody takes a
    clean run for a complete one.

    :param zone: The IANA zone the instrument PC's clock was in, for every
        row in scope. Checked here, before anything is read.
    :type zone: str
    :param instrument: Only this instrument's files; every instrument when
        omitted. Use it where a server holds instruments in several zones.
    :type instrument: str | None
    :param created_min: Only files registered at or after this instant
        (``sample_file_utc_created``); a naive value is read as UTC.
    :type created_min: datetime | None
    :param created_max: Only files registered before this instant.
    :type created_max: datetime | None
    :param dry_run: Report what would change and change nothing. The
        default: a repair in the wrong zone is as wrong as the guess was.
    :type dry_run: bool
    :return: ``{"zone", "dry_run", "candidates", "unchanged", "ambiguous",
        "updated", "left_alone", "previewed"}`` - the rows in scope that
        were guessed as UTC, how many of them already hold the right value,
        how many fall in a repeated or skipped hour, how many were written,
        how many guessed rows were left alone for being guessed in another
        zone, and the first rows with their old and new values.
    :rtype: dict
    :raises ValueError: ``zone`` is not a zone this machine knows.
    """
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
        raise ValueError(f"'{zone}' is not a known IANA timezone") from error

    scope_sql, params = _scope(instrument, created_min, created_max)
    runtime.logger.info(
        f"Repairing acquisition times guessed as UTC, reading them in {zone}"
        + (f" for instrument {instrument}" if instrument else "")
        + (" (dry run)" if dry_run else "")
    )

    async with async_session() as session:
        left_alone = (
            await session.execute(
                text(f"""
                    SELECT count(*) FROM sample_file
                    WHERE {_GUESSED_ELSEWHERE}
                    {scope_sql}
                """),
                params,
            )
        ).scalar_one()
        if left_alone:
            runtime.logger.warning(
                f"{left_alone} guessed row(s) in scope were stored with a "
                "non-zero offset, so they were guessed in a real zone; this "
                "repair is not for them and leaves them alone."
            )

        rows = (
            await session.execute(
                text(f"""
                    SELECT sample_file_id, filename, datetime, datetime_utc
                    FROM sample_file
                    WHERE {_GUESSED_AS_UTC}
                    {scope_sql}
                    ORDER BY datetime
                """),
                params,
            )
        ).all()

        corrections: list[dict] = []
        previewed: list[dict] = []
        unchanged = 0
        ambiguous = 0
        for sample_file_id, filename, local_dt, stored_utc in rows:
            corrected, uncertain = wall_clock_as_utc(local_dt, tz)
            if uncertain:
                ambiguous += 1
                runtime.logger.warning(
                    f"  {filename}: {local_dt.isoformat()} names two instants "
                    f"or none in {zone}; using {corrected.isoformat()}, the "
                    "converter's choice, which may be an hour off"
                )
            if corrected == stored_utc:
                unchanged += 1
                continue
            corrections.append(
                {"sample_file_id": sample_file_id, "corrected": corrected}
            )
            if len(previewed) < PREVIEW_ROWS:
                previewed.append(
                    {
                        "sample_file_id": sample_file_id,
                        "filename": filename,
                        "datetime": local_dt,
                        "datetime_utc": stored_utc,
                        "corrected_utc": corrected,
                    }
                )

        for row in previewed:
            runtime.logger.info(
                f"  {row['filename']}: datetime={row['datetime'].isoformat()}  "
                f"datetime_utc={row['datetime_utc'].isoformat()}  ->  "
                f"{row['corrected_utc'].isoformat()}"
            )
        runtime.logger.info(
            f"{len(rows)} row(s) guessed as UTC in scope: {len(corrections)} to "
            f"correct, {unchanged} already right in {zone}, {ambiguous} in a "
            "repeated or skipped hour"
        )

        updated = 0
        if not dry_run:
            for start in range(0, len(corrections), UPDATE_CHUNK):
                chunk = corrections[start : start + UPDATE_CHUNK]
                result = await session.execute(
                    # The selection is re-checked row by row, so a file that
                    # was re-processed between the read and the write - its
                    # offset then resolved afresh - is not overwritten.
                    text(f"""
                        UPDATE sample_file AS f
                        SET datetime_utc = v.corrected
                        FROM unnest(
                            CAST(:ids AS text[]), CAST(:corrected AS timestamptz[])
                        ) AS v(sample_file_id, corrected)
                        WHERE f.sample_file_id = v.sample_file_id
                          AND {_GUESSED_AS_UTC}
                    """),
                    {
                        "ids": [c["sample_file_id"] for c in chunk],
                        "corrected": [c["corrected"] for c in chunk],
                    },
                )
                updated += result.rowcount
            await session.commit()
            runtime.logger.info(f"Updated {updated} row(s)")
        elif corrections:
            runtime.logger.info(
                f"Dry run: {len(corrections)} row(s) would be updated; nothing "
                "was written"
            )

    return {
        "zone": zone,
        "dry_run": dry_run,
        "candidates": len(rows),
        "unchanged": unchanged,
        "ambiguous": ambiguous,
        "updated": updated,
        "left_alone": left_alone,
        "previewed": previewed,
    }
