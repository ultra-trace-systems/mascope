"""
Maintenance script to record the scan-stream census in files converted before
it existed.

``.props["scan_streams"]`` is the census of what an acquisition measured: its
scans grouped by scan signature (``mascope_thermo.streams``). It is written
once, when a file is converted, and nothing fills it in afterwards -
``write_props`` has a single caller, the file converter, and calibration only
patches ``range`` and ``mz_calibration`` through ``update_props``.

So every file converted before the census shipped carries none, and
``backfill_method_bindings`` skips exactly those: a census-bearing instrument
with no census has no signature class, and guessing one would key the same
method apart from the census later files carry
(``docs/dev/ingest_routing_and_splitting.md``, section 5.3). On a production
server that is the whole Orbitrap history - which is the half of the binding
table where the method key discriminates at all.

The evidence is still on disk. The census comes from the raw file's own scan
filters and trailers, and the raw data is kept beside the converted sample, so
this reopens each file that carries no census and writes one. Nothing is
re-converted and no database row changes.

**Census-bearing instruments only.** A TofDaq acquisition is one stream with
no per-scan filter to tell scans apart, so its census is empty by construction
(``mascope_tofwerk.processor.scan_streams``) and there would be nothing to
fill in. The instrument types that bear a census are the ones
``backfill_method_bindings`` requires one from, so both read the same
constant and cannot come to disagree about which files need one.

Reading a raw file per census is slow, so the run is **bounded and
resumable**: a file that already has a census is never reopened, and
``CENSUS_LIMIT`` caps how many files one run reads. Run it again until it
reports nothing left to do.

Set DRY_RUN=1 to report what would be written without writing.

Usage:
    mascope dev db script run backfill_scan_stream_census
    mascope prod db script run backfill_scan_stream_census

    DRY_RUN=1 mascope prod db script run backfill_scan_stream_census
    CENSUS_LIMIT=5000 mascope prod db script run backfill_scan_stream_census

Date: 2026-09-25
"""

import asyncio
import os
from typing import Callable

from sqlalchemy import bindparam, text

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.method_keys import CENSUS_BEARING_INSTRUMENT_TYPES
from mascope_backend.runtime import runtime
from mascope_thermo.backend import open_backend
from mascope_thermo.streams import scan_streams


#: The props field holding the census. ``SampleFileProps.scan_streams``
#: writes it and ``process.status.read_scan_streams`` reads it back under this
#: same name.
CENSUS_FIELD = "scan_streams"

#: Props read at once while looking for the files that carry no census. Each
#: is a small local JSON load, so this bounds open descriptors rather than
#: speed.
_PROPS_CONCURRENCY = 16

#: Raw files opened at once. A census parses the scan filters of a whole file
#: and samples trailers from each stream it finds, so this is about the load
#: the run puts on a server that is also ingesting, and is deliberately
#: modest.
_READ_CONCURRENCY = 4

#: Files listed individually in the report.
_PREVIEW_LIMIT = 20

#: A progress line every this many files read.
_PROGRESS_EVERY = 500

#: Files whose props are surveyed, and whose raw data is read, per gathered
#: batch. The semaphores above bound how much runs at once; these bound how
#: much is *pending* at once, which is a different thing on a server holding
#: six figures of files - one coroutine and one future each, awaited together,
#: is hundreds of megabytes before any of them does any work.
_SURVEY_BATCH = 2000
_READ_BATCH = 200


async def _candidates() -> list[dict]:
    """Every file of a census-bearing instrument, newest first.

    Newest first because a bounded run should reach the methods still in use:
    the census is here to key a method binding, and a method last run a year
    ago routes nothing today. A run carried through to the end reaches the
    same state whichever way it goes.

    :return: One dict per file: sample_file_id and filename.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text("""
                SELECT sample_file_id, filename
                FROM sample_file
                WHERE instrument_type IN :types
                ORDER BY datetime_utc DESC, filename DESC
            """).bindparams(bindparam("types", expanding=True)),
            {"types": sorted(CENSUS_BEARING_INSTRUMENT_TYPES)},
        )
        return [dict(row._mapping) for row in result]


def needs_census(props: dict) -> bool:
    """Whether ``props`` records no usable census.

    A missing field is the ordinary case: the file was converted before the
    census existed. An empty one is a conversion whose reader could not supply
    it, and is worth retrying rather than skipping - the reader has been
    replaced since, and a file of a census-bearing instrument that holds any
    scans at all holds at least one stream.

    :param props: A sample file's ``.props``.
    :return: True when the file should be reopened for its census.
    :rtype: bool
    """
    streams = props.get(CENSUS_FIELD)
    return not isinstance(streams, list) or not streams


def _read_props(filename: str) -> dict | None:
    """One sample file's ``.props``, or None when there are none to read."""
    try:
        return m_io.read_props(filename)
    except Exception:  # noqa: BLE001
        return None


def _read_census(filename: str) -> list[dict] | None:
    """The scan-stream census of one file, read from its raw data.

    :param filename: Sample file name (base, not full path).
    :return: The census, or None when the file cannot be read - its raw data
        was not kept beside the sample, or the reader refused it.
    :rtype: list[dict] | None
    """
    try:
        with open_backend(m_name.filename_to_datafile_path(filename)) as reader:
            return scan_streams(reader)
    except Exception as exc:  # noqa: BLE001
        # INFO per file: run() raises one summary WARNING for the lot, so a
        # server whose older raw data has been cleared does not report one
        # monitoring event per file.
        runtime.logger.info(f"  Cannot read {filename}: {exc}")
        return None


def _write_census(filename: str, streams: list[dict]) -> None:
    """Record ``streams`` as one file's census, leaving the rest of its props."""
    m_io.update_props(filename, {CENSUS_FIELD: streams})


async def _survey(
    candidates: list[dict],
    read_props: Callable[[str], dict | None],
) -> tuple[list[str], dict[str, int]]:
    """Split the candidates into the files to read and counts for the rest.

    :param candidates: Rows from :func:`_candidates`.
    :param read_props: Reads a file's props: :func:`_read_props`, or a
        stand-in.
    :return: The filenames carrying no census, in candidate order, and the
        ``has_census`` and ``no_props`` counts.
    :rtype: tuple[list[str], dict[str, int]]
    """
    gate = asyncio.Semaphore(_PROPS_CONCURRENCY)

    async def one(filename: str) -> tuple[str, dict | None]:
        async with gate:
            return filename, await asyncio.to_thread(read_props, filename)

    pending: list[str] = []
    counts = {"has_census": 0, "no_props": 0}
    for start in range(0, len(candidates), _SURVEY_BATCH):
        batch = candidates[start : start + _SURVEY_BATCH]
        for filename, props in await asyncio.gather(
            *(one(c["filename"]) for c in batch)
        ):
            if props is None:
                counts["no_props"] += 1
            elif needs_census(props):
                pending.append(filename)
            else:
                counts["has_census"] += 1
    return pending, counts


async def _fill(
    pending: list[str],
    read: Callable[[str], list[dict] | None],
    write: Callable[[str, list[dict]], None],
    dry_run: bool,
) -> dict[str, int]:
    """Read each pending file's census and write it, a few files at a time.

    Written as each read finishes rather than in one pass at the end: the run
    is expected to be interrupted - a production server holds six figures of
    these - and a file whose census is on disk is one the next run skips.

    :param pending: Filenames to read, from :func:`_survey`.
    :param read: Reads one census: :func:`_read_census`, or a stand-in.
    :param write: Writes one census: :func:`_write_census`, or a stand-in.
    :param dry_run: Report what would be written without writing it.
    :return: The ``written``, ``unreadable`` and ``empty`` counts.
    :rtype: dict[str, int]
    """
    gate = asyncio.Semaphore(_READ_CONCURRENCY)
    counts = {"written": 0, "unreadable": 0, "empty": 0}
    previewed = 0
    done = 0

    async def one(filename: str) -> None:
        nonlocal previewed, done
        async with gate:
            streams = await asyncio.to_thread(read, filename)
            if streams is None:
                counts["unreadable"] += 1
            elif not streams:
                # The file opened and reported no streams at all. Writing an
                # empty census would record it as answered and stop the next
                # run retrying it, which is the opposite of what an empty
                # answer from a census-bearing instrument deserves.
                counts["empty"] += 1
                runtime.logger.info(f"  {filename}: no scan streams reported")
            else:
                if not dry_run:
                    await asyncio.to_thread(write, filename, streams)
                counts["written"] += 1
                if previewed < _PREVIEW_LIMIT:
                    previewed += 1
                    runtime.logger.info(f"  {filename}: {len(streams)} scan streams")
            done += 1
            if done % _PROGRESS_EVERY == 0:
                runtime.logger.info(f"  ... {done} of {len(pending)} files read")

    for start in range(0, len(pending), _READ_BATCH):
        await asyncio.gather(
            *(one(filename) for filename in pending[start : start + _READ_BATCH])
        )
    return counts


def _limit() -> int | None:
    """How many files one run may read, from ``CENSUS_LIMIT``.

    :return: The cap, or None for no cap. A value that is not a positive
        number is reported and ignored rather than silently capping the run at
        zero.
    :rtype: int | None
    """
    raw = os.environ.get("CENSUS_LIMIT")
    if not raw:
        return None
    try:
        limit = int(raw)
    except ValueError:
        limit = 0
    if limit <= 0:
        runtime.logger.warning(
            f"CENSUS_LIMIT={raw!r} is not a positive whole number; ignoring it "
            f"and reading every file that carries no census."
        )
        return None
    return limit


async def run() -> None:
    """Find the files carrying no scan-stream census and write them one."""
    await configure_database_engine()
    dry_run = os.environ.get("DRY_RUN") == "1"
    limit = _limit()

    candidates = await _candidates()
    if not candidates:
        runtime.logger.info(
            f"No files of a census-bearing instrument "
            f"({', '.join(sorted(CENSUS_BEARING_INSTRUMENT_TYPES))})."
        )
        return
    runtime.logger.info(f"Files of a census-bearing instrument: {len(candidates)}")

    pending, survey = await _survey(candidates, _read_props)
    runtime.logger.info(
        f"Carrying no census: {len(pending)} "
        f"(already recorded: {survey['has_census']}, no props: {survey['no_props']})"
    )
    if not pending:
        runtime.logger.info("Nothing left to do.")
        return

    batch = pending if limit is None else pending[:limit]
    if len(batch) < len(pending):
        runtime.logger.info(
            f"CENSUS_LIMIT={limit}: reading {len(batch)} of them this run."
        )

    counts = await _fill(batch, _read_census, _write_census, dry_run)

    written = "would record" if dry_run else "recorded"
    remaining = len(pending) - counts["written"]
    runtime.logger.info("=" * 80)
    runtime.logger.info("BACKFILL SCAN STREAM CENSUS COMPLETE")
    runtime.logger.info(
        f"Candidates: {len(candidates)}, "
        f"already recorded: {survey['has_census']}, "
        f"{written}: {counts['written']}, "
        f"no scan streams reported: {counts['empty']}, "
        f"unreadable: {counts['unreadable']}, "
        f"no props: {survey['no_props']}, "
        f"still carrying none after this run: {remaining}"
    )
    runtime.logger.info("=" * 80)
    if counts["unreadable"] or counts["empty"]:
        runtime.logger.warning(
            f"Scan stream census backfill could not read {counts['unreadable']} "
            f"files and found no scan streams in {counts['empty']}; those keep "
            f"no census, and the method binding backfill goes on skipping them."
        )
