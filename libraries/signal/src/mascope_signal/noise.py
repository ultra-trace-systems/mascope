"""Noise-floor measurement shared by the TOF signal-quality decisions.

Blank classification (``mascope_tofwerk.processor``) and ambient-spectrum
detection (``mascope_signal.instrument_func.fit``) judge a TOF spectrum the
same way - the tallest detected peak against the spread of the others - and
differ only in the threshold they hold the result to. The measurement lives
here so the two cannot drift apart, and so the degenerate spectra that have no
ratio at all are answered in one place rather than two.
"""

import numpy as np
from scipy.stats import median_abs_deviation


def max_peak_snr(
    peak_heights: np.ndarray, noise_threshold_factor: float
) -> float | None:
    """Signal-to-noise ratio of the tallest peak over a MAD noise floor.

    The noise floor is the median absolute deviation of the peak heights,
    scaled by ``noise_threshold_factor``. Two spectra have no such ratio, and
    both are answered here rather than by reducing over an empty array or
    dividing by an undefined noise floor:

    - **No peaks at all**, reported as ``0.0``: nothing was detected, so
      nothing rises above the noise. It is answered here rather than left to
      the noise-floor guard below, which would report ``None`` for it, the MAD
      of an empty array being NaN. A caller that reads ``None`` as an
      unbounded ratio would then take the maximum of the empty array itself
      and raise - which is the failure this measurement exists to keep out of
      both call sites.
    - **No measurable noise floor**, reported as ``None``: a single peak, or a
      saturated spectrum whose peaks are all the same height, gives a MAD of 0
      (a non-finite one is treated the same way). What that means is the
      caller's to decide - blank classification reads it as no measurement,
      ambient detection as an unbounded ratio - so this states the fact and
      leaves the reading to them.

    :param peak_heights: Heights of the detected peaks; may be empty.
    :type peak_heights: numpy.ndarray
    :param noise_threshold_factor: Multiplier applied to the noise level before
        the tallest peak is divided by it.
    :type noise_threshold_factor: float
    :return: Ratio of the tallest peak to the noise threshold, ``0.0`` when
        there are no peaks, or ``None`` when there is no noise floor.
    :rtype: float or None
    """
    if peak_heights.size == 0:
        return 0.0

    noise_mad = median_abs_deviation(peak_heights, scale="normal")
    # The 1.4826 repeats the scaling ``scale="normal"`` has already applied, so
    # the threshold comes out ~1.48x stricter than the factor reads. Kept as
    # both call sites had it: correcting it would reclassify existing files,
    # which is a separate change with its own evidence to gather.
    noise_std = 1.4826 * noise_mad
    noise_threshold = noise_std * noise_threshold_factor
    if not np.isfinite(noise_threshold) or noise_threshold <= 0:
        return None

    return float(np.max(peak_heights) / noise_threshold)
