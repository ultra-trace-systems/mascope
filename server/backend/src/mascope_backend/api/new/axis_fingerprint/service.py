"""The axis fingerprint of a raw Orbitrap file, as the API serves it.

The converter computes the fingerprint of every raw Orbitrap file
(``mascope_thermo.axis_fingerprint``) and stores the document in the file's
``.props``. Nothing computes one on request: a file converted before it
existed, or with ``[backend] axis_fingerprint = "off"``, is served the
``missing`` document until it is computed.

The document describes the raw file's own m/z axis. A client of one sample
also has to know which of the file's scans the sample's peak list is built
from, and what Mascope multiplies into the m/z it serves of them, so the
sample's route adds an ``item`` block saying both. A stitched sample's peaks
are of several scan streams, each put on the file's axis by its own factor
(``mascope_signal.mz_factor``), so the block says both stream by stream as
well.
"""

import asyncio

from fastapi import HTTPException
from sqlalchemy import select

import mascope_file.io as m_io
import mascope_file.name as m_name
import mascope_signal.mz_factor as m_factor
import mascope_thermo.thermo as m_thermo
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.db import AcquisitionStream, SampleFile, SampleItem, async_session
from mascope_thermo.axis_fingerprint import describe_scans, reference
from mascope_thermo.thermo import NoScansFoundError


#: The instrument class whose files have a fingerprint (``SampleFile.instrument_type``).
ORBITRAP = "orbi"

#: The statuses of a document that describes its file's scans. The others
#: hold their status alone (an error also its message), and the schema
#: admits nothing more in them, an item block included.
DESCRIBED = ("ok", "no_fingerprint")


def stored_document(props: dict) -> dict:
    """The document a file's properties hold, or ``missing`` where they hold
    none.

    :param props: The file's ``.props``.
    :return: A copy of the document, to add to.
    """
    return dict(props.get("axis_fingerprint") or reference.minimal_document("missing"))


def _not_orbitrap(sample_file_id: str) -> NotFoundException:
    return NotFoundException(
        f"Sample file with ID '{sample_file_id}' is not an Orbitrap file, and "
        "only an Orbitrap file has an axis fingerprint"
    )


@api_controller()
async def get_sample_file_axis_fingerprint(sample_file_id: str) -> dict:
    """The axis fingerprint of a sample file, as its ``.props`` hold it.

    :param sample_file_id: Unique identifier of the sample file.
    :raises NotFoundException: If the sample file is not found, or is not an
        Orbitrap file.
    :return: The document, whatever its status, with ``sample_file_id``.
    """
    async with async_session() as session:
        found = (
            await session.execute(
                select(SampleFile.filename, SampleFile.instrument_type).where(
                    SampleFile.sample_file_id == sample_file_id
                )
            )
        ).one_or_none()
    if found is None:
        raise NotFoundException(f"Sample file with ID '{sample_file_id}' not found")
    if found.instrument_type != ORBITRAP:
        raise _not_orbitrap(sample_file_id)

    props = await asyncio.to_thread(m_io.read_props, found.filename)
    document = {**stored_document(props), "sample_file_id": sample_file_id}
    return {
        "message": (
            f"Axis fingerprint of sample file '{found.filename}': {document['status']}."
        ),
        "data": document,
    }


@api_controller()
async def get_sample_axis_fingerprint(sample_item_id: str) -> dict:
    """The axis fingerprint of a sample's file, with what the sample reads of
    it.

    :param sample_item_id: Unique identifier of the sample.
    :raises NotFoundException: If the sample is not found, or its file is not
        an Orbitrap file.
    :raises HTTPException: 422 when the document describes the file's scans
        but the raw file they are read from is not kept; 500 when it cannot
        be read.
    :return: The file's document with ``sample_file_id``, and where it
        describes the file's scans, its modes listed for the sample and its
        ``item`` block.
    """
    async with async_session() as session:
        found = (
            await session.execute(
                select(
                    SampleItem.sample_file_id,
                    SampleItem.polarity,
                    SampleItem.t0,
                    SampleItem.t1,
                    SampleFile.filename,
                    SampleFile.instrument_type,
                    AcquisitionStream.stream_key,
                    AcquisitionStream.stitch,
                )
                .join(
                    SampleFile, SampleFile.sample_file_id == SampleItem.sample_file_id
                )
                .outerjoin(
                    AcquisitionStream,
                    AcquisitionStream.stream_id == SampleItem.stream_id,
                )
                .where(SampleItem.sample_item_id == sample_item_id)
            )
        ).one_or_none()
    if found is None:
        raise NotFoundException(f"Sample with ID '{sample_item_id}' not found")
    if found.instrument_type != ORBITRAP:
        raise _not_orbitrap(found.sample_file_id)

    props = await asyncio.to_thread(m_io.read_props, found.filename)
    document = {**stored_document(props), "sample_file_id": found.sample_file_id}
    message = (
        f"Axis fingerprint of the file of sample '{sample_item_id}': "
        f"{document['status']}."
    )
    if document["status"] not in DESCRIBED:
        return {"message": message, "data": document}

    # The scans are selected from the raw file as every read of the sample
    # selects them, so it has to be there.
    if await asyncio.to_thread(m_name.get_sample_file_type, found.filename) != (
        "orbi_raw"
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "The scans of a sample are read from its Orbitrap raw file, "
                f"and the file of sample '{sample_item_id}' keeps none"
            ),
        )
    streams = read_streams(found.stream_key, found.stitch)
    try:
        by_stream = await asyncio.to_thread(
            scans_by_stream, found.filename, found.polarity, found.t0, found.t1, streams
        )
    except Exception as e:
        # str(e) can carry internal file paths; the cause is logged
        # server-side (with traceback) by the @api_controller error handling.
        raise HTTPException(
            status_code=500, detail="Could not read the scans of the sample."
        ) from e

    scans = sorted({scan for numbers in by_stream.values() for scan in numbers})
    described = describe_scans(
        document,
        scans,
        polarity=found.polarity,
        stream_keys=[key for key in streams if key is not None],
    )
    record = props.get("mz_calibration")
    # Listed for the sample; dominant_mode stays the file's, as the schema says.
    document["modes"] = described["modes"]
    document["item"] = {
        "sample_item_id": sample_item_id,
        "sample_file_id": found.sample_file_id,
        "polarity": found.polarity,
        "t0_s": found.t0,
        "t1_s": found.t1,
        "stream_key": found.stream_key,
        "scans": scans,
        "mode_id": described["mode_id"],
        "lock": described["lock"],
        "mz_calibration_factor": (
            m_factor.stream_factor(record, found.stream_key) if record else None
        ),
        "file_mz_calibration_factor": m_factor.file_factor(record) if record else None,
        # A sample over its whole polarity reads its streams pooled, on the
        # file's factor, and has none of its own to list.
        "streams": (
            stream_entries(record, by_stream) if found.stream_key is not None else None
        ),
    }
    return {"message": message, "data": document}


def read_streams(stream_key: str | None, stitch: dict | None) -> list[str | None]:
    """The streams a sample's peak list is read from, by the stream row it
    points at.

    :param stream_key: The key of the sample's stream row, None for a sample
        that points at none.
    :param stitch: The row's stitch map, which a composite alone carries.
    :return: The stream's own key; for a composite, the keys of the streams
        that own some m/z of it, in its map's order; ``[None]``, every stream
        of the polarity, for a sample without a row.
    """
    if stream_key is None:
        return [None]
    if stitch is None:
        return [stream_key]
    # A composite selects no scans: its runs name the streams it is read from.
    return list(dict.fromkeys(owner for _lower, _upper, owner in stitch["runs"]))


def scans_by_stream(
    filename: str,
    polarity: str | None,
    t0: float | None,
    t1: float | None,
    streams: list[str | None],
) -> dict[str | None, list[int]]:
    """The scan numbers each stream gives a sample's peak list.

    Selected as every read of the sample selects them: MS1 scans of its
    polarity starting in ``[t0, t1]`` (both ends included), of each stream
    apart, so that each stream's outlier first scan is left out as its peak
    list leaves it out. Synchronous: it reads the raw file.

    :param filename: The sample's file.
    :param polarity: The sample's polarity, None for both.
    :param t0: The start of the sample's window [s], None for the file's.
    :param t1: The end of the sample's window [s], None for the file's.
    :param streams: :func:`read_streams`.
    :return: Per stream, in their order, its scan numbers ascending; empty
        for a stream none of whose scans is selected.
    """
    scans: dict[str | None, list[int]] = {}
    with m_thermo.open_backend(m_name.filename_to_datafile_path(filename)) as backend:
        for stream in streams:
            try:
                selected = backend.scan_indices(
                    polarity, t0, t1, ms_type="Ms", stream=stream
                )
            except NoScansFoundError:
                selected = []
            scans[stream] = sorted(int(scan) for scan in selected)
    return scans


def stream_entries(
    record: dict | None, by_stream: dict[str | None, list[int]]
) -> list[dict]:
    """What each stream a sample reads gives it: its scans, and the factor
    its peaks are put on the file's axis by.

    A stitched sample's peak at some m/z is of the stream that owns that
    m/z, and carries that stream's factor. The sample's peak listing names
    each peak's stream (its ``segment``, keyed in ``segments``), which a
    client looks the factor up here by.

    :param record: The file's calibration record, None for a file never
        calibrated.
    :param by_stream: :func:`scans_by_stream`, of the streams of a sample
        that reads one stream or a composite.
    :return: One entry per stream, in their order: ``stream_key``, ``scans``
        and ``mz_calibration_factor``, None for a file never calibrated.
    """
    return [
        {
            "stream_key": key,
            "scans": scans,
            "mz_calibration_factor": (
                m_factor.stream_factor(record, key) if record else None
            ),
        }
        for key, scans in by_stream.items()
    ]
