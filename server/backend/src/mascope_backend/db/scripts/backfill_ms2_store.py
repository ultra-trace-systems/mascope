"""
Maintenance script to write the MS2 store of files converted before it
existed.

The MS2 store (``mascope_signal.ms2_store``, ``ms2.zarr`` in a sample
directory) holds a file's fragmentation scans, read once, and the MS2 routes
read it instead of the raw file. The converter writes it for a raw Orbitrap
file whose scan-stream census holds a fragmentation stream, and
re-processing writes it again; nothing else does. So every file converted
before the store shipped has none, and its MS2 routes go on reading the raw
file on every request, under a 120-second timeout
(``docs/dev/ms2_analysis.md``, section 2.2).

The census (``.props["scan_streams"]``) says which files hold fragmentation
scans without opening them, and the raw data is kept beside the converted
sample, so this writes the store of each file whose census holds a
fragmentation stream and whose props record no store this build reads. It
writes what the converter would (``write_ms2_store``): nothing is
re-converted.

**It fills the group inventory too.** ``ms2_group`` lists each file's MS2
groups, read from its store and written whenever the store is
(``api/new/ms2/inventory.py``). A store this writes is listed after it, and a
file whose store was written before the inventory existed - or whose listing
failed - is listed from its store alone, with no raw file opened: such a file
is one whose store records other groups than the inventory lists.

**A file with no census is left alone.** Whether it holds fragmentation
scans is not known without opening it, and ``backfill_scan_stream_census``
is the script that opens it. It is counted, so an operator sees how many
there are; run that backfill first and this one again.

Two modes:

- ``count`` (the default) reads only the props and reports, per instrument,
  how many files hold a fragmentation stream and no store, how many have a
  store, and of those how many the inventory does not list, and how many
  hold no census. It opens no raw file and writes nothing.
- ``run`` writes the stores. A file is read **one at a time**, with a pause
  of ``MS2_STORE_PAUSE`` seconds after each, so a production server keeps
  serving and ingesting while it runs. Each store is written as the file is
  read, so the run can be interrupted: a file whose store is recorded is
  never read again. ``MS2_STORE_LIMIT`` caps how many files one run reads,
  and a file whose store could not be written is remembered in its props
  and passed over on the next run; ``MS2_STORE_RETRY=1`` tries those again
  once the cause is dealt with. A file only to be listed is read the same
  way, from its store. Run it until it reports nothing left to do.

Usage:
    mascope dev db script run backfill_ms2_store
    mascope prod db script run backfill_ms2_store                 # count

    MS2_STORE_MODE=run mascope prod db script run backfill_ms2_store
    MS2_STORE_MODE=run MS2_STORE_LIMIT=500 mascope prod db script run backfill_ms2_store
    MS2_STORE_MODE=run MS2_STORE_PAUSE=5 mascope prod db script run backfill_ms2_store
    MS2_STORE_MODE=run MS2_STORE_RETRY=1 mascope prod db script run backfill_ms2_store

    python -m mascope_backend.db.scripts.backfill_ms2_store --count
    python -m mascope_backend.db.scripts.backfill_ms2_store --run

Date: 2026-10-27
"""

import asyncio
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import text

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_backend.api.new.ms2.inventory import write_ms2_inventory
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.method_keys import usable_streams
from mascope_backend.runtime import runtime
from mascope_signal import ms2_store


#: The props field recording an attempt that wrote no store, so that a
#: bounded run makes progress instead of reading the same files again.
#: Written by this script alone; nothing reads it but this script.
ATTEMPT_FIELD = "ms2_store_backfill"

#: The instrument class whose files can have an MS2 store: only a raw
#: Orbitrap file is written one (``ms2_store.write_ms2_store``).
ORBITRAP = "orbi"

#: The modes, and the one a run without any takes: the one that writes
#: nothing.
MODES = ("count", "run")
DEFAULT_MODE = "count"

#: Seconds slept after each file a run reads, unless ``MS2_STORE_PAUSE``
#: says otherwise. A store reads every fragmentation scan of its file, which
#: takes a second or more on a large acquisition; resting as long again
#: keeps the run to about half of one core and the disk it reads from.
DEFAULT_PAUSE = 1.0

#: Props read at once while surveying. Each is a small local JSON load, so
#: this bounds open descriptors rather than speed.
_PROPS_CONCURRENCY = 16

#: Files surveyed per gathered batch: what is pending at once, as in the
#: census backfill.
_SURVEY_BATCH = 2000

#: Group keys named per file in the log; a data-dependent acquisition can
#: hold hundreds of groups.
_KEYS_LOGGED = 12

#: Writer failures logged with a traceback.
_TRACEBACK_LIMIT = 3

#: Every Orbitrap file, newest first: a bounded run reaches the files a
#: client is most likely to ask about. A run carried through to the end
#: reaches the same state whichever way it goes. With each, how many groups
#: the inventory lists of it.
_CANDIDATES_SQL = """
    SELECT
        sf.sample_file_id AS sample_file_id,
        sf.filename AS filename,
        sf.instrument AS instrument,
        (
            SELECT count(*) FROM ms2_group g
            WHERE g.sample_file_id = sf.sample_file_id
        ) AS listed
    FROM sample_file sf
    WHERE sf.instrument_type = :instrument_type
    ORDER BY sf.datetime_utc DESC, sf.filename DESC
"""

#: What :func:`classify` calls a file, and what the report says of each.
NO_PROPS = "no_props"
NO_CENSUS = "no_census"
NO_FRAGMENTATION = "no_fragmentation"
HAS_STORE = "has_store"
UNLISTED = "unlisted"
TRIED = "tried"
NO_RAW = "no_raw"
PENDING = "pending"

#: The classes of a file that holds a fragmentation stream and no store.
WITHOUT_STORE = (PENDING, TRIED, NO_RAW)
#: The classes a run reads: from its raw file, or from its store alone.
TO_READ = (PENDING, UNLISTED)


class BadSetting(ValueError):
    """A setting of this script was given a value it cannot use."""


async def _candidates() -> list[dict]:
    """Every Orbitrap file, newest first.

    :return: One dict per file: sample_file_id, filename, instrument, and
        ``listed``, how many groups the inventory lists of it.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text(_CANDIDATES_SQL), {"instrument_type": ORBITRAP}
        )
        return [dict(row._mapping) for row in result]


def has_current_store(filename: str, props: dict) -> bool:
    """Whether the props record a store this build reads, and it is there.

    The layout and version are those ``Ms2Store.open`` reads; a store of
    another is not read, so the routes would read the raw file for it, and it
    is written again. A store the props record that is not on disk is written
    again too: ``Ms2Store.open`` would warn and read the raw file.

    :param filename: The sample file's stored name.
    :param props: Its ``.props``.
    """
    recorded = props.get("ms2_store") or {}
    if (
        recorded.get("layout") != ms2_store.LAYOUT
        or recorded.get("version") != ms2_store.VERSION
    ):
        return False
    return os.path.isdir(ms2_store.store_path(filename))


def _has_raw(filename: str) -> bool:
    """Whether the sample still holds the raw file a store is read from."""
    try:
        return m_name.get_sample_file_type(filename) == "orbi_raw"
    except Exception:  # noqa: BLE001
        return False


def classify(
    filename: str,
    props: dict | None,
    has_raw: Callable[[str], bool],
    retry: bool = False,
    listed: int | None = None,
) -> str:
    """What the backfill makes of one file.

    :param filename: The sample file's stored name.
    :param props: Its ``.props``, or None when they cannot be read.
    :param has_raw: Whether the raw file is still there: :func:`_has_raw`, or
        a stand-in. Asked only of a file that would otherwise be read.
    :param retry: Read a file a previous run could not write a store for.
    :param listed: How many groups the inventory lists of the file; None
        for not known, which leaves the inventory out of it.
    :return: One of :data:`NO_PROPS`, :data:`NO_CENSUS` (left alone),
        :data:`NO_FRAGMENTATION` (needs no store), :data:`HAS_STORE`,
        :data:`UNLISTED` (to list from its store), :data:`TRIED`,
        :data:`NO_RAW` or :data:`PENDING` (to read).
    """
    if props is None:
        return NO_PROPS
    # Judged as the census backfill judges it: a census it would fill in is
    # not one to read fragmentation off.
    streams = usable_streams(props.get("scan_streams"))
    if not streams:
        return NO_CENSUS
    if not ms2_store.holds_fragmentation(streams):
        return NO_FRAGMENTATION
    if has_current_store(filename, props):
        groups = (props.get("ms2_store") or {}).get("groups") or []
        # The inventory is written from the store whole, so a count that
        # differs is a store it does not list
        return UNLISTED if listed is not None and listed != len(groups) else HAS_STORE
    if props.get(ATTEMPT_FIELD) and not retry:
        return TRIED
    if not has_raw(filename):
        return NO_RAW
    return PENDING


def _read_props(filename: str) -> dict | None:
    """One sample file's ``.props``, or None when there are none to read."""
    try:
        return m_io.read_props(filename)
    except Exception:  # noqa: BLE001
        return None


async def survey(
    candidates: list[dict],
    read_props: Callable[[str], dict | None],
    has_raw: Callable[[str], bool],
    limit: int | None = None,
    retry: bool = False,
) -> tuple[list[dict], dict[str, Counter]]:
    """The files to read, and every candidate counted per instrument.

    Stops as soon as ``limit`` files to read have been found: the way to
    fill a large server is repeated capped runs, and surveying every
    candidate each time would read the whole filestore's props once per run.

    :param candidates: Rows from :func:`_candidates`, newest first.
    :param read_props: Reads a file's props: :func:`_read_props`, or a
        stand-in.
    :param has_raw: :func:`_has_raw`, or a stand-in.
    :param limit: Stop once this many files are found; None surveys them all.
    :param retry: Include files a previous run could not write a store for.
    :return: The candidates to read, newest first, each with ``tried`` set
        when a previous run recorded an attempt on it and ``listed_only``
        when it is only to be listed from its store; and per instrument, how
        many candidates were found of each class (:func:`classify`).
    :rtype: tuple[list[dict], dict[str, Counter]]
    """
    gate = asyncio.Semaphore(_PROPS_CONCURRENCY)

    async def one(candidate: dict) -> tuple[dict, str, bool]:
        async with gate:
            filename = candidate["filename"]
            props = await asyncio.to_thread(read_props, filename)
            found = await asyncio.to_thread(
                classify, filename, props, has_raw, retry, candidate.get("listed")
            )
            return candidate, found, bool(props and props.get(ATTEMPT_FIELD))

    pending: list[dict] = []
    counts: dict[str, Counter] = defaultdict(Counter)
    for start in range(0, len(candidates), _SURVEY_BATCH):
        batch = candidates[start : start + _SURVEY_BATCH]
        for candidate, found, tried in await asyncio.gather(*map(one, batch)):
            counts[candidate["instrument"] or "(none)"][found] += 1
            if found in TO_READ:
                pending.append(
                    {**candidate, "tried": tried, "listed_only": found == UNLISTED}
                )
        if limit is not None and len(pending) >= limit:
            return pending[:limit], counts
    return pending, counts


def write_store(filename: str, tried: bool) -> dict:
    """Write one file's MS2 store, as the converter would.

    Clears the attempt a previous run recorded, so a file written after
    ``MS2_STORE_RETRY=1`` keeps no note saying it could not be.

    :return: What the props now record of the store.
    """
    descriptor = ms2_store.write_ms2_store(filename)
    if tried:
        m_io.update_props(filename, {ATTEMPT_FIELD: None})
    return descriptor


def write_attempt(filename: str, error: Exception) -> None:
    """Record that this file was read and no store could be written."""
    m_io.update_props(
        filename,
        {
            ATTEMPT_FIELD: {
                "at": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(error).__name__}: {error}"[:500],
            }
        },
    )


def describe_groups(descriptor: dict) -> str:
    """One line naming what a store holds: its groups, per polarity."""
    groups = descriptor.get("groups") or []
    per_polarity = Counter(group.get("polarity") for group in groups)
    polarities = ", ".join(
        f"{count} {polarity}" for polarity, count in sorted(per_polarity.items())
    )
    keys = [
        f"{group.get('key')} ({group.get('polarity')}, {group.get('scan_count')} scans)"
        for group in groups[:_KEYS_LOGGED]
    ]
    more = len(groups) - len(keys)
    named = "; ".join(keys) + (f"; and {more} more" if more > 0 else "")
    return f"{len(groups)} groups ({polarities}): {named}"


async def list_groups(candidate: dict) -> int:
    """List one file's groups in the inventory, from its store."""
    return await write_ms2_inventory(candidate["sample_file_id"], candidate["filename"])


async def fill(
    pending: list[dict],
    write: Callable[[str, bool], dict],
    record: Callable[[str, Exception], None],
    pause: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    listing: Callable[[dict], Awaitable[int]] | None = None,
) -> Counter:
    """Write each pending file's store, one at a time, pausing after each.

    A file whose store cannot be written is recorded and the run carries on:
    one sample deleted while the run walks the filestore should not end a
    long job with a traceback and no summary. A store written, and the store
    of a file only to be listed, is then listed in the inventory by
    ``listing``; one that cannot be is counted and left for the next run,
    which finds it unlisted.

    :param pending: From :func:`survey`.
    :param write: :func:`write_store`, or a stand-in.
    :param record: :func:`write_attempt`, or a stand-in.
    :param pause: Seconds to sleep after each file.
    :param sleep: ``asyncio.sleep``, or a stand-in.
    :param listing: :func:`list_groups`, or a stand-in; None lists nothing.
    :return: How many were ``written`` and how many ``failed``, how many
        were ``listed`` and how many ``unlisted``.
    """
    counts: Counter = Counter()
    traced = 0
    for done, candidate in enumerate(pending, start=1):
        filename = candidate["filename"]
        step = f"  [{done}/{len(pending)}] {filename}"
        # A file only to be listed has its store already
        stored = candidate.get("listed_only", False)
        if not stored:
            try:
                descriptor = await asyncio.to_thread(
                    write, filename, candidate["tried"]
                )
            except Exception as error:  # noqa: BLE001 - one file, never the run
                counts["failed"] += 1
                if traced < _TRACEBACK_LIMIT:
                    traced += 1
                    runtime.logger.opt(exception=error).info(
                        f"{step}: no store written"
                    )
                else:
                    runtime.logger.info(f"{step}: no store written: {error}")
                try:
                    await asyncio.to_thread(record, filename, error)
                except Exception as exc:  # noqa: BLE001
                    runtime.logger.info(
                        f"  Cannot record the attempt on {filename}: {exc}"
                    )
            else:
                stored = True
                counts["written"] += 1
                runtime.logger.info(f"{step}: {describe_groups(descriptor)}")
        if stored and listing is not None:
            try:
                listed = await listing(candidate)
            except Exception as error:  # noqa: BLE001 - one file, never the run
                counts["unlisted"] += 1
                runtime.logger.info(f"{step}: its groups not listed: {error}")
            else:
                counts["listed"] += 1
                runtime.logger.info(f"{step}: {listed} groups listed")
        if pause > 0 and done < len(pending):
            await sleep(pause)
    return counts


def _report(counts: dict[str, Counter]) -> None:
    """Log the survey's counts per instrument, and their total."""
    total: Counter = Counter()
    for instrument in sorted(counts):
        found = counts[instrument]
        total.update(found)
        runtime.logger.info(f"  {instrument}: {_counts_line(found)}")
    runtime.logger.info(f"  All instruments: {_counts_line(total)}")


def _counts_line(found: Counter) -> str:
    without = sum(found[c] for c in WITHOUT_STORE)
    return (
        f"{without} with a fragmentation stream and no store "
        f"({found[PENDING]} to read, {found[TRIED]} tried before, "
        f"{found[NO_RAW]} raw file not kept), "
        f"{found[HAS_STORE] + found[UNLISTED]} with a store "
        f"({found[UNLISTED]} whose groups the inventory does not list), "
        f"{found[NO_CENSUS]} with no census (left alone), "
        f"{found[NO_FRAGMENTATION]} with no fragmentation stream, "
        f"{found[NO_PROPS]} with no props"
    )


def _mode(argv: list[str]) -> str:
    """The mode: ``--count`` or ``--run`` on the command line, else
    ``MS2_STORE_MODE``, else :data:`DEFAULT_MODE`.

    :raises BadSetting: Both flags, an unknown argument, or an unknown mode.
    """
    flags = {f"--{mode}": mode for mode in MODES}
    if len(argv) > 1 or any(arg not in flags for arg in argv):
        raise BadSetting(f"Give one of --count or --run, not {' '.join(argv)!r}.")
    if argv:
        return flags[argv[0]]
    mode = os.environ.get("MS2_STORE_MODE") or DEFAULT_MODE
    if mode not in MODES:
        raise BadSetting(
            f"MS2_STORE_MODE={mode!r} is not a mode: give 'count' or 'run'."
        )
    return mode


def _limit() -> int | None:
    """How many files one run may read, from ``MS2_STORE_LIMIT``.

    :raises BadSetting: Set to something that is not a positive whole number.
        Refused rather than ignored: an operator who set it wanted a bounded
        run.
    """
    raw = os.environ.get("MS2_STORE_LIMIT")
    if not raw:
        return None
    try:
        limit = int(raw)
    except ValueError:
        limit = 0
    if limit <= 0:
        raise BadSetting(
            f"MS2_STORE_LIMIT={raw!r} is not a positive whole number. Unset it "
            f"to read every file that needs a store."
        )
    return limit


def _pause() -> float:
    """Seconds to rest after each file, from ``MS2_STORE_PAUSE``.

    :raises BadSetting: Set to something that is not a number of seconds, 0
        or more.
    """
    raw = os.environ.get("MS2_STORE_PAUSE")
    if not raw:
        return DEFAULT_PAUSE
    try:
        pause = float(raw)
    except ValueError:
        pause = -1.0
    if not pause >= 0:
        raise BadSetting(
            f"MS2_STORE_PAUSE={raw!r} is not a number of seconds, 0 or more."
        )
    return pause


async def backfill(
    mode: str,
    limit: int | None = None,
    pause: float = DEFAULT_PAUSE,
    retry: bool = False,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[dict[str, Counter], Counter]:
    """Count, or write, the MS2 stores the Orbitrap files lack, and list
    their groups.

    :param mode: ``count`` or ``run``.
    :param limit: The most files a run reads; None for no cap. A count
        surveys every file whatever it is.
    :param pause: Seconds a run rests after each file.
    :param retry: Read the files a previous run could not write a store for.
    :param sleep: ``asyncio.sleep``, or a stand-in.
    :return: The survey's counts per instrument, and what the run wrote
        (``written``, ``failed``, ``listed``, ``unlisted``; empty for a
        count).
    """
    candidates = await _candidates()
    runtime.logger.info(f"Orbitrap files: {len(candidates)}")
    counting = mode == "count"
    pending, counts = await survey(
        candidates, _read_props, _has_raw, None if counting else limit, retry
    )
    surveyed = sum(sum(found.values()) for found in counts.values())
    runtime.logger.info(
        f"Surveyed {surveyed} of {len(candidates)}"
        + ("" if counting else f", {len(pending)} to read this run")
        + ":"
    )
    _report(counts)
    total_no_census = sum(found[NO_CENSUS] for found in counts.values())
    if total_no_census:
        runtime.logger.info(
            "  A file with no census is not read here: run "
            "backfill_scan_stream_census, then this again."
        )
    if not retry and any(found[TRIED] for found in counts.values()):
        runtime.logger.info(
            "  Files tried before had no store written; MS2_STORE_RETRY=1 tries "
            "them again."
        )
    if counting:
        runtime.logger.info("Counted only; nothing was read or written.")
        return counts, Counter()
    if not pending:
        runtime.logger.info("Nothing left to do.")
        return counts, Counter()

    to_list = sum(1 for candidate in pending if candidate["listed_only"])
    runtime.logger.info(
        f"Writing {len(pending) - to_list} stores and listing {to_list} more, "
        f"one file at a time, resting {pause:g} s after each"
    )
    written = await fill(
        pending, write_store, write_attempt, pause, sleep, listing=list_groups
    )

    runtime.logger.info("=" * 80)
    runtime.logger.info("BACKFILL MS2 STORE COMPLETE")
    runtime.logger.info(
        f"Written: {written['written']}, could not be written: {written['failed']}; "
        f"listed: {written['listed']}, could not be listed: {written['unlisted']}"
    )
    found_pending = sum(found[c] for found in counts.values() for c in TO_READ)
    if surveyed < len(candidates) or found_pending > len(pending):
        runtime.logger.info(
            "The run stopped at MS2_STORE_LIMIT, so run this again until it "
            "reports nothing left to do."
        )
    runtime.logger.info("=" * 80)
    if written["failed"]:
        runtime.logger.warning(
            f"MS2 store backfill: {written['failed']} files had no store "
            "written; their MS2 routes go on reading the raw file."
        )
    if written["unlisted"]:
        runtime.logger.warning(
            f"MS2 store backfill: {written['unlisted']} files had their groups "
            "not listed; run this again to list them."
        )
    return counts, written


async def run(argv: list[str]) -> None:
    """Read the settings, then count or write."""
    mode = _mode(argv)
    limit = _limit()
    pause = _pause()
    retry = os.environ.get("MS2_STORE_RETRY") == "1"
    await configure_database_engine()
    await backfill(mode, limit=limit, pause=pause, retry=retry)


def main() -> None:
    """Entry point for ``mascope dev|prod db script run``."""
    try:
        asyncio.run(run(sys.argv[1:]))
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
