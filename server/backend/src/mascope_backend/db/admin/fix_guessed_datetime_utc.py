"""
Database administration operation: repair ``datetime_utc`` on Orbitrap sample
files whose UTC offset was guessed as zero while the instrument PC's clock
was set to another zone.

A raw file records its acquisition time as the instrument PC's wall clock,
with no offset. The converter places it in UTC with the zone the upload
reported (``utc_offset_source = 'agent'``, or ``'browser'`` for a web
upload), else with its own zone (``'guess'``). The production image runs on
UTC, so every upload that reported no zone - the web app before it reported
the browser's zone (GitHub issue #2385), SDK scripts, File Agents older than
zone reporting - stored its file with an offset of zero: ``datetime_utc`` is
the wall clock read as UTC, wrong by the offset of the zone the PC's clock
was set to, and the ingest of the file appears to precede its acquisition.
Such rows keep arriving until a site's clients all report a zone, so a run
repairs what is there and a later run picks up what came after.

Detection: ``instrument_type = 'orbi'``, ``utc_offset_source = 'guess'``,
``acquisition_timezone IS NULL``, and ``datetime_utc`` equal to ``datetime``
read as UTC (within a second). Only Orbitrap rows: a TOF file's ``datetime``
is not a wall clock read off the instrument but the acquisition's UTC
instant plus the guessed offset, so with a guess of zero its ``datetime_utc``
is already right and it is its local-time column that is off - the opposite
repair, which this script does not make. The same holds for the rare
Orbitrap file whose reader surfaced no creation date, dated from the file's
modification time instead; nothing on the row tells it apart, and a run
over a site with such files moves them wrongly.

A guessed row whose stored offset is *not* zero was either guessed in a real
zone - a development server, say - or repaired by an earlier run of this
script, which leaves the two source columns as they were. Both are left
alone and counted. Rows that predate the ``utc_offset_source`` column have
NULL there and are outside the selection altogether.

A guessed zero offset is also simply correct wherever the instrument PC's
clock is set to UTC. Nothing on the row tells that from a wrong guess, so
the zone to name is the one the PC's clock is set to, not the one the site
is in, and the dry run gives evidence about it: how many rows end after
their own registration (``datetime_utc + length > sample_file_utc_created``)
as stored, and how many would after the repair. A wall clock stored as UTC
east of UTC shows as rows that end after they were registered, which the
repair clears; rows that are already right show none.

Fix: ``datetime_utc`` becomes the wall clock read in the zone named,
resolved the way the converter resolves it (``fold=0``: the first pass of a
repeated hour, the pre-transition offset for a skipped one, see
``resolve_wall_time_offset`` in the converter), so the row ends up as it
would have had the upload reported that zone. The instrument function the
converter fitted from the file carries the same guessed instant, and is
moved with it when its ``datetime_utc`` is the file's old value. Every row
an apply changes is written, old and new value, to a ledger file under the
environment's ``repairs/`` folder before the change is committed, so a
repair made in the wrong zone can be found and undone row by row.

The two source columns are left as they are. ``acquisition_timezone``
records the zone the upload reported, and none was; ``utc_offset_source``
records what decided the offset at conversion, and that was a guess.
Recording the repair there would need a value the column's contract does
not have, which is a call for the plan owner (noted on #2385); the ledger
is what keeps the repair auditable meanwhile.

Entry points:
- Async: ``fix_guessed_datetime_utc()``
- CLI: ``mascope dev db script run fix_guessed_datetime_utc`` (and ``prod``)
"""

import json
import os
from datetime import datetime, timedelta, timezone
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

#: Where an apply's ledger goes when the caller names no folder: beside the
#: filestore, under the environment's directory, which production bind-mounts
#: from the host (``.runtime/env/<env>/repairs``).
LEDGER_FOLDER = "repairs"

#: The rows this repair is for, within whatever scope the caller adds:
#: Orbitrap, guessed with no reported zone, and stored with a zero offset.
_GUESSED_AS_UTC = """
    instrument_type = 'orbi'
    AND utc_offset_source = 'guess'
    AND acquisition_timezone IS NULL
    AND abs(extract(epoch from ((datetime AT TIME ZONE 'UTC') - datetime_utc))) < 1
"""

#: Orbitrap, guessed with no reported zone, but stored with another offset:
#: guessed in a zone that was not UTC, or repaired by an earlier run.
_GUESSED_ELSEWHERE = """
    instrument_type = 'orbi'
    AND utc_offset_source = 'guess'
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


def _ends_after(
    acquired_utc: datetime, length: float | None, created: datetime
) -> bool:
    """Whether an acquisition that starts at ``acquired_utc`` ends after the
    file was registered: impossible for a true acquisition time."""
    return acquired_utc + timedelta(seconds=length or 0.0) > created


def _write_ledger(folder: str | os.PathLike, zone: str, entries: list[dict]) -> str:
    """Write what an apply changed, one JSON object per row, and return the path."""
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(folder, f"fix_guessed_datetime_utc-{stamp}.jsonl")
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps({"zone": zone, **entry}) + "\n")
    return path


async def fix_guessed_datetime_utc(
    zone: str,
    instrument: str | None = None,
    created_min: datetime | None = None,
    created_max: datetime | None = None,
    dry_run: bool = True,
    ledger_dir: str | os.PathLike | None = None,
) -> dict:
    """Repair ``datetime_utc`` on Orbitrap rows that were guessed as UTC.

    Selects the rows the module docstring describes, within the scope the
    other parameters narrow it to, computes each one's correct UTC instant in
    Python, and writes the ones that change, moving each file's instrument
    function with it. A row whose corrected value is the one already stored
    - a zone at UTC for that part of the year - is counted as unchanged and
    not written, which also makes a second run with the same zone a no-op
    for it. Guessed rows stored with another offset are never touched; their
    count is reported, so nobody takes a clean run for a complete one, and
    after an apply it includes the rows that apply repaired.

    :param zone: The IANA zone the instrument PC's clock was set to, for
        every row in scope. Checked here, before anything is read.
    :type zone: str
    :param instrument: Only this instrument's files; every instrument when
        omitted. Use it where a server holds instruments in several zones.
    :type instrument: str | None
    :param created_min: Only files registered at or after this instant
        (``sample_file_utc_created``); a naive value is read as UTC. Rows
        with no registration time fall outside any window and are counted.
    :type created_min: datetime | None
    :param created_max: Only files registered before this instant.
    :type created_max: datetime | None
    :param dry_run: Report what would change and change nothing. The
        default: a repair in the wrong zone is as wrong as the guess was.
    :type dry_run: bool
    :param ledger_dir: Where an apply writes its ledger; the environment's
        ``repairs/`` folder when omitted.
    :type ledger_dir: str | os.PathLike | None
    :return: ``{"zone", "dry_run", "candidates", "unchanged", "ambiguous",
        "with_created", "impossible_before", "impossible_after", "updated",
        "functions_updated", "left_alone", "outside_window", "ledger",
        "previewed"}`` - the Orbitrap rows in scope that were guessed as
        UTC, how many already hold the right value, how many fall in a
        repeated or skipped hour, how many have a registration time and of
        those how many end after it as stored and after the repair, how
        many rows and instrument functions were written, how many guessed
        rows were left alone, how many rows a registration window could
        not hold for having no registration time, the ledger's path (None
        on a dry run or when nothing changed), and the first rows with
        their old and new values.
    :rtype: dict
    :raises ValueError: ``zone`` is not a zone this machine knows.
    """
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as error:
        raise ValueError(f"'{zone}' is not a known IANA timezone") from error

    scope_sql, params = _scope(instrument, created_min, created_max)
    windowed = created_min is not None or created_max is not None
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
                "non-zero offset: guessed in a real zone, or repaired by an "
                "earlier run of this script. This run leaves them alone."
            )

        outside_window = 0
        if windowed:
            unwindowed_sql, unwindowed_params = _scope(instrument, None, None)
            outside_window = (
                await session.execute(
                    text(f"""
                        SELECT count(*) FROM sample_file
                        WHERE {_GUESSED_AS_UTC}
                        AND sample_file_utc_created IS NULL
                        {unwindowed_sql}
                    """),
                    unwindowed_params,
                )
            ).scalar_one()
            if outside_window:
                runtime.logger.warning(
                    f"{outside_window} row(s) guessed as UTC have no registration "
                    "time, so no registration window can hold them; they are "
                    "outside this run. Run without a window to reach them."
                )

        rows = (
            await session.execute(
                text(f"""
                    SELECT sample_file_id, filename, datetime, datetime_utc,
                           length, sample_file_utc_created, instrument_function_id
                    FROM sample_file
                    WHERE {_GUESSED_AS_UTC}
                    {scope_sql}
                    ORDER BY datetime
                """),
                params,
            )
        ).all()

        corrections: dict[str, dict] = {}
        previewed: list[dict] = []
        unchanged = ambiguous = 0
        with_created = impossible_before = impossible_after = 0
        for (
            sample_file_id,
            filename,
            local_dt,
            stored_utc,
            length,
            created,
            function_id,
        ) in rows:
            corrected, uncertain = wall_clock_as_utc(local_dt, tz)
            if uncertain:
                ambiguous += 1
                runtime.logger.warning(
                    f"  {filename}: {local_dt.isoformat()} names two instants "
                    f"or none in {zone}; using {corrected.isoformat()}, the "
                    "converter's choice, which may be an hour off"
                )
            if created is not None:
                with_created += 1
                impossible_before += _ends_after(stored_utc, length, created)
                impossible_after += _ends_after(corrected, length, created)
            if corrected == stored_utc:
                unchanged += 1
                continue
            corrections[sample_file_id] = {
                "sample_file_id": sample_file_id,
                "filename": filename,
                "datetime": local_dt,
                "datetime_utc": stored_utc,
                "corrected_utc": corrected,
                "instrument_function_id": function_id,
            }
            if len(previewed) < PREVIEW_ROWS:
                previewed.append(corrections[sample_file_id])

        for row in previewed:
            runtime.logger.info(
                f"  {row['filename']}: datetime={row['datetime'].isoformat()}  "
                f"datetime_utc={row['datetime_utc'].isoformat()}  ->  "
                f"{row['corrected_utc'].isoformat()}"
            )
        runtime.logger.info(
            f"{len(rows)} Orbitrap row(s) guessed as UTC in scope: "
            f"{len(corrections)} to correct, {unchanged} already right in {zone}, "
            f"{ambiguous} in a repeated or skipped hour"
        )
        runtime.logger.info(
            f"Of {with_created} row(s) with a registration time, "
            f"{impossible_before} end after their own registration as stored and "
            f"{impossible_after} would after the repair. A wall clock stored as "
            "UTC east of UTC shows as rows that end after they were registered, "
            "which the repair clears; rows already right show none, and a PC "
            "whose clock is set to UTC needs no repair."
        )

        updated = functions_updated = 0
        ledger = None
        if not dry_run and corrections:
            ledger_entries: list[dict] = []
            ordered = list(corrections.values())
            for start in range(0, len(ordered), UPDATE_CHUNK):
                chunk = ordered[start : start + UPDATE_CHUNK]
                written = (
                    await session.execute(
                        # The selection is re-checked row by row, so a file
                        # that was re-processed between the read and the
                        # write - its offset then resolved afresh - is not
                        # overwritten.
                        text(f"""
                            UPDATE sample_file AS f
                            SET datetime_utc = v.corrected
                            FROM unnest(
                                CAST(:ids AS text[]), CAST(:corrected AS timestamptz[])
                            ) AS v(sample_file_id, corrected)
                            WHERE f.sample_file_id = v.sample_file_id
                              AND {_GUESSED_AS_UTC}
                            RETURNING f.sample_file_id, f.instrument_function_id
                        """),
                        {
                            "ids": [c["sample_file_id"] for c in chunk],
                            "corrected": [c["corrected_utc"] for c in chunk],
                        },
                    )
                ).all()
                updated += len(written)

                # The fit the converter made from the file was dated with the
                # same guessed instant; move it with the file, where it still
                # carries that instant, so the two keep agreeing.
                with_function = [
                    corrections[file_id]
                    for file_id, function_id in written
                    if function_id is not None
                ]
                moved: set[str] = set()
                if with_function:
                    moved = {
                        function_id
                        for (function_id,) in (
                            await session.execute(
                                text("""
                                    UPDATE instrument_function AS i
                                    SET datetime_utc = v.corrected
                                    FROM unnest(
                                        CAST(:function_ids AS text[]),
                                        CAST(:old AS timestamptz[]),
                                        CAST(:corrected AS timestamptz[])
                                    ) AS v(instrument_function_id, old_utc, corrected)
                                    WHERE i.instrument_function_id = v.instrument_function_id
                                      AND i.datetime_utc = v.old_utc
                                    RETURNING i.instrument_function_id
                                """),
                                {
                                    "function_ids": [
                                        c["instrument_function_id"]
                                        for c in with_function
                                    ],
                                    "old": [c["datetime_utc"] for c in with_function],
                                    "corrected": [
                                        c["corrected_utc"] for c in with_function
                                    ],
                                },
                            )
                        ).all()
                    }
                    functions_updated += len(moved)

                for file_id, function_id in written:
                    row = corrections[file_id]
                    ledger_entries.append(
                        {
                            "sample_file_id": file_id,
                            "filename": row["filename"],
                            "datetime": row["datetime"].isoformat(),
                            "datetime_utc_before": row["datetime_utc"].isoformat(),
                            "datetime_utc_after": row["corrected_utc"].isoformat(),
                            "instrument_function_id": (
                                function_id if function_id in moved else None
                            ),
                        }
                    )

            # Written before the commit: a repair nobody can trace is not
            # made. A failure here rolls the whole repair back.
            folder = ledger_dir or os.path.join(
                os.path.dirname(runtime.filestore()), LEDGER_FOLDER
            )
            ledger = _write_ledger(folder, zone, ledger_entries)
            await session.commit()
            runtime.logger.info(
                f"Updated {updated} row(s) and {functions_updated} instrument "
                f"function(s); ledger at {ledger}"
            )
        elif dry_run and corrections:
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
        "with_created": with_created,
        "impossible_before": impossible_before,
        "impossible_after": impossible_after,
        "updated": updated,
        "functions_updated": functions_updated,
        "left_alone": left_alone,
        "outside_window": outside_window,
        "ledger": ledger,
        "previewed": previewed,
    }
