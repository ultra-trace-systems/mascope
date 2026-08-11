"""Unit tests for the acquisition-drift warning."""

from mascope_backend.api.controllers.calibration.calibration_controller import (
    warn_on_acquisition_drift,
)
from mascope_backend.runtime import runtime


def _capture(fit, instrument="ORBI-1", filename="ORBI-1_file.raw"):
    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        warn_on_acquisition_drift(fit, instrument, filename)
    finally:
        runtime.logger.remove(sink_id)
    return records


def _warnings(records):
    return [r for r in records if r["level"].name == "WARNING"]


class TestWarnOnAcquisitionDrift:
    def test_drift_beyond_threshold_warns(self):
        records = _capture(
            {"quality": {"pre_fit_mz_error_ppm": -12.53}}, instrument="ORBI-1"
        )

        warnings = _warnings(records)
        assert len(warnings) == 1
        assert "ORBI-1" in warnings[0]["message"]
        assert "-13 ppm" in warnings[0]["message"]
        # Per-file detail stays below the monitoring threshold.
        detail = [r for r in records if "drift detail" in r["message"]]
        assert len(detail) == 1
        assert detail[0]["level"].name == "INFO"
        assert "ORBI-1_file.raw" in detail[0]["message"]

    def test_drift_within_threshold_is_silent(self):
        assert _warnings(_capture({"quality": {"pre_fit_mz_error_ppm": 4.2}})) == []

    def test_drift_exactly_at_threshold_is_silent(self):
        assert _warnings(_capture({"quality": {"pre_fit_mz_error_ppm": 10.0}})) == []

    def test_positive_drift_warns_too(self):
        warnings = _warnings(_capture({"quality": {"pre_fit_mz_error_ppm": 12.5}}))
        assert len(warnings) == 1
        assert (
            "+12 ppm" in warnings[0]["message"] or "+13 ppm" in warnings[0]["message"]
        )

    def test_missing_quality_is_silent(self):
        assert _warnings(_capture({"mode": "one-point"})) == []
        assert _warnings(_capture(None)) == []
        assert _warnings(_capture({"quality": {}})) == []
