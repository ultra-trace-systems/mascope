"""
Maintenance script to report what the scan ranges of stitched files read where
two of them overlap.

A file whose method measures one chemistry as several scan ranges is stitched
into one spectrum per polarity: each m/z is taken from the one range that owns
it under the file's stitch map, and the map's rule gives an m/z two ranges
measure to the one with more microscans
(``docs/dev/ingest_routing_and_splitting.md``, section 4.5). Which range
*should* own an overlap is decision 17 of that note, and it is to be made on
what a site's files show rather than on the rule of thumb. Every stitched
peak store records, per pair of ranges whose claims meet, what the two read of
the ions both hold (``stitch_overlaps``); this report reads those records over
a site's newest files and says, per layout and per overlap:

- which range owns the overlap today;
- how many ions the two share, and how the second reads them against the
  first - the ratio and the m/z offset, each as the middle of the files'
  own medians, with how far the files spread;
- how many peaks each range holds in the overlap that the other does not,
  which is what a change of owner would gain and lose;
- the ions far off the layout's ratio in many of the files: the ones the
  two ranges do not measure alike.

**It writes nothing.** It says so to the runner with ``WRITES_NOTHING``, so no
``pg_dump`` is taken before it, and nothing here takes a lock. It reads each
file's peak store and ``.props``, and the file's stream rows for the scan and
microscan counts.

**It reports what the stores record, and decides nothing.** A ratio far from
one can be the layout's window factor or chemistry inside the instrument -
one site's ammonia channel reads twenty times higher in any range that holds
the reagent dimer - and the report cannot tell which. It names the ions.

**Bounded, newest acquisition first.** ``STITCH_REPORT_FILES`` caps the raw
Orbitrap files one run walks (default 5,000). Most of them are files of one
range per polarity and cost one read of their store's attributes; a stitched
one costs a read of its peak list as well.

Usage:
    mascope dev db script run report_stitch_overlaps
    mascope prod db script run report_stitch_overlaps
    STITCH_REPORT_FILES=50000 mascope prod db script run report_stitch_overlaps

Date: 2026-10-09
"""

import asyncio
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime as dt

import numpy as np
from sqlalchemy import select, tuple_

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.mz_factor as m_factor
import mascope_signal.stitch as m_stitch
import mascope_thermo.streams as m_streams
from mascope_backend.api.controllers.samples.lib.samples_segments import (
    segment_label,
)
from mascope_backend.db import (
    AcquisitionStream,
    SampleFile,
    async_session,
    configure_database_engine,
)
from mascope_backend.runtime import runtime
from mascope_thermo.scan_filter import parse_scan_filter


#: Read by ``mascope dev|prod db script run``, which then takes no pre-script
#: backup (``mascope_cli.pg.script_writes_nothing``). It is a promise about
#: this module: nothing here opens a write session, and all it touches
#: outside the database is reading each file's peak store and ``.props``.
WRITES_NOTHING = True

#: Files whose stores are read at once: each is a few small local reads.
_STORE_CONCURRENCY = 8

#: Files fetched per page of the walk.
_PAGE = 500

_DEFAULT_FILES = 5000

#: An ion off the layout's ratio is named where at least this share of the
#: layout's files list it: once is a file, most of the time is the layout.
_OUTLIER_SHARE = 0.25

#: How many such ions a pair lists.
_OUTLIERS_LISTED = 15


@dataclass(frozen=True)
class PairReading:
    """What two ranges of one file read where both measure."""

    #: The two streams' keys, the one whose range starts lower first.
    first: str
    second: str
    #: The m/z ``[lower, upper]`` both claim.
    overlap: tuple
    #: The stream key that owns each part of the overlap under the file's
    #: map, as ``(lower, upper, key)``; a part nobody owns is left out.
    owners: tuple
    #: Ions both hold; the second's height per scan over the first's, and
    #: its m/z less the first's in ppm - each the file's median, None where
    #: they share none.
    shared: int
    ratio: float | None
    ppm: float | None
    #: Kept peaks each holds inside the overlap.
    kept_first: int
    kept_second: int
    #: The ions furthest off the file's median ratio: ``(m/z, ratio)``.
    outliers: tuple = ()


@dataclass(frozen=True)
class FileReading:
    """One stitched polarity of one file."""

    instrument: str
    polarity: str
    #: The keys of the polarity's streams, in the file's order.
    layout: tuple
    acquired: dt | None
    sample_file_id: str
    pairs: tuple = ()
    #: The polarity's stitch map: ``(lower, upper, owner's key)`` per run,
    #: in m/z order.
    runs: tuple = ()


@dataclass
class Walked:
    """What one run of the report read."""

    files: int = 0
    stitched: int = 0
    readings: list[FileReading] = field(default_factory=list)
    unreadable: Counter = field(default_factory=Counter)


def _int_env(name: str, default: int, minimum: int) -> int:
    """Read a bound from the environment, or raise.

    A bound is part of what the report says about itself, so a value that
    cannot be read stops the run rather than falling back to the default.

    :raises ValueError: When the variable is set to anything but a whole
        number of at least ``minimum``.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"{name}={raw!r} is not a whole number. Unset it for {default}."
        ) from None
    if value < minimum:
        raise ValueError(f"{name}={raw!r} is below {minimum}, so it reads nothing.")
    return value


def _polarity(key: str) -> str | None:
    """The polarity a stream key states."""
    return parse_scan_filter(key.split(" R=")[0]).polarity


def _owners(overlap: list, runs: list, keys: list[str]) -> tuple:
    """Who owns each part of an overlap under one polarity's runs."""
    owned = []
    for lower, upper in overlap:
        for run_lower, run_upper, index in runs:
            low, high = max(lower, run_lower), min(upper, run_upper)
            if low < high:
                owned.append((low, high, keys[index]))
    return tuple(owned)


def read_file(sample_file: SampleFile) -> list[FileReading]:
    """The overlap readings of one file, per stitched polarity.

    Synchronous: it reads the filestore, so it runs in a worker thread.

    :param sample_file: The file.
    :return: One reading per polarity the file's store stitches; none for a
        file with no store, or whose store stitches nothing - nearly every
        file.
    :raises Exception: If the store or the ``.props`` cannot be read, or the
        store carries only part of what a per-stream store does.
    """
    filename = sample_file.filename
    path = m_name.filename_to_zarr_path(filename, "peak_timeseries")
    if not os.path.exists(path):
        return []
    attrs = dict(m_io.open_zarr_store(path).attrs)
    keys = attrs.get("streams")
    stitch = attrs.get(m_stitch.STITCH_MAP_ATTR)
    if not keys or not stitch or not stitch.get("runs"):
        return []
    recorded = attrs.get(m_stitch.STITCH_OVERLAPS_ATTR) or []

    # The peaks each range holds in an overlap, which the record leaves out:
    # it counts the ions both hold, and a change of owner is about the rest
    store = m_io.load_array(filename, var="peak_timeseries")
    mz = store.mz.values
    stream = store.stream.values
    # Each peak by its own stream's factor, as the store places it
    factor = m_factor.stream_factors(
        m_io.read_props(filename).get("mz_calibration"), keys
    )[stream]
    kept = ~(store.is_weak.values | store.is_satellite.values) & (
        store.sum_peak_heights.values > 0
    )

    def held(index: int, overlap: list) -> int:
        inside = np.zeros(mz.shape, dtype=bool)
        for lower, upper in overlap:
            inside |= (mz >= lower * factor) & (mz < upper * factor)
        return int(np.count_nonzero(kept & inside & (stream == index)))

    readings = []
    for polarity, runs in stitch["runs"].items():
        members = [key for key in keys if _polarity(key) == polarity]
        pairs = []
        for reading in recorded:
            first, second = reading["streams"]
            if keys[first] not in members:
                continue
            ratio, ppm = reading.get("ratio"), reading.get("ppm")
            pairs.append(
                PairReading(
                    first=keys[first],
                    second=keys[second],
                    overlap=tuple(tuple(part) for part in reading["overlap"]),
                    owners=_owners(reading["overlap"], runs, keys),
                    shared=int(reading["shared"]),
                    ratio=ratio[1] if ratio else None,
                    ppm=ppm[1] if ppm else None,
                    kept_first=held(first, reading["overlap"]),
                    kept_second=held(second, reading["overlap"]),
                    outliers=tuple(
                        (float(ion), float(off)) for ion, off in reading["outliers"]
                    ),
                )
            )
        readings.append(
            FileReading(
                instrument=sample_file.instrument,
                polarity=polarity,
                layout=tuple(members),
                acquired=sample_file.datetime_utc,
                sample_file_id=sample_file.sample_file_id,
                pairs=tuple(pairs),
                runs=tuple((lower, upper, keys[index]) for lower, upper, index in runs),
            )
        )
    return readings


def _spread(values: list[float]) -> list[float] | None:
    """The quartiles of a layout's per-file values, or None where it has none."""
    if not values:
        return None
    return [float(quartile) for quartile in np.percentile(values, [25, 50, 75])]


def _ions_listed(read: list[PairReading]) -> list[dict]:
    """The ions most of a layout's files list as far off the pair's ratio.

    An ion is the same ion from file to file within
    ``mascope_signal.stitch.OVERLAP_MATCH_PPM``, which is what makes two
    readings of one file the same ion. Rounding the m/z instead would split
    an ion that lies near a rounding step between two entries, each with
    part of its files, and drop it from the list or show it twice. A file
    counts once toward an ion, however often it lists it.

    :param read: One pair's reading in each file of a layout.
    :return: Per ion at least :data:`_OUTLIER_SHARE` of the files list:
        ``mz``, the middle of its readings; ``files``, how many list it; and
        ``ratio``, the middle of their ratios. The ion most files list
        first.
    """
    listings = sorted(
        (ion, ratio, file)
        for file, pair in enumerate(read)
        for ion, ratio in pair.outliers
    )
    ions: list[list[tuple]] = []
    for listing in listings:
        # Against the lowest reading of the ion, so that a run of readings
        # each near the last cannot string two ions into one
        lowest = ions[-1][0][0] if ions else None
        if lowest is not None and (
            listing[0] - lowest <= lowest * m_stitch.OVERLAP_MATCH_PPM * 1e-6
        ):
            ions[-1].append(listing)
        else:
            ions.append([listing])
    return sorted(
        (
            {
                "mz": round(float(np.median([ion for ion, _ratio, _file in group])), 4),
                "files": len({file for _ion, _ratio, file in group}),
                "ratio": float(np.median([ratio for _ion, ratio, _file in group])),
            }
            for group in ions
            if len({file for _ion, _ratio, file in group}) >= _OUTLIER_SHARE * len(read)
        ),
        key=lambda entry: (-entry["files"], entry["mz"]),
    )


def summarise(readings: list[FileReading]) -> list[dict]:
    """What each layout's files read in each of its overlaps.

    A layout is an instrument's polarity, the ranges it runs and the map
    they are stitched by. The map is part of it because a range's key does
    not say everything the map is drawn from: a method whose microscan
    counts were changed keeps its keys and can change who owns an overlap,
    and its files before and after are then read apart, each with its own
    owner and its own count.

    :param readings: The files' readings, as :func:`read_file` gives them.
    :return: One entry per (instrument, polarity, layout, map), the one with
        the most files first: its ``files``, when the first and the last of
        them were acquired, the newest file's id, its ``runs``, and per pair
        of ranges whose claims meet what the files read there.
    """
    by_layout: dict[tuple, list[FileReading]] = defaultdict(list)
    for reading in readings:
        by_layout[
            (reading.instrument, reading.polarity, reading.layout, reading.runs)
        ].append(reading)

    summary = []
    for (instrument, polarity, layout, runs), files in by_layout.items():
        by_pair: dict[tuple, list[PairReading]] = defaultdict(list)
        for file in files:
            for pair in file.pairs:
                by_pair[(pair.first, pair.second)].append(pair)
        acquired = sorted(file.acquired for file in files if file.acquired)
        newest = max(files, key=lambda file: (file.acquired is not None, file.acquired))
        pairs = []
        for (first, second), read in by_pair.items():
            listed = _ions_listed(read)
            pairs.append(
                {
                    "first": first,
                    "second": second,
                    "files": len(read),
                    "overlap": Counter(pair.overlap for pair in read).most_common(1)[0][
                        0
                    ],
                    "owners": Counter(pair.owners for pair in read).most_common(1)[0][
                        0
                    ],
                    "shared": _spread([pair.shared for pair in read]),
                    "ratio": _spread(
                        [pair.ratio for pair in read if pair.ratio is not None]
                    ),
                    "ppm": _spread([pair.ppm for pair in read if pair.ppm is not None]),
                    "only_first": _spread(
                        [pair.kept_first - pair.shared for pair in read]
                    ),
                    "only_second": _spread(
                        [pair.kept_second - pair.shared for pair in read]
                    ),
                    "off_ratio": listed[:_OUTLIERS_LISTED],
                    "off_ratio_more": max(0, len(listed) - _OUTLIERS_LISTED),
                }
            )
        summary.append(
            {
                "instrument": instrument,
                "polarity": polarity,
                "layout": list(layout),
                "runs": [list(run) for run in runs],
                "files": len(files),
                "first_acquired": acquired[0] if acquired else None,
                "last_acquired": acquired[-1] if acquired else None,
                "newest_file_id": newest.sample_file_id,
                "pairs": pairs,
            }
        )
    return sorted(summary, key=lambda entry: -entry["files"])


async def _file_pages(limit: int):
    """The raw Orbitrap files to read, newest acquisition first, in pages.

    Paged by the last row read rather than by OFFSET, as the other reports
    are: the gain is stability, since nothing indexes ``datetime_utc``.
    """
    read = 0
    cursor = None
    async with async_session() as session:
        while read < limit:
            query = (
                select(SampleFile)
                .where(SampleFile.instrument_type == "orbi")
                .order_by(SampleFile.datetime_utc.desc(), SampleFile.filename.desc())
                .limit(min(_PAGE, limit - read))
            )
            if cursor is not None:
                query = query.where(
                    tuple_(SampleFile.datetime_utc, SampleFile.filename) < cursor
                )
            page = (await session.execute(query)).scalars().all()
            if not page:
                return
            read += len(page)
            yield page
            cursor = (page[-1].datetime_utc, page[-1].filename)


async def walk(files_limit: int) -> Walked:
    """Read the overlap records of the newest files.

    :param files_limit: Raw Orbitrap files to walk.
    :return: The readings, and what could not be read, by kind of failure.
    """
    walked = Walked()
    gate = asyncio.Semaphore(_STORE_CONCURRENCY)

    async def one(sample_file: SampleFile) -> None:
        async with gate:
            try:
                readings = await asyncio.to_thread(read_file, sample_file)
            except Exception as error:  # noqa: BLE001 - one file's failure
                walked.unreadable[type(error).__name__] += 1
                return
        if readings:
            walked.stitched += 1
            walked.readings.extend(readings)

    async for page in _file_pages(files_limit):
        walked.files += len(page)
        await asyncio.gather(*(one(sample_file) for sample_file in page))
    return walked


async def _stream_settings(sample_file_id: str) -> dict[str, str]:
    """How each stream of a file was measured, by key: its scans and
    microscans as the file's stream rows record them."""
    async with async_session() as session:
        rows = (
            await session.execute(
                select(AcquisitionStream).where(
                    AcquisitionStream.sample_file_id == sample_file_id
                )
            )
        ).scalars()
        settings = {}
        for row in rows:
            microscans = m_streams.microscans(
                {"acquisition_params": row.acquisition_params}
            )
            said = []
            if row.scan_count is not None:
                said.append(
                    f"{row.scan_count} scan{'s' if row.scan_count != 1 else ''}"
                )
            if microscans is not None:
                said.append(f"{microscans:g} microscan{'s' if microscans != 1 else ''}")
            settings[row.stream_key] = ", ".join(said)
    return settings


def _three(spread: list[float] | None, digits: int = 2) -> str:
    """Quartiles as ``median (lower to upper)``."""
    if spread is None:
        return "none"
    lower, median, upper = (f"{value:.{digits}f}" for value in spread)
    return f"{median} ({lower} to {upper})"


def _named(key: str, settings: dict[str, str]) -> str:
    said = settings.get(key)
    return f"{segment_label(key)} [{said}]" if said else segment_label(key)


async def _log(walked: Walked, files_limit: int) -> None:
    """Say what was read, layout by layout."""
    log = runtime.logger.info
    log(
        f"Stitch overlaps: read {walked.files} raw Orbitrap file(s), newest "
        f"first (STITCH_REPORT_FILES={files_limit}); {walked.stitched} of them "
        "are stitched."
    )
    if walked.unreadable:
        log(
            "  Could not be read: "
            + ", ".join(
                f"{count} ({kind})" for kind, count in walked.unreadable.most_common()
            )
            + "."
        )
    if not walked.readings:
        log(
            "  No stitched file among them. A file is stitched when it is "
            "converted, or re-processed, with composite_scan_streams on and "
            "its method measures more than one thing in a polarity."
        )
        return

    for layout in summarise(walked.readings):
        settings = await _stream_settings(layout["newest_file_id"])
        span = (
            f"{layout['first_acquired']:%Y-%m-%d %H:%M} to "
            f"{layout['last_acquired']:%Y-%m-%d %H:%M} UTC"
            if layout["first_acquired"]
            else "acquisition time unknown"
        )
        log(
            f"- {layout['instrument']}, polarity {layout['polarity']}: "
            f"{layout['files']} file(s), {span}"
        )
        for key in layout["layout"]:
            log(f"    range {_named(key, settings)}")
        if layout["runs"]:
            log(
                "    stitched: "
                + "; ".join(
                    f"m/z {lower:g}-{upper:g} from {segment_label(key)}"
                    for lower, upper, key in layout["runs"]
                )
            )
        if not layout["pairs"]:
            log("    No two of its ranges overlap.")
        for pair in layout["pairs"]:
            first, second = segment_label(pair["first"]), segment_label(pair["second"])
            overlap = "; ".join(
                f"{lower:g}-{upper:g}" for lower, upper in pair["overlap"]
            )
            owners = (
                "; ".join(
                    f"{lower:g}-{upper:g} by {segment_label(key)}"
                    for lower, upper, key in pair["owners"]
                )
                or "nobody"
            )
            log(
                f"    {first} and {second} overlap over m/z {overlap}, "
                f"owned {owners} ({pair['files']} file(s))"
            )
            shared = f"      ions both hold: {_three(pair['shared'], 0)}"
            if pair["ratio"] is not None:
                shared += (
                    f"; {second} reads them at {_three(pair['ratio'])} of "
                    f"{first}, {_three(pair['ppm'])} ppm apart"
                )
            log(shared)
            log(
                f"      peaks only {first} holds there: "
                f"{_three(pair['only_first'], 0)}; only {second}: "
                f"{_three(pair['only_second'], 0)}"
            )
            for ion in pair["off_ratio"]:
                log(
                    f"      off the ratio: m/z {ion['mz']:.3f} at "
                    f"{ion['ratio']:.3g} in {ion['files']} file(s)"
                )
            if pair["off_ratio_more"]:
                log(f"      ...and {pair['off_ratio_more']} more ion(s).")
    log(
        "  Each figure is the middle of the files' own medians, with the "
        "quartiles of the files in brackets. A ratio is of height per scan, "
        "the second range over the first."
    )


async def stitch_overlap_report(files_limit: int) -> list[dict]:
    """Read the newest files' overlap records, log them, and return them.

    :param files_limit: Raw Orbitrap files to walk.
    :return: The summary, as :func:`summarise` gives it.
    """
    walked = await walk(files_limit)
    await _log(walked, files_limit)
    return summarise(walked.readings)


async def run() -> None:
    """Initialise the database and report on the overlaps."""
    await configure_database_engine()
    await stitch_overlap_report(
        files_limit=_int_env("STITCH_REPORT_FILES", _DEFAULT_FILES, 1),
    )


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
