"""
How far auto-processing got with a sample file.

A converted file is registered as a ``sample_file`` row, and auto-processing
then binds it to ionization modes, calibrates it and matches it. Each stage
records a status on the row. A file that ends with no samples, or with samples
that were never matched, then says why: it needs a chemistry, its calibration
failed, or processing stopped on an error. The Raw files view shows the
status, and an agent that uploaded the file can read it back from the file
list.

The detail is one or two plain sentences for a person: what the status means
for this file, and, for a file whose MS1 scans of one polarity come from more
than one scan stream, that peak detection pools them. A file stitched from
its streams says more of them: what two scan ranges read where they overlap,
and which ranges have no fit of their own and run on a neighbour's m/z
calibration. Its ranges are named as the views name them, by scan range, so
that what is said of them has room inside the detail's bound.
"""

import asyncio
from collections.abc import Collection
from datetime import datetime, timezone

from sqlalchemy import or_, update

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
import mascope_signal.mz_factor as m_factor
import mascope_signal.stitch as m_stitch
from mascope_backend.api.controllers.samples.lib.samples_segments import (
    segment_label,
)
from mascope_backend.api.models.sample.files.config import (
    IN_PROGRESS,
    STALLED_AFTER,
    ProcessingStatus,
)
from mascope_backend.api.new.notifications.service import (
    emit_notification_changes,
    keep_processing_outcome,
)
from mascope_backend.db import SampleFile, async_session
from mascope_backend.method_keys import usable_streams
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import emit_record_updated
from mascope_file.io import read_props


#: Upper bound on a stored detail. An error's text can carry a whole SQL
#: statement or traceback summary; the detail is for a person to read.
_DETAIL_LIMIT = 1000


def _clip(detail: str | None) -> str | None:
    """The detail as stored: stripped, bounded, and None when empty."""
    if detail is None:
        return None
    detail = detail.strip()
    if len(detail) > _DETAIL_LIMIT:
        detail = detail[: _DETAIL_LIMIT - 3].rstrip() + "..."
    return detail or None


def compose_detail(*parts: str | None) -> str | None:
    """Join the sentences of a detail, skipping the ones that are absent."""
    return _clip(" ".join(part.strip() for part in parts if part and part.strip()))


def pooled_streams_note(
    streams: list[dict], stitched: Collection[str] = ()
) -> str | None:
    """Name the polarities whose MS1 scans come from more than one stream,
    and say what peak detection did with them.

    Peak detection pools every MS1 scan of a polarity into one averaged
    spectrum and one peak list, so a file whose method runs more than one
    experiment in a polarity has its streams mixed: alternating scan ranges
    or scan modes, the same scan at another microscan count, or one scan
    definition repeated later in the method. The census that shows it is the
    converter's ``scan_streams`` (``SampleFileProps.scan_streams``).

    Unless the file's peaks were detected per stream and stitched. Its peak
    store then holds a peak list for each and the map that makes one
    spectrum of them, and saying they are pooled would be wrong: the
    sentence says they are stitched instead, and names each stream by its
    scan range, as the sample's views and everything else the detail says
    of a stitched file do (``segment_label``). The full keys are in the
    file's stream rows; four of them alone take a quarter of the detail.

    :param streams: The file's scan stream census, as
        :func:`read_scan_streams` returns it: every entry a dict with a dict
        ``signature``.
    :param stitched: The keys of the streams the file's peak store holds a
        peak list for each of, under a stitch map, as
        :func:`read_store_stream_keys` returns them. Empty for a file whose
        store is pooled, which is nearly every file.
    :return: One sentence per polarity that holds more than one MS1 stream,
        or None when none does.
    """
    by_polarity: dict[str, list[str]] = {}
    for stream in streams:
        signature = stream.get("signature", {})
        if signature.get("ms_order") == 1:
            # str(): the polarity is only printed here, and a census whose
            # polarity is a list would otherwise be an unhashable key. The
            # grouping is what the sentence counts, so a malformed value
            # reads oddly rather than failing a file's registration.
            by_polarity.setdefault(str(signature.get("polarity")), []).append(
                str(stream.get("key"))
            )
    notes = [
        (
            f"Polarity {polarity} stitches {len(keys)} MS1 scan streams into one "
            f"spectrum: {'; '.join(segment_label(key) for key in keys)}."
            if all(key in stitched for key in keys)
            else f"Polarity {polarity} pools {len(keys)} MS1 scan streams into "
            f"one peak list: {'; '.join(keys)}."
        )
        for polarity, keys in by_polarity.items()
        if len(keys) > 1
    ]
    return " ".join(notes) or None


async def read_scan_streams(filename: str) -> list[dict] | None:
    """A stored file's scan-stream census, from its ``.props``.

    **``[]`` and ``None`` mean different things.** ``[]`` is a file that
    records no census: one converted before the census existed, or by a reader
    that takes none. Both are ordinary, and nearly every Orbitrap file
    predating the census is re-processed sooner or later. ``None`` is a
    ``.props`` that could not be read at all, which is an anomaly worth a line
    in the log - so the caller can tell the two apart instead of treating
    every old file as a fault.

    Nothing that reads this may cost the file its processing - registration
    reads it too, and so does the pipeline - so an unreadable props answers
    ``None`` rather than raising.

    **The shape is checked here, not by each caller.** Every entry that comes
    back is a dict whose ``signature`` is a dict, so a caller may walk
    ``stream["signature"].get(...)`` without guarding each field. A census of
    another shape is a `.props` file nothing in Mascope wrote; the streams
    that do not fit are dropped rather than failing the file.

    :param filename: The sample file's stored name.
    :return: The census, ``[]`` when the file records none, or ``None`` when
        its ``.props`` could not be read.
    """
    try:
        props = await asyncio.to_thread(read_props, filename)
        return usable_streams(props.get("scan_streams"))
    except Exception:  # noqa: BLE001 - a missing census is not a processing error
        runtime.logger.opt(exception=True).debug(
            f"Could not read the .props of {filename}"
        )
        return None


def _store_stream_keys(filename: str) -> list[str]:
    """Synchronous body of :func:`read_store_stream_keys`."""
    try:
        store = m_io.load_array(filename, var="peak_timeseries")
        keys = m_compute.peak_store_streams(store)
        if keys:
            # A per-stream store without a map is not stitched; every read
            # of it is refused as stale, and so is this one
            m_compute.peak_store_stitch_map(store)
        return keys
    except Exception:  # noqa: BLE001 - a missing store is not a processing error
        runtime.logger.opt(exception=True).debug(
            f"Could not read the peak store's streams of {filename}"
        )
        return []


async def read_store_stream_keys(filename: str) -> list[str]:
    """The keys of the streams a stored file's peaks were detected per and
    stitched by, read from its peak store.

    Empty for a file whose store holds one peak list per polarity, which is
    nearly every file, and for one with no store or an unreadable one: like
    the census, nothing that reads this may cost a file its processing.

    :param filename: The sample file's stored name.
    :return: The store's stream keys, or ``[]``.
    """
    return await asyncio.to_thread(_store_stream_keys, filename)


async def read_pooled_streams_note(filename: str) -> str | None:
    """:func:`pooled_streams_note` for a stored file, read from its ``.props``
    and its peak store, with what a stitched file's ranges read where they
    overlap (:func:`overlap_readings_note`).

    :param filename: The sample file's stored name.
    :return: The note, or None.
    """
    streams = await read_scan_streams(filename)
    if not streams:
        return None
    return compose_detail(
        pooled_streams_note(streams, await read_store_stream_keys(filename)),
        await read_overlap_readings_note(filename),
    )


def overlap_readings_note(keys: list[str], readings: list[dict]) -> str | None:
    """What the scan ranges of a stitched file read where two of them overlap.

    Two ranges that both record an ion read it apart, in m/z and in
    intensity, and the composite takes one of the two readings. The peak
    store records the other as well (``mascope_signal.stitch.overlap_readings``):
    over the ions both hold, how far the second range reads them from the
    first in ppm, as the instrument recorded them, and how high per scan.
    The offset is what a range short of calibrants is calibrated across, and
    the ratio is the layout's factor between two windows; both are steady
    from file to file of one layout, so a file that reads otherwise shows
    here.

    One clause per pair that shares an ion, the medians over the ions
    shared. A pair that shares none says nothing: there is nothing to read.

    :param keys: The store's stream keys, as the readings index them.
    :param readings: The store's overlap readings.
    :return: One sentence, or None where no pair shares an ion.
    """
    clauses = []
    for reading in readings:
        if not reading.get("shared") or not reading.get("ppm"):
            continue
        first, second = (segment_label(keys[index]) for index in reading["streams"])
        offset, ratio = reading["ppm"][1], reading["ratio"][1]
        shared = reading["shared"]
        clauses.append(
            f"{second} reads the {shared} ion{'' if shared == 1 else 's'} it shares "
            f"with {first} {abs(offset):.2f} ppm {'lower' if offset < 0 else 'higher'}"
            f", at {ratio:.2f} times the intensity"
        )
    if not clauses:
        return None
    return f"Where two scan ranges overlap: {'; '.join(clauses)}."


def _store_overlap_readings_note(filename: str) -> str | None:
    """Synchronous body of :func:`read_overlap_readings_note`."""
    try:
        store = m_io.load_array(filename, var="peak_timeseries")
        # A pooled store records none, and so says nothing
        readings = store.attrs.get(m_stitch.STITCH_OVERLAPS_ATTR) or []
        return overlap_readings_note(m_compute.peak_store_streams(store), readings)
    except Exception:  # noqa: BLE001 - a missing store is not a processing error
        runtime.logger.opt(exception=True).debug(
            f"Could not read the peak store's overlap readings of {filename}"
        )
        return None


async def read_overlap_readings_note(filename: str) -> str | None:
    """:func:`overlap_readings_note` for a stored file, read from its peak
    store.

    None for a file whose store is pooled, which is nearly every file, and
    for one with no store or an unreadable one: nothing that reads this may
    cost a file its processing.

    :param filename: The sample file's stored name.
    :return: The note, or None.
    """
    return await asyncio.to_thread(_store_overlap_readings_note, filename)


def carried_calibration_note(mz_calibration: dict | None) -> str | None:
    """Which scan ranges of a stitched file run on a neighbour's m/z calibration.

    A stitched file is calibrated range by range, and a range with no fit
    of its own takes the calibration of a neighbouring range: across their
    overlap where they share enough ions, else as it is. The file's
    calibration record lists the ranges with how each came by its own
    (``quality.segments``).

    Why a range has no fit is the fit's to say, and it is quoted where it
    was recorded (``note``): a range can hold no calibrant of the
    collection, or hold ones too weak for the fit, or ones that disagree,
    and only the first is a collection that falls short of the window. The
    sentence says the range has no fit of its own and nothing it does not
    know.

    :param mz_calibration: The file's calibration record.
    :return: One sentence per range that was not fitted on calibrants of its
        own, or None: every range was, the file is not stitched, or it holds
        no applied fit.
    """
    segments = ((mz_calibration or {}).get("quality") or {}).get("segments") or []
    sentences = []
    for segment in segments:
        source, label, origin = (
            segment.get("source"),
            segment.get("label"),
            segment.get("origin"),
        )
        note = (segment.get("note") or "").strip().rstrip(".")
        unfitted = f"{label} has no fit of its own" + (
            f" ({note[0].lower()}{note[1:]})" if note else ""
        )
        if source == m_factor.OVERLAP:
            shared = segment.get("shared_ions")
            sentences.append(
                f"{unfitted}: calibrated from {origin} across the "
                f"{shared} ion{'' if shared == 1 else 's'} both measure."
            )
        elif source == m_factor.BORROWED:
            sentences.append(f"{unfitted}: given the calibration of {origin} as it is.")
    return " ".join(sentences) or None


async def claim_for_processing(sample_file_ids: list[str], detail: str) -> list[str]:
    """Mark files ``queued`` for a run, unless one has them already.

    One conditional update, so of two requests for the same file only one
    claims it; the other finds it in progress. A file whose run is still
    queued, or going, is left alone - unless the run has recorded nothing for
    ``STALLED_AFTER``. Such a row has no run behind it, and would otherwise
    hold the file until a full restart marked it failed.

    Not best effort, unlike :func:`record_processing_status`: a run must not
    start on a file it could not claim.

    :param sample_file_ids: The files to claim.
    :param detail: What the file is queued for, for a person to read.
    :return: The ids of the files claimed.
    """
    if not sample_file_ids:
        return []
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        rows = (
            await session.scalars(
                update(SampleFile)
                .where(
                    SampleFile.sample_file_id.in_(sample_file_ids),
                    or_(
                        SampleFile.processing_status.is_(None),
                        SampleFile.processing_status.not_in(
                            [status.value for status in IN_PROGRESS]
                        ),
                        SampleFile.processing_updated_utc < now - STALLED_AFTER,
                    ),
                )
                .values(
                    processing_status=ProcessingStatus.QUEUED.value,
                    processing_detail=_clip(detail),
                    processing_updated_utc=now,
                )
                .returning(SampleFile)
            )
        ).all()
        records = [row.to_dict() for row in rows]
        await session.commit()
    for record in records:
        await emit_record_updated(
            record_type="acquisition",
            record_id=record["sample_file_id"],
            record=record,
            room=record["instrument"],
        )
    return [record["sample_file_id"] for record in records]


async def record_processing_status(
    sample_file_id: str,
    status: ProcessingStatus,
    detail: str | None = None,
) -> None:
    """Record how far a sample file's processing got, and tell its viewers.

    The row is updated in a session of its own, so the status stands whatever
    the caller's transaction does next. The file's instrument room then gets
    the updated row, which the Raw files view puts in place of the one it
    shows. The whole row is sent rather than the changed fields alone: a view
    that replaces rows would otherwise blank every column but these.

    An outcome that needs someone is also kept as a notification for the
    people answerable for the instrument, and any outcome may settle the
    instrument's open ones (``api/new/notifications/service.py``). Both are
    written in the status's transaction, so neither stands without the other.

    Best effort: a status is a report on the processing, and failing to write
    one must never be the reason the processing fails. An error is logged and
    swallowed.

    :param sample_file_id: The file whose status this is.
    :param status: The stage reached.
    :param detail: What the stage means for this file, for a person to read.
    """
    detail = _clip(detail)
    try:
        async with async_session() as session:
            sample_file = (
                await session.execute(
                    update(SampleFile)
                    .where(SampleFile.sample_file_id == sample_file_id)
                    .values(
                        processing_status=status.value,
                        processing_detail=detail,
                        processing_updated_utc=datetime.now(timezone.utc),
                    )
                    .returning(SampleFile)
                )
            ).scalar_one_or_none()
            record = sample_file.to_dict() if sample_file is not None else None
            changes = (
                await keep_processing_outcome(session, record, status, detail)
                if record is not None
                else []
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - the processing matters more than its report
        # The WARNING names no file: error monitoring groups issues by the
        # message, and an outage fails this write for every file in flight.
        runtime.logger.info(
            f"Could not record processing status '{status.value}' for sample "
            f"file {sample_file_id}"
        )
        runtime.logger.opt(exception=True).warning(
            f"Could not record a sample file's processing status '{status.value}'"
        )
        return

    if record is None:
        # The file was deleted while it was being processed.
        return
    await emit_record_updated(
        record_type="acquisition",
        record_id=sample_file_id,
        record=record,
        room=record["instrument"],
    )
    await emit_notification_changes(changes)
