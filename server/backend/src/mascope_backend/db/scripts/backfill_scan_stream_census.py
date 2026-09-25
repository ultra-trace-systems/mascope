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
resumable**: a file that already has a census is never reopened,
``CENSUS_LIMIT`` caps how many files one run reads, and every attempt that
produced no census is recorded in the file's props so the next run moves past
it instead of retrying the same head of the list forever. Run it again until
it reports nothing left to do. ``CENSUS_RETRY=1`` clears that memory and tries
the failures again, after whatever made them fail has been dealt with.

``DRY_RUN=1`` reports what the run would do - the counts all come from the
survey - and reads a handful of files to show what a census looks like. It
deliberately does not read them all: that is where the time goes, and reading
them twice to write them once is the expensive way to be careful.

Usage:
    mascope dev db script run backfill_scan_stream_census
    mascope prod db script run backfill_scan_stream_census

    DRY_RUN=1 mascope prod db script run backfill_scan_stream_census
    CENSUS_LIMIT=5000 mascope prod db script run backfill_scan_stream_census
    CENSUS_RETRY=1 mascope prod db script run backfill_scan_stream_census

Date: 2026-09-25
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import bindparam, text

import mascope_file.io as m_io
import mascope_file.name as m_name
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.method_keys import CENSUS_BEARING_INSTRUMENT_TYPES, usable_streams
from mascope_backend.runtime import runtime
from mascope_thermo.backend import open_backend
from mascope_thermo.streams import scan_streams


#: The props field holding the census. ``SampleFileProps.scan_streams``
#: writes it and ``process.status.read_scan_streams`` reads it back under this
#: same name.
CENSUS_FIELD = "scan_streams"

#: The props field recording an attempt that produced no census, so that a
#: bounded run makes progress instead of reopening the same files. Written by
#: this script alone; nothing reads it but this script.
ATTEMPT_FIELD = "scan_streams_backfill"

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

#: Files a dry run actually opens, to show what a census looks like.
_DRY_RUN_SAMPLE = 20

#: Reader failures logged with a traceback, so a reader regression is visible
#: rather than lost among files whose raw data was simply not kept.
_TRACEBACK_LIMIT = 3

#: A progress line every this many files read.
_PROGRESS_EVERY = 500

#: Files whose props are surveyed, and whose raw data is read, per gathered
#: batch. The semaphores above bound how much runs at once; these bound how
#: much is *pending* at once, which is a different thing on a server holding
#: six figures of files - one coroutine and one future each, awaited together,
#: is hundreds of megabytes before any of them does any work.
_SURVEY_BATCH = 2000
_READ_BATCH = 200

#: Every file of a census-bearing instrument, the ones a method binding could
#: be learned from first.
#:
#: ``routed`` is ``process.service._pipeline_item()`` spelled in SQL, the same
#: filter ``backfill_method_bindings`` reads its history through: an
#: ACQUISITION item, in an ACQUISITION batch, of an ACQUISITION dataset, in a
#: system workspace. Files outside it are still worth a census - the pooled
#: streams note reads one - but a capped run should spend its reads where the
#: bindings are, not on recent manual uploads into people's own workspaces.
_CANDIDATES_SQL = """
    SELECT
        sf.sample_file_id AS sample_file_id,
        sf.filename       AS filename,
        EXISTS (
            SELECT 1
            FROM sample_item si
            JOIN sample_batch sb ON sb.sample_batch_id = si.sample_batch_id
            JOIN dataset d       ON d.dataset_id = sb.dataset_id
            JOIN workspace w     ON w.workspace_id = d.workspace_id
            WHERE si.sample_file_id = sf.sample_file_id
              AND si.sample_item_type = 'ACQUISITION'
              AND sb.sample_batch_type = 'ACQUISITION'
              AND d.dataset_type = 'ACQUISITION'
              AND w.is_system IS TRUE
        ) AS routed
    FROM sample_file sf
    WHERE sf.instrument_type IN :types
    ORDER BY routed DESC, sf.datetime_utc DESC, sf.filename DESC
"""


class BadLimit(ValueError):
    """``CENSUS_LIMIT`` was set to something that is not a bound."""


async def _candidates() -> list[dict]:
    """Every file of a census-bearing instrument, best candidates first.

    Routed files first, then newest first, because a bounded run should reach
    the methods still in use: the census is here to key a method binding, and
    a method last run a year ago routes nothing today. A run carried through
    to the end reaches the same state whichever way it goes.

    :return: One dict per file: sample_file_id, filename and routed.
    :rtype: list[dict]
    """
    async with async_session() as session:
        result = await session.execute(
            text(_CANDIDATES_SQL).bindparams(bindparam("types", expanding=True)),
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

    The shape is judged by :func:`usable_streams`, the same filter
    ``read_scan_streams`` applies before the binding backfill sees a census.
    Anything it drops is a census the binding cannot key on, so calling such a
    file filled here would leave it skipped there for good.

    :param props: A sample file's ``.props``.
    :return: True when the file should be reopened for its census.
    :rtype: bool
    """
    return not usable_streams(props.get(CENSUS_FIELD))


def _attempted(props: dict) -> bool:
    """Whether a previous run already tried this file and got no census."""
    return bool(props.get(ATTEMPT_FIELD))


def _read_props(filename: str) -> dict | None:
    """One sample file's ``.props``, or None when there are none to read."""
    try:
        return m_io.read_props(filename)
    except Exception:  # noqa: BLE001
        return None


def _has_raw(filename: str) -> bool:
    """Whether the sample still holds the raw data a census is read from.

    Two ``os.path.isfile`` calls, so the survey can leave out the files whose
    raw data was cleared without opening anything. Without this they stay at
    the head of the list on every run and a capped run makes no progress.
    """
    try:
        return m_name.get_sample_file_type(filename).endswith("_raw")
    except Exception:  # noqa: BLE001
        return False


def _read_census(
    filename: str,
) -> tuple[list[dict] | None, str | None, Exception | None]:
    """The scan-stream census of one file, read from its raw data.

    The reader's exception comes back rather than being logged here, because
    the caller decides how many of them are worth a traceback - and it has to
    be the object rather than the live exception state: this runs in a worker
    thread, so by the time the caller logs, ``opt(exception=True)`` would find
    nothing and this backend's formatter raises on the empty record.

    :param filename: Sample file name (base, not full path).
    :return: ``(census, None, None)``, or ``(None, reason, error)`` where
        reason is ``"no_raw"`` when the raw data is not there to read and
        ``"reader"`` when it is and the reader refused it. The two are counted
        apart because a reader regression would otherwise read as cleared raw
        data.
    :rtype: tuple[list[dict] | None, str | None, Exception | None]
    """
    try:
        path = m_name.filename_to_datafile_path(filename)
    except FileNotFoundError:
        # The sample holds no source data at all: converted data only.
        return None, "no_raw", None
    if not os.path.isfile(path):
        return None, "no_raw", None
    try:
        with open_backend(path) as reader:
            return scan_streams(reader), None, None
    except Exception as exc:  # noqa: BLE001
        # Decided from the path above rather than from the exception: a
        # reader raises its own type for a file it cannot open, so catching
        # FileNotFoundError here counted a missing file as a reader failure
        # and defeated the point of telling the two apart.
        return None, "reader", exc


def _write_census(filename: str, streams: list[dict]) -> None:
    """Record ``streams`` as one file's census, leaving the rest of its props.

    Clears any attempt a previous run recorded: after a successful
    ``CENSUS_RETRY=1`` the file would otherwise keep a note saying the reader
    refused it, next to the census the reader produced.
    """
    m_io.update_props(filename, {CENSUS_FIELD: streams, ATTEMPT_FIELD: None})


def _write_attempt(filename: str, result: str) -> None:
    """Record that this file was read and produced no census."""
    m_io.update_props(
        filename,
        {
            ATTEMPT_FIELD: {
                "at": datetime.now(timezone.utc).isoformat(),
                "result": result,
            }
        },
    )


async def _survey(
    candidates: list[dict],
    read_props: Callable[[str], dict | None],
    has_raw: Callable[[str], bool],
    limit: int | None = None,
    retry: bool = False,
) -> tuple[list[str], dict[str, int]]:
    """The files to read, and counts for the candidates left out.

    Stops as soon as ``limit`` files have been found. The documented way to
    fill a large server is repeated capped runs, and surveying every candidate
    each time would read the whole filestore's props once per run to fill one
    run's worth of censuses.

    :param candidates: Rows from :func:`_candidates`, best first.
    :param read_props: Reads a file's props: :func:`_read_props`, or a
        stand-in.
    :param has_raw: Whether the raw data is still there: :func:`_has_raw`, or
        a stand-in.
    :param limit: Stop once this many files are found; None surveys them all.
    :param retry: Include files a previous run tried and got no census from.
    :return: The filenames to read, best first, and the counts of the
        candidates left out. ``surveyed`` says how many were looked at, which
        is every candidate only on an uncapped run.
    :rtype: tuple[list[str], dict[str, int]]
    """
    gate = asyncio.Semaphore(_PROPS_CONCURRENCY)

    async def one(filename: str) -> tuple[str, dict | None]:
        async with gate:
            return filename, await asyncio.to_thread(read_props, filename)

    pending: list[str] = []
    counts = {
        "surveyed": 0,
        "has_census": 0,
        "no_props": 0,
        "no_raw": 0,
        "already_tried": 0,
    }
    for start in range(0, len(candidates), _SURVEY_BATCH):
        batch = candidates[start : start + _SURVEY_BATCH]
        for filename, props in await asyncio.gather(
            *(one(c["filename"]) for c in batch)
        ):
            counts["surveyed"] += 1
            if props is None:
                counts["no_props"] += 1
            elif not needs_census(props):
                counts["has_census"] += 1
            elif _attempted(props) and not retry:
                counts["already_tried"] += 1
            elif not await asyncio.to_thread(has_raw, filename):
                counts["no_raw"] += 1
            else:
                pending.append(filename)
        if limit is not None and len(pending) >= limit:
            return pending[:limit], counts
    return pending, counts


async def _fill(
    pending: list[str],
    read: Callable[[str], tuple[list[dict] | None, str | None]],
    write: Callable[[str, list[dict]], None],
    write_attempt: Callable[[str, str], None],
) -> dict[str, int]:
    """Read each pending file's census and write it, a few files at a time.

    Written as each read finishes rather than in one pass at the end: the run
    is expected to be interrupted - a production server holds six figures of
    these - and a file whose census is on disk is one the next run skips.

    A file that produces no census has the attempt recorded instead, so the
    next run moves past it. A write that fails is counted and the run carries
    on: one sample deleted while the run is walking the filestore should not
    end a multi-hour job with a traceback and no summary.

    :param pending: Filenames to read, from :func:`_survey`.
    :param read: Reads one census as ``(streams, reason, error)``:
        :func:`_read_census`, or a stand-in.
    :param write: Writes one census: :func:`_write_census`, or a stand-in.
    :param write_attempt: Records a read that produced none.
    :return: The per-outcome counts.
    :rtype: dict[str, int]
    """
    gate = asyncio.Semaphore(_READ_CONCURRENCY)
    counts = {"written": 0, "no_raw": 0, "reader": 0, "empty": 0, "unwritable": 0}
    previewed = 0
    traced = 0
    done = 0

    def record(filename: str, result: str) -> None:
        try:
            write_attempt(filename, result)
        except Exception as exc:  # noqa: BLE001
            counts["unwritable"] += 1
            runtime.logger.info(f"  Cannot record the attempt on {filename}: {exc}")

    async def one(filename: str) -> None:
        nonlocal previewed, traced, done
        async with gate:
            streams, reason, error = await asyncio.to_thread(read, filename)
            if reason == "no_raw":
                counts["no_raw"] += 1
                # Nothing is recorded: the survey leaves these out by itself,
                # cheaply, on every run.
            elif reason == "reader":
                counts["reader"] += 1
                if traced < _TRACEBACK_LIMIT:
                    traced += 1
                    runtime.logger.opt(exception=error).info(
                        f"  Reader refused {filename}"
                    )
                else:
                    runtime.logger.info(f"  Reader refused {filename}")
                await asyncio.to_thread(record, filename, "reader")
            elif not streams:
                # The file opened and reported no streams at all. Writing an
                # empty census would record it as answered and stop the next
                # run retrying it, which is the opposite of what an empty
                # answer from a census-bearing instrument deserves - so the
                # attempt is recorded instead, and CENSUS_RETRY=1 tries again.
                counts["empty"] += 1
                runtime.logger.info(f"  {filename}: no scan streams reported")
                await asyncio.to_thread(record, filename, "empty")
            else:
                try:
                    await asyncio.to_thread(write, filename, streams)
                except Exception as exc:  # noqa: BLE001
                    counts["unwritable"] += 1
                    runtime.logger.info(f"  Cannot write {filename}: {exc}")
                else:
                    counts["written"] += 1
                    if previewed < _PREVIEW_LIMIT:
                        previewed += 1
                        runtime.logger.info(
                            f"  {filename}: {len(streams)} scan streams"
                        )
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

    :return: The cap, or None when the variable is unset.
    :raises BadLimit: The variable is set to something that is not a positive
        whole number. Refused rather than ignored: an operator who set it
        wanted a bounded run, and falling back to reading every raw file on
        the server is the expensive way to answer a typo.
    """
    raw = os.environ.get("CENSUS_LIMIT")
    if not raw:
        return None
    try:
        limit = int(raw)
    except ValueError as exc:
        raise BadLimit(
            f"CENSUS_LIMIT={raw!r} is not a whole number. Unset it to read "
            f"every file that carries no census."
        ) from exc
    if limit <= 0:
        raise BadLimit(
            f"CENSUS_LIMIT={raw!r} is not a positive number. Unset it to read "
            f"every file that carries no census."
        )
    return limit


async def run() -> None:
    """Find the files carrying no scan-stream census and write them one."""
    await configure_database_engine()
    dry_run = os.environ.get("DRY_RUN") == "1"
    retry = os.environ.get("CENSUS_RETRY") == "1"
    limit = _limit()

    candidates = await _candidates()
    if not candidates:
        runtime.logger.info(
            f"No files of a census-bearing instrument "
            f"({', '.join(sorted(CENSUS_BEARING_INSTRUMENT_TYPES))})."
        )
        return
    routed = sum(1 for c in candidates if c["routed"])
    runtime.logger.info(
        f"Files of a census-bearing instrument: {len(candidates)} "
        f"({routed} of them routed by the pipeline, read first)"
    )

    # A dry run surveys every candidate: the counts are the point of it, and
    # they are only true of the whole server when nothing stopped early.
    pending, survey = await _survey(
        candidates, _read_props, _has_raw, None if dry_run else limit, retry
    )
    capped = survey["surveyed"] < len(candidates)
    runtime.logger.info(
        f"Surveyed {survey['surveyed']} of {len(candidates)}: "
        f"{len(pending)} to read, already recorded {survey['has_census']}, "
        f"raw data not kept {survey['no_raw']}, "
        f"tried before {survey['already_tried']}, no props {survey['no_props']}"
    )
    if survey["already_tried"] and not retry:
        runtime.logger.info(
            "  Files tried before produced no census; CENSUS_RETRY=1 tries them again."
        )
    if not pending:
        runtime.logger.info("Nothing left to do.")
        return

    if dry_run:
        sample = pending[:_DRY_RUN_SAMPLE]
        runtime.logger.info(
            f"DRY_RUN=1: {len(pending)} files would be read and recorded"
            f"{f' (capped at {limit} per run)' if limit else ''}. "
            f"Reading {len(sample)} of them to show what is there; "
            f"nothing is written."
        )
        counts = await _fill(sample, _read_census, _no_write, _no_write)
        runtime.logger.info("=" * 80)
        runtime.logger.info("BACKFILL SCAN STREAM CENSUS COMPLETE (DRY RUN)")
        runtime.logger.info(
            f"Candidates: {len(candidates)}, would record: {len(pending)}, "
            f"already recorded: {survey['has_census']}, "
            f"raw data not kept: {survey['no_raw']}, "
            f"no props: {survey['no_props']}, "
            f"sampled {len(sample)}: {counts['written']} with a census, "
            f"{counts['empty']} with none, {counts['reader']} the reader refused"
        )
        runtime.logger.info("=" * 80)
        return

    if limit and len(pending) >= limit:
        runtime.logger.info(f"CENSUS_LIMIT={limit}: reading {len(pending)} this run.")

    counts = await _fill(pending, _read_census, _write_census, _write_attempt)

    runtime.logger.info("=" * 80)
    runtime.logger.info("BACKFILL SCAN STREAM CENSUS COMPLETE")
    runtime.logger.info(
        f"Candidates: {len(candidates)}, surveyed: {survey['surveyed']}, "
        f"recorded: {counts['written']}, "
        f"no scan streams reported: {counts['empty']}, "
        f"reader refused: {counts['reader']}, "
        f"raw data not kept: {survey['no_raw'] + counts['no_raw']}, "
        f"could not be written: {counts['unwritable']}, "
        f"no props: {survey['no_props']}"
    )
    if capped:
        runtime.logger.info(
            "The survey stopped at the cap, so run this again until it reports "
            "nothing left to do."
        )
    runtime.logger.info("=" * 80)
    if counts["reader"] or counts["empty"] or counts["unwritable"]:
        runtime.logger.warning(
            f"Scan stream census backfill: the reader refused {counts['reader']} "
            f"files, {counts['empty']} reported no scan streams, and "
            f"{counts['unwritable']} could not be written. Those keep no census, "
            f"and the method binding backfill goes on skipping them."
        )


def _no_write(*_args) -> None:
    """A write that does nothing, for the dry run."""


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
