"""The m/z calibration factor a raw Orbitrap reading is put on its file's axis by.

An Orbitrap m/z calibration is one factor: an m/z as the instrument recorded
it, times the factor, is the m/z on the file's calibrated axis. A file
carries it in the calibration record of its ``.props``
(``mz_calibration["par"]["calibration_factor"]``), and everything that reads
the raw file again multiplies by it.

A file whose peaks are detected per scan stream and stitched
(``mascope_signal.stitch``) is calibrated stream by stream: two ranges of one
file trap different ion populations and read one ion up to a ppm apart. Its
record then carries a factor for each stream as well, under ``streams`` by
stream key, and a reading of one stream goes by that stream's factor:

.. code-block:: python

    {
        "mode": "one-point",
        "par": {"calibration_factor": 1.0000012},
        "streams": {
            "FTMS + p NSI SIM ms [72.0000-118.0000] R=120000": {
                "calibration_factor": 1.0000009,
                ...
            },
        },
    }

The file's own factor stays what a reading of the file as a whole goes by -
every scan of a polarity pooled, an MS2 scan - and what a stream the record
does not name goes by. A record with no ``streams`` is every file calibrated
before streams were, and reads as it always did.

Nothing here reads a file: the functions take the record.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np


#: The entry of a calibration record that holds the factor of each stream.
STREAM_FACTORS = "streams"


def file_factor(record: dict | None) -> float:
    """The factor a reading of the file as a whole is calibrated by.

    :param record: The file's calibration record, None for a file no
        calibration was applied to
    :type record: dict | None
    :return: The factor, 1.0 for an uncalibrated file
    :rtype: float
    """
    if not record:
        return 1.0
    return record["par"]["calibration_factor"]


def stream_factor(record: dict | None, key: str | None) -> float:
    """The factor a reading of one scan stream is calibrated by.

    :param record: The file's calibration record, None for a file no
        calibration was applied to
    :type record: dict | None
    :param key: The stream's key, None for the file as a whole
    :type key: str | None
    :return: The stream's own factor where the record names the stream,
        else the file's
    :rtype: float
    """
    if not record:
        return 1.0
    named = (record.get(STREAM_FACTORS) or {}).get(key) if key is not None else None
    if named and named.get("calibration_factor") is not None:
        return named["calibration_factor"]
    return file_factor(record)


def stream_factors(record: dict | None, keys: Iterable[str]) -> np.ndarray:
    """The factor of each of a peak store's streams, as its labels index them.

    :param record: The file's calibration record
    :type record: dict | None
    :param keys: The store's stream keys
    :type keys: Iterable[str]
    :return: One factor per key
    :rtype: np.ndarray
    """
    return np.array([stream_factor(record, key) for key in keys], dtype=np.float64)


#: How a stream came by its factor: fitted on calibrants of its own, carried
#: from a neighbour by what both read in their overlap, or a neighbour's as
#: it is.
ANCHORS, OVERLAP, BORROWED = "anchors", "overlap", "borrowed"

#: How many ions two streams have to share in their overlap for its m/z
#: offset to carry a calibration from one to the other. The offset taken is
#: the median of the ions' own, and under five of them one mismatched pair
#: is a large part of it. Measured over 264 composite files, the overlaps a
#: layout was drawn with share eleven to twenty-nine ions file after file,
#: and the one it was not, three.
OVERLAP_SHIFT_MIN_SHARED = 5


def carry_across(
    anchored: dict[int, float],
    members: Iterable[int],
    overlaps: Iterable[dict],
    extents: dict[int, tuple[float, float]],
) -> dict[int, dict]:
    """Give every stream of a polarity a factor, from those fitted on their own.

    A stream that holds calibrants is fitted on them, and that is its
    factor. The rest take theirs from a stream that has one, two ways, the
    better first:

    - **across an overlap.** Two streams whose ranges overlap read the ions
      both hold, and the peak store records how far apart
      (``mascope_signal.stitch.overlap_readings``): the median offset, as the
      instrument recorded the two. A stream takes the factor of the other
      shifted by it, so that both put a shared ion on one m/z. It goes
      stream to stream: everything the fitted streams reach by one overlap,
      then what those reach, an overlap of more shared ions before one of
      fewer. An overlap sharing fewer than
      :data:`OVERLAP_SHIFT_MIN_SHARED` carries nothing.
    - **unshifted.** Where no overlap reaches what is left, the stream of
      those left that lies nearest in m/z to one that has a factor takes
      that factor as it is, and the overlaps are tried again from there.
      Two ranges of one file read an ion about a ppm apart, which bounds
      what this costs; a stream with no factor at all is not bounded.

    :param anchored: The factor of each stream fitted on its own calibrants,
        by stream index
    :type anchored: dict[int, float]
    :param members: The streams of the polarity, by index
    :type members: Iterable[int]
    :param overlaps: The store's overlap readings
    :type overlaps: Iterable[dict]
    :param extents: The lowest and the highest m/z each stream holds a peak
        at, by stream index
    :type extents: dict[int, tuple[float, float]]
    :return: Per stream index: ``calibration_factor``; ``source``, one of
        :data:`ANCHORS`, :data:`OVERLAP` and :data:`BORROWED`; ``origin``,
        the index of the stream the factor came from, None for a stream
        fitted on its own; ``shift_ppm``, how far it was shifted on the
        way; and ``shared``, the ions the overlap it came across shares.
        Empty where no stream is fitted.
    :rtype: dict[int, dict]
    """
    members = sorted(set(members))
    placed = {
        index: {
            "calibration_factor": float(anchored[index]),
            "source": ANCHORS,
            "origin": None,
            "shift_ppm": None,
            "shared": None,
        }
        for index in members
        if index in anchored
    }
    if not placed:
        return {}

    # A reading is of the second stream against the first: the second reads
    # an ion that much higher, so it takes that much less to put it right
    links = []
    for reading in overlaps:
        first, second = reading["streams"]
        if not reading.get("ppm") or reading["shared"] < OVERLAP_SHIFT_MIN_SHARED:
            continue
        offset = 1.0 + reading["ppm"][1] * 1e-6
        links.append((first, second, 1.0 / offset, reading["shared"]))
        links.append((second, first, offset, reading["shared"]))

    # An overlap with a stream of another polarity links nothing here: only
    # a member is ever placed or pending
    pending = [index for index in members if index not in placed]
    while pending:
        reached: dict[int, tuple] = {}
        for origin, target, shift, shared in links:
            if origin not in placed or target not in pending:
                continue
            candidate = (-shared, origin, shift)
            if target not in reached or candidate < reached[target]:
                reached[target] = candidate
        if reached:
            for target, (shared, origin, shift) in reached.items():
                placed[target] = {
                    "calibration_factor": placed[origin]["calibration_factor"] * shift,
                    "source": OVERLAP,
                    "origin": origin,
                    "shift_ppm": (shift - 1.0) * 1e6,
                    "shared": -shared,
                }
        else:
            _distance, target, origin = min(
                (_apart(extents[target], extents[origin]), target, origin)
                for target in pending
                for origin in placed
            )
            placed[target] = {
                "calibration_factor": placed[origin]["calibration_factor"],
                "source": BORROWED,
                "origin": origin,
                "shift_ppm": None,
                "shared": None,
            }
        pending = [index for index in pending if index not in placed]
    return placed


def _apart(one: tuple[float, float], other: tuple[float, float]) -> tuple[float, float]:
    """How far apart two m/z extents lie: the gap between them, then between
    their middles, so that of two that overlap the more central is nearer."""
    gap = max(0.0, one[0] - other[1], other[0] - one[1])
    return gap, abs((one[0] + one[1]) - (other[0] + other[1])) / 2.0
