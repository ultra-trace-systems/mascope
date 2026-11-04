"""
Maintenance script to flag the peaks of raw Orbitrap files weak under the
local floor, in the peak stores written before the floor existed.

An Orbitrap peak is weak when its signal-to-noise is under
``SIGNAL_TO_NOISE_THRESHOLD``, or when it sits under the floor of a dense
stretch of its own spectrum (``mascope_signal.peak.below_local_floor``,
#2752). The flag is set when a file's peaks are detected, so every store
written before the floor keeps the signal-to-noise flag alone until its file
is detected again. Around the reagent ions of a file averaged over many
microscans that leaves a floor of maxima, tens of thousands of peaks a
file, which the peak list, calibration and matching all read as peaks.

The rule reads only what the store holds - each peak's m/z,
``sum_peak_heights`` and ``signal_to_noise``, and its ``polarity``, or its
``stream`` in a per-stream store - so this recomputes the flag in place,
exactly as ``OrbiPeakDetector._flag_weak_peaks`` does, with no raw file and
no new detection. It writes ``is_weak`` back only to a store where it
differs.

**What else a new flag changes.**

* A per-stream store's ``stitch_overlaps`` attribute is what two streams
  read of the ions both hold among the peaks a load keeps
  (``OrbiPeakDetector._stitch``), so it is computed again along with the
  flag, from the store's own stream keys, which state each stream's
  polarity and scan ranges: all the reading takes of a stream.
* The matches of a file's samples were made on the old peak list. Before
  the flag is written, every batch holding a sample of a file whose flag
  changed is set to ``rematch`` and every match of those samples is
  removed, as a calibration apply does for its file. A batch in
  ``rematch`` shows that it is out of date, and its status icon or
  **Refresh matches** computes the matches its samples lack: all of them,
  on the new list. Set to ``rematch`` alone, that would keep the matches on
  the peaks now weak. A run that stops before the write leaves nothing
  worse than a rematch on the same peaks, and the file is read again.
* The calibration is left as it was fit, on the peaks it had then: this
  writes no fit. **Recalibrate** in a batch's context menu fits its files
  again on the new peak list.

**Raw Orbitrap files only** (``sample_file.instrument_type = 'orbi'`` whose
raw file is kept): an Orbitrap file converted to zarr is detected on another
path, which flags no peak weak, and is left as it is.

**Bounded and resumable**, as the axis fingerprint backfill is. Every file
read is recorded in its ``.props`` with the rule's parameters, whatever came
of it, so a later run moves past it; ``REFLAG_LIMIT`` caps how many files
one run reads. Run it again until it reports nothing left to do. A change
of the rule's parameters reaches every file again. ``REFLAG_RETRY=1`` reads
again the files a run could not read.

The flag is written under the file's detection lock, and only where the
store and the calibration in its props still hold what it was computed
from, read once before it: a detection or a calibration of the file that
landed in between is counted as moved, and the file is read again by the
next run. The overlaps are written before the flag and the record after
it, so a write that stops partway is finished by the next run. A raw file
with no peak store yet is not offered: it gets the rule when its peaks are
detected.

``DRY_RUN=1`` reads the same files and writes nothing: per file whose flag
would change, how many peaks would turn weak (and kept), how many samples
would have their matches removed and how many batches would be set to
rematch, with the totals per instrument.

**Undoing it.** The pre-script ``pg_dump`` covers the batch statuses and
the matches; the flags are in the filestore. The flag before the floor was the
signal-to-noise cutoff alone, which the store still holds, so it can be
computed back from it if ever needed.

Usage:
    DRY_RUN=1 mascope prod db script run reflag_orbitrap_weak_peaks
    mascope prod db script run reflag_orbitrap_weak_peaks

    REFLAG_LIMIT=2000 mascope prod db script run reflag_orbitrap_weak_peaks
    REFLAG_RETRY=1 mascope prod db script run reflag_orbitrap_weak_peaks

Date: 2026-11-03
Issue: #2754
"""

import asyncio
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

import numpy as np
import zarr
from sqlalchemy import text

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
import mascope_signal.mz_factor as m_factor
import mascope_signal.peak as m_peak
import mascope_signal.stitch as m_stitch
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.runtime import runtime
from mascope_thermo.scan_filter import parse_stream_key


#: The props field recording that a file was read under the rule, and what
#: came of it. Written by this script alone; nothing reads it but this
#: script.
RECORD_FIELD = "weak_flag_reflag"

#: The instrument class whose files carry the rule.
ORBITRAP = "orbi"

#: The sample file type the raw Orbitrap detector writes stores for.
ORBITRAP_RAW = "orbi_raw"

#: What a file's read came to, as its record says it.
CHANGED = "changed"
CURRENT = "current"
UNREADABLE = "unreadable"

#: Props read at once while surveying: small local JSON loads.
_PROPS_CONCURRENCY = 16

#: Stores read at once. A dense store's floor is a median per peak, seconds
#: to a minute of numpy, so this is about the load the run puts on a server
#: that is also ingesting.
_READ_CONCURRENCY = 2

#: Files surveyed, and read, per gathered batch.
_SURVEY_BATCH = 2000
_READ_BATCH = 50

#: A progress line every this many files read.
_PROGRESS_EVERY = 200

#: Read failures logged with a traceback.
_TRACEBACK_LIMIT = 3

#: Every Orbitrap file, newest first: a bounded run reaches the files a
#: person is most likely to be looking at.
_CANDIDATES_SQL = """
    SELECT sf.sample_file_id AS sample_file_id, sf.filename AS filename,
           sf.instrument AS instrument
    FROM sample_file sf
    WHERE sf.instrument_type = :instrument_type
    ORDER BY sf.datetime_utc DESC, sf.filename DESC
"""


class BadLimit(ValueError):
    """``REFLAG_LIMIT`` was set to something that is not a bound."""


class Moved(RuntimeError):
    """The store changed between the read and the write."""


def rule() -> dict:
    """The parameters of the weak flag, as a file's record holds them: a
    file read under other parameters is read again."""
    return {
        "signal_to_noise": m_peak.SIGNAL_TO_NOISE_THRESHOLD,
        "window_ppm": m_peak.LOCAL_FLOOR_WINDOW_PPM,
        "min_neighbours": m_peak.LOCAL_FLOOR_MIN_NEIGHBOURS,
        "factor": m_peak.LOCAL_FLOOR_FACTOR,
    }


@dataclass(frozen=True)
class Reflag:
    """The flag one store gets under the rule, and what it changes."""

    #: The new flag, one per row of the store.
    is_weak: np.ndarray
    #: Rows that turn weak, and rows weak before that are kept now.
    turned_weak: int
    turned_kept: int
    #: The store's new ``stitch_overlaps``, or None where it records none.
    overlaps: list | None
    #: What it was computed from (:func:`snapshot`), to tell at the write
    #: whether the store still holds it.
    inputs: dict

    @property
    def changed(self) -> bool:
        return bool(self.turned_weak or self.turned_kept)


#: The store's variables the flag and the overlaps are computed from, and
#: the flag they replace.
_VARIABLES = (
    "mz",
    "sum_peak_heights",
    "signal_to_noise",
    "polarity",
    "is_weak",
    "is_satellite",
    "stream",
    "scan_stream",
)


def snapshot(store, props: dict) -> dict:
    """Everything the flag and the overlaps are computed from, read once.

    A store opened by ``mascope_file.io.load_array`` is lazy: each
    ``.values`` reads the disk again. So the computation takes its arrays
    from here, and the write compares a fresh snapshot against this one: a
    detection or a calibration that lands while the floor is computed is
    caught.

    :param store: A raw Orbitrap file's whole peak store.
    :param props: The file's ``.props``: the calibration the overlaps place
        each stream's peaks by.
    """
    found = {
        name: np.asarray(store[name].values)
        for name in _VARIABLES
        if name in store.variables
    }
    found["streams"] = m_compute.peak_store_streams(store)
    found["records_overlaps"] = m_stitch.STITCH_OVERLAPS_ATTR in store.attrs
    found["mz_calibration"] = props.get("mz_calibration")
    return found


def _same(first: dict, second: dict) -> bool:
    if first.keys() != second.keys():
        return False
    for name, a in first.items():
        b = second[name]
        if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
            if not (
                isinstance(a, np.ndarray)
                and isinstance(b, np.ndarray)
                and np.array_equal(a, b, equal_nan=a.dtype.kind in "fc")
            ):
                return False
        elif a != b:
            return False
    return True


def _json(value):
    """A value as the store's attributes hand it back: JSON."""
    return json.loads(json.dumps(value))


def recompute(store, props: dict) -> Reflag:
    """The weak flag of one store under the rule.

    :param store: A raw Orbitrap file's whole peak store, weak and satellite
        peaks included (``mascope_file.io.load_array``).
    :param props: The file's ``.props``, for the calibration its streams'
        peaks carry.
    :raises KeyError: If the store lacks a variable the rule reads.
    :return: The flag, and the overlaps of a per-stream store that records
        them, read among the peaks the new flag keeps.
    """
    inputs = snapshot(store, props)
    mz = inputs["mz"]
    heights = inputs["sum_peak_heights"]
    keys = inputs["streams"]
    # One spectrum per stream for a store detected per stream, else one per
    # polarity: OrbiPeakDetector._flag_weak_peaks
    spectrum = inputs["stream"] if keys else inputs["polarity"]
    is_weak = (
        inputs["signal_to_noise"] < m_peak.SIGNAL_TO_NOISE_THRESHOLD
    ) | m_peak.below_local_floor(mz, heights, spectrum)
    before = inputs["is_weak"].astype(bool)

    overlaps = None
    if keys and inputs["records_overlaps"]:
        stream = inputs["stream"]
        # A stream's reading takes its polarity and its scan ranges, which
        # its key states as the census does (four decimals, as the map
        # reads an edge)
        streams = [
            {"signature": parse_stream_key(key).scan_filter.signature()} for key in keys
        ]
        overlaps = m_stitch.overlap_readings(
            streams,
            mz,
            stream,
            heights,
            ~(is_weak | inputs["is_satellite"].astype(bool)),
            np.bincount(inputs["scan_stream"], minlength=len(keys)),
            calibration=m_factor.stream_factors(inputs["mz_calibration"], keys)[stream],
        )
    return Reflag(
        is_weak=is_weak,
        turned_weak=int(np.count_nonzero(is_weak & ~before)),
        turned_kept=int(np.count_nonzero(~is_weak & before)),
        overlaps=overlaps,
        inputs=inputs,
    )


def _load_store(filename: str):
    """A file's whole peak store, or None where it has none."""
    try:
        return m_io.load_array(filename, var="peak_timeseries")
    except FileNotFoundError:
        return None


def has_store(filename: str) -> bool:
    """Whether a file has a peak store, asked without opening it."""
    return os.path.exists(m_name.filename_to_zarr_path(filename, "peak_timeseries"))


def read(filename: str) -> Reflag | None:
    """The new flag of one file's store, or None where it has no store.

    :raises Exception: If the store or the props cannot be read.
    """
    store = _load_store(filename)
    if store is None:
        return None
    return recompute(store, m_io.read_props(filename))


def _write_overlaps(filename: str, overlaps: list) -> None:
    path = m_name.filename_to_zarr_path(filename, "peak_timeseries")
    with m_io.zarr_write_lock(path):
        group = m_io.open_zarr_store(path, mode="r+")
        group.attrs[m_stitch.STITCH_OVERLAPS_ATTR] = _json(overlaps)
        zarr.consolidate_metadata(path)


def _record_of(result: str, reflag: Reflag | None = None) -> dict:
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "rule": rule(),
        "result": result,
    }
    if reflag is not None:
        record["turned_weak"] = reflag.turned_weak
        record["turned_kept"] = reflag.turned_kept
    return record


def write(filename: str, reflag: Reflag) -> None:
    """Write a store's new flag and its overlaps, where it still holds what
    they were computed from, and record the file.

    Under the file's detection lock, which a detection and a calibration of
    the file take as well (``mascope_file.io.peak_detection_lock_path``):
    the store, and the calibration in the props, are compared against what
    the flag was computed from under it. The overlaps are written first and
    the flag after them, so the flag is what tells a later run the file is
    done: a write that stops after the overlaps leaves the flag as it was,
    and the next run reads the file again and writes both. The record is
    last, under the same lock, since a calibration writes the props under it.

    :raises Moved: If the store or its calibration changed since it was read.
    :raises TimeoutError: If a detection or a calibration of the file held
        it for longer than ``mascope_file.io.ZARR_PROCESS_LOCK_TIMEOUT``.
    """
    with m_io.zarr_write_lock(m_io.peak_detection_lock_path(filename)):
        store = _load_store(filename)
        if store is None or not _same(
            snapshot(store, m_io.read_props(filename)), reflag.inputs
        ):
            raise Moved(filename)
        if reflag.overlaps is not None:
            _write_overlaps(filename, reflag.overlaps)
        m_io.write_peak_variable(filename, "is_weak", reflag.is_weak)
        m_io.update_props(filename, {RECORD_FIELD: _record_of(CHANGED, reflag)})


def _record(filename: str, result: str) -> None:
    """Record that a file was read under the rule and left as it was, or
    could not be read; under its detection lock, as :func:`write` records."""
    with m_io.zarr_write_lock(m_io.peak_detection_lock_path(filename)):
        m_io.update_props(filename, {RECORD_FIELD: _record_of(result)})


def _read_props(filename: str) -> dict | None:
    try:
        return m_io.read_props(filename)
    except Exception:  # noqa: BLE001
        return None


def _is_raw(filename: str) -> bool:
    """Whether a file is a raw Orbitrap file, asked without opening it."""
    try:
        return m_name.get_sample_file_type(filename) == ORBITRAP_RAW
    except Exception:  # noqa: BLE001
        return False


def is_current(props: dict, retry: bool = False) -> bool:
    """Whether a file was read under the rule of this build already.

    :param props: Its ``.props``.
    :param retry: Whether a file that could not be read is read again.
    """
    record = props.get(RECORD_FIELD)
    if not isinstance(record, dict) or record.get("rule") != rule():
        return False
    return not (retry and record.get("result") == UNREADABLE)


async def _candidates() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            text(_CANDIDATES_SQL), {"instrument_type": ORBITRAP}
        )
        return [dict(row._mapping) for row in result]


async def _samples_of(sample_file_id: str) -> tuple[list[str], list[str]]:
    """The samples of one file, and the batches holding them."""
    from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
        fetch_affected_sample_data,
    )

    affected = await fetch_affected_sample_data(sample_file_ids=[sample_file_id])
    return affected.affected_sample_item_ids, affected.affected_sample_batch_ids


async def _remove_matches(sample_item_ids: list[str]) -> None:
    """Remove every match of the samples, as a calibration apply removes
    those of its file's samples: a rematch of a batch in ``rematch``
    (its status icon, **Refresh matches**) computes only the matches a
    sample lacks, and would keep those made on the peaks now weak."""
    from mascope_backend.api.controllers.match.match_controller import (
        match_remove_sample,
    )

    for sample_item_id in sample_item_ids:
        await match_remove_sample(
            sample_item_id=sample_item_id,
            full_remove=True,
            independent_transaction=False,
        )


async def _queue_rematch(sample_batch_ids: list[str]) -> None:
    """Set batches to ``rematch``, as ``set_batches_to_rematch_status``
    does."""
    from mascope_backend.api.controllers.sample.batches.status.service import (
        update_sample_batch_status,
    )

    await update_sample_batch_status(
        sample_batch_ids=sample_batch_ids,
        status="rematch",
        independent_transaction=False,
    )


async def survey(
    candidates: list[dict],
    read_props: Callable[[str], dict | None],
    is_raw: Callable[[str], bool],
    has_store: Callable[[str], bool],
    limit: int | None = None,
    retry: bool = False,
) -> tuple[list[dict], Counter]:
    """The files to read, and counts for the candidates left out.

    Stops as soon as ``limit`` files are found, between batches, so repeated
    capped runs do not each read every props on the server.

    :param candidates: Rows from the candidates query, newest first.
    :param read_props: Reads a file's props, or a stand-in.
    :param is_raw: Whether a file is a raw Orbitrap file, or a stand-in.
    :param has_store: Whether a file has a peak store, or a stand-in. One
        without (a detection that failed, or a file still being ingested)
        gets the rule when its peaks are detected, and is not offered.
    :param limit: Stop once this many are found; None surveys them all.
    :param retry: Read again a file a run could not read.
    :return: The candidates to read, newest first, and the counts:
        ``surveyed``, ``not_raw``, ``no_props``, ``no_store``, ``current``,
        ``pending`` and ``beyond_cap``, those the cap left for a later run.
    """
    gate = asyncio.Semaphore(_PROPS_CONCURRENCY)

    async def one(candidate: dict) -> tuple[dict, str]:
        async with gate:
            filename = candidate["filename"]
            if not await asyncio.to_thread(is_raw, filename):
                return candidate, "not_raw"
            props = await asyncio.to_thread(read_props, filename)
            if props is None:
                return candidate, "no_props"
            if not await asyncio.to_thread(has_store, filename):
                return candidate, "no_store"
            if is_current(props, retry):
                return candidate, "current"
            return candidate, "pending"

    pending: list[dict] = []
    counts: Counter = Counter()
    for start in range(0, len(candidates), _SURVEY_BATCH):
        batch = candidates[start : start + _SURVEY_BATCH]
        for candidate, kind in await asyncio.gather(*(one(c) for c in batch)):
            counts["surveyed"] += 1
            counts[kind] += 1
            if kind == "pending":
                pending.append(candidate)
        if limit is not None and len(pending) >= limit:
            counts["beyond_cap"] = len(pending) - limit
            return pending[:limit], counts
    return pending, counts


@dataclass
class Outcome:
    """What a run did, file by file and in total."""

    counts: Counter
    #: Per instrument: files whose flag changes, rows turned weak, rows
    #: turned kept.
    instruments: dict
    #: Every batch set, or to be set, to rematch.
    batches: set
    #: Every sample whose matches were, or would be, removed.
    samples: set


async def reflag(
    pending: list[dict],
    read_store: Callable[[str], Reflag | None],
    write_store: Callable[[str, Reflag], None] | None,
    record: Callable[[str, str], None] | None,
    samples_of: Callable[[str], Awaitable[tuple[list[str], list[str]]]],
    queue_rematch: Callable[[list[str]], Awaitable[None]] | None,
    remove_matches: Callable[[list[str]], Awaitable[None]] | None,
) -> Outcome:
    """Read each pending file's store, and write what changed.

    A file whose flag changes has its batches set to rematch first, then its
    samples' matches removed, then its store written and recorded
    (:func:`write`), so an interrupted run leaves nothing a later one does
    not do again: the file is read again until its record is written. The
    dry run passes None for the four writers and writes nothing.

    :param pending: Candidates from :func:`survey`.
    :param read_store: :func:`read`, or a stand-in.
    :param write_store: :func:`write`, a stand-in, or None.
    :param record: :func:`_record`, a stand-in, or None: records a file left
        as it was, or one that could not be read.
    :param samples_of: The samples of a file, and the batches holding them,
        by its id.
    :param queue_rematch: Sets batches to rematch, or None.
    :param remove_matches: Removes every match of samples, or None.
    :return: What was done.
    """
    gate = asyncio.Semaphore(_READ_CONCURRENCY)
    outcome = Outcome(
        counts=Counter(),
        instruments=defaultdict(lambda: [0, 0, 0]),
        batches=set(),
        samples=set(),
    )
    counts = outcome.counts
    dry_run = write_store is None
    traced = 0
    done = 0

    async def note(filename: str, result: str):
        if record is None:
            return
        try:
            await asyncio.to_thread(record, filename, result)
        except Exception as exc:  # noqa: BLE001
            counts["unrecorded"] += 1
            runtime.logger.info(f"  Cannot record {filename}: {exc}")

    async def one(candidate: dict) -> None:
        nonlocal traced, done
        filename = candidate["filename"]
        async with gate:
            try:
                found = await asyncio.to_thread(read_store, filename)
            except Exception as exc:  # noqa: BLE001
                counts["unreadable"] += 1
                if traced < _TRACEBACK_LIMIT:
                    traced += 1
                    runtime.logger.opt(exception=exc).info(
                        f"  Cannot read the store of {filename}"
                    )
                else:
                    runtime.logger.info(f"  Cannot read the store of {filename}: {exc}")
                await note(filename, UNREADABLE)
                return
            finally:
                done += 1
                if done % _PROGRESS_EVERY == 0:
                    runtime.logger.info(f"  ... {done} of {len(pending)} files read")
            if found is None:
                # Nothing recorded: a file with no store yet gets the rule
                # when its peaks are detected
                counts["no_store"] += 1
                return
            if not found.changed:
                counts["current"] += 1
                await note(filename, CURRENT)
                return

            samples, batches = await samples_of(candidate["sample_file_id"])
            line = (
                f"  {filename}: {found.is_weak.size} peaks, "
                f"{found.turned_weak} turn weak"
                + (f", {found.turned_kept} turn kept" if found.turned_kept else "")
                + f", matches of {len(samples)} sample(s) removed"
                + f", {len(batches)} batch(es) to rematch"
            )
            if not dry_run:
                try:
                    if batches and queue_rematch is not None:
                        await queue_rematch(batches)
                    if samples and remove_matches is not None:
                        await remove_matches(samples)
                    await asyncio.to_thread(write_store, filename, found)
                except Moved:
                    counts["moved"] += 1
                    runtime.logger.info(
                        f"  {filename}: its store changed while it was read; "
                        "the next run reads it again"
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    counts["unwritable"] += 1
                    runtime.logger.info(f"  Cannot write {filename}: {exc}")
                    return
            runtime.logger.info(line)
            counts["changed"] += 1
            counts["turned_weak"] += found.turned_weak
            counts["turned_kept"] += found.turned_kept
            if found.overlaps is not None:
                counts["overlaps"] += 1
            totals = outcome.instruments[candidate.get("instrument") or "unknown"]
            totals[0] += 1
            totals[1] += found.turned_weak
            totals[2] += found.turned_kept
            outcome.batches.update(batches)
            outcome.samples.update(samples)

    for start in range(0, len(pending), _READ_BATCH):
        await asyncio.gather(*(one(c) for c in pending[start : start + _READ_BATCH]))
    return outcome


def _limit() -> int | None:
    """How many files one run may read, from ``REFLAG_LIMIT``.

    :raises BadLimit: The variable is set to something that is not a
        positive whole number: an operator who set it wanted a bounded run.
    """
    raw = os.environ.get("REFLAG_LIMIT")
    if not raw:
        return None
    try:
        limit = int(raw)
    except ValueError as exc:
        raise BadLimit(
            f"REFLAG_LIMIT={raw!r} is not a whole number. Unset it to read "
            f"every file not yet read under the rule."
        ) from exc
    if limit <= 0:
        raise BadLimit(
            f"REFLAG_LIMIT={raw!r} is not a positive number. Unset it to read "
            f"every file not yet read under the rule."
        )
    return limit


async def run() -> None:
    """Find the raw Orbitrap stores not yet read under the rule, and flag
    their peaks by it."""
    dry_run = os.environ.get("DRY_RUN") == "1"
    retry = os.environ.get("REFLAG_RETRY") == "1"
    limit = _limit()
    await configure_database_engine()

    candidates = await _candidates()
    if not candidates:
        runtime.logger.info("No Orbitrap files.")
        return
    runtime.logger.info(f"Orbitrap files: {len(candidates)}")

    pending, surveyed = await survey(
        candidates, _read_props, _is_raw, has_store, limit, retry
    )
    capped = surveyed["surveyed"] < len(candidates) or surveyed["beyond_cap"] > 0
    runtime.logger.info(
        f"Surveyed {surveyed['surveyed']} of {len(candidates)}: "
        f"{len(pending) + surveyed['beyond_cap']} to read, "
        f"read under the rule already {surveyed['current']}, "
        f"not a raw Orbitrap file {surveyed['not_raw']}, "
        f"no props {surveyed['no_props']}, no peak store yet "
        f"{surveyed['no_store']}; reading {len(pending)} this run"
        + (" (DRY_RUN=1: nothing is written)" if dry_run else "")
    )
    if not pending:
        runtime.logger.info("Nothing left to do.")
        return

    if dry_run:
        outcome = await reflag(pending, read, None, None, _samples_of, None, None)
    else:
        outcome = await reflag(
            pending,
            read,
            write,
            _record,
            _samples_of,
            _queue_rematch,
            _remove_matches,
        )
    counts = outcome.counts

    runtime.logger.info("=" * 80)
    runtime.logger.info(
        "REFLAG ORBITRAP WEAK PEAKS COMPLETE" + (" (DRY RUN)" if dry_run else "")
    )
    verb = "would change" if dry_run else "changed"
    runtime.logger.info(
        f"Read {len(pending)}: {verb} {counts['changed']} "
        f"({counts['turned_weak']} peaks turn weak, {counts['turned_kept']} "
        f"turn kept, stitch overlaps of {counts['overlaps']} computed again), "
        f"already flagged by the rule {counts['current']}, "
        f"no peak store {counts['no_store']}, unreadable {counts['unreadable']}"
        + (
            ""
            if dry_run
            else f", changed while read {counts['moved']}, could not be "
            f"written {counts['unwritable']}, could not be recorded "
            f"{counts['unrecorded']}"
        )
    )
    for instrument, (files, weak, kept) in sorted(
        outcome.instruments.items(), key=lambda item: -item[1][1]
    ):
        runtime.logger.info(
            f"  {instrument}: {files} file(s), {weak} peaks turn weak"
            + (f", {kept} turn kept" if kept else "")
        )
    runtime.logger.info(
        f"Samples {'to have' if dry_run else 'that had'} their matches removed: "
        f"{len(outcome.samples)}; batches {'to set' if dry_run else 'set'} to "
        f"rematch: {len(outcome.batches)}"
    )
    if outcome.batches and not dry_run:
        runtime.logger.info(
            "  Rematch them (their status icon, or Refresh matches) to match "
            "their samples on the new flag."
        )
    if capped:
        runtime.logger.info(
            f"REFLAG_LIMIT={limit} left files for later runs, so run this again "
            f"until it reports nothing left to do."
        )
    if counts["unreadable"] and not retry:
        runtime.logger.info(
            "  Files that could not be read are recorded; REFLAG_RETRY=1 reads "
            "them again."
        )
    runtime.logger.info("=" * 80)


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
