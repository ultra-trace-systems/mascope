"""Unit tests for the acquisition-drift warning."""

import pytest

from mascope_backend.api.controllers.calibration.calibration_controller import (
    acquisition_drift_limit_ppm,
    acquisition_drift_ppm,
    carry_acquisition_drift,
    warn_on_acquisition_drift,
)
from mascope_backend.runtime import runtime


ORBI_FILE = "ORBI-1_file.raw"
TOF_FILE = "TOF-1_file.h5"


def _capture(fit, instrument="ORBI-1", filename=ORBI_FILE):
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


class TestAcquisitionDriftLimitPpm:
    def test_orbi_file_gets_default_limit(self):
        assert acquisition_drift_limit_ppm(ORBI_FILE) == 10.0

    def test_tof_file_gets_tof_limit(self):
        assert acquisition_drift_limit_ppm(TOF_FILE) == 50.0

    def test_unresolvable_instrument_gets_default_limit(self):
        assert acquisition_drift_limit_ppm("MYSTERY-1_file.raw") == 10.0


class TestWarnOnAcquisitionDrift:
    def test_drift_beyond_threshold_warns(self):
        records = _capture(
            {"quality": {"pre_fit_mz_error_ppm": -12.53}}, instrument="ORBI-1"
        )

        warnings = _warnings(records)
        assert len(warnings) == 1
        assert "ORBI-1" in warnings[0]["message"]
        assert "beyond 10 ppm" in warnings[0]["message"]
        # Per-file detail stays below the monitoring threshold.
        detail = [r for r in records if "drift detail" in r["message"]]
        assert len(detail) == 1
        assert detail[0]["level"].name == "INFO"
        assert "ORBI-1_file.raw" in detail[0]["message"]
        assert "-12.53 ppm" in detail[0]["message"]

    def test_warning_text_is_identical_across_magnitudes(self):
        # Monitoring groups events by message: the magnitude of ongoing
        # drift wanders file-to-file, so it must not enter the message or
        # each ppm value becomes its own issue.
        first = _warnings(_capture({"quality": {"pre_fit_mz_error_ppm": 84.0}}))
        second = _warnings(_capture({"quality": {"pre_fit_mz_error_ppm": 122.4}}))
        assert first[0]["message"] == second[0]["message"]
        assert "pre-calibration error" not in first[0]["message"]

    def test_drift_within_threshold_is_silent(self):
        assert _warnings(_capture({"quality": {"pre_fit_mz_error_ppm": 4.2}})) == []

    def test_drift_exactly_at_threshold_is_silent(self):
        assert _warnings(_capture({"quality": {"pre_fit_mz_error_ppm": 10.0}})) == []

    def test_tof_drift_within_tof_threshold_is_silent(self):
        records = _capture(
            {"quality": {"pre_fit_mz_error_ppm": 30.0}},
            instrument="TOF-1",
            filename=TOF_FILE,
        )
        assert _warnings(records) == []

    def test_tof_drift_beyond_tof_threshold_warns(self):
        records = _capture(
            {"quality": {"pre_fit_mz_error_ppm": 84.0}},
            instrument="TOF-1",
            filename=TOF_FILE,
        )
        warnings = _warnings(records)
        assert len(warnings) == 1
        assert "beyond 50 ppm" in warnings[0]["message"]

    def test_missing_quality_is_silent(self):
        assert _warnings(_capture({"mode": "one-point"})) == []
        assert _warnings(_capture(None)) == []
        assert _warnings(_capture({"quality": {}})) == []


class TestAcquisitionDriftPpm:
    def test_returns_drift_beyond_threshold(self):
        assert acquisition_drift_ppm(
            {"quality": {"pre_fit_mz_error_ppm": -12.5}}, 10.0
        ) == pytest.approx(-12.5)

    def test_none_within_threshold_or_unknown(self):
        assert (
            acquisition_drift_ppm({"quality": {"pre_fit_mz_error_ppm": 9.9}}, 10.0)
            is None
        )
        assert (
            acquisition_drift_ppm({"quality": {"pre_fit_mz_error_ppm": 30.0}}, 50.0)
            is None
        )
        assert acquisition_drift_ppm({"quality": {}}, 10.0) is None
        assert acquisition_drift_ppm(None, 10.0) is None


class TestCarryAcquisitionDrift:
    def test_fresh_drift_is_stamped_with_magnitude(self):
        fit = {"quality": {"pre_fit_mz_error_ppm": 12.6}}
        carry_acquisition_drift(fit, None, ORBI_FILE)

        assert fit["acquisition_drift"] is True
        assert fit["acquisition_drift_ppm"] == pytest.approx(12.6)

    def test_marker_survives_recalibration_on_corrected_axis(self):
        fit = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
        previous = {"acquisition_drift": True, "acquisition_drift_ppm": 12.6}
        carry_acquisition_drift(fit, previous, ORBI_FILE)

        assert fit["acquisition_drift"] is True
        assert fit["acquisition_drift_ppm"] == pytest.approx(12.6)

    def test_marker_within_loosened_threshold_is_dropped(self):
        # A TOF record flagged under the previous global 10 ppm threshold
        # sheds the marker once its magnitude sits within the TOF limit.
        fit = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
        previous = {"acquisition_drift": True, "acquisition_drift_ppm": 12.6}
        carry_acquisition_drift(fit, previous, TOF_FILE)

        assert "acquisition_drift" not in fit

    def test_marker_beyond_tof_threshold_is_carried_for_tof(self):
        fit = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
        previous = {"acquisition_drift": True, "acquisition_drift_ppm": 84.0}
        carry_acquisition_drift(fit, previous, TOF_FILE)

        assert fit["acquisition_drift"] is True
        assert fit["acquisition_drift_ppm"] == pytest.approx(84.0)

    def test_legacy_marker_recovers_magnitude_from_quality(self):
        fit = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
        previous = {
            "acquisition_drift": True,
            "quality": {"pre_fit_mz_error_ppm": 12.6},
        }
        carry_acquisition_drift(fit, previous, ORBI_FILE)

        assert fit["acquisition_drift"] is True
        assert fit["acquisition_drift_ppm"] == pytest.approx(12.6)

    def test_legacy_marker_without_magnitude_is_carried(self):
        fit = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
        previous = {"acquisition_drift": True}
        carry_acquisition_drift(fit, previous, ORBI_FILE)

        assert fit["acquisition_drift"] is True
        assert fit["acquisition_drift_ppm"] is None

    def test_no_marker_without_drift_history(self):
        fit = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
        carry_acquisition_drift(fit, {"status": "ok", "verified": True}, ORBI_FILE)

        assert "acquisition_drift" not in fit
