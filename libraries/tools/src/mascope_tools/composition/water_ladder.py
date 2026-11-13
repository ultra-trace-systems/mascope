"""The reagent ions' water ladders, measured on a batch's own series.

A soft-interface chemical-ionization source carries its reagent ions hydrated -
Br-.(H2O)n, NO3-.(H2O)n, HNO3.NO3-.(H2O)n - and how far a ladder reaches moves
with the source and the humidity: on a long TOF batch a ladder that ends at a
few waters before an instrument restart runs past fifteen after it. One
spectrum cannot tell a high rung from an organic of the same mass, so the
reagent pre-pass climbs a core only as far as a work names its hydrates
(step 3.3d), and a long ladder reads as organics. A batch can tell: a rung is a
line present in most spectra of a stretch of acquisition, above a ladder that
is present all the way down to its core, where a mass picked at random beside
it is not.

This module is the pure half of the ninth rider of step 4.2 of the assignment
plan (``docs/dev/assignment_quality_plan.md``, step 4.2j; the reference engine's
0.10 line, written from its published description and never ported). It reads
a batch's traces - its batch peaks, each a trace centre, a window and an
intensity per spectrum - for the ladders the profile declares
(``reagents.water_ladders``), and says which rungs pass in which stretch of the
batch. The caller moves the rows.

The segments
------------

A batch is cut into acquisition segments where the time between two spectra
runs past :data:`MIN_GAP_SECONDS` and :data:`GAP_FACTOR` times the batch's
median spacing (:func:`acquisition_segments`): an instrument restart, a night
off. A segment of fewer than :data:`MIN_SEGMENT_SPECTRA` spectra joins the
neighbour it is closer to in time, as a presence share over a handful of
spectra says little. Every rung is read per segment.

A rung
------

Per declared core, per track (the core's monoisotopic line and each heavy
line of its envelope bright enough to be a trace of its own,
``reagents.water_tracks``), per segment, :func:`read_ladder` climbs n = 1 to
45. A rung **passes** where every one of these holds:

- **Presence and contiguity.** The core and every rung from 1 to n are present
  in at least :data:`PRESENCE_SHARE` of the segment's spectra. The ladder ends
  at its first rung under that share.
- **The decoy margin.** The rung is present at least :data:`DECOY_MARGIN` times
  as often as the mean of its decoys: the same segment's presence at masses
  beside the rung, where only chance puts a peak. On an Orbitrap the decoys sit
  at :data:`ORBITRAP_DECOY_DA` either side; on a TOF, whose line is 0.05 to
  0.07 Da wide at m/z 450 to 700 and whose picker reports a weak satellite
  within a line width either side of a bright line, at
  :data:`TOF_DECOY_FWHM` line widths. A decoy at another declared ladder's line
  is that ladder, not chance, and is not read; the decoys' mean is floored at
  :data:`DECOY_FLOOR`.
- **No carbon, on a TOF.** At a TOF's width a hydrated cluster and an organic
  share a nominal mass, and its 13C line is what tells them apart: where the
  rung is bright enough that a five-carbon ion's 13C line would clear the
  spectrum's local floor, the line one mass unit above it stands no taller, as
  the median over those spectra, than the cluster's own M+1 plus
  :data:`CARBON_TEST_CARBONS` carbons' worth. Where that line would blend with
  another declared ladder's line present in the segment the test is not asked.

The thresholds are the rider's own, as the reference describes its rule; the
bench's card reads them (milestone 6).

A water rung is the commonest step a humid source makes, which is why one
spectrum never lets a water rung displace a molecule [good70, sun88]
(``reagents.match_ion_ladders``); a ladder present across a segment of the
batch, contiguous to its core and above its decoys, is the batch's evidence
that it can.
"""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from mascope_tools.composition.reagents import (
    MAX_BATCH_WATERS,
    WATER_RUNG_REFERENCES,
    WaterLadder,
    WaterTrack,
)


#: The share of a segment's spectra a core and each rung below a passing rung
#: must be present in: the share a trace must be present in to count as
#: persistent in the co-variation reading's common mode
#: (``coherence.PERSISTENT_SHARE``).
PRESENCE_SHARE = 0.5

#: How many times more often than at its decoys a rung must be present.
DECOY_MARGIN = 3.0

#: The floor under the decoys' mean presence. Under :data:`PRESENCE_SHARE` it
#: never binds (3 x 0.02 is under a half); it keeps a rung no decoy could be
#: read for from dividing by nothing.
DECOY_FLOOR = 0.02

#: An Orbitrap rung's decoys, in Da either side of it: outside the line, which
#: is a few mDa wide, and inside the stretch where a peak is as likely as at the
#: rung by chance.
ORBITRAP_DECOY_DA = (0.02, 0.035, 0.05)

#: A TOF rung's decoys, in line widths (FWHM) either side of it: past the
#: satellites a TOF picker reports within a width or so of a bright line, and
#: near enough to read the local chance of a peak.
TOF_DECOY_FWHM = (2.0, 2.5, 3.0, 3.5, 4.0, 5.0)

#: A decoy within this many line widths, plus the window a line is read in, of
#: another declared ladder's line is that ladder's and is not read.
DECOY_SKIP_FWHM = 1.5

#: The carbons a TOF rung's M+1 line is tested against: a rung bright enough to
#: show this many carbons' 13C must show no more than its own M+1 and theirs.
CARBON_TEST_CARBONS = 5

#: What one carbon adds to an ion's M+1 line, over its monoisotopic line: 13C's
#: abundance over 12C's (1.07 % / 98.93 %).
C13_PER_CARBON = 0.0108

#: The spectrum's local floor a five-carbon 13C line must clear for the carbon
#: test to read a rung in that spectrum: this quantile of the heights of the
#: spectrum's peaks within :data:`LOCAL_FLOOR_DA` of the rung, the edge the
#: picker reports peaks down to there.
LOCAL_FLOOR_QUANTILE = 0.05
LOCAL_FLOOR_DA = 25.0

#: Where a rung's M+1 line sits within this many line widths, plus the window,
#: of another declared ladder's line present in the segment, a TOF's line there
#: is the blend of both and the carbon test is not asked.
BLEND_FWHM = 0.5

#: A gap between two spectra longer than both of these cuts the batch into
#: acquisition segments: an hour, and five times the batch's median spacing.
MIN_GAP_SECONDS = 3600.0
GAP_FACTOR = 5.0

#: A segment of fewer spectra than this joins its nearer neighbour.
MIN_SEGMENT_SPECTRA = 10

#: Where the carbon test looks for a rung's M+1 line: a 13C line's distance
#: above its 12C line, in Da.
C13_SPACING = 1.0033548

#: The drift margin a batch peak's window carries beyond half its line width,
#: in ppm: the fold's (``batch_peaks.DEFAULT_DRIFT_MARGIN_PPM``), so a line
#: width is read back off the windows the fold minted.
WINDOW_DRIFT_MARGIN_PPM = 2.0

#: Why a rung read in a segment did not pass.
FAILED_ABSENT = "absent"
FAILED_DECOYS = "decoys"
FAILED_CARBON = "carbon"
PASSED = "passed"


def thresholds() -> dict:
    """The rule's constants, as a run records them."""
    return {
        "max_waters": MAX_BATCH_WATERS,
        "presence_share": PRESENCE_SHARE,
        "decoy_margin": DECOY_MARGIN,
        "decoy_floor": DECOY_FLOOR,
        "orbitrap_decoy_da": list(ORBITRAP_DECOY_DA),
        "tof_decoy_fwhm": list(TOF_DECOY_FWHM),
        "decoy_skip_fwhm": DECOY_SKIP_FWHM,
        "carbon_test_carbons": CARBON_TEST_CARBONS,
        "local_floor_quantile": LOCAL_FLOOR_QUANTILE,
        "local_floor_da": LOCAL_FLOOR_DA,
        "blend_fwhm": BLEND_FWHM,
        "min_gap_seconds": MIN_GAP_SECONDS,
        "gap_factor": GAP_FACTOR,
        "min_segment_spectra": MIN_SEGMENT_SPECTRA,
        "references": list(WATER_RUNG_REFERENCES),
    }


# --- the segments ------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """A stretch of a batch's acquisition, read on its own.

    :param index: Its place among the batch's segments, from 0.
    :param spectra: Its spectra, in acquisition order.
    :param start: When its first spectrum was acquired, in seconds; None for
        the segment of spectra with no time.
    :param end: When its last one was.
    """

    index: int
    spectra: tuple[str, ...]
    start: Optional[float] = None
    end: Optional[float] = None

    def to_json(self) -> dict:
        return {
            "index": self.index,
            "spectra": len(self.spectra),
            "start": self.start,
            "end": self.end,
        }


def acquisition_segments(
    times: Mapping[str, Optional[float]],
    *,
    min_gap_seconds: float = MIN_GAP_SECONDS,
    gap_factor: float = GAP_FACTOR,
    min_spectra: int = MIN_SEGMENT_SPECTRA,
) -> list[Segment]:
    """Cut a batch's spectra into acquisition segments.

    The spectra are put in acquisition order and cut where the gap between two
    distinct acquisition times is longer than ``min_gap_seconds`` and than
    ``gap_factor`` times the median gap between distinct times (spectra of one
    time, a file's segments, are one time point). A segment of fewer than
    ``min_spectra`` spectra then joins the neighbour across the shorter gap,
    the smallest first, until none is left or one segment remains. Spectra with
    no time are a segment of their own, after the others: nothing places them.

    :param times: Spectrum -> its acquisition time in seconds, or None.
    :return: The segments, in acquisition order.
    """
    timed = sorted(
        ((time, spectrum) for spectrum, time in times.items() if time is not None),
        key=lambda pair: (pair[0], pair[1]),
    )
    groups: list[list[tuple[float, str]]] = []
    if timed:
        distinct = sorted({time for time, _ in timed})
        spacings = np.diff(distinct)
        cut = max(
            min_gap_seconds,
            gap_factor * float(np.median(spacings)) if spacings.size else 0.0,
        )
        groups = [[timed[0]]]
        for previous, current in zip(timed, timed[1:]):
            if current[0] - previous[0] > cut:
                groups.append([])
            groups[-1].append(current)
        while len(groups) > 1:
            small = [i for i, group in enumerate(groups) if len(group) < min_spectra]
            if not small:
                break
            i = min(small, key=lambda k: (len(groups[k]), k))
            if i == 0:
                into = 1
            elif i == len(groups) - 1:
                into = i - 1
            else:
                before = groups[i][0][0] - groups[i - 1][-1][0]
                after = groups[i + 1][0][0] - groups[i][-1][0]
                into = i - 1 if before <= after else i + 1
            low, high = sorted((i, into))
            groups[low : high + 1] = [groups[low] + groups[high]]
    segments = [
        Segment(
            index,
            tuple(spectrum for _, spectrum in group),
            float(group[0][0]),
            float(group[-1][0]),
        )
        for index, group in enumerate(groups)
    ]
    untimed = sorted(spectrum for spectrum, time in times.items() if time is None)
    if untimed:
        segments.append(Segment(len(segments), tuple(untimed)))
    return segments


# --- the traces --------------------------------------------------------------------


@dataclass(frozen=True)
class Trace:
    """One batch peak as the ladder reads it.

    :param key: Its id.
    :param mz: Its trace centre.
    :param tol_ppm: Its window, half its line width and a drift margin: a peak
        at a mass inside it folds into it.
    :param intensities: Spectrum -> its intensity there, for each spectrum it
        is present in.
    """

    key: str
    mz: float
    tol_ppm: float
    intensities: Mapping[str, float]


class TraceIndex:
    """A partition's traces by m/z."""

    def __init__(self, traces: Iterable[Trace]) -> None:
        self.traces: list[Trace] = sorted(traces, key=lambda t: (t.mz, t.key))
        self._mzs = [trace.mz for trace in self.traces]
        self._widest = max((t.tol_ppm for t in self.traces), default=0.0)

    def at(self, mz: float) -> list[Trace]:
        """The traces a peak at this mass would fold into: each whose centre
        sits within its own window of it. Usually one; a trace the fold split
        in two is both."""
        reach = mz * self._widest * 1e-6
        lo = bisect.bisect_left(self._mzs, mz - reach)
        hi = bisect.bisect_right(self._mzs, mz + reach)
        return [
            trace
            for trace in self.traces[lo:hi]
            if abs(trace.mz - mz) <= trace.mz * trace.tol_ppm * 1e-6
        ]

    def presence(self, mz: float, spectra: Sequence[str]) -> float:
        """The share of ``spectra`` a peak is present in at this mass."""
        if not spectra:
            return 0.0
        held = set()
        for trace in self.at(mz):
            held.update(trace.intensities)
        return sum(1 for spectrum in spectra if spectrum in held) / len(spectra)

    def intensity(self, mz: float, spectrum: str) -> Optional[float]:
        """The intensity at this mass in one spectrum, the brightest trace's
        where two hold it; None where none does."""
        values = [
            trace.intensities[spectrum]
            for trace in self.at(mz)
            if spectrum in trace.intensities
        ]
        return max(values) if values else None

    def window_ppm(self, mz: float) -> float:
        """The window a line at this mass is read in: the median of the
        windows of the traces within :data:`LOCAL_FLOOR_DA` of it."""
        lo = bisect.bisect_left(self._mzs, mz - LOCAL_FLOOR_DA)
        hi = bisect.bisect_right(self._mzs, mz + LOCAL_FLOOR_DA)
        near = [trace.tol_ppm for trace in self.traces[lo:hi]]
        if not near:
            near = [trace.tol_ppm for trace in self.traces]
        return float(statistics.median(near)) if near else 0.0


@dataclass
class Spectra:
    """Each spectrum's peaks as the batch holds them, by m/z: what a TOF's
    carbon test reads a spectrum's local floor off."""

    by_spectrum: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)

    @classmethod
    def of(cls, traces: Iterable[Trace]) -> "Spectra":
        peaks: dict[str, list[tuple[float, float]]] = {}
        for trace in traces:
            for spectrum, intensity in trace.intensities.items():
                peaks.setdefault(spectrum, []).append((trace.mz, intensity))
        by_spectrum = {}
        for spectrum, pairs in peaks.items():
            pairs.sort()
            by_spectrum[spectrum] = (
                np.array([mz for mz, _ in pairs], dtype=float),
                np.array([value for _, value in pairs], dtype=float),
            )
        return cls(by_spectrum)

    def local_floor(self, spectrum: str, mz: float) -> Optional[float]:
        """The spectrum's floor near a mass: :data:`LOCAL_FLOOR_QUANTILE` of
        its peaks' intensities within :data:`LOCAL_FLOOR_DA`; None where it
        holds none there."""
        held = self.by_spectrum.get(spectrum)
        if held is None:
            return None
        mzs, values = held
        lo = int(np.searchsorted(mzs, mz - LOCAL_FLOOR_DA, side="left"))
        hi = int(np.searchsorted(mzs, mz + LOCAL_FLOOR_DA, side="right"))
        near = values[lo:hi]
        near = near[near > 0]
        if not near.size:
            return None
        return float(np.quantile(near, LOCAL_FLOOR_QUANTILE))


# --- a rung ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CarbonReading:
    """A TOF rung's carbon test in one segment.

    :param asked: Whether it was read: not where the M+1 line blends with
        another ladder's line, nor where no spectrum showed the rung bright
        enough.
    :param spectra: The spectra bright enough to read it in.
    :param ratio: The median M+1 over the rung's line in them.
    :param bar: What a carbon-free cluster may show: its own M+1 and
        :data:`CARBON_TEST_CARBONS` carbons'.
    :param blend: The ladder line its M+1 blends with, where it does.
    """

    asked: bool
    spectra: int = 0
    ratio: Optional[float] = None
    bar: Optional[float] = None
    blend: Optional[float] = None

    @property
    def carbon_free(self) -> bool:
        return not self.asked or self.ratio is None or self.ratio <= self.bar

    def to_json(self) -> dict:
        return {
            "asked": self.asked,
            "spectra": self.spectra,
            "ratio": None if self.ratio is None else round(self.ratio, 4),
            "bar": None if self.bar is None else round(self.bar, 4),
            "blend_mz": None if self.blend is None else round(self.blend, 5),
        }


@dataclass(frozen=True)
class RungReading:
    """One rung's track read in one segment.

    :param waters: n.
    :param mz: Where the track's line sits.
    :param keys: The batch peaks at it.
    :param presence: The share of the segment's spectra it is present in.
    :param decoy_presence: The decoys' mean presence, floored; None for a rung
        absent (no decoy is read for it).
    :param decoys: How many decoys were read.
    :param carbon: The carbon test, on a TOF; None elsewhere, and for a rung
        absent or failing its decoys.
    :param verdict: :data:`PASSED`, or why not.
    """

    waters: int
    mz: float
    keys: tuple[str, ...]
    presence: float
    decoy_presence: Optional[float]
    decoys: int
    carbon: Optional[CarbonReading]
    verdict: str

    @property
    def passed(self) -> bool:
        return self.verdict == PASSED

    def to_json(self) -> dict:
        return {
            "waters": self.waters,
            "mz": round(self.mz, 5),
            "presence": round(self.presence, 4),
            "decoy_presence": (
                None if self.decoy_presence is None else round(self.decoy_presence, 4)
            ),
            "decoys": self.decoys,
            "carbon": None if self.carbon is None else self.carbon.to_json(),
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class LadderReading:
    """One core's track read in one segment.

    :param core_presence: The share of the segment's spectra its core is in.
    :param rungs: Each rung read, from n = 1 up to the first absent one; none
        where the core is under :data:`PRESENCE_SHARE`.
    """

    ladder: WaterLadder
    track: WaterTrack
    segment: Segment
    core_presence: float
    rungs: tuple[RungReading, ...] = ()

    @property
    def passed(self) -> tuple[RungReading, ...]:
        return tuple(rung for rung in self.rungs if rung.passed)

    @property
    def reach(self) -> int:
        """The highest rung that passes; 0 for none."""
        return max((rung.waters for rung in self.passed), default=0)

    def to_json(self) -> dict:
        return {
            "core": self.ladder.core.label,
            "track": self.track.label,
            "segment": self.segment.index,
            "core_presence": round(self.core_presence, 4),
            "tested": len(self.rungs),
            "passed": len(self.passed),
            "reach": self.reach,
        }


@dataclass(frozen=True)
class Partition:
    """What a partition's ladders read: its traces, its spectra and its width.

    :param traces: Its batch peaks.
    :param instrument_type: ``"tof"`` reads the TOF's decoys and its carbon
        test; anything else an Orbitrap's.
    :param other_lines: Every declared ladder's line in the partition, n = 0
        to the top, each track, sorted (:func:`ladder_lines`): a decoy at one
        of them is not read, and a rung's M+1 at one whose ladder the segment
        shows blends with it. A line's own is left out by the reading.
    :param line_cores: Each of those lines -> its ladder's core on the same
        track, which the segment must show for a blend; a line not here is its
        own core.
    :param spectra: Each spectrum's peaks, for a TOF's carbon test.
    """

    traces: TraceIndex
    instrument_type: Optional[str]
    other_lines: tuple[float, ...] = ()
    line_cores: Mapping[float, float] = field(default_factory=dict)
    spectra: Optional[Spectra] = None

    @classmethod
    def of(
        cls,
        traces: Sequence[Trace],
        instrument_type: Optional[str],
        ladders: Sequence[WaterLadder],
        *,
        max_waters: int = MAX_BATCH_WATERS,
    ) -> "Partition":
        """A partition's reading of its traces for its profile's ladders."""
        cores = ladder_line_cores(ladders, max_waters=max_waters)
        return cls(
            TraceIndex(traces),
            instrument_type,
            tuple(sorted(cores)),
            cores,
            Spectra.of(traces),
        )

    @property
    def tof(self) -> bool:
        return (self.instrument_type or "").strip().lower() == "tof"

    def fwhm_da(self, mz: float) -> float:
        """The line width at a mass, read off the windows the fold minted its
        batch peaks at: twice the window less its drift margin."""
        window = self.traces.window_ppm(mz)
        return max(0.0, 2.0 * (window - WINDOW_DRIFT_MARGIN_PPM)) * mz * 1e-6

    def window_da(self, mz: float) -> float:
        return self.traces.window_ppm(mz) * mz * 1e-6

    def shows(self, line: float, spectra: Sequence[str]) -> bool:
        """Whether a segment shows another ladder's line: the line and its
        ladder's core each present in half its spectra or more."""
        core = self.line_cores.get(line, line)
        return (
            self.traces.presence(core, spectra) >= PRESENCE_SHARE
            and self.traces.presence(line, spectra) >= PRESENCE_SHARE
        )


def _near_line(mz: float, lines: Sequence[float], reach: float) -> Optional[float]:
    """A line within ``reach`` of a mass, the nearest; None for none."""
    lo = bisect.bisect_left(lines, mz - reach)
    hi = bisect.bisect_right(lines, mz + reach)
    near = lines[lo:hi]
    return min(near, key=lambda line: abs(line - mz)) if near else None


def decoy_masses(mz: float, partition: Partition, own: float) -> list[float]:
    """A rung's decoys, but those at another declared ladder's line.

    :param mz: The rung's line.
    :param own: The rung's own line, which is not another's.
    """
    width = partition.fwhm_da(mz)
    if partition.tof:
        offsets = [k * width for k in TOF_DECOY_FWHM] if width > 0 else []
    else:
        offsets = list(ORBITRAP_DECOY_DA)
    skip = DECOY_SKIP_FWHM * width + partition.window_da(mz)
    others = [line for line in partition.other_lines if abs(line - own) > 1e-6]
    decoys = []
    for offset in offsets:
        for decoy in (mz - offset, mz + offset):
            if _near_line(decoy, others, skip) is None:
                decoys.append(decoy)
    return decoys


def read_carbon(
    ladder: WaterLadder,
    track: WaterTrack,
    waters: int,
    partition: Partition,
    segment: Segment,
) -> CarbonReading:
    """A TOF rung's carbon test in one segment (the module's docstring)."""
    mz = ladder.line_mz(waters, track)
    above = mz + C13_SPACING
    reach = BLEND_FWHM * partition.fwhm_da(above) + partition.window_da(above)
    lo = bisect.bisect_left(partition.other_lines, above - reach)
    hi = bisect.bisect_right(partition.other_lines, above + reach)
    for line in partition.other_lines[lo:hi]:
        if abs(line - mz) > 1e-6 and partition.shows(line, segment.spectra):
            return CarbonReading(False, blend=line)
    spectra = partition.spectra or Spectra()
    carbons = CARBON_TEST_CARBONS * C13_PER_CARBON
    ratios = []
    for spectrum in segment.spectra:
        height = partition.traces.intensity(mz, spectrum)
        if not height or height <= 0:
            continue
        floor = spectra.local_floor(spectrum, mz)
        if floor is None or height * carbons < floor:
            continue
        line = partition.traces.intensity(above, spectrum) or 0.0
        ratios.append(line / height)
    if not ratios:
        return CarbonReading(False)
    bar = ladder.m1_share(waters, track) + carbons
    return CarbonReading(True, len(ratios), float(statistics.median(ratios)), bar)


def read_rung(
    ladder: WaterLadder,
    track: WaterTrack,
    waters: int,
    partition: Partition,
    segment: Segment,
) -> RungReading:
    """One rung's track in one segment: present, above its decoys and, on a
    TOF, carbon-free. Contiguity is :func:`read_ladder`'s."""
    mz = ladder.line_mz(waters, track)
    keys = tuple(trace.key for trace in partition.traces.at(mz))
    presence = partition.traces.presence(mz, segment.spectra)
    if presence < PRESENCE_SHARE:
        return RungReading(waters, mz, keys, presence, None, 0, None, FAILED_ABSENT)
    decoys = decoy_masses(mz, partition, mz)
    decoy_presence = max(
        DECOY_FLOOR,
        float(np.mean([partition.traces.presence(d, segment.spectra) for d in decoys]))
        if decoys
        else 0.0,
    )
    if presence < DECOY_MARGIN * decoy_presence:
        return RungReading(
            waters, mz, keys, presence, decoy_presence, len(decoys), None, FAILED_DECOYS
        )
    carbon = (
        read_carbon(ladder, track, waters, partition, segment)
        if partition.tof
        else None
    )
    verdict = PASSED if carbon is None or carbon.carbon_free else FAILED_CARBON
    return RungReading(
        waters, mz, keys, presence, decoy_presence, len(decoys), carbon, verdict
    )


def read_ladder(
    ladder: WaterLadder,
    track: WaterTrack,
    partition: Partition,
    segment: Segment,
    *,
    max_waters: int = MAX_BATCH_WATERS,
) -> LadderReading:
    """Climb one core's track in one segment: from its core, a rung at a time,
    to the first rung under :data:`PRESENCE_SHARE` of the segment's spectra,
    which ends the ladder. Each rung reached is read (:func:`read_rung`);
    nothing is climbed where the core itself is under the share."""
    core_presence = partition.traces.presence(ladder.line_mz(0, track), segment.spectra)
    if core_presence < PRESENCE_SHARE:
        return LadderReading(ladder, track, segment, core_presence)
    rungs = []
    for waters in range(1, min(max_waters, len(ladder.rungs)) + 1):
        reading = read_rung(ladder, track, waters, partition, segment)
        rungs.append(reading)
        if reading.verdict == FAILED_ABSENT:
            break
    return LadderReading(ladder, track, segment, core_presence, tuple(rungs))


def ladder_line_cores(
    ladders: Sequence[WaterLadder], *, max_waters: int = MAX_BATCH_WATERS
) -> dict[float, float]:
    """Every declared ladder's line, cores included, every track, each with
    its ladder's core on the same track."""
    return {
        ladder.line_mz(waters, track): ladder.line_mz(0, track)
        for ladder in ladders
        for track in ladder.tracks
        for waters in range(0, min(max_waters, len(ladder.rungs)) + 1)
    }


def ladder_lines(
    ladders: Sequence[WaterLadder], *, max_waters: int = MAX_BATCH_WATERS
) -> tuple[float, ...]:
    """Every declared ladder's line, cores included, every track, sorted: what
    a decoy and a rung's M+1 are read against (:attr:`Partition.other_lines`)."""
    return tuple(sorted(ladder_line_cores(ladders, max_waters=max_waters)))


def read_ladders(
    ladders: Sequence[WaterLadder],
    partition: Partition,
    segments: Sequence[Segment],
    *,
    max_waters: int = MAX_BATCH_WATERS,
) -> list[LadderReading]:
    """Every declared core's every track, read in every segment."""
    return [
        read_ladder(ladder, track, partition, segment, max_waters=max_waters)
        for ladder in ladders
        for track in ladder.tracks
        for segment in segments
    ]


__all__ = [
    "BLEND_FWHM",
    "C13_PER_CARBON",
    "CARBON_TEST_CARBONS",
    "DECOY_FLOOR",
    "DECOY_MARGIN",
    "DECOY_SKIP_FWHM",
    "FAILED_ABSENT",
    "FAILED_CARBON",
    "FAILED_DECOYS",
    "GAP_FACTOR",
    "LOCAL_FLOOR_DA",
    "LOCAL_FLOOR_QUANTILE",
    "MIN_GAP_SECONDS",
    "MIN_SEGMENT_SPECTRA",
    "ORBITRAP_DECOY_DA",
    "PASSED",
    "PRESENCE_SHARE",
    "TOF_DECOY_FWHM",
    "WINDOW_DRIFT_MARGIN_PPM",
    "CarbonReading",
    "LadderReading",
    "Partition",
    "RungReading",
    "Segment",
    "Spectra",
    "Trace",
    "TraceIndex",
    "acquisition_segments",
    "decoy_masses",
    "ladder_line_cores",
    "ladder_lines",
    "read_carbon",
    "read_ladder",
    "read_ladders",
    "read_rung",
    "thresholds",
]
