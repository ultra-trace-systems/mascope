"""
Maintenance script to rebuild the raw Orbitrap peak stores their files no
longer read back, in place, keeping every sample.

A raw Orbitrap file's peak store keeps the scans it was built on, while the
scans the reader selects are decided anew on every read. The rule that leaves
a file's first scan out as an outlier is one of those decisions, and it has
changed more than once: it once compared the first scan with every other scan
of the file, and since 1.15.0 with those of its own MS order and polarity.
Since this release it also leaves out a first scan far above the other scans
of its own experiment (``mascope_thermo.first_scan``), where it kept anything
under five times their median. A store built under an earlier rule holds one
scan more, or one fewer, than its file now reads, and every read that divides
the store's sums by the file's scans refuses it as stale
(``mascope_signal.compute.StalePeakStoreError``): the file's peak list fails,
and so does every alignment of a batch that holds one of its samples.

Re-processing repairs such a file, but it makes all of the file's samples
anew, and it refuses a file a person made a sample from - which every file
copied into a workspace's batch is. *Detect peaks* repairs a file and keeps
its samples, one file at a time. This does what *Detect peaks* does, to every
file that needs it:

- **Which files.** Every raw Orbitrap file whose peak store holds peaks. The
  store's scan axis is compared with the scans the file reads now, from the
  raw file's scan index alone: a pooled store against every scan of the file,
  a per-stream store stream by stream, each against its own stream's scans -
  the check the reads make (``mascope_signal.compute.check_stored_scan_axis``).
  A store that agrees is not touched.
- **What a rebuild does.** The file's peaks are detected again by the
  decision its ``.props`` records, whole or per stream, and on the m/z
  calibration it has (``mascope_signal.peak.compute_peaks``, as *Detect
  peaks* runs it). Every sample of the file keeps its definition, in every
  batch and every workspace: nothing is deleted or made anew, and no
  calibration is refit.
- **Before the store is written**, every batch holding one of the file's
  samples is set to ``rematch`` - one already waiting on ``recalibrate``
  keeps it, as its recalibration rematches it too - every match of those
  samples is removed and the batch's aligned-peak cache is dropped: the new
  peak list has new peak
  ids, which nothing made on the old one finds. Each sample's TIC is read
  again from the file, as a new sample's is (``fix_sample_item_tic``), and a
  file with an MS2 store has it written again with its group inventory, as a
  re-processing writes them: both hold the scans the reader selects. The peak
  store is written last, so a run that stops partway leaves the file stale,
  and the next run does all of it again.
- **Once the run is done**, rematch the batches it set to ``rematch``: their
  status icon, **Refresh matches**, or a dataset's rematch, which takes every
  batch of the dataset in turn.

**A store without scan counts.** A raw Orbitrap file's store keeps, per
peak, how many of the scans averaged into it hold it
(``mascope_signal.peak.N_SCANS``), and the fit score of peak assignment reads
a dim isotope line by it. A store detected before the count was kept reads as
it always did, and is not stale: the run counts such stores and says how many,
and rebuilds them only when asked, with ``FILL_SCAN_COUNTS=1``. Their rebuild
is the one above, so every batch holding one of their samples is set to
rematch: ask for it when those batches can be matched again.

A file whose store is rebuilt reads it back, so the next run leaves it alone:
run it again until it reports nothing to rebuild. ``REBUILD_LIMIT`` caps how
many files one run rebuilds. A file whose rebuild fails, or whose rebuilt
store still disagrees with its file, is counted and named, and read again by
the next run.

``DRY_RUN=1`` reads the same files and writes nothing: each file whose store
is stale, the scans it holds against those its file reads, and how many
samples would have their matches removed and how many batches would be set
to rematch, with the totals per instrument.

**Undoing it.** The pre-script ``pg_dump`` covers the batch statuses, the
matches and the TICs. The stores are in the filestore: a rebuilt one is the
store *Detect peaks* would write, and the stale one it replaces was refused by
every read that checks it.

Usage:
    DRY_RUN=1 mascope prod db script run rebuild_stale_peak_stores
    mascope prod db script run rebuild_stale_peak_stores

    REBUILD_LIMIT=500 mascope prod db script run rebuild_stale_peak_stores

    FILL_SCAN_COUNTS=1 DRY_RUN=1 mascope prod db script run rebuild_stale_peak_stores
    FILL_SCAN_COUNTS=1 mascope prod db script run rebuild_stale_peak_stores

    python -m mascope_backend.db.scripts.rebuild_stale_peak_stores

Date: 2026-11-05
"""

import asyncio
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np
from sqlalchemy import text

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.compute as m_compute
import mascope_thermo.thermo as m_thermo
from mascope_backend.db import async_session, configure_database_engine
from mascope_backend.runtime import runtime
from mascope_signal.peak import N_SCANS


#: The instrument class whose files carry the rule.
ORBITRAP = "orbi"

#: The sample file type whose scans the reader selects anew on every read.
ORBITRAP_RAW = "orbi_raw"

#: What :func:`check` calls a file. A store that is :data:`UNCOUNTED` is
#: current but for the scan counts it predates.
NO_STORE = "no_store"
NO_PEAKS = "no_peaks"
CURRENT = "current"
STALE = "stale"
UNCOUNTED = "uncounted"

#: Files checked at once: each opens its raw file's scan index, a few
#: milliseconds of reading, on a server that keeps serving.
_CHECK_CONCURRENCY = 4

#: Files checked per gathered batch.
_CHECK_BATCH = 500

#: A progress line every this many files checked.
_PROGRESS_EVERY = 5000

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
    """``REBUILD_LIMIT`` was set to something that is not a bound."""


@dataclass(frozen=True)
class Checked:
    """What one file's store is against the scans its file reads."""

    #: :data:`NO_STORE`, :data:`NO_PEAKS`, :data:`CURRENT`, :data:`STALE` or
    #: :data:`UNCOUNTED`.
    state: str
    #: The scans the store holds, and those the file reads; 0 where the file
    #: was not read for them.
    stored: int = 0
    reads: int = 0


def _stream_scans(filename: str, key: str) -> np.ndarray | None:
    """The scan times one stream of a file reads, or None where the file
    holds no stream under that key any more."""
    try:
        return m_compute.get_scan_timestamps(filename, stream=key)
    except m_thermo.UnknownStreamError:
        return None


def check_store(
    store,
    file_scans: Callable[[], np.ndarray],
    stream_scans: Callable[[str], np.ndarray | None],
) -> Checked:
    """Whether a peak store is the scans its file reads now.

    :param store: The file's whole peak store, as ``mascope_file.io.load_array``
        opens it.
    :param file_scans: The scan times the file reads, of no one polarity: what
        a pooled store's axis was allocated from.
    :param stream_scans: The scan times one stream reads, by its key, or None
        where the file holds no stream under it.
    :return: :data:`NO_PEAKS` for a store with nothing to divide (a blank's),
        else :data:`STALE`, or for one its file reads back :data:`CURRENT`, or
        :data:`UNCOUNTED` where it keeps no scan counts.
    """
    if not store.mz.size:
        return Checked(NO_PEAKS)
    checked = _check_axis(store, file_scans, stream_scans)
    if checked.state == CURRENT and N_SCANS not in store.data_vars:
        return Checked(UNCOUNTED)
    return checked


def _check_axis(
    store,
    file_scans: Callable[[], np.ndarray],
    stream_scans: Callable[[str], np.ndarray | None],
) -> Checked:
    """Whether a store with peaks holds the scans its file reads now
    (:func:`check_store`): :data:`CURRENT` or :data:`STALE`."""
    stored_time = np.asarray(store.time.values)
    try:
        streams = m_compute.peak_store_streams(store)
    except ValueError:
        # Labelled in part, which nothing reads: a detection writes all of it
        return Checked(STALE, stored_time.size, 0)
    if not streams:
        live = file_scans()
        try:
            m_compute.check_stored_scan_axis(live, stored_time)
        except m_compute.StalePeakStoreError:
            return Checked(STALE, stored_time.size, live.size)
        return Checked(CURRENT)

    try:
        m_compute.peak_store_stitch_map(store)
    except m_compute.StalePeakStoreError:
        return Checked(STALE, stored_time.size, 0)
    scan_stream = np.asarray(store.scan_stream.values, dtype=int)
    stored = reads = 0
    stale = False
    for index, key in enumerate(streams):
        own = stored_time[scan_stream == index]
        live = stream_scans(key)
        stored += own.size
        if live is None:
            stale = True
            continue
        reads += live.size
        try:
            m_compute.check_stored_scan_axis(live, own)
        except m_compute.StalePeakStoreError:
            stale = True
    return Checked(STALE, stored, reads) if stale else Checked(CURRENT)


def check(filename: str) -> Checked:
    """One file's store against the scans its file reads now.

    :param filename: The sample file's stored name.
    :raises Exception: If the store or the file's scans cannot be read.
    """
    try:
        store = m_io.load_array(filename, var="peak_timeseries")
    except FileNotFoundError:
        return Checked(NO_STORE)
    return check_store(
        store,
        lambda: m_compute.get_scan_timestamps(filename),
        lambda key: _stream_scans(filename, key),
    )


def _is_raw(filename: str) -> bool:
    """Whether a file keeps its raw Orbitrap file, asked without opening it.
    An Orbitrap file converted to zarr reads the scans its conversion stored,
    and its store never goes stale this way."""
    try:
        return m_name.get_sample_file_type(filename) == ORBITRAP_RAW
    except Exception:  # noqa: BLE001
        return False


async def survey(
    candidates: list[dict],
    is_raw: Callable[[str], bool],
    check_one: Callable[[str], Checked],
    limit: int | None = None,
    uncounted: bool = False,
) -> tuple[list[dict], dict[str, Counter]]:
    """The files whose store is to be rebuilt, and every file counted per
    instrument.

    Stops as soon as ``limit`` such files are found, between batches, so
    repeated capped runs do not each read every file on the server.

    :param candidates: Rows from :func:`_candidates`, newest first.
    :param is_raw: :func:`_is_raw`, or a stand-in.
    :param check_one: :func:`check`, or a stand-in.
    :param limit: Stop once this many are found; None checks them all.
    :param uncounted: Rebuild the stores that keep no scan counts too, beside
        the stale ones.
    :return: The files to rebuild, newest first, each with its ``state``,
        ``stored`` and ``reads``; and per instrument, how many files were
        found of each kind: ``not_raw``, ``unreadable`` and those
        :func:`check` returns.
    """
    wanted = {STALE, UNCOUNTED} if uncounted else {STALE}
    gate = asyncio.Semaphore(_CHECK_CONCURRENCY)
    traced = 0
    done = 0

    async def one(candidate: dict) -> tuple[dict, str, Checked | None]:
        nonlocal traced, done
        filename = candidate["filename"]
        async with gate:
            try:
                if not await asyncio.to_thread(is_raw, filename):
                    return candidate, "not_raw", None
                found = await asyncio.to_thread(check_one, filename)
            except Exception as exc:  # noqa: BLE001 - one file, never the run
                message = f"  {filename}: could not be read: {exc}"
                if traced < _TRACEBACK_LIMIT:
                    traced += 1
                    runtime.logger.opt(exception=exc).info(message)
                else:
                    runtime.logger.info(message)
                return candidate, "unreadable", None
            finally:
                done += 1
                if done % _PROGRESS_EVERY == 0:
                    runtime.logger.info(f"  ... {done} of {len(candidates)} checked")
            return candidate, found.state, found

    stale: list[dict] = []
    counts: dict[str, Counter] = defaultdict(Counter)
    for start in range(0, len(candidates), _CHECK_BATCH):
        batch = candidates[start : start + _CHECK_BATCH]
        for candidate, kind, found in await asyncio.gather(*map(one, batch)):
            counts[candidate.get("instrument") or "(none)"][kind] += 1
            if kind in wanted:
                stale.append(
                    {
                        **candidate,
                        "state": kind,
                        "stored": found.stored,
                        "reads": found.reads,
                    }
                )
        if limit is not None and len(stale) >= limit:
            return stale[:limit], counts
    return stale, counts


@dataclass
class Steps:
    """What a rebuild does to one file, each step a stand-in in the tests.

    A dry run has none of them but :attr:`samples_of`.
    """

    #: The samples of a file, and the batches holding them, by its id.
    samples_of: Callable[[str], Awaitable[tuple[list[str], list[str]]]]
    #: Sets batches to rematch and drops their aligned-peak caches.
    queue_rematch: Callable[[list[str]], Awaitable[None]] | None = None
    #: Removes every match of samples.
    remove_matches: Callable[[list[str]], Awaitable[None]] | None = None
    #: Reads a file's samples' TIC again and stores it, by file id and name.
    refresh_tic: Callable[[str, str], Awaitable[int]] | None = None
    #: Writes a file's MS2 store and its inventory again, where it has one.
    refresh_ms2: Callable[[str, str], Awaitable[bool]] | None = None
    #: Detects a file's peaks again and writes its store.
    rebuild: Callable[[str], Awaitable[None]] | None = None
    #: The check, asked again once the store is written.
    check_one: Callable[[str], Checked] | None = None


@dataclass
class Outcome:
    """What a run did, file by file and in total."""

    counts: Counter = field(default_factory=Counter)
    #: Per instrument: files rebuilt, or to be.
    instruments: Counter = field(default_factory=Counter)
    #: Every batch set, or to be set, to rematch.
    batches: set = field(default_factory=set)
    #: Every sample whose matches were, or would be, removed.
    samples: set = field(default_factory=set)
    #: Files whose rebuild failed or did not take, by name.
    failed: list = field(default_factory=list)


async def rebuild_all(stale: list[dict], steps: Steps) -> Outcome:
    """Rebuild each stale file, one at a time.

    One at a time: a detection averages the file's scans and fits its peaks
    in a process pool of its own, and this runs on a server that keeps
    serving and ingesting.

    :param stale: Files from :func:`survey`.
    :param steps: What a rebuild does; a dry run's has only ``samples_of``.
    :return: What was done.
    """
    outcome = Outcome()
    counts = outcome.counts
    dry_run = steps.rebuild is None
    for position, candidate in enumerate(stale, start=1):
        filename = candidate["filename"]
        samples, batches = await steps.samples_of(candidate["sample_file_id"])
        what = (
            "keeps no scan counts"
            if candidate.get("state") == UNCOUNTED
            else f"{candidate['stored']} scans stored, {candidate['reads']} read"
        )
        line = (
            f"  {candidate['sample_file_id']}  {filename}: {what}; "
            f"{len(samples)} sample(s), {len(batches)} batch(es)"
        )
        if not dry_run:
            try:
                if batches and steps.queue_rematch is not None:
                    await steps.queue_rematch(batches)
                if samples and steps.remove_matches is not None:
                    await steps.remove_matches(samples)
                if steps.refresh_tic is not None:
                    counts["tic_items"] += await steps.refresh_tic(
                        candidate["sample_file_id"], filename
                    )
                if steps.refresh_ms2 is not None and await steps.refresh_ms2(
                    candidate["sample_file_id"], filename
                ):
                    counts["ms2_stores"] += 1
                await steps.rebuild(filename)
            except Exception as exc:  # noqa: BLE001 - one file, never the run
                counts["failed"] += 1
                outcome.failed.append(filename)
                runtime.logger.opt(exception=exc).info(
                    f"  {filename}: could not be rebuilt; the next run tries again"
                )
                continue
            if steps.check_one is not None:
                try:
                    again = await asyncio.to_thread(steps.check_one, filename)
                except Exception:  # noqa: BLE001
                    again = None
                if again is None or again.state in (STALE, UNCOUNTED):
                    counts["still_stale"] += 1
                    outcome.failed.append(filename)
                    runtime.logger.warning(
                        f"The peak store rebuilt for '{filename}' still "
                        + (
                            "keeps no scan counts."
                            if again is not None and again.state == UNCOUNTED
                            else "disagrees with the file it was detected on."
                        )
                    )
                    continue
        runtime.logger.info(f"[{position}/{len(stale)}]{line}")
        counts["rebuilt"] += 1
        outcome.instruments[candidate.get("instrument") or "(none)"] += 1
        outcome.batches.update(batches)
        outcome.samples.update(samples)
    return outcome


# --- the steps, against the database and the filestore -------------------------


async def _candidates() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            text(_CANDIDATES_SQL), {"instrument_type": ORBITRAP}
        )
        return [dict(row._mapping) for row in result]


async def _samples_of(sample_file_id: str) -> tuple[list[str], list[str]]:
    """The samples of one file, in every workspace, and the batches holding
    them."""
    from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
        fetch_affected_sample_data,
    )

    affected = await fetch_affected_sample_data(sample_file_ids=[sample_file_id])
    return affected.affected_sample_item_ids, affected.affected_sample_batch_ids


async def _queue_rematch(sample_batch_ids: list[str]) -> None:
    """Set batches to ``rematch``, and drop the peaks each aligned: they name
    the peak ids being replaced.

    A request for work, as the ionization-mode and target-collection
    flaggers make: a batch already waiting on a stronger one, a
    ``recalibrate``, keeps it (``never_lower``), since its recalibration
    rematches it as well. Its aligned peaks go all the same.
    """
    from mascope_backend.api.controllers.sample.batches.status.service import (
        update_sample_batch_status,
    )

    await update_sample_batch_status(
        sample_batch_ids=sample_batch_ids,
        status="rematch",
        independent_transaction=False,
        never_lower=True,
    )
    for sample_batch_id in sample_batch_ids:
        await asyncio.to_thread(m_io.delete_batch_cache, sample_batch_id)


async def _remove_matches(sample_item_ids: list[str]) -> None:
    """Remove every match of the samples, as *Detect peaks* does before it
    rematches them: they were made on the peak ids being replaced."""
    from mascope_backend.api.controllers.match.match_controller import (
        match_remove_sample,
    )

    for sample_item_id in sample_item_ids:
        await match_remove_sample(
            sample_item_id=sample_item_id,
            full_remove=True,
            independent_transaction=False,
        )


async def _refresh_tic(sample_file_id: str, filename: str) -> int:
    """Read the TIC of a file's samples again, polarity by polarity, as a new
    sample's is read, and store it on every one of them that stores one.

    :return: How many samples were updated.
    """
    from mascope_backend.api.controllers.sample.items.sample_items_controller import (
        _sample_item_tic,
    )

    async with async_session() as session:
        result = await session.execute(
            text(
                "SELECT DISTINCT polarity FROM sample_item "
                "WHERE sample_file_id = :id AND tic IS NOT NULL"
            ),
            {"id": sample_file_id},
        )
        polarities = [row[0] for row in result]
    updated = 0
    for polarity in polarities:
        tic = await _sample_item_tic(filename=filename, polarity=polarity)
        if not np.isfinite(tic):
            continue
        async with async_session() as session:
            result = await session.execute(
                text("""
                    UPDATE sample_item
                    SET tic = :tic, sample_item_utc_modified = NOW()
                    WHERE sample_file_id = :id
                      AND polarity IS NOT DISTINCT FROM :polarity
                      AND tic IS NOT NULL
                """),
                {"tic": tic, "id": sample_file_id, "polarity": polarity},
            )
            await session.commit()
            updated += result.rowcount
    return updated


async def _refresh_ms2(sample_file_id: str, filename: str) -> bool:
    """Write the MS2 store of a file that has one again, and list its groups,
    as a re-processing does (``process.service._rewrite_ms2_store``). A file
    with none is left without: the MS2 store backfill writes those.

    :return: Whether the file had a store to write again.
    """
    from mascope_backend.api.new.ms2.inventory import write_ms2_inventory
    from mascope_signal.ms2_store import refresh_ms2_store

    props = await asyncio.to_thread(m_io.read_props, filename)
    if not props.get("ms2_store"):
        return False
    await asyncio.to_thread(refresh_ms2_store, filename)
    await write_ms2_inventory(sample_file_id, filename)
    return True


async def _rebuild(filename: str) -> None:
    """Detect a file's peaks again and write its store, as *Detect peaks*
    does: by the decision its props record, on the calibration it has."""
    from mascope_backend.api.new.instrument_configs.lib import (
        read_instrument_functions,
    )
    from mascope_signal.peak import compute_peaks

    instrument_functions = await read_instrument_functions(filename=filename)
    await asyncio.to_thread(compute_peaks, filename, instrument_functions)


def _limit() -> int | None:
    """How many files one run may rebuild, from ``REBUILD_LIMIT``.

    :raises BadLimit: The variable is set to something that is not a
        positive whole number: an operator who set it wanted a bounded run.
    """
    raw = os.environ.get("REBUILD_LIMIT")
    if not raw:
        return None
    try:
        limit = int(raw)
    except ValueError as exc:
        raise BadLimit(
            f"REBUILD_LIMIT={raw!r} is not a whole number. Unset it to rebuild "
            "every stale store."
        ) from exc
    if limit <= 0:
        raise BadLimit(
            f"REBUILD_LIMIT={raw!r} is not a positive number. Unset it to "
            "rebuild every stale store."
        )
    return limit


def _fill_scan_counts() -> bool:
    """Whether ``FILL_SCAN_COUNTS=1`` asks for the stores without scan counts
    to be rebuilt too."""
    return os.environ.get("FILL_SCAN_COUNTS") == "1"


def _counts_line(found: Counter) -> str:
    return (
        f"{found[STALE]} stale, {found[CURRENT]} current, "
        f"{found[UNCOUNTED]} current without scan counts, "
        f"{found[NO_PEAKS]} with no peaks, {found[NO_STORE]} with no store, "
        f"{found['unreadable']} unreadable, {found['not_raw']} with no raw file"
    )


async def run() -> None:
    """Find the raw Orbitrap stores their files no longer read back, and
    rebuild them."""
    dry_run = os.environ.get("DRY_RUN") == "1"
    uncounted = _fill_scan_counts()
    limit = _limit()
    await configure_database_engine()

    candidates = await _candidates()
    if not candidates:
        runtime.logger.info("No Orbitrap files.")
        return
    runtime.logger.info(f"Orbitrap files: {len(candidates)}")

    stale, counts = await survey(candidates, _is_raw, check, limit, uncounted)
    total: Counter = Counter()
    runtime.logger.info("Per instrument:")
    for instrument in sorted(counts):
        total.update(counts[instrument])
        runtime.logger.info(f"  {instrument}: {_counts_line(counts[instrument])}")
    checked = sum(total.values())
    runtime.logger.info(
        f"  Checked {checked} of {len(candidates)}: {_counts_line(total)}"
        + (" (DRY_RUN=1: nothing is written)" if dry_run else "")
    )
    if total[UNCOUNTED] and not uncounted:
        runtime.logger.info(
            f"  {total[UNCOUNTED]} store(s) predate the scan counts peak "
            "assignment reads a dim isotope line by. FILL_SCAN_COUNTS=1 "
            "rebuilds them too, and sets every batch holding one of their "
            "samples to rematch."
        )
    if not stale:
        runtime.logger.info("No peak store to rebuild.")
        return

    if dry_run:
        steps = Steps(samples_of=_samples_of)
    else:
        steps = Steps(
            samples_of=_samples_of,
            queue_rematch=_queue_rematch,
            remove_matches=_remove_matches,
            refresh_tic=_refresh_tic,
            refresh_ms2=_refresh_ms2,
            rebuild=_rebuild,
            check_one=check,
        )
    outcome = await rebuild_all(stale, steps)
    done = outcome.counts

    runtime.logger.info("=" * 80)
    runtime.logger.info(
        "REBUILD STALE PEAK STORES COMPLETE" + (" (DRY RUN)" if dry_run else "")
    )
    verb = "would rebuild" if dry_run else "rebuilt"
    without = sum(1 for row in stale if row.get("state") == UNCOUNTED)
    runtime.logger.info(
        f"Stale stores: {len(stale) - without}, without scan counts: {without}; "
        f"{verb} {done['rebuilt']}"
        + (
            ""
            if dry_run
            else f", failed {done['failed']}, still stale {done['still_stale']}; "
            f"TIC read again for {done['tic_items']} sample(s), MS2 store written "
            f"again for {done['ms2_stores']} file(s)"
        )
    )
    for instrument, files in sorted(outcome.instruments.items()):
        runtime.logger.info(f"  {instrument}: {files} file(s)")
    runtime.logger.info(
        f"Samples {'to have' if dry_run else 'that had'} their matches removed: "
        f"{len(outcome.samples)}; batches {'to set' if dry_run else 'set'} to "
        f"rematch: {len(outcome.batches)}"
    )
    if outcome.batches and not dry_run:
        runtime.logger.info(
            "  Rematch them (their status icon, Refresh matches, or a "
            "dataset's rematch) to match their samples on the rebuilt peaks."
        )
    if outcome.failed:
        runtime.logger.warning(
            f"{len(outcome.failed)} peak store(s) could not be rebuilt to "
            "match their file; see the lines above. The next run tries them "
            "again."
        )
    if limit is not None and len(stale) >= limit:
        runtime.logger.info(
            f"REBUILD_LIMIT={limit} may have left stores for later runs: run "
            "this again until it reports nothing to rebuild."
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
