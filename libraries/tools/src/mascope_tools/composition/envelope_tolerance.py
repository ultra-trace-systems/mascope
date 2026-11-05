"""How well a run measures an isotope line's share, fitted from the run itself.

The fit score (``heuristic_filter.score_pattern_v2``) reads a matched isotope
line's height against the share its formula predicts, at a tolerance of 5% of
that share for a clean peak, and charges an absent line wherever its expected
signal-to-noise - the anchor's S/N times the line's share - is at least 3. Both
numbers are a claim about the instrument, and on an Orbitrap neither holds for
a dim line. An Orbitrap scan keeps a centroid only where it clears that scan's
floor, and the file's peak list averages the scans, so a minor line near the
floor is read diluted by the scans it missed, or not found at all. Measured on
the gate's committed 13C lines (design note, step 5.3): by height a median 0.72
to 0.92 of the share at an expected S/N of 3 to 10, 0.87 to 0.92 at 10 to 20,
0.97 from 50 up; and at 3 to 10, 62 to 89% of the predicted lines absent from
the list. The onset of the miss is the file's own: the one 100-microscan file of
a set misses lines its 10-microscan siblings keep.

So the run fits the tolerance on its own confidently committed envelopes
(decision 32), in the form the instrument class takes:

- **Orbitrap-class: a response curve per file.** The 13C lines' observed /
  predicted share, binned by expected S/N (:data:`RESPONSE_SNR_EDGES`); per bin
  the median and the 20th percentile, made non-decreasing in S/N and capped at
  1.0. A matched line scores as a full match anywhere between the curve's 20th
  percentile at its expected S/N and its natural share, and is charged from the
  nearer end at today's width outside that band. The same lines set where an
  absent line starts to count: from the lowest bin in which at least half the
  predicted 13C lines are in the list, where a bin below it falls short of
  that (:func:`fit_response_curve`).
- **TOF: a centre and a width per run.** The committed lines' relative error
  against their share; the score reads a line about the centre, at the width
  where that is wider than 5%. A TOF's S/N column did not track height until
  its peaks were detected again, so it is not read as a curve.

A file too thin for a curve of its own takes the curve fitted over its lines
and those of the files beside it that share its acquisition settings, then
today's numbers (:func:`fit_envelope_tolerance`). Every level is recorded
(:meth:`EnvelopeFit.record`), and the record rebuilds the tolerance
(:meth:`EnvelopeTolerance.from_record`), so a formula measured outside the run
is judged where the run judged it.

The one seam a later step needs is :meth:`EnvelopeTolerance.admitted`: it takes
a line's expected S/N and returns the band of observed / predicted share that
scores as a match, so an estimate that knows how many scans held the line can
stand in front of it without the score changing shape.

References: the reference engine's 0.10 line (its #76, 2026-10-10) reads the
same censoring; the response form is its own. Isotonic regression by pool
adjacent violators: Barlow, Bartholomew, Bremner & Brunk, *Statistical
inference under order restrictions* (Wiley, 1972).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np


#: The fit score's relative intensity floor today: a clean line's share is
#: judged at 5% of itself.
RELATIVE_FLOOR = 0.05

#: The expected S/N at which an absent line is charged, where nothing was
#: fitted. ``heuristic_filter.DETECT_SNR_K``; restated here because that module
#: imports this one, and pinned equal to it by a test.
DEFAULT_DETECT_SNR = 3.0

#: The lower edges of the response curve's bins of expected line S/N; the last
#: bin is open. The edges 5.3 measured on, from the detectability threshold up:
#: a line under 3 is in the list as good as never, and none is charged there.
RESPONSE_SNR_EDGES = (3.0, 10.0, 20.0, 50.0, 100.0)

#: The quantile of a bin's observed / predicted share the admitted band opens
#: down to (the reference's form, decision 32).
RESPONSE_QUANTILE = 0.2

#: The tracking 13C lines a curve is fitted on at the fewest. Committed lines
#: are thin on several gate sets - 34 over 7 files, 30 over 6, 52 over 5 - so a
#: file there takes the pooled curve, and twenty lines put four or five in each
#: of the bins where most of them fall.
MIN_RESPONSE_LINES = 20

#: The lines a bin needs for its median and 20th percentile to be read, and the
#: predicted lines it needs for its presence to be judged.
MIN_BIN_LINES = 5

#: The share of a bin's predicted lines that must be in the list for an absent
#: one there to be charged: "the lowest bin in which at least half the predicted
#: 13C lines are in the list" (the plan owner's example, decision 32).
PRESENCE_SHARE = 0.5

#: The committed lines a TOF run's centre and width are fitted on at the fewest.
MIN_CENTRE_WIDTH_LINES = 20

#: The forms a run's tolerance takes, by instrument class.
FORM_CURVE = "curve"
FORM_CENTRE_WIDTH = "centre_width"
FORM_DEFAULT = "default"

#: Where a run's tolerance came from: its own lines, its lines pooled with
#: those of the files beside it that share its acquisition settings, or today's
#: numbers because neither held enough.
LEVEL_OWN = "own"
LEVEL_POOLED = "pooled"
LEVEL_DEFAULT = "default"

#: Decimals the record keeps.
_SNR_DIGITS = 2
_RATIO_DIGITS = 4


@dataclass(frozen=True)
class EnvelopeTolerance:
    """What a matched isotope line's share is judged against, and when an
    absent line counts.

    The default is today's score exactly: no band, a 5% floor, and the
    caller's detectability threshold.

    :param snr_edges: The lower edges of the curve's bins of expected S/N,
        ascending; the last bin is open. Empty for no curve.
    :param lower: Per bin, the lowest observed / predicted share that still
        scores as a full match: the curve's 20th percentile, non-decreasing
        and at most 1, the band's upper end where there is a curve.
    :param centre: The relative error a line's share is read about: the TOF's
        fitted centre, 0 elsewhere. The band's upper end is ``1 + centre``.
    :param floor: The width of a miss, as a fraction of the share, where the
        line's own noise is narrower.
    :param detect_snr: The expected S/N at or above which an absent line is
        charged; None leaves it to the caller's ``k_detect``.
    """

    snr_edges: tuple[float, ...] = ()
    lower: tuple[float, ...] = ()
    centre: float = 0.0
    floor: float = RELATIVE_FLOOR
    detect_snr: float | None = None

    def __post_init__(self):
        if len(self.snr_edges) != len(self.lower):
            raise ValueError(
                "EnvelopeTolerance needs one lower end per bin; got "
                f"{len(self.snr_edges)} edges and {len(self.lower)} lower ends"
            )

    def admitted(self, expected_snr) -> tuple[np.ndarray, np.ndarray]:
        """The band of observed / predicted share that scores as a full match.

        A line read anywhere inside it costs nothing; outside it, the score
        charges the distance to the nearer end. Without a curve the band is the
        single point ``1 + centre``. A line whose expected S/N is unknown (no
        noise estimate for the anchor) is given no allowance: the curve is a
        function of S/N, and a line it cannot place is read as today.

        :param expected_snr: Each line's expected S/N: the anchor's S/N times
            the line's predicted share.
        :return: The band's lower and upper ends, per line, as multiples of
            the line's predicted share.
        """
        snr = np.asarray(expected_snr, dtype=float)
        upper = np.full(snr.shape, 1.0 + self.centre)
        if not self.snr_edges:
            return upper.copy(), upper
        edges = np.asarray(self.snr_edges, dtype=float)
        lower = np.asarray(self.lower, dtype=float)
        # Below the first edge a line takes the first bin's end: a matched
        # line under an expected S/N of 3 is rare, and reads high if anything.
        bins = np.clip(np.searchsorted(edges, snr, side="right") - 1, 0, edges.size - 1)
        return np.where(np.isfinite(snr), lower[bins], upper), upper

    @classmethod
    def from_record(cls, record: dict | None) -> EnvelopeTolerance:
        """The tolerance a run's record says it scored at.

        :param record: A run's ``envelope_tolerance`` record
            (:meth:`EnvelopeFit.record`), or None.
        :return: That tolerance; today's where the record is absent, from
            before the fit, or at the default level.
        """
        if not isinstance(record, dict) or record.get("level") not in (
            LEVEL_OWN,
            LEVEL_POOLED,
        ):
            return cls()
        detect = record.get("detect_snr")
        detect_snr = (
            None
            if detect is None or float(detect) == DEFAULT_DETECT_SNR
            else float(detect)
        )
        if record.get("form") == FORM_CURVE:
            bins = record.get("bins") or []
            return cls(
                snr_edges=tuple(float(b["snr"][0]) for b in bins),
                lower=tuple(float(b["lower"]) for b in bins),
                detect_snr=detect_snr,
            )
        if record.get("form") == FORM_CENTRE_WIDTH:
            return cls(
                centre=float(record.get("centre") or 0.0),
                floor=float(record.get("floor") or RELATIVE_FLOOR),
                detect_snr=detect_snr,
            )
        return cls()


#: Today's numbers.
DEFAULT_TOLERANCE = EnvelopeTolerance()


@dataclass(frozen=True)
class EnvelopeLines:
    """The lines a tolerance is fitted on, from one file's committed envelopes.

    :param expected_snr: Each line's expected S/N.
    :param ratio: Each line's observed / predicted share; NaN for a predicted
        line the list did not hold, which counts toward the presence of its bin
        and toward nothing else.
    """

    expected_snr: np.ndarray = field(default_factory=lambda: np.empty(0))
    ratio: np.ndarray = field(default_factory=lambda: np.empty(0))

    def __post_init__(self):
        snr = np.asarray(self.expected_snr, dtype=float).ravel()
        ratio = np.asarray(self.ratio, dtype=float).ravel()
        if snr.size != ratio.size:
            raise ValueError(
                "EnvelopeLines needs one ratio per line; got "
                f"{snr.size} S/N values and {ratio.size} ratios"
            )
        # A line no S/N places is no observation of the curve, and an infinite
        # or non-positive ratio is no reading of a share.
        keep = np.isfinite(snr) & (np.isnan(ratio) | (np.isfinite(ratio) & (ratio > 0)))
        object.__setattr__(self, "expected_snr", snr[keep])
        object.__setattr__(self, "ratio", ratio[keep])

    @property
    def present(self) -> np.ndarray:
        """Which lines the list held."""
        return ~np.isnan(self.ratio)

    @property
    def lines(self) -> int:
        """How many of them the list held."""
        return int(self.present.sum())

    @property
    def absent(self) -> int:
        """How many it did not."""
        return int(self.ratio.size - self.lines)

    @classmethod
    def concatenate(cls, parts: Sequence[EnvelopeLines]) -> EnvelopeLines:
        """Several files' lines as one pool."""
        if not parts:
            return cls()
        return cls(
            np.concatenate([part.expected_snr for part in parts]),
            np.concatenate([part.ratio for part in parts]),
        )

    def record(self) -> dict:
        """The lines as a record keeps them, for a file beside this one to pool."""
        return {
            "expected_snr": [round(float(v), _SNR_DIGITS) for v in self.expected_snr],
            "ratio": [
                None if math.isnan(v) else round(float(v), _RATIO_DIGITS)
                for v in self.ratio
            ],
        }

    @classmethod
    def from_record(cls, record: dict | None) -> EnvelopeLines:
        """Lines a record kept (:meth:`record`); none where it kept none."""
        if not isinstance(record, dict):
            return cls()
        snr = record.get("expected_snr") or []
        ratio = record.get("ratio") or []
        if len(snr) != len(ratio):
            return cls()
        return cls(
            np.asarray(snr, dtype=float),
            np.asarray([np.nan if v is None else v for v in ratio], dtype=float),
        )


def _non_decreasing(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """The weighted least-squares non-decreasing fit to ``values``.

    Pool adjacent violators: a bin that reads above the bin after it is pooled
    with it into their weighted mean, until no pair is out of order. A noisy
    bin at low S/N then cannot lift the bins above it, which a running maximum
    would, nor drag the bins below it, which a running minimum would.
    """
    blocks: list[list[float]] = []  # [weighted mean, weight, bins pooled]
    for value, weight in zip(values, weights):
        blocks.append([float(value), float(weight), 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            value_b, weight_b, count_b = blocks.pop()
            value_a, weight_a, count_a = blocks.pop()
            total = weight_a + weight_b
            blocks.append(
                [
                    (value_a * weight_a + value_b * weight_b) / total,
                    total,
                    count_a + count_b,
                ]
            )
    return np.array([value for value, _, count in blocks for _ in range(count)])


@dataclass(frozen=True)
class ResponseCurve:
    """A fitted response curve and what it was fitted on.

    :param bins: Per bin of :data:`RESPONSE_SNR_EDGES`, a record: its S/N
        range, its tracking lines in the list, their median and 20th
        percentile as read and as made non-decreasing (None where the bin has
        too few lines), the admitted lower end the score takes, and its
        predicted lines, in the list or not.
    :param detect_snr: The expected S/N at or above which an absent line is
        charged; None where no bin's presence falls short below the lowest
        bin that holds its lines, which leaves the caller's threshold.
    """

    bins: tuple[dict, ...]
    detect_snr: float | None

    @property
    def tolerance(self) -> EnvelopeTolerance:
        """The tolerance the curve scores at."""
        return EnvelopeTolerance(
            snr_edges=tuple(float(b["snr"][0]) for b in self.bins),
            lower=tuple(float(b["lower"]) for b in self.bins),
            detect_snr=self.detect_snr,
        )


def fit_response_curve(
    lines: EnvelopeLines,
    *,
    min_lines: int = MIN_RESPONSE_LINES,
    min_bin_lines: int = MIN_BIN_LINES,
) -> ResponseCurve | None:
    """Fit an Orbitrap-class file's response curve on its 13C lines.

    Per bin of expected S/N with at least ``min_bin_lines`` lines in the list,
    the median and the 20th percentile of their observed / predicted share.
    Each of the two is made non-decreasing in S/N across the bins that have
    one (:func:`_non_decreasing`, weighted by the bins' line counts) and capped
    at 1.0, since a line cannot be censored above its natural share. The band a
    line scores in opens down to the 20th percentile. A bin with too few lines
    takes the lower end of the nearest bin above it that has one, and above
    the last such bin the band is today's, the natural share alone: without a
    reading the curve concedes nothing it has not measured.

    Where an absent line counts is read off the same lines: a bin with at least
    ``min_bin_lines`` predicted lines, present or absent, is judged, and it
    falls short where fewer than :data:`PRESENCE_SHARE` of them are in the
    list. The threshold is the lower edge of the lowest judged bin that holds
    at least that share, where a judged bin below it falls short: below it the
    instrument usually does not deliver the line, so its absence is no
    evidence, and above it a short bin is charged as today. Where no judged
    bin below the lowest that holds its lines falls short, the caller's
    threshold stands. Where every judged bin falls short, the threshold is the
    upper edge of the highest, or the open top bin's lower edge, so the
    brightest lines are always charged.

    :param lines: The file's tracking 13C lines and its absent predicted ones.
    :param min_lines: The tracking lines in the list a curve needs.
    :param min_bin_lines: The lines a bin needs to be read.
    :return: The curve, or None where the lines in the list are too few.
    """
    edges = np.asarray(RESPONSE_SNR_EDGES, dtype=float)
    upper_edges = np.append(edges[1:], np.inf)
    snr, ratio, present = lines.expected_snr, lines.ratio, lines.present
    in_range = snr >= edges[0]
    if int((present & in_range).sum()) < min_lines:
        return None

    which = np.searchsorted(edges, snr, side="right") - 1
    medians = np.full(edges.size, np.nan)
    quantiles = np.full(edges.size, np.nan)
    counts = np.zeros(edges.size, dtype=int)
    predicted = np.zeros(edges.size, dtype=int)
    for index in range(edges.size):
        in_bin = in_range & (which == index)
        observed = ratio[in_bin & present]
        counts[index] = observed.size
        predicted[index] = int(in_bin.sum())
        if observed.size >= min_bin_lines:
            medians[index] = float(np.median(observed))
            quantiles[index] = float(np.quantile(observed, RESPONSE_QUANTILE))

    read = ~np.isnan(medians)
    if not read.any():
        return None
    fitted_median = np.full(edges.size, np.nan)
    fitted_quantile = np.full(edges.size, np.nan)
    fitted_median[read] = np.minimum(_non_decreasing(medians[read], counts[read]), 1.0)
    fitted_quantile[read] = np.minimum(
        _non_decreasing(quantiles[read], counts[read]), 1.0
    )
    # Each bin takes the nearest reading at or above it, and the bins above the
    # last one read take today's band, the natural share alone.
    lower = np.ones(edges.size)
    above = 1.0
    for index in range(edges.size - 1, -1, -1):
        if read[index]:
            above = float(fitted_quantile[index])
        lower[index] = above

    # Counted from the bottom: a short bin above one that holds its lines is
    # charged as today, so a few bright lines turned away cannot free the
    # dimmer ones the file plainly delivers.
    judged = predicted >= min_bin_lines
    short = judged & (counts < PRESENCE_SHARE * predicted)
    held = judged & ~short
    detect_snr = None
    if held.any():
        lowest = int(np.flatnonzero(held)[0])
        if short[:lowest].any():
            detect_snr = float(edges[lowest])
    elif short.any():
        highest = int(np.flatnonzero(short)[-1])
        bound = upper_edges[highest]
        detect_snr = float(bound if np.isfinite(bound) else edges[highest])

    def rounded(value: float) -> float | None:
        return None if math.isnan(value) else round(float(value), _RATIO_DIGITS)

    bins = tuple(
        {
            "snr": [float(edges[index]), float(upper_edges[index])]
            if np.isfinite(upper_edges[index])
            else [float(edges[index]), None],
            "lines": int(counts[index]),
            "median": rounded(medians[index]),
            "p20": rounded(quantiles[index]),
            "fitted_median": rounded(fitted_median[index]),
            "fitted_p20": rounded(fitted_quantile[index]),
            "lower": round(float(lower[index]), _RATIO_DIGITS),
            "predicted": int(predicted[index]),
        }
        for index in range(edges.size)
    )
    return ResponseCurve(bins=bins, detect_snr=detect_snr)


def fit_centre_width(
    lines: EnvelopeLines, *, min_lines: int = MIN_CENTRE_WIDTH_LINES
) -> tuple[float, float] | None:
    """Fit a run's centre and width of a committed line's relative error.

    The median and the scaled median absolute deviation of observed /
    predicted share minus one, as the mass accuracy is fitted
    (``mass_accuracy.fit_mass_accuracy``): a line another peak swelled sits far
    out, and one of those would set the width for every line the run scores.

    :param lines: The run's tracking lines; absent ones are not read.
    :param min_lines: The lines in the list the fit needs.
    :return: ``(centre, width)``, or None where the lines are too few.
    """
    errors = lines.ratio[lines.present] - 1.0
    if errors.size < min_lines:
        return None
    centre = float(np.median(errors))
    width = float(1.4826 * np.median(np.abs(errors - centre)))
    return centre, width


@dataclass(frozen=True)
class EnvelopeFit:
    """The tolerance a run scores at, and how it got it.

    :param tolerance: What the score reads.
    :param form: The form the instrument class takes (:data:`FORM_CURVE`,
        :data:`FORM_CENTRE_WIDTH`, :data:`FORM_DEFAULT`).
    :param level: Where it came from (:data:`LEVEL_OWN`, :data:`LEVEL_POOLED`,
        :data:`LEVEL_DEFAULT`).
    :param lines: The file's own lines.
    :param curve: The fitted curve, where the form is a curve and one was fitted.
    :param centre_width: The fitted centre and width, on a TOF that fitted them.
    :param pooled_files: The files a pooled curve was fitted over, this one
        among them; 0 below the pooled level.
    :param pooled_lines: The lines in the list among them.
    """

    tolerance: EnvelopeTolerance
    form: str
    level: str
    lines: EnvelopeLines
    curve: ResponseCurve | None = None
    centre_width: tuple[float, float] | None = None
    pooled_files: int = 0
    pooled_lines: int = 0

    def record(self) -> dict:
        """What the run records it scored a line's share at
        (``config.envelope_tolerance``).

        The form and the level; the line counts the fit asks for; the file's
        own lines in the list and absent; for a curve its bins and the
        detectability threshold, and the file's lines themselves, which a file
        beside it pools; for a centre and a width both, and the floor the
        score takes. A reader rebuilds the tolerance from it
        (:meth:`EnvelopeTolerance.from_record`).
        """
        detect = self.tolerance.detect_snr
        record: dict = {
            "form": self.form,
            "level": self.level,
            "lines": self.lines.lines,
            "absent": self.lines.absent,
            "detect_snr": float(DEFAULT_DETECT_SNR if detect is None else detect),
        }
        if self.form == FORM_CURVE:
            record.update(
                min_lines=MIN_RESPONSE_LINES,
                min_bin_lines=MIN_BIN_LINES,
                quantile=RESPONSE_QUANTILE,
                presence_share=PRESENCE_SHARE,
                observations=self.lines.record(),
            )
            if self.curve is not None:
                record["bins"] = list(self.curve.bins)
            if self.level == LEVEL_POOLED:
                record.update(
                    pooled_files=self.pooled_files, pooled_lines=self.pooled_lines
                )
        elif self.form == FORM_CENTRE_WIDTH:
            record["min_lines"] = MIN_CENTRE_WIDTH_LINES
            if self.centre_width is not None:
                centre, width = self.centre_width
                record.update(
                    centre=round(centre, _RATIO_DIGITS),
                    width=round(width, _RATIO_DIGITS),
                    floor=round(self.tolerance.floor, _RATIO_DIGITS),
                )
        return record


def fit_envelope_tolerance(
    form: str,
    lines: EnvelopeLines,
    pooled: Sequence[EnvelopeLines] = (),
) -> EnvelopeFit:
    """The tolerance a run scores a line's share at, at the first level that
    holds enough lines.

    A curve is fitted on the file's own lines; where they are too few, on them
    pooled with the lines of the files beside it that share its acquisition
    settings (``pooled``, which the caller selects); where those are too few as
    well, today's numbers. A centre and a width are fitted on the run's own
    lines or not at all: a TOF's S/N column is not read as a curve until it is
    detected again, and nothing is pooled for it. Any other form is today's.

    :param form: The form the run's instrument class takes.
    :param lines: The file's own lines.
    :param pooled: The lines of the files beside it, one entry per file.
    :return: The fit, with the level it reached.
    """
    if form == FORM_CURVE:
        curve = fit_response_curve(lines)
        if curve is not None:
            return EnvelopeFit(curve.tolerance, form, LEVEL_OWN, lines, curve=curve)
        if pooled:
            pool = EnvelopeLines.concatenate([lines, *pooled])
            curve = fit_response_curve(pool)
            if curve is not None:
                return EnvelopeFit(
                    curve.tolerance,
                    form,
                    LEVEL_POOLED,
                    lines,
                    curve=curve,
                    pooled_files=len(pooled) + 1,
                    pooled_lines=pool.lines,
                )
        return EnvelopeFit(DEFAULT_TOLERANCE, form, LEVEL_DEFAULT, lines)
    if form == FORM_CENTRE_WIDTH:
        fitted = fit_centre_width(lines)
        if fitted is None:
            return EnvelopeFit(DEFAULT_TOLERANCE, form, LEVEL_DEFAULT, lines)
        centre, width = fitted
        # Never tighter than today: the fit exists because 5% is too narrow
        # for how a TOF measures a share, and like the S/N terms it only widens.
        # Scored at the record's own rounded numbers, as a curve is, so the
        # record rebuilds exactly what the run scored at.
        tolerance = EnvelopeTolerance(
            centre=round(centre, _RATIO_DIGITS),
            floor=round(max(RELATIVE_FLOOR, width), _RATIO_DIGITS),
        )
        return EnvelopeFit(tolerance, form, LEVEL_OWN, lines, centre_width=fitted)
    return EnvelopeFit(DEFAULT_TOLERANCE, FORM_DEFAULT, LEVEL_DEFAULT, lines)
