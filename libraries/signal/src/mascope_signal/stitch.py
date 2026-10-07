"""The stitch: one spectrum from the segments of a composite acquisition.

A composite method measures one chemistry as several scan ranges, each an
experiment of its own, so that each fills the trap with its own ions
(``docs/dev/ingest_routing_and_splitting.md``, section 4.5). Its file holds
one MS1 scan stream per range in a polarity, and peak detection gives each a
peak list of its own (``mascope_signal.peak``). The stitch makes one spectrum
of them: every m/z belongs to one stream, its owner under the **stitch map**,
and the composite is each stream's peaks and signal within the m/z it owns.
Nothing is averaged across streams and nothing is rescaled: where two
streams both record an ion, the owner's reading is the composite's, whole.

The map is drawn from what the file's census says of each stream
(``mascope_thermo.streams``), its scan ranges and its microscan count, and
from nothing else:

- a stream claims each of its ranges trimmed inside its edges, by
  :data:`LOWER_TRIM` of the m/z at the lower edge and :data:`UPPER_TRIM` of
  the m/z at the upper, each rounded to a whole m/z. A window reads weakest
  toward its edges, and toward the upper one most;
- where two claims meet, the stream with more microscans owns the m/z, and
  among equals the one whose range starts higher;
- an m/z no claim covers goes to a stream whose untrimmed range holds it,
  by the same order. That is how the top of the highest window is owned,
  and a band between two windows that touch without overlapping;
- an m/z no range holds is nobody's, and the composite has a gap there.

Two details the rule leaves open are settled here. An edge is taken at the
four decimals a stream's key states it to, and a trimmed edge that falls
exactly on a half goes to the even m/z, so no map depends on how a product
of floats happens to round. And two streams the rule cannot tell apart, of
the same microscans on ranges that start at the same m/z, go by their order
in the file: the first owns.

The boundaries are m/z as the instrument records them. A file's m/z
calibration moves its peaks by parts per million and leaves what its method
asked for where it was, so whoever asks which stream owns an m/z says which
calibration factor that m/z carries, and the boundaries are put on the same
axis before the two are compared. A file is then cut in the same places
whenever it is cut, before its calibration or after.

Nothing here reads a file or a store. The functions take what the census
and the peak store hold and answer in plain values, so that the rule can be
read and tested on its own.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import NamedTuple

import numpy as np


#: The rule :func:`stitch_map` draws a default map by, recorded with every
#: map: a store stitched under another rule is told apart from one stitched
#: under this.
STITCH_RULE = 1

#: How far inside its lower edge a range is claimed, as a share of that
#: edge's m/z.
LOWER_TRIM = Fraction(1, 100)

#: The same at the upper edge, where a window loses more.
UPPER_TRIM = Fraction(2, 100)

#: The trailer value that says how many transients a scan averaged, as both
#: reader backends name it among a stream's ``acquisition_params``.
MICROSCANS = "Micro Scan Count:"

#: The peak store attribute holding the map of :func:`stitch_map`.
STITCH_MAP_ATTR = "stitch_map"

#: The peak store attribute holding the readings of :func:`overlap_readings`.
STITCH_OVERLAPS_ATTR = "stitch_overlaps"

#: How near two streams' readings of one ion are, in parts per million of
#: its m/z. The streams of one file read an ion within about one of each
#: other; five is what the reader takes for one ion from scan to scan.
OVERLAP_MATCH_PPM = 5.0

#: How far an ion's own ratio may be from its overlap's median before the
#: ion is listed as one the two streams do not measure alike. Two windows of
#: one layout read an ion up to three times apart; an ion beyond that is
#: measuring something else in one of them.
WINDOW_FACTOR = 3.0

#: How many such ions one reading lists, the furthest first. It counts all.
OUTLIERS_LISTED = 20


class Segment(NamedTuple):
    """One stream of a per-stream store, as the map takes it."""

    #: Its place among the store's streams, which is what the store's
    #: ``stream`` labels and a map's runs hold.
    index: int
    polarity: str | None
    #: Its scan ranges, each ``(lower, upper)``. More than one for a
    #: multiplexed scan only.
    ranges: tuple[tuple[Fraction, Fraction], ...]
    #: Zero where the census could not say.
    microscans: float


class _Window(NamedTuple):
    """One scan range of a segment, in whole m/z.

    ``claim`` and ``held`` are ``(first, stop)``: the whole m/z from
    ``first`` up to, not including, ``stop``.
    """

    segment: Segment
    lower: Fraction
    claim: tuple[int, int]
    held: tuple[int, int]


def _edge(value: float) -> Fraction:
    """A scan range edge, exactly, at the four decimals a stream key states."""
    return Fraction(format(float(value), ".4f"))


def _microscans(stream: dict) -> float:
    """A stream's microscan count, or zero where its census holds none.

    A reader reports it as a number or as text, by backend. A count that
    varied among the scans sampled is listed by name only, and reads as
    unknown here.
    """
    constant = (stream.get("acquisition_params") or {}).get("constant") or {}
    try:
        count = float(constant.get(MICROSCANS))
    except (TypeError, ValueError):
        return 0.0
    return count if math.isfinite(count) else 0.0


def segments(streams: list[dict]) -> list[Segment]:
    """The streams of a per-stream store, as the map takes them.

    :param streams: The streams the store's labels index, as the census
        gives them (``mascope_thermo.streams.peak_streams``)
    :type streams: list[dict]
    :return: One segment per stream, in the same order
    :rtype: list[Segment]
    """
    out = []
    for index, stream in enumerate(streams):
        signature = stream.get("signature") or {}
        ranges = tuple(
            (_edge(lower), _edge(upper))
            for lower, upper in signature.get("scan_ranges") or []
            if upper > lower
        )
        out.append(
            Segment(index, signature.get("polarity"), ranges, _microscans(stream))
        )
    return out


def _windows(segment: Segment) -> list[_Window]:
    return [
        _Window(
            segment,
            lower,
            (round(lower * (1 + LOWER_TRIM)), round(upper * (1 - UPPER_TRIM))),
            (math.floor(lower), math.ceil(upper)),
        )
        for lower, upper in segment.ranges
    ]


def _owner(mz: int, windows: list[_Window]) -> int | None:
    """The stream that owns one whole m/z, or None where no range holds it."""
    candidates = [w for w in windows if w.claim[0] <= mz < w.claim[1]]
    if not candidates:
        candidates = [w for w in windows if w.held[0] <= mz < w.held[1]]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda w: (-w.segment.microscans, -w.lower, w.segment.index),
    ).segment.index


def _default_runs(members: list[Segment]) -> list[list]:
    """The map of one polarity by the rule: ``[first, stop, stream index]``
    for every stretch of whole m/z one stream owns, in m/z order."""
    windows = [window for member in members for window in _windows(member)]
    runs: list[list] = []
    if not windows:
        return runs
    first = min(window.held[0] for window in windows)
    stop = max(window.held[1] for window in windows)
    for mz in range(first, stop):
        owner = _owner(mz, windows)
        if owner is None:
            continue
        if runs and runs[-1][1] == mz and runs[-1][2] == owner:
            runs[-1][1] = mz + 1
        else:
            runs.append([mz, mz + 1, owner])
    return runs


def _number(value: float) -> int | float:
    """An m/z as a whole number where it is one, as the default map's are."""
    return int(value) if float(value).is_integer() else float(value)


def _layout_runs(
    given: list, members: list[Segment]
) -> tuple[list[list] | None, str | None]:
    """The runs a layout fixes for one polarity, or why it does not apply.

    A layout names the owner of each of its runs by that stream's scan
    range, since a stream has no other name that holds from file to file. It
    applies to a file only where every range it names is measured by exactly
    one stream of the polarity, and its runs do not overlap. A run is kept
    to the m/z its owner's range reaches.

    :param given: ``[lower, upper, [range lower, range upper]]`` per run
    :param members: The polarity's segments
    :return: The runs and None, or None and the reason
    """
    by_range: dict[tuple[Fraction, Fraction], list[Segment]] = {}
    for member in members:
        for window in member.ranges:
            by_range.setdefault(window, []).append(member)

    runs = []
    for lower, upper, (range_lower, range_upper) in given:
        named = (_edge(range_lower), _edge(range_upper))
        holders = by_range.get(named, [])
        if len(holders) != 1:
            return None, (
                f"it names the scan range {float(named[0]):g}-{float(named[1]):g}, "
                f"which {'none' if not holders else 'more than one'} of the "
                "file's streams measures"
            )
        lower, upper = max(lower, float(named[0])), min(upper, float(named[1]))
        if lower < upper:
            runs.append([_number(lower), _number(upper), holders[0].index])
    runs.sort(key=lambda run: run[0])
    for before, after in zip(runs, runs[1:]):
        if after[0] < before[1]:
            return None, (
                f"its runs overlap at m/z {after[0]:g}, so they name two owners there"
            )
    return runs, None


def stitch_map(streams: list[dict], layout: dict | None = None) -> dict:
    """The stitch map of a per-stream store.

    One map per polarity that holds more than one stream. A polarity with a
    single stream has nothing to stitch and no entry: every peak of it is
    its composite's. See the module docstring for the rule.

    A ``layout`` fixes the map of the polarities it names, where a method's
    design knows better than the rule: where an edge has to sit for a
    reagent ion to stay out of a window is nothing a file can say. It applies
    only to a file that holds the ranges it names (:func:`_layout_runs`).
    One that does not is passed over, the polarity gets the default map, and
    the map says so in its notes.

    :param streams: The streams the store's labels index, as the census
        gives them
    :type streams: list[dict]
    :param layout: ``{polarity: [[lower, upper, [range lower, range
        upper]], ...]}``, each run naming its owner by scan range
    :type layout: dict, optional
    :return: ``{"rule", "runs", "sources", "notes"}``: the rule's version;
        per stitched polarity the runs ``[lower, upper, stream index]`` in
        m/z order, a run owning ``lower <= m/z < upper``; per stitched
        polarity ``"default"`` or ``"layout"``; and what was left out of the
        map, in words. JSON-safe.
    :rtype: dict
    """
    by_polarity: dict[str, list[Segment]] = {}
    for segment in segments(streams):
        if segment.polarity is not None:
            by_polarity.setdefault(segment.polarity, []).append(segment)

    runs: dict[str, list[list]] = {}
    sources: dict[str, str] = {}
    notes: list[str] = []
    for polarity, members in by_polarity.items():
        if len(members) < 2:
            continue
        for member in members:
            if not member.ranges:
                notes.append(
                    f"Scan stream '{streams[member.index].get('key')}' states "
                    "no scan range, so it owns no m/z and its peaks are in no "
                    "composite."
                )
        fixed = None
        if layout and polarity in layout:
            fixed, refusal = _layout_runs(layout[polarity], members)
            if fixed is None:
                notes.append(
                    f"The layout given for polarity {polarity} was not "
                    f"applied: {refusal}. Its map is the default one."
                )
        runs[polarity] = _default_runs(members) if fixed is None else fixed
        sources[polarity] = "default" if fixed is None else "layout"
    return {"rule": STITCH_RULE, "runs": runs, "sources": sources, "notes": notes}


def owners(mz: np.ndarray, runs: list, calibration: float = 1.0) -> np.ndarray:
    """The stream that owns each m/z under one polarity's runs.

    :param mz: m/z values
    :type mz: np.ndarray
    :param runs: The polarity's runs, as :func:`stitch_map` gives them
    :type runs: list
    :param calibration: The m/z calibration factor the values carry: what
        the instrument recorded, multiplied by it. The runs' edges are
        multiplied by it too, which places a value exactly as its recorded
        m/z would be placed. Dividing the values back would not.
    :type calibration: float, optional
    :return: The owner's stream index per m/z, -1 where the map has a gap
    :rtype: np.ndarray
    """
    mz = np.asarray(mz, dtype=np.float64)
    out = np.full(mz.shape, -1, dtype=np.int64)
    if not len(runs):
        return out
    lower = np.array([run[0] for run in runs], dtype=np.float64) * calibration
    upper = np.array([run[1] for run in runs], dtype=np.float64) * calibration
    index = np.array([run[2] for run in runs], dtype=np.int64)
    at = np.searchsorted(lower, mz, side="right") - 1
    inside = (at >= 0) & (mz < upper[np.clip(at, 0, None)])
    out[inside] = index[at[inside]]
    return out


def composite_mask(
    mz: np.ndarray,
    stream: np.ndarray,
    polarity: np.ndarray,
    stitch: dict,
    calibration: float = 1.0,
) -> np.ndarray:
    """Which peaks of a per-stream store are in their polarity's composite.

    A peak is, where its own stream owns its m/z under the map. Every peak
    of a polarity the map does not stitch is: its one stream is the whole of
    its composite.

    :param mz: The peaks' m/z
    :type mz: np.ndarray
    :param stream: The stream index of each peak
    :type stream: np.ndarray
    :param polarity: The polarity of each peak
    :type polarity: np.ndarray
    :param stitch: The store's map, from :func:`stitch_map`
    :type stitch: dict
    :param calibration: The m/z calibration factor the peaks carry
        (:func:`owners`)
    :type calibration: float, optional
    :return: One flag per peak
    :rtype: np.ndarray
    """
    mz = np.asarray(mz, dtype=np.float64)
    stream = np.asarray(stream)
    polarity = np.asarray(polarity)
    mask = np.ones(mz.shape, dtype=bool)
    for stitched, runs in stitch["runs"].items():
        rows = np.flatnonzero(polarity == stitched)
        mask[rows] = owners(mz[rows], runs, calibration) == stream[rows]
    return mask


def owned_slice(
    mz: np.ndarray, lower: float, upper: float, calibration: float = 1.0
) -> slice:
    """The samples of an ascending m/z axis one run owns: ``lower <= m/z <
    upper``, as :func:`owners` reads a run.

    :param mz: The axis
    :type mz: np.ndarray
    :param lower: The run's lower edge
    :type lower: float
    :param upper: The run's upper edge
    :type upper: float
    :param calibration: The m/z calibration factor the axis carries
        (:func:`owners`)
    :type calibration: float, optional
    :return: The positions of the samples inside the run
    :rtype: slice
    """
    return slice(
        int(np.searchsorted(mz, lower * calibration, side="left")),
        int(np.searchsorted(mz, upper * calibration, side="left")),
    )


def _nearest(axis: np.ndarray, values: np.ndarray) -> np.ndarray:
    """For each value, the position of the nearest entry of a sorted axis."""
    right = np.clip(np.searchsorted(axis, values), 0, axis.size - 1)
    left = np.clip(right - 1, 0, axis.size - 1)
    nearer_left = np.abs(values - axis[left]) <= np.abs(axis[right] - values)
    return np.where(nearer_left, left, right)


def _shared_ions(first: np.ndarray, second: np.ndarray) -> tuple:
    """The ions two sorted peak lists both hold: positions in each.

    Two peaks are one ion where each is the other's nearest and they are
    within :data:`OVERLAP_MATCH_PPM`, so that no peak is paired twice.
    """
    if not (first.size and second.size):
        return np.empty(0, dtype=np.intp), np.empty(0, dtype=np.intp)
    to_second = _nearest(second, first)
    to_first = _nearest(first, second)
    positions = np.arange(first.size)
    paired = (to_first[to_second] == positions) & (
        np.abs(second[to_second] - first) <= first * OVERLAP_MATCH_PPM * 1e-6
    )
    return positions[paired], to_second[paired]


def _quartiles(values: np.ndarray) -> list[float]:
    return [float(q) for q in np.percentile(values, [25, 50, 75])]


def overlap_readings(
    streams: list[dict],
    mz: np.ndarray,
    stream: np.ndarray,
    heights: np.ndarray,
    kept: np.ndarray,
    scans: np.ndarray,
    calibration: float = 1.0,
) -> list[dict]:
    """What two streams of one polarity read where both measure.

    One reading for every pair of streams whose claims meet, taken over the
    m/z both claim: inside each one's trimmed range, where neither is at its
    edge. It is what the composite does not use, the other stream's reading
    of an ion its owner also holds, and it says two things of a file. The
    m/z offset between the two readings is what a stream short of calibrants
    can take its neighbour's calibration across by. Their intensity ratio is
    the layout's factor between the two windows, and an ion far off it is
    one the two windows do not measure alike.

    A reading is of the second stream against the first, the first being the
    one whose range starts lower. Its ratio is of heights per scan of each
    stream, and a peak's height is summed over every scan of its stream, one
    that missed the ion included. So the ratio carries how often each stream
    saw an ion as well as how high it read it, and a short scan at one
    microscan misses a weak ion where a window at ten does not.

    :param streams: The streams the store's labels index, as the census
        gives them
    :type streams: list[dict]
    :param mz: The peaks' m/z
    :type mz: np.ndarray
    :param stream: The stream index of each peak
    :type stream: np.ndarray
    :param heights: Each peak's height summed over its own stream's scans
    :type heights: np.ndarray
    :param kept: Which peaks a load of the store keeps: neither weak nor a
        satellite
    :type kept: np.ndarray
    :param scans: How many scans each stream holds, by stream index
    :type scans: np.ndarray
    :param calibration: The m/z calibration factor the peaks carry
        (:func:`owners`)
    :type calibration: float, optional
    :return: Per pair: ``streams``, the two stream indexes; ``overlap``, the
        m/z ``[lower, upper]`` both claim; ``shared``, how many ions both
        hold there; ``ratio``, the quartiles of their per-scan height in
        the second over the first; ``ppm``, the quartiles of their m/z in
        the second less the first; ``outlier_count``, how many are more
        than :data:`WINDOW_FACTOR` off the median ratio; and ``outliers``,
        the furthest of those as ``[m/z in the first, ratio]``. The
        quartiles are None where no ion is shared. JSON-safe.
    :rtype: list[dict]
    """
    mz = np.asarray(mz, dtype=np.float64)
    stream = np.asarray(stream)
    heights = np.asarray(heights, dtype=np.float64)
    usable = np.asarray(kept, dtype=bool) & (heights > 0)

    by_polarity: dict[str, list[Segment]] = {}
    for segment in segments(streams):
        if segment.polarity is not None and segment.ranges:
            by_polarity.setdefault(segment.polarity, []).append(segment)

    readings = []
    for members in by_polarity.values():
        members = sorted(members, key=lambda s: (min(s.ranges), s.index))
        for position, first in enumerate(members):
            for second in members[position + 1 :]:
                overlap = [
                    [max(a.claim[0], b.claim[0]), min(a.claim[1], b.claim[1])]
                    for a in _windows(first)
                    for b in _windows(second)
                    if max(a.claim[0], b.claim[0]) < min(a.claim[1], b.claim[1])
                ]
                if not overlap or not (scans[first.index] and scans[second.index]):
                    continue
                inside = np.zeros(mz.shape, dtype=bool)
                for lower, upper in overlap:
                    inside |= (mz >= lower * calibration) & (mz < upper * calibration)
                rows_first = np.flatnonzero(usable & inside & (stream == first.index))
                rows_second = np.flatnonzero(usable & inside & (stream == second.index))
                in_first, in_second = _shared_ions(mz[rows_first], mz[rows_second])
                rows_first, rows_second = rows_first[in_first], rows_second[in_second]
                reading = {
                    "streams": [first.index, second.index],
                    "overlap": overlap,
                    "shared": int(rows_first.size),
                    "ratio": None,
                    "ppm": None,
                    "outlier_count": 0,
                    "outliers": [],
                }
                if rows_first.size:
                    ratio = (heights[rows_second] / scans[second.index]) / (
                        heights[rows_first] / scans[first.index]
                    )
                    reading["ratio"] = _quartiles(ratio)
                    reading["ppm"] = _quartiles(
                        (mz[rows_second] - mz[rows_first]) / mz[rows_first] * 1e6
                    )
                    median = reading["ratio"][1]
                    off = np.maximum(ratio / median, median / ratio)
                    apart = np.flatnonzero(off > WINDOW_FACTOR)
                    apart = apart[np.argsort(-off[apart], kind="stable")]
                    reading["outlier_count"] = int(apart.size)
                    reading["outliers"] = [
                        [float(mz[rows_first[i]]), float(ratio[i])]
                        for i in apart[:OUTLIERS_LISTED]
                    ]
                readings.append(reading)
    return readings
