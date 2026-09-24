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
than one scan stream, that peak detection pools them.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import or_, update

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


def pooled_streams_note(streams: list[dict]) -> str | None:
    """Name the polarities whose MS1 scans come from more than one stream.

    Peak detection pools every MS1 scan of a polarity into one averaged
    spectrum and one peak list, so a file whose method alternates scan ranges
    or scan modes within a polarity has its streams mixed. The census that
    shows it is the converter's ``scan_streams``
    (``SampleFileProps.scan_streams``).

    :param streams: The file's scan stream census.
    :return: One sentence per pooled polarity, or None when nothing is pooled.
    """
    by_polarity: dict[str, list[str]] = {}
    for stream in streams:
        signature = stream.get("signature") or {}
        if signature.get("ms_order") == 1:
            by_polarity.setdefault(signature.get("polarity"), []).append(
                str(stream.get("key"))
            )
    notes = [
        f"Polarity {polarity} pools {len(keys)} MS1 scan streams into one peak "
        f"list: {'; '.join(keys)}."
        for polarity, keys in by_polarity.items()
        if len(keys) > 1
    ]
    return " ".join(notes) or None


async def read_scan_streams(filename: str) -> list[dict]:
    """A stored file's scan-stream census, from its ``.props``.

    A file converted before the census existed, or by a reader that takes
    none, has no streams to report. Nothing that reads this may cost the file
    its processing - registration reads it too - so a props file that cannot
    be read, or one whose census is not a list of streams, reports no streams
    rather than raising. The shape is checked here so that every caller gets
    a census it can walk without guarding each field.

    :param filename: The sample file's stored name.
    :return: The census, or ``[]``.
    """
    try:
        props = await asyncio.to_thread(read_props, filename)
        streams = props.get("scan_streams")
        if not isinstance(streams, list):
            return []
        return [stream for stream in streams if isinstance(stream, dict)]
    except Exception:  # noqa: BLE001 - a missing census is not a processing error
        runtime.logger.opt(exception=True).debug(
            f"No scan stream census readable for {filename}"
        )
        return []


async def read_pooled_streams_note(filename: str) -> str | None:
    """:func:`pooled_streams_note` for a stored file, read from its ``.props``.

    :param filename: The sample file's stored name.
    :return: The note, or None.
    """
    return pooled_streams_note(await read_scan_streams(filename))


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
