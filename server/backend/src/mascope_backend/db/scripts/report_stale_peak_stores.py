"""
Maintenance script to list the raw Orbitrap files whose pooled peak store
lacks a scan the file reads.

A pooled peak store keeps the scan axis it was allocated with, while the
scans the reader selects are decided on every read. The reader leaves a
file's first scan out where its TIC is an outlier, and until that rule
compared the first scan with the scans of its own MS order and polarity it
compared it with every other scan of the file: in a file whose fragmentation
scans outnumber its MS1 scans, or one whose other polarity is much weaker,
an ordinary first scan was left out. Those files' stores were built a scan
short, and every read that divides their sums by the file's scans now
refuses them as stale (``mascope_signal.compute.StalePeakStoreError``).
Only a detection of their peaks repairs them, and nothing queues one for a
file whose samples are already matched.

This lists them, so an operator can re-process them. It reads only the
database, each candidate's ``.props``, its store's metadata and its raw
file's scan index, and writes nothing.

- **A candidate** is a raw Orbitrap file whose scan-stream census holds a
  fragmentation stream or MS1 scans of both polarities: the files the old
  rule could misjudge. A single-polarity file of MS1 scans alone reads as it
  always did, and is not opened. A file with no census is opened, since what
  it holds is not known otherwise.
- **A per-stream store** compares each stream's first scan with its own
  stream's scans, which the rule always did, so it is counted and not
  opened.
- **The check** is the one a re-processing makes before it decides to
  detect a file's peaks again (``process.peaks.outgrown``): the store's axis
  against the scans the file reads now.

**Repairing them.** Re-process the files listed (*Re-process* in Raw files,
or ``POST /api/sample/files/reprocess``, 25 files at a time): a re-processing
detects the peaks of a file that has outgrown its store, then calibrates and
matches it on them and writes its MS2 store again. A file a person made a
sample from is refused by a re-processing, and is marked so in the list:
*Detect peaks* on it rebuilds its store and matches its samples again.

Usage:
    mascope dev db script run report_stale_peak_stores
    mascope prod db script run report_stale_peak_stores

    python -m mascope_backend.db.scripts.report_stale_peak_stores

Date: 2026-10-27
"""

import asyncio
from collections import Counter, defaultdict
from typing import Callable

from sqlalchemy import select, text

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.sample.files.process.peaks import outgrown
from mascope_backend.api.controllers.sample.files.process.service import (
    _pipeline_item,
)
from mascope_backend.db import SampleItem, async_session, configure_database_engine
from mascope_backend.method_keys import usable_streams
from mascope_backend.runtime import runtime


#: Read by ``mascope dev|prod db script run``, which then takes no pre-script
#: backup (``mascope_cli.pg.script_writes_nothing``). It is a promise about
#: this module: nothing here opens a write session, and all it touches
#: outside the database is reading each candidate's ``.props``, its peak
#: store and its raw file's scan index.
WRITES_NOTHING = True

#: The instrument class whose files are read for their scans: a raw Orbitrap
#: file's are selected anew on every read.
ORBITRAP = "orbi"

#: Every Orbitrap file, by instrument and then oldest first, as the list is
#: read.
_CANDIDATES_SQL = """
    SELECT sf.sample_file_id AS sample_file_id, sf.filename AS filename,
           sf.instrument AS instrument
    FROM sample_file sf
    WHERE sf.instrument_type = :instrument_type
    ORDER BY sf.instrument, sf.datetime_utc, sf.filename
"""

#: What :func:`classify` and :func:`check` call a file.
NO_PROPS = "no_props"
MS1_ONE_POLARITY = "ms1_one_polarity"
PER_STREAM = "per_stream"
NO_STORE = "no_store"
UNREADABLE = "unreadable"
CURRENT = "current"
OUTGROWN = "outgrown"

#: Candidate classes, as :func:`classify` returns them.
CENSUS = "census"
NO_CENSUS = "no_census"

#: Read failures logged with a traceback.
_TRACEBACK_LIMIT = 3


def classify(props: dict | None) -> str:
    """Whether a file can have been read a scan short, by its census.

    :param props: Its ``.props``, or None when they cannot be read.
    :return: :data:`NO_PROPS`; :data:`MS1_ONE_POLARITY`, a file the old rule
        read as the new one does; or a candidate to open, :data:`CENSUS` or
        :data:`NO_CENSUS`.
    """
    if props is None:
        return NO_PROPS
    streams = usable_streams(props.get("scan_streams"))
    if not streams:
        return NO_CENSUS
    orders = {stream["signature"].get("ms_order") for stream in streams}
    polarities = {
        stream["signature"].get("polarity")
        for stream in streams
        if stream["signature"].get("ms_order") == 1
    }
    if orders - {1} or len(polarities) > 1:
        return CENSUS
    return MS1_ONE_POLARITY


def check(filename: str) -> tuple[str, int, int]:
    """Whether one candidate's pooled store is the scans its file reads.

    :param filename: The sample file's stored name.
    :raises Exception: If the store or the file's scans cannot be read.
    :return: :data:`NO_STORE`, :data:`PER_STREAM`, :data:`CURRENT` or
        :data:`OUTGROWN`, with the scans the store holds and those the file
        reads (both 0 where the file was not read for them).
    """
    try:
        store = m_io.load_array(filename, var="peak_timeseries")
    except FileNotFoundError:
        return NO_STORE, 0, 0
    if m_compute.peak_store_streams(store):
        return PER_STREAM, 0, 0
    if not outgrown(filename, store):
        return CURRENT, 0, 0
    return OUTGROWN, store.time.size, m_compute.get_scan_timestamps(filename).size


def _read_props(filename: str) -> dict | None:
    """One sample file's ``.props``, or None when there are none to read."""
    try:
        return m_io.read_props(filename)
    except Exception:  # noqa: BLE001
        return None


async def survey(
    candidates: list[dict],
    read_props: Callable[[str], dict | None],
    check_one: Callable[[str], tuple[str, int, int]],
) -> tuple[list[dict], dict[str, Counter]]:
    """The files that have outgrown their store, and every file counted.

    One file at a time: a candidate's raw file is opened, and this runs on a
    server that keeps serving.

    :param candidates: Rows from :func:`_candidates`.
    :param read_props: :func:`_read_props`, or a stand-in.
    :param check_one: :func:`check`, or a stand-in.
    :return: The outgrown files, each with ``stored`` and ``reads`` (scan
        counts); and per instrument, how many files were found of each class.
    :rtype: tuple[list[dict], dict[str, Counter]]
    """
    found: list[dict] = []
    counts: dict[str, Counter] = defaultdict(Counter)
    traced = 0
    for candidate in candidates:
        filename = candidate["filename"]
        tally = counts[candidate["instrument"] or "(none)"]
        kind = classify(await asyncio.to_thread(read_props, filename))
        if kind not in (CENSUS, NO_CENSUS):
            tally[kind] += 1
            continue
        if kind == NO_CENSUS:
            tally[NO_CENSUS] += 1
        try:
            state, stored, reads = await asyncio.to_thread(check_one, filename)
        except Exception as error:  # noqa: BLE001 - one file, never the run
            tally[UNREADABLE] += 1
            message = f"  {filename}: could not be read: {error}"
            if traced < _TRACEBACK_LIMIT:
                traced += 1
                runtime.logger.opt(exception=error).info(message)
            else:
                runtime.logger.info(message)
            continue
        tally[state] += 1
        if state == OUTGROWN:
            found.append({**candidate, "stored": stored, "reads": reads})
    return found, counts


async def _candidates() -> list[dict]:
    """Every Orbitrap file.

    :return: One dict per file: sample_file_id, filename and instrument.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text(_CANDIDATES_SQL), {"instrument_type": ORBITRAP}
        )
        return [dict(row._mapping) for row in result]


async def _with_samples_a_person_made(sample_file_ids: list[str]) -> set[str]:
    """The files among these that a re-processing refuses: those with a
    sample that auto-processing did not make (``_pipeline_item``)."""
    if not sample_file_ids:
        return set()
    async with async_session() as session:
        result = await session.execute(
            select(SampleItem.sample_file_id)
            .where(
                SampleItem.sample_file_id.in_(sample_file_ids),
                ~_pipeline_item(),
            )
            .distinct()
        )
        return {sample_file_id for (sample_file_id,) in result.all()}


def _counts_line(found: Counter) -> str:
    opened = found[CURRENT] + found[OUTGROWN] + found[PER_STREAM] + found[NO_STORE]
    return (
        f"{found[OUTGROWN]} outgrown their store, "
        f"{found[CURRENT]} read back their store, "
        f"{found[PER_STREAM]} per stream, {found[NO_STORE]} with no store, "
        f"{found[UNREADABLE]} unreadable "
        f"({opened + found[UNREADABLE]} candidates, {found[NO_CENSUS]} of them "
        f"with no census); {found[MS1_ONE_POLARITY]} MS1 files of one polarity "
        f"not opened, {found[NO_PROPS]} with no props"
    )


async def report() -> tuple[list[dict], dict[str, Counter]]:
    """List the Orbitrap files that have outgrown their pooled peak store.

    :return: The outgrown files, each with ``person_made`` set where a
        re-processing refuses it, and the counts per instrument.
    """
    candidates = await _candidates()
    runtime.logger.info(f"Orbitrap files: {len(candidates)}")
    found, counts = await survey(candidates, _read_props, check)
    person_made = await _with_samples_a_person_made(
        [row["sample_file_id"] for row in found]
    )
    for row in found:
        row["person_made"] = row["sample_file_id"] in person_made

    runtime.logger.info("Per instrument:")
    total: Counter = Counter()
    for instrument in sorted(counts):
        total.update(counts[instrument])
        runtime.logger.info(f"  {instrument}: {_counts_line(counts[instrument])}")
    runtime.logger.info(f"  All instruments: {_counts_line(total)}")

    if not found:
        runtime.logger.info("No file has outgrown its peak store.")
        return found, counts
    runtime.logger.info(
        f"{len(found)} files have outgrown their peak store "
        "(sample_file_id, filename, scans stored -> scans the file reads):"
    )
    for row in found:
        mark = "  [samples a person made: Detect peaks]" if row["person_made"] else ""
        runtime.logger.info(
            f"  {row['sample_file_id']}  {row['filename']}  "
            f"{row['stored']} -> {row['reads']}{mark}"
        )
    runtime.logger.info(
        "Re-process them, 25 at a time, to detect their peaks again; a file "
        "marked as holding samples a person made is refused by a "
        "re-processing, and Detect peaks rebuilds it instead. Nothing was "
        "written."
    )
    return found, counts


async def run() -> None:
    """Connect, then report."""
    await configure_database_engine()
    await report()


def main() -> None:
    """Entry point for ``mascope dev|prod db script run``."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        runtime.logger.info("Cancelled by user (Ctrl+C)")
    except Exception as e:
        runtime.logger.exception(f"Script failed: {e}")
        raise


if __name__ == "__main__":
    main()
