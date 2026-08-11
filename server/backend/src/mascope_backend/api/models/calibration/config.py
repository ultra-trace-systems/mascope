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
