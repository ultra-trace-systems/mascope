"""
Calibration configuration settings.

Centralized configuration for calibration parameters and rules.
"""

from pydantic import BaseModel


class CalibrationConfig(BaseModel):
    """
    Configuration settings for calibration parameters and rules.
    """

    DEFAULT_MATCH_SCORE_MIN: float = 0.0
    DEFAULT_PEAK_INTENSITY_MIN: float = 0.0
    DEFAULT_ISOTOPE_ABUNDANCE_MIN: float = 0.15

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
    # axis a ppm or more out. Measured on a fleet corpus of production files:
    # Orbitrap fits on three or more points leave at most 0.47 ppm, and the
    # TOF fits 0.2-1.6 ppm, so the residual bounds sit above both.
    ORBI_MAX_POST_FIT_MZ_ERROR_PPM: float = 1.0
    TOF_MAX_POST_FIT_MZ_ERROR_PPM: float = 3.0
    # Below this many points the residual says little: two calibrants that
    # disagree split the difference, and one zeroes its own residual. Such a
    # fit is only trusted while it moves the axis no further than
    # LOW_POINT_MAX_AXIS_CORRECTION_PPM - one- and two-point fits are the
    # norm for narrow-range and EasyIC modes and are healthy when the
    # correction is small, while the one-point fits that anchored to the wrong
    # peak moved the axis 76-81 ppm and reported a zero residual.
    MIN_VERIFIED_CALIBRATION_POINTS: int = 3
    LOW_POINT_MAX_AXIS_CORRECTION_PPM: float = 5.0
    # A fit on MIN_VERIFIED_CALIBRATION_POINTS or more must also draw them
    # from this many ions, or it is one ion's isotopes agreeing with each
    # other.
    MIN_VERIFIED_CALIBRATION_IONS: int = 2
    # Summed calibrant intensity as a fraction of the TIC. Orbitrap only:
    # healthy TOF fits span 5e-7 to 0.1, so no bar is set for TOF (None
    # disables the check). On Orbitrap, good fits reach down to 1.3e-4.
    ORBI_MIN_CALIBRANT_TO_TIC: float | None = 1e-4
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
