"""What the spectrum holds where a row's predicted line should be (step 3.4e).

The absent-line rule (``tiering.absent_line_reason``) asks each committed
monoisotopic row's envelope of the sample's peaks, line by line, at the gate
the run records (:class:`LineGate`). Each line it asks has one of three answers
(:func:`read_line`):

* **found**: a listed peak is the line. One inside the run's match window about
  the line is, as the match itself reads it. Where the file's peaks are wider
  than that window, as a TOF's are, the window is half the peak width the
  file's resolving power gives at the line's m/z, and a peak in the wider part
  is the line where its height is one the line can have: the nearest such
  peak, the line model's matcher (``line_model.nearest_peak``) under its height
  bound (``ISOTOPE_MATCHING_INTENSITY_TOLERANCE``).
* **untestable**, the line cannot be read: a peak taller than the line can be
  sits within one peak width of it, so the line could not stand apart from it
  (:data:`UNTESTABLE_NEIGHBOUR`); only an artifact row's peak sits in its window
  (:data:`UNTESTABLE_ARTIFACT`); or it lies beyond the peak list's m/z span
  (:data:`UNTESTABLE_RANGE`). None of the three is evidence against the
  reading.
* **absent**: none of these. Only an absent line refutes the reading.

A line is asked at all only where the file's peak list could hold it
(:func:`list_floor`): where its predicted height, its share times the row's
own, is at or above the smallest height the list keeps within
:data:`LIST_FLOOR_SPAN_MZ` of it. A TOF's peak list keeps nothing under a floor
relative to the tallest peak near it (``TofPeakDetector``: at most five peaks
per unit mass, and no further peak once one cuts the residual by under 10 %),
so a line predicted under that floor is one the list could not have held
whatever the spectrum shows.

Measured on the bench's three TOF nitrate sets (MAS-996, 180 refuted rows drawn
with seed 996): 110 of the "absent" 13C lines had a listed peak 3.5 to 63 times
the predicted height within half a peak width, 43 a peak of about the line's
height 20 to 200 ppm off, outside the 15 ppm window but within half a width,
and 12 no listed peak where the spectrum held the line under the list's floor.
14 showed nothing in the spectrum.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from mascope_backend.api.new.peak_assignments.mass_gate import SpectrumLines
from mascope_tools.composition.config import ISOTOPE_MATCHING_INTENSITY_TOLERANCE
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance
from mascope_tools.composition.line_model import REL_DETECT_NO_SNR, height_within
from mascope_tools.composition.line_model import (
    detect_threshold as line_detect_threshold,
)


#: What the spectrum held at a predicted line: a peak that is not an
#: artifact's, nothing at all, or nothing it can be read on.
LINE_FOUND = "found"
LINE_ABSENT = "absent"
LINE_UNTESTABLE = "untestable"

#: Why a line is untestable: only artifact rows' peaks sit in its window, it
#: lies beyond the peak list's m/z span, or a peak taller than the line can be
#: sits within one peak width of it.
UNTESTABLE_ARTIFACT = "artifact"
UNTESTABLE_RANGE = "out_of_range"
UNTESTABLE_NEIGHBOUR = "neighbour"

#: Where the detectability threshold came from: the file's own response curve
#: (step 3.5b), or the fit's default.
DETECT_FROM_CURVE = "file_curve"
DETECT_DEFAULT = "default"

#: How far either side of a line, in m/z, the peak list's floor is read
#: (:func:`list_floor`). Five, the span MAS-996 read the TOF lists' smallest
#: peaks over: there the smallest listed height near an absent 13C line was 0.6
#: to 21 times its predicted height, and in two of the three sets 85 to 95 % of
#: the asked lines were predicted under it. Wide enough to hold several unit
#: masses' peaks, so one crowded mass does not set it, and narrow enough to
#: follow the list's floor as it rises with the signal along the axis.
LIST_FLOOR_SPAN_MZ = 5.0

#: The tallest a listed peak may be, as a factor of a line's predicted height,
#: and still be read as the line: the line model's height bound, the upper end
#: of its matcher's window (``line_model.within_intensity_window``).
LINE_HEIGHT_AT_MOST = 1.0 + ISOTOPE_MATCHING_INTENSITY_TOLERANCE


def detect_threshold(tolerance: EnvelopeTolerance | None) -> tuple[float, str]:
    """The expected signal-to-noise from which an absent line counts.

    The fit's own: the threshold the file's response curve set where step 3.5b
    fitted one that sets it, else ``line_model.DETECT_SNR_K``, as
    ``score_pattern_v2`` reads it (``line_model.detect_threshold``).

    :param tolerance: The envelope tolerance the run scored at, or None.
    :return: The threshold, and :data:`DETECT_FROM_CURVE` or
        :data:`DETECT_DEFAULT` for where it came from.
    """
    threshold = line_detect_threshold(tolerance)
    if tolerance is not None and tolerance.detect_snr is not None:
        return threshold, DETECT_FROM_CURVE
    return threshold, DETECT_DEFAULT


@dataclass(frozen=True)
class LineGate:
    """What the absent-line rule asks a row's lines at.

    A run's: its match window and envelope floor, the detectability threshold
    its tolerance sets (:func:`detect_threshold`), the share it asks lines from
    on a peak with no signal-to-noise, and its instrument class; whether it
    reads a line's window and neighbours at the file's peak width
    (``width_window``), and the span its peak list's floor is read over
    (``floor_span_mz``, None for no floor). A run of rule set 16 or earlier
    recorded neither, and asked at its match window alone. Whether a bromine
    ion's own 81Br line is asked whatever its expected signal-to-noise
    (``bromine_m2``): rule set 18 on, on every instrument.
    The run records it with the rule set (:meth:`record`), and a hand
    assignment on the run is asked at what the run read back
    (:meth:`from_record`), so the row a person commits and the rows the run
    committed are held to one gate.
    """

    mz_tolerance_ppm: float
    abundance_floor: float
    detect_snr: float
    detect_from: str
    rel_detect_no_snr: float
    tof: bool
    width_window: bool = False
    floor_span_mz: float | None = None
    bromine_m2: bool = False

    def record(self) -> dict:
        """The gate as the run's tiering record carries it."""
        return {
            "mz_tolerance_ppm": self.mz_tolerance_ppm,
            "abundance_floor": self.abundance_floor,
            "detect_snr": self.detect_snr,
            "detect_from": self.detect_from,
            "rel_detect_no_snr": self.rel_detect_no_snr,
            "tof_m2": self.tof,
            "width_window": self.width_window,
            "floor_span_mz": self.floor_span_mz,
            "bromine_m2": self.bromine_m2,
        }

    @classmethod
    def at(
        cls,
        *,
        mz_tolerance_ppm: float,
        abundance_floor: float,
        envelope_tolerance: EnvelopeTolerance | None,
        instrument_type: str | None,
    ) -> LineGate:
        """The gate a run asks its rows' lines at, from what it measures with.

        :param mz_tolerance_ppm: The match window it scores at.
        :param abundance_floor: The envelope floor it scores at.
        :param envelope_tolerance: The line-share tolerance it scores at, whose
            detectability threshold the rule asks lines from; None for the
            fit's default.
        :param instrument_type: Its instrument class.
        """
        detect_snr, detect_from = detect_threshold(envelope_tolerance)
        return cls(
            mz_tolerance_ppm=float(mz_tolerance_ppm),
            abundance_floor=float(abundance_floor),
            detect_snr=detect_snr,
            detect_from=detect_from,
            rel_detect_no_snr=REL_DETECT_NO_SNR,
            tof=instrument_type == "tof",
            width_window=True,
            floor_span_mz=LIST_FLOOR_SPAN_MZ,
            bromine_m2=True,
        )

    @classmethod
    def from_record(cls, tiering: Any) -> LineGate | None:
        """The gate a run asked its rows' lines at, read off its tiering record.

        :param tiering: The run's ``config.tiering``.
        :return: The gate; None where the run asked no line: one tiered before
            rule set 11, one that read no spectrum, or a record that does not
            say what it asked at.
        """
        block = tiering.get("absent_line") if isinstance(tiering, dict) else None
        if not isinstance(block, dict) or block.get("spectrum") is not True:
            return None
        numbers = [
            block.get(key)
            for key in (
                "mz_tolerance_ppm",
                "abundance_floor",
                "detect_snr",
                "rel_detect_no_snr",
            )
        ]
        if not all(_positive(value) for value in numbers):
            return None
        span = block.get("floor_span_mz")
        return cls(
            mz_tolerance_ppm=float(numbers[0]),
            abundance_floor=float(numbers[1]),
            detect_snr=float(numbers[2]),
            detect_from=(
                DETECT_FROM_CURVE
                if block.get("detect_from") == DETECT_FROM_CURVE
                else DETECT_DEFAULT
            ),
            rel_detect_no_snr=float(numbers[3]),
            tof=block.get("tof_m2") is True,
            width_window=block.get("width_window") is True,
            floor_span_mz=float(span) if _positive(span) else None,
            bromine_m2=block.get("bromine_m2") is True,
        )


def _positive(value: Any) -> bool:
    """Whether a recorded value is a finite positive number."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def list_floor(
    spectrum: SpectrumLines, line_mz: float, span_mz: float, own_peak: str
) -> float | None:
    """The smallest height the peak list keeps near a line.

    The least positive intensity of the listed peaks within ``span_mz`` of the
    line, the row's own peak aside; None where no other peak is listed there,
    which says nothing of what the list could hold. Read off the heights alone,
    not their signal-to-noise.
    """
    heights = [
        height
        for peak_id, _, height in spectrum.around(line_mz, span_mz)
        if peak_id != own_peak and height > 0
    ]
    return min(heights) if heights else None


def under_list_floor(
    spectrum: SpectrumLines,
    gate: LineGate,
    line_mz: float,
    height: float,
    own_peak: str,
) -> bool:
    """Whether a line's predicted height is under what the peak list keeps
    near it (:func:`list_floor`), so the list could not have held it.

    False where the gate reads no floor, where the row's own height is not
    positive, and where no other peak is listed near the line.
    """
    if gate.floor_span_mz is None or height <= 0:
        return False
    floor = list_floor(spectrum, line_mz, gate.floor_span_mz, own_peak)
    return floor is not None and height < floor


def read_line(
    spectrum: SpectrumLines,
    line_mz: float,
    *,
    share: float,
    shift_ppm: float,
    tolerance_ppm: float,
    own_peak: str,
    own_intensity: float,
    artifact_peak_ids: frozenset[str],
    width_window: bool = False,
) -> dict:
    """What the spectrum holds where a predicted line should be.

    The window is about the line, and about the line moved by the row's own
    mass error, since two lines of one ion are one measurement of one axis: a
    peak in either is read. A peak no artifact row holds inside the run's match
    window is the line, the tallest of them as the probe. Where the run reads
    the file's peak width (``width_window``) and half of it is wider than the
    match window, the window is that half width, and a peak in the wider part
    is the line where its height is one the line can have (the module's
    docstring); then a peak taller than that within one peak width holds the
    place, and the line cannot be read. An artifact confirms no line and
    refutes none, so a window only artifact rows hold is untestable, as is an
    empty one beyond the peak list's m/z span: its first and last peaks are
    what the run takes as the ends of what the spectrum measured.
    """
    shifted = line_mz * (1.0 + shift_ppm * 1e-6)
    low, high = min(line_mz, shifted), max(line_mz, shifted)
    match = line_mz * tolerance_ppm * 1e-6
    fwhm_ppm = spectrum.fwhm_ppm(line_mz) if width_window else None
    width = 0.0 if fwhm_ppm is None else line_mz * fwhm_ppm * 1e-6
    window = max(match, width / 2.0)
    reach = max(window, width)
    middle = (low + high) / 2.0
    peaks = [
        (peak_id, mz, height, max(low - mz, mz - high, 0.0))
        for peak_id, mz, height in spectrum.around(middle, (high - low) / 2.0 + reach)
        if peak_id != own_peak
    ]
    held = [peak for peak in peaks if peak[0] not in artifact_peak_ids]
    predicted = share * own_intensity
    inside = [peak for peak in held if peak[3] <= match]
    line_high = [
        height_within(peak[2], predicted, at_most=LINE_HEIGHT_AT_MOST) for peak in held
    ]
    if not inside:
        # The nearest peak of the line's height, as the line model's matcher
        # takes the nearest peak in a line's window.
        fits = [peak for peak, fit in zip(held, line_high) if fit and peak[3] <= window]
        inside = [min(fits, key=lambda peak: peak[3])] if fits else []
    if inside:
        peak_id, _, intensity, _ = max(inside, key=lambda peak: peak[2])
        found: dict = {"state": LINE_FOUND, "peak_id": peak_id}
        if own_intensity > 0:
            found["observed_share"] = round(intensity / own_intensity, 6)
        return found
    taller = [peak for peak, fit in zip(held, line_high) if not fit]
    if taller and width > 0:
        # The tallest within one width is the one holding the place.
        peak_id, _, height, distance = max(taller, key=lambda peak: peak[2])
        return {
            "state": LINE_UNTESTABLE,
            "untestable": UNTESTABLE_NEIGHBOUR,
            "peak_id": peak_id,
            "widths": round(distance / width, 3),
            "height_ratio": round(height / predicted, 2) if predicted > 0 else None,
        }
    if any(peak[3] <= window for peak in peaks):
        return {"state": LINE_UNTESTABLE, "untestable": UNTESTABLE_ARTIFACT}
    span = spectrum.span()
    if span is None or not span[0] <= line_mz <= span[1]:
        return {"state": LINE_UNTESTABLE, "untestable": UNTESTABLE_RANGE}
    return {"state": LINE_ABSENT}


def unread_line_detail(line: dict) -> str:
    """Why a line of a row's record could not be read, as a reason names it."""
    said = (
        f"its {line['line']} line, predicted at m/z "
        f"{float(line['predicted_mz']):.4f} at "
        f"{float(line['predicted_share']):.1%} of its own line, could not be read"
    )
    why = line.get("untestable")
    if why == UNTESTABLE_NEIGHBOUR:
        ratio = line.get("height_ratio")
        taller = (
            "a taller peak"
            if ratio is None
            else f"a peak {ratio:g} times its predicted height"
        )
        return (
            f"{said}: {taller} sits "
            f"{float(line.get('widths') or 0.0):.2f} peak widths from it, so it "
            "cannot stand apart"
        )
    if why == UNTESTABLE_RANGE:
        return f"{said}: it lies beyond the peak list's m/z span"
    return f"{said}: only an artifact's peak sits where it should be"


__all__ = [
    "DETECT_DEFAULT",
    "DETECT_FROM_CURVE",
    "LINE_ABSENT",
    "LINE_FOUND",
    "LINE_HEIGHT_AT_MOST",
    "LINE_UNTESTABLE",
    "LIST_FLOOR_SPAN_MZ",
    "LineGate",
    "UNTESTABLE_ARTIFACT",
    "UNTESTABLE_NEIGHBOUR",
    "UNTESTABLE_RANGE",
    "detect_threshold",
    "list_floor",
    "read_line",
    "under_list_floor",
    "unread_line_detail",
]
