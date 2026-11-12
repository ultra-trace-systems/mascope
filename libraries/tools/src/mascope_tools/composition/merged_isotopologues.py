"""A merged row that is another merged ion's isotopologue, read over a batch.

A batch's ledger holds one row per species the fold merged across its files.
Each file's run decides on its own whether a peak is an analyte or a line of
another analyte's envelope, and a file whose run did not read a dim 13C, 18O,
15N, 34S, 37Cl, 81Br or Si line as its parent's commits it as an analyte of its
own; merged, that is a row of the batch ledger which is no compound. The batch
shows what one file cannot: a true isotope line sits at its parent's predicted
position and holds its predicted share of the parent in nearly every spectrum
that shows both, an independent compound at that position in few. This
module is the pure half of the fifth rider of step 4.2 of the assignment plan
(``docs/dev/assignment_quality_plan.md``, step 4.2f; the reference engine's
0.10 line), the batch-level form of step 2.4e's per-file isotopologue claims:
which lines are read, how close a row must sit to one, and how its share is
judged over the batch.

The lines
---------

:func:`predict_isotopologue_lines` reads a parent ion's 13C line and its
diagnostic isotope lines (``diagnostic_isotopologues``, step 4.2c): one heavy
atom of the element, every other atom at its most abundant, at the parent's
resolving power, the lines one peak cannot separate merged as the fit score
merges them. A line under half of the peak it falls in is the peak of another
line and is not read; a resolved one is read at that peak's centre and share,
which is what the peak measures.

The position
------------

A row is at a line where its m/z sits within :func:`position_window_ppm` of
the line's: four times the batch's own mass scatter
(:func:`mass_scatter_ppm`, the robust spread of its members' m/z around their
rows' centres), and never under :data:`WINDOW_FLOOR_PPM`.

The share
---------

:func:`read_share` reads the row's intensity over the parent's in every
spectrum holding both, as a factor of the predicted share - by area on a store
that records areas (a TOF), by height on one that records heights (an
Orbitrap, the height its envelope tolerance is read by, decision 32). The row
is the parent's isotopologue where the median factor over
:data:`MIN_SPECTRA` or more spectra is between :data:`MEDIAN_LOW` and
:data:`MEDIAN_HIGH`, with fewer than :data:`MAX_HIGH_SHARE` of the spectra
above :data:`HIGH_FACTOR`: a line can read low where an Orbitrap scan censors
it near its floor (step 5.3), but a peak that is often twice its share is
another species sitting at the position.

References: isotope masses and abundances, the IUPAC values as ``pyteomics``
and ``IsoSpecPy`` carry them (Meija et al., Pure Appl. Chem. 2016, 88, 293);
the fine-structure envelope, Lacki et al., Anal. Chem. 2017, 89, 3272
(IsoSpec); the isotope pattern as a filter on a formula, Kind & Fiehn, BMC
Bioinformatics 2007, 8, 105 (rule 4); the scaled median absolute deviation,
Rousseeuw & Croux, J. Am. Stat. Assoc. 1993, 88, 1273.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from mascope_tools.composition.diagnostic_isotopologues import (
    DIAGNOSTIC_ISOTOPES,
    DiagnosticIsotopologue,
    predict_diagnostic_isotopologues,
)


#: The lines a merged row is read against: the 13C line, then the diagnostic
#: ones of step 4.2c, as the envelope predictor labels them.
MERGED_ROW_ISOTOPES = ("13C", *DIAGNOSTIC_ISOTOPES)

#: The fewest spectra holding both peaks a share is read over: the brief's
#: three, a gate set's six samples reading it in most of them.
MIN_SPECTRA = 3

#: The band the median factor (observed share over predicted) must fall in:
#: the reference engine's (its 0.10 line; step 4.2f's brief). It reaches
#: further below one than above: an Orbitrap scan keeps a dim line only where
#: it clears that scan's floor, so near the floor the summed list reads a line
#: at about 0.3 of its share, while mid signal-to-noise lines were measured at
#: most 7 to 13 % high (step 5.3).
MEDIAN_LOW = 0.4
MEDIAN_HIGH = 1.4

#: A spectrum whose factor is above this reads the position as more than the
#: line, and fewer than :data:`MAX_HIGH_SHARE` of the spectra may.
HIGH_FACTOR = 2.0
MAX_HIGH_SHARE = 0.1

#: The position window: :data:`WINDOW_SIGMAS` times the batch's mass scatter,
#: never under :data:`WINDOW_FLOOR_PPM`.
WINDOW_FLOOR_PPM = 1.0
WINDOW_SIGMAS = 4.0

#: The fewest members a row needs for its spread to count in the batch's mass
#: scatter: the median of fewer is mostly one of them.
SCATTER_MIN_MEMBERS = 5

#: The median absolute deviation of a normal sample times this is its standard
#: deviation (1 / Phi^-1(3/4)).
MAD_TO_SIGMA = 1.4826

#: A share's verdicts over the batch.
VERDICT_ISOTOPOLOGUE = "isotopologue"
VERDICT_OFF_RATIO = "off_ratio"
VERDICT_UNTESTABLE = "untestable"


def predict_isotopologue_lines(
    ion_formula: str, resolution: float | None = None
) -> tuple[DiagnosticIsotopologue, ...]:
    """A parent ion's 13C line and diagnostic isotope lines, in
    :data:`MERGED_ROW_ISOTOPES` order.

    :param ion_formula: The ion's formula with its charge.
    :param resolution: The resolving power (m / FWHM) the lines are read at,
        or None, which merges nothing.
    :return: One line per isotope the ion holds a natural atom of.
    """
    return predict_diagnostic_isotopologues(
        ion_formula, resolution, isotopes=MERGED_ROW_ISOTOPES
    )


def mass_scatter_ppm(traces: Iterable[Sequence[float]]) -> float | None:
    """The batch's mass scatter: the scaled median absolute deviation of its
    members' m/z around their rows' centres, in ppm.

    Each row's centre is its members' median m/z, and the deviations of every
    row with :data:`SCATTER_MIN_MEMBERS` or more members are pooled, so a
    bright row of many spectra and a dim one of five count member for member.

    :param traces: Each row's members' m/z.
    :return: The scatter, or None where no row has enough members.
    """
    deviations = []
    for mzs in traces:
        values = [float(mz) for mz in mzs if mz is not None and math.isfinite(mz)]
        if len(values) < SCATTER_MIN_MEMBERS:
            continue
        centre = statistics.median(values)
        if centre <= 0:
            continue
        deviations.extend(abs(mz - centre) / centre * 1e6 for mz in values)
    if not deviations:
        return None
    return MAD_TO_SIGMA * statistics.median(deviations)


def position_window_ppm(scatter_ppm: float | None) -> float:
    """How far, in ppm, a row may sit from a line's predicted m/z and still
    be read as at it: :data:`WINDOW_SIGMAS` times the batch's mass scatter,
    and at least :data:`WINDOW_FLOOR_PPM`.

    :param scatter_ppm: :func:`mass_scatter_ppm`, or None where the batch has
        none.
    """
    if scatter_ppm is None or not math.isfinite(scatter_ppm) or scatter_ppm < 0:
        return WINDOW_FLOOR_PPM
    return max(WINDOW_FLOOR_PPM, WINDOW_SIGMAS * scatter_ppm)


def at_position(mz: float, line_mz: float, window_ppm: float) -> bool:
    """Whether ``mz`` sits within ``window_ppm`` of a line at ``line_mz``."""
    return line_mz > 0 and abs(mz - line_mz) / line_mz * 1e6 <= window_ppm


def brighter(
    parent_intensity: float | None, row_intensity: float | None, share: float
) -> bool:
    """Whether a parent is brighter than a row it might own as a line.

    A line is dimmer than its parent unless its share says otherwise: a
    bromine's 81Br line is 97% of its parent and a dibromide's 195%, and read
    up to :data:`MEDIAN_HIGH` of that it can outshine the parent. So the
    parent is compared at the brightest its line could read.

    :param parent_intensity: The parent's brightest member.
    :param row_intensity: The row's brightest member.
    :param share: The line's predicted share of the parent.
    """
    if parent_intensity is None or row_intensity is None:
        return False
    return parent_intensity * max(1.0, share * MEDIAN_HIGH) > row_intensity


@dataclass(frozen=True)
class ShareReading:
    """A row's share of a parent, read over the spectra holding both.

    :ivar spectra: The spectra read: both peaks present, both intensities
        positive.
    :ivar median_factor: The median of the observed share over the predicted
        one; None where no spectrum was read.
    :ivar above: The spectra whose factor is above :data:`HIGH_FACTOR`.
    :ivar verdict: :data:`VERDICT_ISOTOPOLOGUE`, :data:`VERDICT_OFF_RATIO`
        or :data:`VERDICT_UNTESTABLE`.
    """

    spectra: int
    median_factor: float | None
    above: int
    verdict: str

    @property
    def is_isotopologue(self) -> bool:
        return self.verdict == VERDICT_ISOTOPOLOGUE

    @property
    def closeness(self) -> float:
        """How far the median factor is from one, in log: what ranks two
        parents a row reads as the line of; infinite where none was read."""
        if not self.median_factor or self.median_factor <= 0:
            return math.inf
        return abs(math.log(self.median_factor))

    def to_json(self) -> dict:
        return {
            "spectra": self.spectra,
            "median_factor": (
                None if self.median_factor is None else round(self.median_factor, 4)
            ),
            "above": self.above,
            "verdict": self.verdict,
        }


def read_share(ratios: Iterable[float], expected: float) -> ShareReading:
    """Whether a row holds a line's predicted share of its parent over a batch.

    :param ratios: Per spectrum holding both, the row's intensity over the
        parent's. A ratio that is not finite and positive is left out.
    :param expected: The line's predicted share
        (:attr:`DiagnosticIsotopologue.ratio`).
    :return: The reading.
    """
    if not expected > 0:
        return ShareReading(0, None, 0, VERDICT_UNTESTABLE)
    factors = [
        float(ratio) / expected
        for ratio in ratios
        if ratio is not None and math.isfinite(ratio) and ratio > 0
    ]
    if not factors:
        return ShareReading(0, None, 0, VERDICT_UNTESTABLE)
    median = statistics.median(factors)
    above = sum(1 for factor in factors if factor > HIGH_FACTOR)
    if len(factors) < MIN_SPECTRA:
        return ShareReading(len(factors), median, above, VERDICT_UNTESTABLE)
    holds = MEDIAN_LOW <= median <= MEDIAN_HIGH and above < MAX_HIGH_SHARE * len(
        factors
    )
    return ShareReading(
        len(factors),
        median,
        above,
        VERDICT_ISOTOPOLOGUE if holds else VERDICT_OFF_RATIO,
    )


def thresholds() -> dict:
    """The constants a reading was made under, as a run records them."""
    return {
        "isotopes": list(MERGED_ROW_ISOTOPES),
        "min_spectra": MIN_SPECTRA,
        "median_low": MEDIAN_LOW,
        "median_high": MEDIAN_HIGH,
        "high_factor": HIGH_FACTOR,
        "max_high_share": MAX_HIGH_SHARE,
        "window_floor_ppm": WINDOW_FLOOR_PPM,
        "window_sigmas": WINDOW_SIGMAS,
    }


__all__ = [
    "HIGH_FACTOR",
    "MAD_TO_SIGMA",
    "MAX_HIGH_SHARE",
    "MEDIAN_HIGH",
    "MEDIAN_LOW",
    "MERGED_ROW_ISOTOPES",
    "MIN_SPECTRA",
    "SCATTER_MIN_MEMBERS",
    "VERDICT_ISOTOPOLOGUE",
    "VERDICT_OFF_RATIO",
    "VERDICT_UNTESTABLE",
    "WINDOW_FLOOR_PPM",
    "WINDOW_SIGMAS",
    "ShareReading",
    "at_position",
    "brighter",
    "mass_scatter_ppm",
    "position_window_ppm",
    "predict_isotopologue_lines",
    "read_share",
    "thresholds",
]
