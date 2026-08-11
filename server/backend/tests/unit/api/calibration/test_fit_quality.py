"""Unit tests for the fit_quality summary stored with applied calibrations."""

import numpy as np

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    fit_quality,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    OrbiCalibrationParams,
)


def _stats_with_summary():
    """Two per-point rows plus the aggregate summary row handlers append."""
    return [
        {"mz": 61.0, "match_mz_error": -12.5, "calibration_mz_error": 0.4},
        {"mz": 63.0, "match_mz_error": -12.3, "calibration_mz_error": -0.4},
        {
            "match_mz_error": np.float64(12.4),
            "calibration_mz_error": np.float64(0.4),
            "mz_error_diff": np.float64(12.0),
            "calibrant_to_tic": np.float64(0.35),
        },
    ]


class TestFitQuality:
    def test_summarizes_points_and_errors(self):
        quality = fit_quality(_stats_with_summary(), OrbiCalibrationParams())

        assert quality["n_points"] == 2
        assert quality["pre_fit_mz_error_ppm"] == 12.4
        assert quality["post_fit_mz_error_ppm"] == 0.4
        assert quality["calibrant_to_tic"] == 0.35
        assert quality["mz_error_tolerance"] == 5
        assert quality["refine_window"] == 50

    def test_values_are_json_safe_builtins(self):
        """numpy scalars must not leak into the JSON column."""
        quality = fit_quality(_stats_with_summary(), OrbiCalibrationParams())

        for value in quality.values():
            assert value is None or type(value) in (int, float)

    def test_nan_summary_values_become_none(self):
        stats = [
            {"mz": 61.0},
            {"match_mz_error": float("nan"), "calibration_mz_error": float("inf")},
        ]

        quality = fit_quality(stats, OrbiCalibrationParams())

        assert quality["n_points"] == 1
        assert quality["pre_fit_mz_error_ppm"] is None
        assert quality["post_fit_mz_error_ppm"] is None

    def test_stats_without_summary_row_yield_point_count_only(self):
        stats = [{"mz": 61.0}, {"mz": 63.0}]

        quality = fit_quality(stats, None)

        assert quality["n_points"] == 2
        assert quality["pre_fit_mz_error_ppm"] is None
        assert quality["mz_error_tolerance"] is None

    def test_no_stats_yield_none(self):
        assert fit_quality(None, OrbiCalibrationParams()) is None
        assert fit_quality([], OrbiCalibrationParams()) is None
