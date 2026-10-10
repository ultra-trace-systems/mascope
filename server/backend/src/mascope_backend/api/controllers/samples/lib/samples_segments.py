"""The segments of a stitched sample, for the views that show them.

A file whose method measures one chemistry as several scan ranges is read as
one spectrum per polarity: each m/z taken from the scan stream that owns it
under the file's stitch map (``mascope_signal.stitch``;
``docs/dev/ingest_routing_and_splitting.md``, section 4.5). Nothing is
rescaled where two streams meet, so the spectrum can step at a boundary, and
a peak's intensity is what its own stream measured. A person reading such a
spectrum has to see where the boundaries are and which stream a peak came
from, so the spectrum and the peak listing say.

**Decided by the peak store, as every reader of a stitched file is.** The
store's stream keys and its map say what the composite is made of
(``mascope_signal.compute.peak_store_composite``). A segment is named by its
index among the store's keys, which is what a peak's ``stream`` label and a
spectrum sample's ``segment`` already are. A sample whose store stitches
nothing of its polarity has no segments, and the views show it as before.

**The file's stream rows only add to it.** How many scans a stream holds and
at how many microscans is in ``acquisition_stream``, written at the file's
last processing. Where the file has a row under a segment's key, the segment
carries both; where it has none, it carries neither and is shown without.
"""

import re
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select

import mascope_signal.compute as m_compute
import mascope_signal.stitch as m_stitch
import mascope_thermo.streams as m_streams
from mascope_backend.db import AcquisitionStream, async_session
from mascope_thermo.scan_filter import parse_scan_filter


@dataclass(frozen=True)
class SampleSegments:
    """What a sample's composite is made of."""

    #: One entry per stream that owns some m/z of the composite, in the
    #: order of their index: ``index`` among the store's stream keys,
    #: ``key``, and a ``label`` a person can read.
    segments: list[dict] = field(default_factory=list)
    #: The map's runs ``[lower, upper, stream index]`` in m/z order, in m/z
    #: as the instrument recorded them.
    runs: list[list] = field(default_factory=list)
    #: The m/z calibration factor each stream carries, by stream index: a
    #: run is placed on the file's own axis by its owner's
    #: (``mascope_signal.mz_factor``).
    factors: list[float] = field(default_factory=list)

    def _factor(self, index: int) -> float:
        return self.factors[index] if index < len(self.factors) else 1.0

    def boundaries(self) -> list[dict]:
        """The runs on the file's own m/z axis: who owns from where to where.

        Each run's edges are where its owner's samples and peaks lie. Two
        segments calibrated apart therefore need not meet at one m/z: a
        boundary can show a gap or an overlap of the difference of the two
        factors, some parts per million of it.
        """
        return [
            {
                "segment": int(index),
                "mz_lower": float(lower) * self._factor(index),
                "mz_upper": float(upper) * self._factor(index),
            }
            for lower, upper, index in self.runs
        ]

    def runs_of(self, mz: np.ndarray, segment: np.ndarray | None = None) -> list[dict]:
        """The runs with where each one's samples lie in a stitched signal.

        :param mz: The m/z axis of the signal as it is answered: the
            stitched signal's, or a part of it, ascending.
        :param segment: The stream each sample is of, as the stitched signal
            labels it. With it a run takes exactly its own samples where its
            edge, placed by its owner's factor, falls among a neighbour's;
            without, the samples inside its edges.
        :return: :meth:`boundaries`, each with ``from`` and ``to``: the
            half-open range of positions on ``mz`` the run's samples take.
            Empty for a run none of whose samples are on ``mz``.
        """
        described = []
        position = 0
        for run, (lower, upper, index) in zip(self.boundaries(), self.runs):
            owned = m_stitch.owned_slice(mz, lower, upper, self._factor(index))
            start = max(position, int(owned.start))
            stop = max(start, int(owned.stop))
            if segment is not None:
                own = np.flatnonzero(np.asarray(segment[start:stop]) == index)
                stop = start + int(own[-1]) + 1 if own.size else start
                start = start + int(own[0]) if own.size else start
            position = stop
            described.append({**run, "from": start, "to": stop})
        return described


def segment_label(key: str) -> str:
    """A stream key as a person reads a segment: its scan range and mode.

    A key is the scan filter of the stream's scans with what else tells it
    from the file's other streams: ``FTMS - p NSI SIM ms [72.0000-118.0000]
    R=120000`` reads ``m/z 72-118, SIM``. A full scan is not said to be one,
    and an experiment that shares its filter with another says which it is.
    A key that states no range is shown as it is.
    """
    scan_filter = parse_scan_filter(key.split(" R=")[0])
    if not scan_filter.scan_ranges:
        return key
    ranges = ", ".join(
        f"{lower:g}-{upper:g}" for lower, upper in scan_filter.scan_ranges
    )
    parts = [f"m/z {ranges}"]
    if scan_filter.scan_mode and scan_filter.scan_mode.lower() != "full":
        parts.append(scan_filter.scan_mode)
    if event := re.search(r"\bevent=(\d+)", key):
        parts.append(f"event {event.group(1)}")
    return ", ".join(parts)


def read_sample_segments(filename: str, polarity: str) -> SampleSegments | None:
    """What a sample's composite is made of, or None where nothing is stitched.

    Synchronous: it reads the filestore, so it runs in a worker thread.

    :param filename: The sample's file.
    :param polarity: The sample's polarity.
    :raises ValueError: If the store carries only part of what a per-stream
        store does
    :raises mascope_signal.compute.StalePeakStoreError: If it is a
        per-stream store with no map
    :return: The segments and the runs, or None for a store that stitches
        nothing of the polarity - nearly every sample.
    """
    composite = m_compute.peak_store_composite(filename, polarity)
    if composite is None:
        return None
    keys = composite["keys"]
    owners = sorted({index for _lower, _upper, index in composite["runs"]})
    return SampleSegments(
        segments=[
            {"index": index, "key": keys[index], "label": segment_label(keys[index])}
            for index in owners
        ],
        runs=composite["runs"],
        factors=composite["factors"],
    )


async def with_stream_rows(sample_file_id: str, segments: list[dict]) -> list[dict]:
    """The segments, each with what the file's stream row says of it.

    :param sample_file_id: The sample's file.
    :param segments: The segments, as :attr:`SampleSegments.segments`.
    :return: The same entries with ``scans`` and ``microscans``: the
        stream's scan count and microscan count where the file has a row
        under the segment's key that records them, else None.
    """
    async with async_session() as session:
        rows = {
            row.stream_key: row
            for row in (
                await session.execute(
                    select(AcquisitionStream).where(
                        AcquisitionStream.sample_file_id == sample_file_id,
                        AcquisitionStream.stream_key.in_(
                            [segment["key"] for segment in segments]
                        ),
                    )
                )
            ).scalars()
        }
    described = []
    for segment in segments:
        row = rows.get(segment["key"])
        described.append(
            {
                **segment,
                "scans": row.scan_count if row else None,
                "microscans": (
                    m_streams.microscans({"acquisition_params": row.acquisition_params})
                    if row
                    else None
                ),
            }
        )
    return described
