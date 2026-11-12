"""An ion's diagnostic isotopologues, and whether a batch's lines hold them.

The 15N, 18O, 34S, 29Si, 30Si, 37Cl and 81Br lines of an ion say which of
those elements it holds and how many: each is one atom of the element in its
heavier isotope, at a mass defect no carbon line shares, at a height that
rises with the element's count. Most of them are dim - one nitrogen's 15N line
is 0.37% of its parent, one oxygen's 18O line 0.21% - so in most files of a
batch they sit below the peak picker's edge and stand only where a plume lifts
the parent; a per-file fold, which reads one file, never sees them. Across a
batch they can be read where they stand: a true isotope line holds its
predicted height ratio to the parent in nearly every spectrum that shows both,
an independent compound that happens to sit at the position in few. This
module is the pure half of the second rider of step 4.2 of the assignment plan
(``docs/dev/assignment_quality_plan.md``, the reference engine's #45): what
the isotope lines are, how one spectrum is judged, and how the track is.

The isotope lines
-----------------

:func:`predict_diagnostic_isotopologues` predicts an ion's envelope
(``heuristic_filter.predict_isotopes``, to :data:`PREDICTION_DEPTH` of its
brightest line) and reads off each diagnostic isotopologue: one atom of the
element in its heavy isotope, every other atom at its most abundant. Its
height ratio to the monoisotopic line is ``n * a_heavy / a_light`` for ``n``
atoms of the element. A labelled reagent's atom (``^N``) is the label's, not a
natural atom of its element: it is already 15N, so it adds no 15N line, and
the monoisotopic line the offsets are measured from is the labelled line.

An isotope line is judged only where a peak of the file's width shows it on its
own. At the anchor's resolving power the envelope's lines one peak cannot
separate are merged as the fit score merges them
(``heuristic_filter.group_unresolved_lines``): on a TOF the 15N line sits
inside the 13C peak, on an Orbitrap at a low resolution the 18O line of a
large ion inside its 13C2 peak. An isotope line whose own share is under
:data:`MIN_DOMINANCE` of the peak it falls in is *unresolved*: the peak there
is another line's, and its height says little of the element. A resolved one
is read at the merged peak's centre and share, which is what the peak measures.

One spectrum
------------

:func:`judge_ratio` reads an isotope line's height over its parent's against the
predicted ratio as the fit score reads an isotope line's share
(``heuristic_filter.score_pattern_v2``): inside the band the run's fitted
envelope tolerance admits at the line's expected S/N (step 3.5b,
``envelope_tolerance.EnvelopeTolerance.admitted``) it is the predicted share;
outside, the distance to the band's nearer end is read in the line's width -
its own and its parent's noise, the tolerance's floor of the share, and the
score's absolute floor of :data:`SHARE_WIDTH_FLOOR`, whichever is widest. The
ratio holds within :data:`HOLD_WIDTHS` of those widths.

A spectrum tells an isotope line from another peak only where that reach is
narrow against the ratio itself. A 15N line of 0.4% of its parent, read at an
S/N of five, holds anything from nothing to twice its share, so a peak of any
compound that happens to sit there holds it too. A spectrum is *informative*
where :data:`HOLD_WIDTHS` widths at the predicted ratio reach no further than
:data:`MAX_SPAN` of it; the others are left out of the track.

The track
---------

:func:`read_track` counts the informative spectra (both lines present) and
the ones whose ratio holds. An isotope line is *stamped* where at least
:data:`MIN_JUDGED` spectra were informative and the share holding reaches
:data:`TRACK_SHARE`; fewer say too little either way (*untestable*). The
numbers are measured on this project's own batches; the constants say how.

Nothing here moves a tier: the stamps are facts that later steps (4.2g's
batch isotope checks, the tiers) count.

References: isotope masses and abundances, the IUPAC values as ``pyteomics``
and ``IsoSpecPy`` carry them (Meija et al., Pure Appl. Chem. 2016, 88, 293);
the fine-structure envelope, Lacki et al., Anal. Chem. 2017, 89, 3272
(IsoSpec); diagnostic heavy-isotope lines for element counts, Kind & Fiehn,
BMC Bioinformatics 2007, 8, 105 (rule 4, the isotopic pattern).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_TOLERANCE,
    EnvelopeTolerance,
)
from mascope_tools.composition.heuristic_filter import (
    group_unresolved_lines,
    predict_isotopes,
)


#: The isotope lines, as the envelope predictor labels a line of one heavy atom
#: of the element, lightest element first and then by nominal offset.
DIAGNOSTIC_ISOTOPES = ("15N", "18O", "34S", "29Si", "30Si", "37Cl", "81Br")

#: How deep the envelope is predicted, relative to its brightest line: deep
#: enough to hold one nitrogen's 15N line (0.37% of its parent) under a
#: tribromide's brightest line, three times its monoisotopic one.
PREDICTION_DEPTH = 1e-5

#: The least share of the peak it falls in an isotope line must carry to be read
#: on its own. Under half, the peak is another line's.
MIN_DOMINANCE = 0.5

#: The fit score's absolute floor of a line's width, as a share of the parent
#: (``score_pattern_v2``'s ``1e-3``): a dim line is never judged tighter.
SHARE_WIDTH_FLOOR = 1e-3

#: How many of its widths a ratio may sit outside the admitted band and still
#: hold: the band a normal reading of the share stays in 95% of the time.
HOLD_WIDTHS = 2.0

#: How far, as a share of the predicted ratio, :data:`HOLD_WIDTHS` widths may
#: reach for a spectrum to be informative: a reading that cannot tell the
#: ratio from half or one and a half of it says nothing of the isotope line.
#: Measured with :data:`TRACK_SHARE`.
MAX_SPAN = 0.5

#: The informative spectra a track is read over at the fewest. Measured with
#: :data:`TRACK_SHARE`: under the span, a track of three or four spectra is
#: already as clean as a long one, and a gate set has six.
MIN_JUDGED = 3

#: The share of informative spectra whose ratio must hold for an isotope line
#: to be stamped. Measured on the testbed's 20 batch ledgers (2026-11-11), at a
#: 3.5b response curve fitted per file on the batch's own 13C lines, each
#: predicted isotope line beside a decoy: the same parent, ratio and offset
#: taken below the parent (``M - delta``), where no line of its envelope is. Of
#: the isotope lines the files' own runs had found, 88% of tracks of three to
#: nine spectra and 78% of longer ones reach 0.8; of the decoys 0.9%, 6.7% and
#: 0.5%, every decoy of the middle band but one a bromine one, whose ratio near
#: one makes the mirror of a real 79Br/81Br pair look like a pair. Without the
#: span, decoys over three to nine spectra reach it in half the cases. The
#: numbers are in the design note's as-built block for the rider.
TRACK_SHARE = 0.8

#: An isotope line's verdicts over the track.
VERDICT_STAMPED = "stamped"
VERDICT_NOT_STAMPED = "not_stamped"
VERDICT_UNTESTABLE = "untestable"

#: Why a predicted isotope line was not judged at all.
REASON_UNRESOLVED = "unresolved"
REASON_FEW_SPECTRA = "few_spectra"

#: An ion formula's charge: its sign once per charge, at the end
#: (``target_ions_compute.charge_string``), since a count before the sign
#: would read as the last element's.
_CHARGE = re.compile(r"(\++|-+)$")


def ion_charge(ion_formula: str) -> int | None:
    """The charge an ion formula ends in (``C6H13O6+``, ``C2HO4-``,
    ``C10H22N2O4++``); None for a formula without one."""
    if not isinstance(ion_formula, str):
        return None
    found = _CHARGE.search(ion_formula.strip())
    if found is None:
        return None
    signs = found.group(1)
    return len(signs) if signs[0] == "+" else -len(signs)


def _neutral_part(ion_formula: str) -> str:
    """The formula of an ion without its charge."""
    return _CHARGE.sub("", ion_formula.strip()).strip()


@dataclass(frozen=True)
class DiagnosticIsotopologue:
    """One diagnostic isotopologue of an ion.

    :ivar label: The isotope (``81Br``).
    :ivar mz_offset: Its m/z above the ion's monoisotopic line: the centre of
        the peak it falls in, less the monoisotopic peak's.
    :ivar ratio: The predicted height of that peak over the monoisotopic one.
    :ivar own_ratio: The isotope line's own share of the monoisotopic line, the
        lines it merges with left out.
    :ivar resolved: Whether a peak of the file's width shows it on its own
        (:data:`MIN_DOMINANCE`); an unresolved isotope line is not judged.
    """

    label: str
    mz_offset: float
    ratio: float
    own_ratio: float
    resolved: bool

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "mz_offset": round(self.mz_offset, 6),
            "ratio": round(self.ratio, 6),
            "own_ratio": round(self.own_ratio, 6),
            "resolved": self.resolved,
        }


def predict_diagnostic_isotopologues(
    ion_formula: str, resolution: float | None = None
) -> tuple[DiagnosticIsotopologue, ...]:
    """An ion's diagnostic isotopologues, in :data:`DIAGNOSTIC_ISOTOPES` order.

    :param ion_formula: The ion's formula with its charge (``C10H18O7^N-``);
        a labelled reagent's atom is the label's.
    :param resolution: The resolving power (m / FWHM) of the file the lines
        are read in, or None, which merges nothing: one peak per isotopologue,
        as the fit score predicts where a run could read no resolution.
    :return: One :class:`DiagnosticIsotopologue` per diagnostic isotope the ion
    holds a natural atom of; none for a formula with no charge or no
    prediction.
    """
    charge = ion_charge(ion_formula)
    if not charge:
        return ()
    try:
        mzs, intensities, labels = predict_isotopes(
            _neutral_part(ion_formula), charge, None, PREDICTION_DEPTH
        )
    except Exception:  # noqa: BLE001 - a formula the predictor refuses has none
        return ()
    mzs = np.asarray(mzs, dtype=float)
    intensities = np.asarray(intensities, dtype=float)
    labels = list(labels)
    if "M0" not in labels:
        return ()
    groups = _groups(mzs, intensities, resolution)
    group_of = {index: members for members in groups for index in members}
    mono = labels.index("M0")
    mono_group = group_of[mono]
    mono_share = float(intensities[mono_group].sum())
    mono_centre = _centre(mzs, intensities, mono_group)
    isotopologues = []
    for label in DIAGNOSTIC_ISOTOPES:
        if label not in labels:
            continue
        index = labels.index(label)
        members = group_of[index]
        share = float(intensities[members].sum())
        isotopologues.append(
            DiagnosticIsotopologue(
                label=label,
                mz_offset=_centre(mzs, intensities, members) - mono_centre,
                ratio=share / mono_share,
                own_ratio=float(intensities[index]) / float(intensities[mono]),
                resolved=float(intensities[index]) >= MIN_DOMINANCE * share,
            )
        )
    return tuple(isotopologues)


def _groups(
    mzs: np.ndarray, intensities: np.ndarray, resolution: float | None
) -> list[list[int]]:
    """The lines one peak of the file's width holds, as index lists."""
    if resolution is None or not resolution > 0 or mzs.size < 2:
        return [[index] for index in range(mzs.size)]
    width: Callable[[float], float] = lambda _mz: float(resolution)  # noqa: E731
    return [
        list(map(int, members))
        for members in group_unresolved_lines(mzs, intensities, width)
    ]


def _centre(mzs: np.ndarray, intensities: np.ndarray, members: list[int]) -> float:
    weights = intensities[members]
    total = float(weights.sum())
    if total <= 0:
        return float(mzs[members].mean())
    return float(np.sum(mzs[members] * weights) / total)


@dataclass(frozen=True)
class RatioReading:
    """One spectrum's reading of an isotope line against its parent.

    :ivar observed: The isotope line's height over the parent's.
    :ivar expected: The predicted ratio.
    :ivar deviation: How far the observed ratio sits outside the admitted
        band, in the line's widths; 0 inside it, signed.
    :ivar informative: Whether :data:`HOLD_WIDTHS` widths at the predicted
        ratio reach no further than :data:`MAX_SPAN` of it.
    :ivar holds: Whether the deviation is within :data:`HOLD_WIDTHS`.
    """

    observed: float
    expected: float
    deviation: float
    informative: bool
    holds: bool


def _usable(snr: float | None) -> float | None:
    """A signal-to-noise the width can read: finite and positive, else None,
    which widens nothing (as the fit score reads an unknown one)."""
    if snr is None:
        return None
    value = float(snr)
    return value if math.isfinite(value) and value > 0 else None


def _width(
    ratio: float, expected: float, tolerance: EnvelopeTolerance, inverse_snr: float
) -> float:
    """A line's width at a ratio: its noise, the tolerance's floor of the
    predicted share, or the score's absolute floor, whichever is widest."""
    return max(ratio * inverse_snr, tolerance.floor * expected, SHARE_WIDTH_FLOOR)


def judge_ratio(
    observed: float,
    expected: float,
    *,
    tolerance: EnvelopeTolerance = DEFAULT_TOLERANCE,
    parent_snr: float | None = None,
    line_snr: float | None = None,
) -> RatioReading:
    """Whether a line's height ratio to its parent holds in one spectrum.

    :param observed: The isotope line's height (or area) over the parent's.
    :param expected: Its predicted ratio (:attr:`DiagnosticIsotopologue.ratio`).
    :param tolerance: The envelope tolerance the spectrum's run fitted; today's
        where it fitted none.
    :param parent_snr: The parent peak's signal-to-noise, or None.
    :param line_snr: The line's peak's signal-to-noise, or None.
    :return: The reading.
    """
    parent = _usable(parent_snr)
    own = _usable(line_snr)
    expected_snr = parent * expected if parent is not None else math.nan
    lower, upper = tolerance.admitted(np.array([expected_snr]))
    low, high = float(lower[0]) * expected, float(upper[0]) * expected
    if observed < low:
        outside = observed - low
    elif observed > high:
        outside = observed - high
    else:
        outside = 0.0
    inverse = math.hypot(1.0 / own if own else 0.0, 1.0 / parent if parent else 0.0)
    deviation = outside / _width(observed, expected, tolerance, inverse)
    reach = HOLD_WIDTHS * _width(expected, expected, tolerance, inverse)
    return RatioReading(
        observed=float(observed),
        expected=float(expected),
        deviation=float(deviation),
        informative=reach <= MAX_SPAN * expected,
        holds=abs(deviation) <= HOLD_WIDTHS,
    )


@dataclass(frozen=True)
class TrackReading:
    """An isotope line read over the batch.

    :ivar judged: The informative spectra holding both lines.
    :ivar holds: Those whose ratio holds.
    :ivar uninformative: The spectra holding both lines that could not tell
        (:attr:`RatioReading.informative`).
    :ivar verdict: :data:`VERDICT_STAMPED`, :data:`VERDICT_NOT_STAMPED` or
        :data:`VERDICT_UNTESTABLE`.
    :ivar reason: Why it is untestable, else None.
    """

    judged: int
    holds: int
    uninformative: int
    verdict: str
    reason: str | None = None

    @property
    def share(self) -> float | None:
        """The share of informative spectra whose ratio holds; None on none."""
        return self.holds / self.judged if self.judged else None

    def to_json(self) -> dict:
        share = self.share
        return {
            "judged": self.judged,
            "holds": self.holds,
            "uninformative": self.uninformative,
            "share": None if share is None else round(share, 4),
            "verdict": self.verdict,
            "reason": self.reason,
        }


def stamps_in(reading: RatioReading) -> bool:
    """Whether a spectrum's reading puts a stamp on the line there: it could
    tell, and the ratio held."""
    return reading.informative and reading.holds


def read_track(readings: Sequence[RatioReading]) -> TrackReading:
    """The track's verdict from each spectrum's reading.

    :param readings: One reading per spectrum holding both lines.
    """
    informative = [reading for reading in readings if reading.informative]
    judged = len(informative)
    held = sum(1 for reading in informative if reading.holds)
    left_out = len(readings) - judged
    if judged < MIN_JUDGED:
        return TrackReading(
            judged, held, left_out, VERDICT_UNTESTABLE, REASON_FEW_SPECTRA
        )
    verdict = VERDICT_STAMPED if held >= TRACK_SHARE * judged else VERDICT_NOT_STAMPED
    return TrackReading(judged, held, left_out, verdict)


__all__ = [
    "DIAGNOSTIC_ISOTOPES",
    "HOLD_WIDTHS",
    "MAX_SPAN",
    "MIN_DOMINANCE",
    "MIN_JUDGED",
    "PREDICTION_DEPTH",
    "REASON_FEW_SPECTRA",
    "REASON_UNRESOLVED",
    "SHARE_WIDTH_FLOOR",
    "TRACK_SHARE",
    "VERDICT_NOT_STAMPED",
    "VERDICT_STAMPED",
    "VERDICT_UNTESTABLE",
    "RatioReading",
    "DiagnosticIsotopologue",
    "TrackReading",
    "ion_charge",
    "judge_ratio",
    "predict_diagnostic_isotopologues",
    "read_track",
    "stamps_in",
]
