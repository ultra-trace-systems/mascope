"""
Calibration configuration settings.

Centralized configuration for calibration parameters and rules.
"""

import numpy as np
from pydantic import BaseModel


class CalibrationConfig(BaseModel):
    """
    Configuration settings for calibration parameters and rules.
    """

    DEFAULT_MATCH_SCORE_MIN: float = 0.0
    DEFAULT_PEAK_INTENSITY_MIN: float = 0.0
    DEFAULT_ISOTOPE_ABUNDANCE_MIN: float = 0.15

    # The brightest lines are the worst calibrants: an Orbitrap line that
    # carries a large share of the ion current reads high against the weaker
    # lines of its own file. Measured on the matched calibrants of the
    # assignment gate's Orbitrap files on two instruments, each line held
    # against the trend over m/z of its file's committed analytes (43
    # files): the 106 lines at or below 16 % of the TIC sit -1.0 to +0.7 ppm
    # from it, 0.3 ppm apart, and every line from 32 % up reads +0.6 to
    # +4.0 ppm above it (five lines on four sets, both instruments). The
    # knee lies between. The only lines in between are a bromide set's two
    # calibrants, at 13 to 24.6 % of the TIC file by file, which a lower cap
    # would leave out only to fit on them again (the fallback below). A cap
    # at 25 % would sit within half a point of them, so it sits at 28 %, in
    # the empty gap from 24.6 to 31.8 %, where no file's share falls. The two
    # instruments' bright lines do not tell them apart, so the cap is the
    # class's. A line's share is of the TIC of its own stream for a fit
    # stream by stream, of its polarity's for a fit of the file. TOF off:
    # unmeasured there. An absolute cap on a line's intensity has no default.
    #
    # A cap that leaves fewer calibrants than the fit needs is set aside and
    # the fit made on every line, as with no cap, the fit's record saying so:
    # a fit on the bright lines is the fit the file had before, and the
    # quality bar still judges it.
    ORBI_CALIBRANT_TIC_SHARE_MAX: float | None = 0.28
    TOF_CALIBRANT_TIC_SHARE_MAX: float | None = None
    DEFAULT_PEAK_INTENSITY_MAX: float | None = None

    # The offset the Orbitrap fit reports beside its factor, the
    # `ppm = factor + offset * 1000 / mz` line over the calibrants it kept,
    # is fitted only where those can tell the two terms apart: three lines
    # or more, so that one is left over to measure the line's scatter by,
    # and spread over m/z so that 1000 / mz differs by at least
    # ORBI_OFFSET_MIN_INVERSE_SPAN across them. That is where an offset of
    # 0.1 mDa, the size of the one measured on the sparse uronium set,
    # moves the error 0.3 ppm across the lines, which is their scatter
    # (above). Over a narrower span an offset reads as a factor.
    ORBI_OFFSET_MIN_LINES: int = 3
    ORBI_OFFSET_MIN_INVERSE_SPAN: float = 3.0

    # Pre-calibration m/z error above which the applied fit is reported as
    # acquisition-side drift: the software corrects it, but the instrument's
    # internal calibration is off and an operator should retune it. TOF mass
    # axes legitimately wander tens of ppm between retunings, so TOF files
    # get a far looser threshold than the Orbitrap-grade default.
    ACQUISITION_DRIFT_WARNING_PPM: float = 10.0
    TOF_ACQUISITION_DRIFT_WARNING_PPM: float = 50.0

    # How long a given drift warning stays suppressed after it fires. Drift is
    # a standing condition an operator resolves by retuning the instrument,
    # not an event per file: a busy instrument otherwise reports it on every
    # acquisition (one production Orbitrap logged it 172 times in 19 hours).
    # One reminder a day is enough to keep it visible until it is fixed.
    ACQUISITION_DRIFT_WARNING_INTERVAL_S: float = 24 * 60 * 60

    # Quality bar an applied fit must clear to be stored ``verified``, which
    # is what matching and peak assignment read as "this file's mass axis is
    # right". The m/z error tolerances below are the window calibrants are
    # matched in, not a quality bar: a fit can pass them and still leave the
    # axis a ppm or more out. What a bar below it does - warn or keep the
    # sample out of matching - is the deployment's ``calibration_quality_gate``.
    #
    # Measured on 60 days of production calibrations: Orbitrap fits on three
    # or more points sit well within 1 ppm unless their calibrants disagree,
    # and two calibrants that disagree by 2.5 ppm leave 1.27 ppm. TOF fits on
    # lower-resolution APi-TOFs leave 3-4 ppm as a matter of course, while the
    # broken TOF fits seen sat at 11 ppm and beyond.
    ORBI_MAX_POST_FIT_MZ_ERROR_PPM: float = 1.0
    TOF_MAX_POST_FIT_MZ_ERROR_PPM: float = 10.0
    # A fit on a single point zeroes its own residual, and two isotopes of one
    # ion agree with each other whatever peak they sit on, so such a fit says
    # nothing about whether it found the right peak. It is only trusted while
    # it moves the axis no further than LOW_POINT_MAX_AXIS_CORRECTION_PPM -
    # the Orbitrap drift-warning threshold. One-point fits are the norm for
    # narrow-range and EasyIC modes and correct a few ppm; the ones that
    # anchored to the wrong peak moved the axis 76-221 ppm. Two points from two
    # ions corroborate each other and are judged on their residual alone.
    MIN_VERIFIED_CALIBRATION_POINTS: int = 3
    LOW_POINT_MAX_AXIS_CORRECTION_PPM: float = 10.0
    # A stream of a stitched file fitted on too few calibrants to vouch for
    # its own correction (one point, or isotopes of one ion) is vouched for
    # by another stream of the file that can - calibrants from several ions,
    # clearing the bar on its own - when their corrections lie within this
    # of each other. Both then read the instrument's offset from the
    # acquisition axis in two scan ranges, which differ by about a ppm (0.1
    # to 0.8 ppm between the one-point and the several-point ranges of the
    # stitched files on an internal test deployment); twice the Orbitrap
    # residual bound leaves room for the error of both fits. A stream
    # anchored to a wrong peak lands tens of ppm from the others.
    STREAM_CORROBORATION_PPM: float = 2.0
    # A fit on MIN_VERIFIED_CALIBRATION_POINTS or more must also draw them
    # from this many ions, or it is one ion's isotopes agreeing with each
    # other.
    MIN_VERIFIED_CALIBRATION_IONS: int = 2
    # A TOF fit must keep at least this share of the distinct lines its
    # calibration collection offers above the abundance floor, None
    # disabling the check. A TOF axis read further off than the refine
    # window leaves the reagent lines without a peak near them, while lines
    # of sample ions among dense peaks each find a neighbour; three such
    # neighbours fit the two-parameter model with a small residual. On a
    # 60-day production replay a collection of 11 lines fitted that way kept
    # 3 or 4 of them (27-36 %), 230 ppm from the right fit, while the right
    # fits' lower quartile per instrument and mode kept 60-100 % of the
    # collection's lines. The replay recorded no per-file minimum, so a right
    # fit on few of a long collection's lines can fall below the bar too. A
    # collection of 7 lines or fewer cannot fall below it on the 3 points a
    # TOF fit needs. Conservative until the TOF thresholds are tuned.
    TOF_MIN_KEPT_LINE_SHARE: float | None = 0.4
    # Summed calibrant intensity as a fraction of the mean TIC per scan, None
    # disabling the check. Off for both: in production it only ever flagged
    # fits that were otherwise healthy, down to 3e-5 on Orbitrap and 5e-7 on
    # TOF. Those were shares of the TIC summed over the file's scans, which
    # the share is no longer taken of: against the mean they are larger by
    # the file's number of scans, and a TOF one by a further 1 / the sample
    # interval in ns. A floor has to be read off fits made since.
    ORBI_MIN_CALIBRANT_TO_TIC: float | None = None
    TOF_MIN_CALIBRANT_TO_TIC: float | None = None

    # TOF calibration parameters
    TOF_MZ_ERROR_TOLERANCE: int = 15  # in ppm
    TOF_DEFAULT_REFINE_WINDOW: int = 100
    TOF_SNR_THRESHOLD: float = 10.0

    # Orbi calibration parameters. The refine window must be wide enough to
    # contain the true calibrant centroid under a realistic instrument-side
    # calibration offset (offsets beyond 10 ppm occur in practice); when the
    # true centroid is outside the window, the only in-window candidates are
    # its FTMS sidelobes and the fit anchors to the wrong m/z.
    ORBI_MZ_ERROR_TOLERANCE: int = 5  # in ppm
    ORBI_DEFAULT_REFINE_WINDOW: int = 50
    ORBI_SNR_THRESHOLD: float = 50.0


# Global calibration configuration instance
calibration_config = CalibrationConfig()


def calibrants_over_cap(
    intensity,
    tic_share,
    tic_share_max: float | None,
    peak_intensity_max: float | None,
) -> np.ndarray:
    """Which calibrant lines are brighter than the cap allows.

    A line is over the cap when its share of the ion current exceeds
    ``tic_share_max`` or its intensity exceeds ``peak_intensity_max``: of the
    two, whichever leaves out more applies. A cap that is None leaves out
    nothing, and neither does a share or an intensity that is not known.
    The m/z calibration node leaves such lines out of its fit
    (``ORBI_CALIBRANT_TIC_SHARE_MAX`` says why), and the assignment engine's
    mass gate leaves them out of its anchors.

    :param intensity: Each line's intensity.
    :param tic_share: Each line's share of the ion current.
    :param tic_share_max: The largest share one line may carry, or None.
    :param peak_intensity_max: The largest intensity, or None.
    :return: True for each line over the cap.
    :rtype: np.ndarray
    """
    intensity = np.asarray(intensity, dtype=float)
    tic_share = np.asarray(tic_share, dtype=float)
    over = np.zeros(intensity.shape, dtype=bool)
    if tic_share_max is not None:
        over |= tic_share > tic_share_max
    if peak_intensity_max is not None:
        over |= intensity > peak_intensity_max
    return over
