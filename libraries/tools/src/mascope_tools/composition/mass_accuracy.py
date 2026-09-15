"""What a sample's own mass errors measure, and the width a fit is judged at.

The fit score's mass term is a Gaussian likelihood, so it needs a width. The
honest one is the sample's own: the spread of the mass errors its known ions
actually show, which is a property of the instrument, its calibration and that
run, and differs by more than an order of magnitude between an Orbitrap at 0.2
ppm and a TOF at 5. Where too few known ions matched to measure anything, the
instrument class's width stands in
(:func:`profiles.resolve_fallback_sigma_ppm`).

This lives in the library rather than in an engine because two engines fit it:
Mascope's assignment engine, off the isotopologue rows its targeted stage
matched for the target library (a reference mirror's lines in the same frame
measure the match window on a TOF, so the engine leaves them out), and an
outside engine off whatever anchors it has. Two implementations
of one measurement would be two answers to "how well does this have to agree",
and a comparison between the engines would then measure the difference between
their fits as much as between their assignments.

The offset is one number, and on an Orbitrap the error is not one number in
ppm: below about m/z 120 the calibration's residual is closer to a constant
ABSOLUTE offset, which in ppm grows as 1/mz. :func:`fit_mass_trend` fits that
shape and accepts it only where the errors demand it; everything else keeps
the constant centre.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd


#: Matched anchors below which no width is fitted and the caller falls back.
#: A handful of mass errors has a median and a spread like any sample does, but
#: not one that says anything about the instrument; the fit reports no width at
#: all rather than a confident wrong one.
MASS_ACCURACY_MIN_ANCHORS = 8

#: The offset needs the same anchors the width does, and this says so rather
#: than a second constant saying otherwise. It reads like the easier
#: measurement - a median needs fewer points than a spread - and the gate
#: measured the opposite: an anchor set too small to say how wide it is, is too
#: small to say where its centre is. On a TOF set matched in a 15 ppm window,
#: three samples of one acquisition put their five to seven anchors' median at
#: -5.3, -6.9 and -8.6 ppm while each sample's own committed rows sat at -2.1 to
#: -2.6; correcting by the anchors moved every candidate about 4 ppm the wrong
#: way and widened the committed mass error from 2.03 to 2.33 ppm. What made
#: those anchors useless is what the refused width would have reported: they
#: scatter as wide as the window they were matched in.
MASS_OFFSET_MIN_ANCHORS = MASS_ACCURACY_MIN_ANCHORS

#: The floor on a fitted width, in ppm. A run whose anchors happen to agree to
#: within a rounding does not thereby measure to that precision, and a sigma at
#: zero would make the mass likelihood a step function.
MIN_FITTED_SIGMA_PPM = 0.05

#: Correct matches spread wider than the calibration anchors do - centroiding,
#: prediction error and the analyte's own tail - so the fitted instrument width
#: is widened by this in quadrature before a candidate is scored against it.
#: Added by :func:`scoring_sigma_ppm`, which is how every caller should reach
#: it: the fitted width and the width a score is judged at are different
#: numbers, and only the second one includes this.
PRED_SIGMA_PPM = 0.5


def fit_mass_accuracy(
    errors_ppm: Sequence[float] | np.ndarray | pd.Series,
) -> tuple[float | None, float | None]:
    """Robust ``(mu, sigma)`` in ppm of a sample's matched mass errors.

    Median and scaled median absolute deviation rather than mean and standard
    deviation: a mis-assigned anchor sits several ppm out, and one of those
    would set the width for every candidate the run scores.

    The two answers are reported separately, each ``None`` where it was not
    measured, because a caller has to be able to tell an offset of zero from no
    offset at all. This returned ``0.0`` for both cases, so a sample the fit had
    nothing to say about was indistinguishable from one it had measured to be
    well centred, and every consumer of the pair - the score, the run's record
    of what it scored at, a comparison against another engine - read the second
    one.

    :param errors_ppm: The mass errors of the sample's matched anchors, in ppm.
        Non-finite values are ignored.
    :return: The offset, or ``None`` below :data:`MASS_OFFSET_MIN_ANCHORS`
        anchors; and the width, or ``None`` below
        :data:`MASS_ACCURACY_MIN_ANCHORS`. The two minimums are equal, and the
        answers are still reported separately because the caller does different
        things with them. Neither ``None`` is a small measurement - it is no
        measurement, and the caller decides what stands in
        (:func:`scoring_sigma_ppm` for the width; a zero offset for the offset,
        which is what "uncorrected" means).
    """
    me = pd.Series(errors_ppm, dtype=float)
    me = me[np.isfinite(me)]
    if len(me) < MASS_OFFSET_MIN_ANCHORS:
        return None, None
    mu = float(me.median())
    if len(me) < MASS_ACCURACY_MIN_ANCHORS:
        return mu, None
    sigma = max(float(1.4826 * (me - mu).abs().median()), MIN_FITTED_SIGMA_PPM)
    return mu, sigma


def mass_accuracy_anchors(match_isotope_df: pd.DataFrame) -> pd.Series:
    """The matched mass errors a sample's accuracy is fitted from.

    One definition of "anchor" for the fit and for anything that reports how
    many it had: a row that paired to a peak with an intensity and a usable
    mass error. A caller that counted them itself would drift from the fit.
    Which rows the frame holds is the caller's choice: the assignment engine
    hands this its target library's rows and not a reference mirror's.

    :param match_isotope_df: A match frame carrying ``match_mz_error`` and
        ``sample_peak_intensity``. A frame missing either column has matched
        nothing, and yields no anchors rather than an error.
    :return: The anchors' mass errors in ppm.
    """
    empty = pd.Series(dtype=float)
    me = pd.to_numeric(match_isotope_df.get("match_mz_error", empty), errors="coerce")
    inten = pd.to_numeric(
        match_isotope_df.get("sample_peak_intensity", empty), errors="coerce"
    )
    return me[(inten.fillna(0) > 0) & me.notna()]


def fit_sample_mass_accuracy(
    match_isotope_df: pd.DataFrame,
) -> tuple[float | None, float | None]:
    """Robust ``(mu, sigma)`` ppm of a match frame's matched isotopologues.

    The frame-shaped form of :func:`fit_mass_accuracy`, for a caller holding
    the isotopologue rows a targeted match produced.

    :param match_isotope_df: A match frame, as ``compute_match_isotopes``
        produces it.
    :return: As :func:`fit_mass_accuracy` - including for a frame that carries
        neither column, which has not measured a mass error, and that is the
        same answer as a frame carrying too few rows.
    """
    return fit_mass_accuracy(mass_accuracy_anchors(match_isotope_df))


def scoring_sigma_ppm(
    fitted_sigma_ppm: float | None,
    fallback_sigma_ppm: float,
) -> float:
    """The width the fit score judges a mass error against.

    The sample's own fitted width where it has one and the instrument class's
    where it does not, in both cases widened by :data:`PRED_SIGMA_PPM` - so a
    fallback is a substitute for the measurement at the same place in the
    arithmetic, and two engines scoring the same sample judge it at the same
    width.

    :param fitted_sigma_ppm: What :func:`fit_mass_accuracy` measured, or None.
    :param fallback_sigma_ppm: The instrument class's width, from
        :func:`profiles.resolve_fallback_sigma_ppm`.
    :return: The Gaussian width of the score's mass term, in ppm.
    """
    sigma = fallback_sigma_ppm if fitted_sigma_ppm is None else fitted_sigma_ppm
    return float(np.hypot(float(sigma), PRED_SIGMA_PPM))


#: Points below which no mass trend is fitted, before trimming or after it. A
#: line has two parameters where an offset has one, and the rule that accepts
#: it tests the slope against its own standard error and splits the points in
#: two halves, so it asks for more than the offset and the width do. Twenty is
#: the minimum peaky's calibration settled on for the same fit.
MASS_TREND_MIN_POINTS = 20

#: The narrowest spread of ``1000 / mz`` a trend is fitted over. The slope is
#: measured along that axis, so a span this short - m/z 250 to 400, or m/z 87 to
#: 100 - leaves it to the scatter.
MASS_TREND_MIN_SPAN = 1.5

#: How many standard errors the slope must clear. Two is a 5% two-sided test,
#: and a flat source then grows a trend in about one sample of sixteen at any
#: point count; three keeps that under one in fifty.
MASS_TREND_SLOPE_MIN_SE = 3.0

#: The largest share of the constant model's residual RMS the trend's residual
#: RMS may keep, on the same points. RMS rather than a robust width, on
#: purpose: the low-mass tail the trend exists for is a minority of the
#: points, which a median absolute deviation does not see.
MASS_TREND_RMS_RATIO_MAX = 0.8

#: The largest absolute offset, in mDa, a trend may carry. ``1000 / mz`` makes
#: the slope the calibration curve's constant absolute residual, and 0.5 mDa is
#: already 1.25 ppm at m/z 400: a working calibration has no constant term that
#: large, so beyond it the axis is miscalibrated and the answer is the constant
#: model, not a 1/mz correction worth 8 ppm at m/z 61.
MASS_TREND_MAX_OFFSET_MDA = 0.5

#: Kept points required in each half of the fitted ``1000 / mz`` range. One
#: point alone at one end of the mass range is a lever: the least-squares line
#: passes through it, so its residual is near zero, trimming never removes it,
#: the residual ratio improves, and the slope comes out significant because
#: that one point carries the spread along the axis.
MASS_TREND_MIN_SIDE_POINTS = 5

#: The absolute floor, in mDa, on the width a row is judged at where a trend is
#: judged. The residual curves faster than 1/mz at the low-mass edge - m/z 46
#: sat 0.04 mDa off a fitted trend on an Orbitrap file - and 0.03 mDa is the
#: absolute accuracy an Orbitrap's calibrated ions show across the range. It
#: only acts where it exceeds the width in ppm: below m/z 60 at a 0.5 ppm width.
MASS_TREND_ABS_FLOOR_MDA = 0.03

#: Why a trend was not accepted, one per rule, in the order the rules are asked.
TREND_TOO_FEW_POINTS = "too_few_points"
TREND_NARROW_RANGE = "narrow_range"
TREND_ONE_SIDED = "one_sided"
TREND_SLOPE_NOT_SIGNIFICANT = "slope_not_significant"
TREND_NO_BETTER_THAN_CONSTANT = "no_better_than_constant"
TREND_OFFSET_TOO_LARGE = "offset_too_large"

#: Trimming rounds, and the robust widths from the line beyond which a point is
#: trimmed: a mis-assigned row several ppm out would otherwise tilt it.
_TREND_TRIM_ROUNDS = 3
_TREND_TRIM_WIDTHS = 3.0


@dataclass(frozen=True)
class MassTrend:
    """A mass error that follows ``ppm = a + b * 1000 / mz``.

    With ``1000 / mz`` as the axis the slope ``b`` is an absolute offset in mDa,
    the same at every mass, and ``a`` is the ppm the error tends to at high
    mass. The m/z the points covered is part of the trend: outside it the line
    was never measured.
    """

    intercept_ppm: float
    offset_mda: float
    #: The kept points' robust spread about the line.
    sigma_ppm: float
    #: The points kept after trimming.
    points: int
    mz_lo: float
    mz_hi: float

    def centre_ppm(self, mz: float) -> float:
        """The centre at ``mz``, held at the nearest edge outside the coverage.

        A trend fitted over m/z 150-480 would put its 1/mz extrapolation two ppm
        away on an m/z 61 it never saw; held, the centre there is the one the
        lowest point measured.
        """
        held = min(max(float(mz), self.mz_lo), self.mz_hi)
        return self.intercept_ppm + self.offset_mda * 1000.0 / held


def trend_width_floor_ppm(mz: float) -> float:
    """:data:`MASS_TREND_ABS_FLOOR_MDA` at ``mz``, in ppm.

    Not held to the trend's coverage the way :meth:`MassTrend.centre_ppm` is:
    below the lowest point the centre is frozen and may be wrong by an amount
    nothing measured, and a floor that keeps widening there errs toward
    admitting such an ion rather than condemning it on that centre.
    """
    return MASS_TREND_ABS_FLOOR_MDA * 1000.0 / float(mz)


def fit_mass_trend(
    mz: Sequence[float] | np.ndarray,
    errors_ppm: Sequence[float] | np.ndarray,
    *,
    min_points: int = MASS_TREND_MIN_POINTS,
    sigma_floor_ppm: float = MIN_FITTED_SIGMA_PPM,
) -> tuple[MassTrend | None, str | None]:
    """Fit ``ppm = a + b * 1000 / mz`` over mass errors, if they demand it.

    Least squares, trimmed for three rounds at three robust widths from the
    line. The trend is accepted only when all of these hold, in this order:
    enough points survive trimming (:data:`MASS_TREND_MIN_POINTS`) in each
    half of the fitted range (:data:`MASS_TREND_MIN_SIDE_POINTS`), the slope
    clears :data:`MASS_TREND_SLOPE_MIN_SE` standard errors, the trend's
    residual RMS is at most :data:`MASS_TREND_RMS_RATIO_MAX` of the constant
    model's on the same points, and the offset is inside
    :data:`MASS_TREND_MAX_OFFSET_MDA`. Anything else keeps the constant model.
    The rules are ported from peaky's ``assignment/masscal.py``
    (github.com/ultra-trace-systems/peaky), so the two engines judge a trend
    alike; which rows a caller fits over is its own choice.

    :param mz: The points' m/z. A non-finite or non-positive one is ignored,
        with its error.
    :param errors_ppm: Their mass errors in ppm, in the same order.
    :param min_points: Points below which nothing is fitted.
    :param sigma_floor_ppm: The floor on the robust width the trimming reads,
        so points that agree to a rounding do not trim everything else.
    :return: The trend and None, or None and the rule that refused it (one of
        the ``TREND_*`` values).
    """
    mz_values = np.asarray(mz, dtype=float)
    errors = np.asarray(errors_ppm, dtype=float)
    usable = np.isfinite(mz_values) & (mz_values > 0) & np.isfinite(errors)
    x = 1000.0 / mz_values[usable]
    y = errors[usable]
    if len(x) < min_points:
        return None, TREND_TOO_FEW_POINTS
    if x.max() - x.min() < MASS_TREND_MIN_SPAN:
        return None, TREND_NARROW_RANGE
    keep = np.ones(len(x), dtype=bool)
    intercept = slope = 0.0
    for _ in range(_TREND_TRIM_ROUNDS):
        if keep.sum() < min_points:
            return None, TREND_TOO_FEW_POINTS
        design = np.column_stack([np.ones(keep.sum()), x[keep]])
        (intercept, slope), *_ = np.linalg.lstsq(design, y[keep], rcond=None)
        residuals = y - (intercept + slope * x)
        kept = residuals[keep]
        mad = 1.4826 * np.median(np.abs(kept - np.median(kept)))
        if mad <= 0:
            break
        keep = np.abs(residuals) <= _TREND_TRIM_WIDTHS * max(mad, sigma_floor_ppm)
    if keep.sum() < min_points:
        return None, TREND_TOO_FEW_POINTS
    residuals = y - (intercept + slope * x)
    x_kept, y_kept, r_kept = x[keep], y[keep], residuals[keep]
    midpoint = 0.5 * (x_kept.min() + x_kept.max())
    low_mass_half = int((x_kept >= midpoint).sum())
    if min(len(x_kept) - low_mass_half, low_mass_half) < MASS_TREND_MIN_SIDE_POINTS:
        return None, TREND_ONE_SIDED
    spread = float(((x_kept - x_kept.mean()) ** 2).sum())
    if spread <= 0:
        return None, TREND_NARROW_RANGE
    slope_se = float(np.sqrt((r_kept**2).sum() / max(len(r_kept) - 2, 1) / spread))
    if abs(slope) <= MASS_TREND_SLOPE_MIN_SE * slope_se:
        return None, TREND_SLOPE_NOT_SIGNIFICANT
    rms_trend = float(np.sqrt(np.mean(r_kept**2)))
    rms_constant = float(np.sqrt(np.mean((y_kept - np.median(y_kept)) ** 2)))
    if rms_constant <= 0 or rms_trend > MASS_TREND_RMS_RATIO_MAX * rms_constant:
        return None, TREND_NO_BETTER_THAN_CONSTANT
    if abs(slope) > MASS_TREND_MAX_OFFSET_MDA:
        return None, TREND_OFFSET_TOO_LARGE
    mz_kept = 1000.0 / x_kept
    return (
        MassTrend(
            intercept_ppm=float(intercept),
            offset_mda=float(slope),
            sigma_ppm=max(float(1.4826 * np.median(np.abs(r_kept))), sigma_floor_ppm),
            points=int(keep.sum()),
            mz_lo=float(mz_kept.min()),
            mz_hi=float(mz_kept.max()),
        ),
        None,
    )
