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
matched, and an outside engine off whatever anchors it has. Two implementations
of one measurement would be two answers to "how well does this have to agree",
and a comparison between the engines would then measure the difference between
their fits as much as between their assignments.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd


#: Matched anchors below which no width is fitted and the caller falls back.
#: A handful of mass errors has a median and a spread like any sample does, but
#: not one that says anything about the instrument; the fit reports no width at
#: all rather than a confident wrong one.
MASS_ACCURACY_MIN_ANCHORS = 8

#: Matched anchors below which no OFFSET is fitted either. Lower than the width's
#: minimum because the two are different measurements of different difficulty: a
#: median needs far less than a spread does, and a sample whose handful of
#: anchors all sit a ppm to one side has measured an offset even though it has
#: measured no width. Five rather than three because three admits a mis-matched
#: anchor as the median - on a TOF sample matched in a 15 ppm window, three
#: anchors at -10.5, -10.4 and -2.8 ppm are two mis-matches outvoting the one
#: real line, and correcting by their median moves every candidate the wrong way.
MASS_OFFSET_MIN_ANCHORS = 5

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
    measured, because they are measurable from different amounts of evidence and
    a caller has to be able to tell an offset of zero from no offset at all.
    This returned ``0.0`` for both cases, and the difference is the difference
    between scoring a well-centred sample and scoring one a ppm out as though it
    were well centred: a set of six anchors sitting at -1.2 ppm has measured its
    offset, and reporting that as "zero" silently moves every candidate of that
    sample by more than an Orbitrap's whole accuracy.

    :param errors_ppm: The mass errors of the sample's matched anchors, in ppm.
        Non-finite values are ignored.
    :return: The offset, or ``None`` below :data:`MASS_OFFSET_MIN_ANCHORS`
        anchors; and the width, or ``None`` below
        :data:`MASS_ACCURACY_MIN_ANCHORS`. Neither ``None`` is a small
        measurement - it is no measurement, and the caller decides what stands
        in (:func:`scoring_sigma_ppm` for the width; a zero offset for the
        offset, which is what "uncorrected" means).
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
