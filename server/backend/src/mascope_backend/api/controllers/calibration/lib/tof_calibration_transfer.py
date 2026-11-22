"""
Calibration transfer: an m/z calibration for a TOF file whose mode has no calibrants.

Some modes of a multi-scheme instrument carry no calibrant collection: an
ambient or a zero mode, run between the reagent modes within minutes. Their
files would keep the acquisition software's axis, which on those instruments
is typically 90 to 130 ppm off. Their neighbours of the same instrument and
polarity are fitted by the node, and a TOF's axis drifts slowly: within a run
by a few ppm. So such a file takes its calibration from them (MAS-178 chose
this for multi-scheme instruments, MAS-187 built it):

- **Its sources** are the nearest file before it and the nearest after it, of
  the same instrument and polarity, whose record is a node fit that cleared
  the quality bar (``status: "ok"``, verified), within
  :data:`calibration_config.TOF_TRANSFER_WINDOW_S` of it. Times are each
  file's centre: its start plus half its length. A transferred record is never
  a source, so a transfer does not travel from file to file.
- **Its parameters** are the two sources' interpolated in time, or the one
  source's where only one is within the window. A fit of mode 0 is a fit of
  mode 2 with ``p3 = 0.5`` (``i = p1 * m**p3 + p2``), so a mode-0 and a mode-2
  source are interpolated in mode 2. With no source within the window, the
  file stays unfitted.
- **The index the parameters count from.** The node fits a file on the TOF
  index of its stored axis, and the converter cut the raw axis's samples below
  ``DEFAULT_NONSENSE_MZ`` off its start. How many it cut depends on the file's
  own acquisition axis, so it differs from file to file, and one sample is
  about 20 ppm at 100 Th. Each source's ``p2`` is moved to the raw axis's
  index by its own cut, interpolated there, and moved back by the file's
  (:func:`raw_index_offset`).
- **The record** says ``status: "transferred"``, verified, and keeps what it
  was made from under ``transfer``: each source's id, its signed time gap in
  seconds and its weight, the gap to the nearest source, the window, and the
  file's own cut. A fit of the file itself, once its mode has calibrants,
  replaces it like any other record; a transfer never replaces a fit of the
  file itself, nor a failed fit's marker.
"""

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

import mascope_file.name as m_name
import mascope_signal.compute as m_compute
from mascope_backend.api.models.calibration.config import calibration_config
from mascope_backend.db import SampleFile, async_session
from mascope_tofwerk.calibration import FITTED_MODES
from mascope_tofwerk.tofwerk import open_h5_file


#: The status of a transferred record.
TRANSFERRED = "transferred"

#: ``p3`` of a mode-2 fit that is a mode-0 fit: ``p1 * m**0.5 + p2``.
_MODE0_P3 = 0.5


def is_transferred_record(record: dict | None) -> bool:
    """Whether a calibration record is a transfer from neighbouring files."""
    return isinstance(record, dict) and record.get("status") == TRANSFERRED


def is_transfer_source(record: dict | None) -> bool:
    """Whether a file's record can lend its calibration to a neighbour.

    A node fit that cleared the quality bar and is verified, in a mode the
    node fits, with the parameters that mode has, all finite.
    """
    if not isinstance(record, dict):
        return False
    if record.get("status") != "ok" or record.get("verified") is not True:
        return False
    if not isinstance(record.get("quality"), dict):
        return False
    mode, par = record.get("mode"), record.get("par")
    if isinstance(mode, bool) or mode not in FITTED_MODES:
        return False
    if not isinstance(par, list) or len(par) != FITTED_MODES[mode]:
        return False
    return all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in par
    )


#: No TOF file runs for a day. A longer length is not one an acquisition
#: had, and :func:`centre_time` reads it as unknown. It also bounds the
#: query on start times (:func:`candidate_sources`): a source's centre is
#: at most half this after its start.
_LONGEST_FILE = timedelta(days=1)


def centre_time(start: datetime, length: float | None) -> datetime:
    """A file's centre in time: its start plus half its length in seconds.

    A length that is missing, not finite, negative or a day or more is
    unknown, and the centre is the start. Stored lengths that big exist, far
    beyond what a ``timedelta`` holds, and one such file within reach must
    not stop the transfer of every file around it.
    """
    if (
        length is None
        or not math.isfinite(length)
        or not 0 <= length < _LONGEST_FILE.total_seconds()
    ):
        return start
    return start + timedelta(seconds=length / 2)


def raw_index_offset(filename: str) -> int | None:
    """How many samples of the raw TOF axis the file's stored axis starts after.

    The converter keeps the raw axis's samples from ``DEFAULT_NONSENSE_MZ`` up
    (``mascope_tofwerk.tofwerk.compute_sum_signal``), so the stored axis is the
    raw one less that many samples at its start: the raw axis's length less
    the stored sum signal's, which is also what an apply rewrites
    (``TofCalibrationHandler._apply_sync``).

    Synchronous: it reads the filestore.

    :return: The offset, or None for a file without its Tofwerk ``.h5`` (a
        converted ``signal.zarr``), or whose stored axis is longer than the
        raw one.
    """
    if m_name.get_sample_file_type(filename) != "tof_h5":
        return None
    with open_h5_file(m_name.filename_to_datafile_path(filename)) as h5_file:
        raw = int(h5_file["FullSpectra"]["MassAxis"].shape[0])
    stored = int(m_compute.get_sum_signal(filename).size)
    offset = raw - stored
    return offset if offset >= 0 else None


def _in_mode(mode: int, par: list[float], target: int) -> list[float]:
    """Parameters of mode ``mode`` as parameters of mode ``target``."""
    if mode == target:
        return [float(value) for value in par]
    # Only 0 -> 2: a mode-0 fit is a mode-2 fit with p3 = 0.5.
    return [float(par[0]), float(par[1]), _MODE0_P3]


@dataclass(frozen=True)
class Source:
    """A neighbouring file a transfer may read."""

    sample_file_id: str
    filename: str
    #: The file's centre in time (:func:`centre_time`).
    centre: datetime
    mode: int
    par: list[float]


@dataclass(frozen=True)
class Transfer:
    """A transfer worked out for one file, or why there is none."""

    #: The record to apply, or None when there is nothing to transfer.
    record: dict | None
    #: Why there is no transfer, as a clause; None when there is one.
    reason: str | None = None


def nearest_sources(
    centre: datetime, candidates: list[Source], window_s: float
) -> tuple[Source | None, Source | None]:
    """The nearest source before the file and the nearest after it, each
    within the window.

    A source at the file's own centre counts as before it. Two equally near
    on one side are told apart by their ids, so the choice does not depend
    on the order they were read in.
    """
    before = after = None
    for source in sorted(candidates, key=lambda source: source.sample_file_id):
        gap = (source.centre - centre).total_seconds()
        if abs(gap) > window_s:
            continue
        if gap <= 0:
            if before is None or source.centre > before.centre:
                before = source
        elif after is None or source.centre < after.centre:
            after = source
    return before, after


def transferred_record(
    centre: datetime,
    before: Source | None,
    after: Source | None,
    offsets: dict[str, int],
    window_s: float,
) -> dict:
    """The record a file takes from its sources.

    :param centre: The file's centre in time.
    :param before: The nearest source before it, if any.
    :param after: The nearest source after it, if any.
    :param offsets: The raw index offset (:func:`raw_index_offset`) of the
        file, under its id ``""``, and of each source, under its id.
    :param window_s: The window the sources were found within.
    :raises ValueError: With neither source.
    """
    sources = [source for source in (before, after) if source is not None]
    if not sources:
        raise ValueError("A transfer needs a source")
    if before is not None and after is not None:
        span = (after.centre - before.centre).total_seconds()
        weight_after = (centre - before.centre).total_seconds() / span
        weights = [1.0 - weight_after, weight_after]
    else:
        weights = [1.0]
    mode = max(source.mode for source in sources)
    raw = []
    for source in sources:
        par = _in_mode(source.mode, source.par, mode)
        par[1] += offsets[source.sample_file_id]
        raw.append(par)
    par = [
        sum(weight * values[at] for weight, values in zip(weights, raw))
        for at in range(len(raw[0]))
    ]
    par[1] -= offsets[""]
    gaps = [(source.centre - centre).total_seconds() for source in sources]
    return {
        "mode": mode,
        "par": par,
        "status": TRANSFERRED,
        "verified": True,
        "transfer": {
            "sources": [
                {
                    "sample_file_id": source.sample_file_id,
                    "gap_s": gap,
                    "weight": weight,
                }
                for source, gap, weight in zip(sources, gaps, weights)
            ],
            "gap_s": min(abs(gap) for gap in gaps),
            "window_s": window_s,
            "raw_index_offset": offsets[""],
        },
    }


async def candidate_sources(sample_file: SampleFile, window_s: float) -> list[Source]:
    """The files of the instrument and polarity that could lend ``sample_file``
    their calibration: with their centre within the window of its centre, and
    a record that is a source (:func:`is_transfer_source`).

    The files are read in two steps: their times first, of the fits that
    cleared the bar within bounds on their start, then the records of those
    whose centre is within the window, so that a day's records are not read
    for two. The bounds and the status only narrow the reading: which file
    is a source, and within the window, is decided here, on the record and
    the centres. The file itself is never one: a transfer is planned only for
    a file whose record is the converter's or a transfer.
    """
    centre = centre_time(sample_file.datetime_utc, sample_file.length)
    window = timedelta(seconds=window_s)
    async with async_session() as session:
        rows = (
            await session.execute(
                select(
                    SampleFile.sample_file_id,
                    SampleFile.datetime_utc,
                    SampleFile.length,
                ).where(
                    SampleFile.instrument == sample_file.instrument,
                    SampleFile.polarity == sample_file.polarity,
                    SampleFile.instrument_type == "tof",
                    SampleFile.datetime_utc >= centre - window - _LONGEST_FILE,
                    SampleFile.datetime_utc <= centre + window,
                    SampleFile.mz_calibration["status"].as_string() == "ok",
                )
            )
        ).all()
        centres = {
            row.sample_file_id: centre_time(row.datetime_utc, row.length)
            for row in rows
        }
        near = [
            sample_file_id
            for sample_file_id, at in centres.items()
            if abs((at - centre).total_seconds()) <= window_s
        ]
        if not near:
            return []
        records = (
            await session.execute(
                select(
                    SampleFile.sample_file_id,
                    SampleFile.filename,
                    SampleFile.mz_calibration,
                ).where(SampleFile.sample_file_id.in_(near))
            )
        ).all()
    return [
        Source(
            sample_file_id=row.sample_file_id,
            filename=row.filename,
            centre=centres[row.sample_file_id],
            mode=row.mz_calibration["mode"],
            par=[float(value) for value in row.mz_calibration["par"]],
        )
        for row in records
        if is_transfer_source(row.mz_calibration)
    ]


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:g} minutes"


def transfer_note(record: dict | None) -> str | None:
    """What a transferred record says of itself, for the file's processing
    detail; None for any other record."""
    if not is_transferred_record(record):
        return None
    transfer = record.get("transfer") or {}
    sources = transfer.get("sources") or []
    gap = transfer.get("gap_s")
    files = (
        "two neighbouring fitted files, interpolated in time"
        if len(sources) == 2
        else "a neighbouring fitted file"
    )
    nearest = (
        f", the nearest {gap / 60:.0f} min away"
        if isinstance(gap, (int, float)) and len(sources) == 2
        else f" {gap / 60:.0f} min away"
        if isinstance(gap, (int, float))
        else ""
    )
    return f"m/z calibration transferred from {files}{nearest}."


async def plan_transfer(sample_file: SampleFile) -> Transfer:
    """The transfer a TOF file in a mode without calibrants takes, or why none.

    Reads the database, then each file's raw axis in a thread.
    """
    window_s = float(calibration_config.TOF_TRANSFER_WINDOW_S)
    if sample_file.polarity not in ("+", "-"):
        return Transfer(
            None,
            "its scans are of both polarities, and a transfer is of one polarity",
        )
    candidates = await candidate_sources(sample_file, window_s)
    centre = centre_time(sample_file.datetime_utc, sample_file.length)
    before, after = nearest_sources(centre, candidates, window_s)
    if before is None and after is None:
        return Transfer(
            None,
            "no fitted file of its instrument and polarity is within "
            f"{_minutes(window_s)} of it to transfer a calibration from",
        )
    sources = [source for source in (before, after) if source is not None]
    offsets = {}
    for key, filename in [("", sample_file.filename)] + [
        (source.sample_file_id, source.filename) for source in sources
    ]:
        offset = await asyncio.to_thread(raw_index_offset, filename)
        if offset is None:
            return Transfer(
                None,
                "the raw TOF axis of "
                + ("the file" if key == "" else "a neighbouring fitted file")
                + " cannot be read, so no calibration can be transferred",
            )
        offsets[key] = offset
    return Transfer(transferred_record(centre, before, after, offsets, window_s))
