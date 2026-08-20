"""Unit tests for the acquisition-drift warning."""

import pytest

from mascope_backend.api.controllers.calibration import calibration_controller
from mascope_backend.api.controllers.calibration.calibration_controller import (
    _drift_warning_due,
    acquisition_drift_limit_ppm,
    acquisition_drift_ppm,
    carry_acquisition_drift,
    warn_on_acquisition_drift,
)
from mascope_backend.api.models.calibration.config import calibration_config
from mascope_backend.runtime import runtime


ORBI_FILE = "ORBI-1_file.raw"
TOF_FILE = "TOF-1_file.h5"


@pytest.fixture(autouse=True)
def reset_drift_suppression():
    """Give every test an empty suppression window to start from."""
    calibration_controller._drift_warned_at.clear()
    yield
    calibration_controller._drift_warned_at.clear()


def _observe(fit, instrument="ORBI-1", filename=ORBI_FILE):
    """Run one drift observation, returning the log records it produced."""
    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        warn_on_acquisition_drift(fit, instrument, filename)
    finally:
        runtime.logger.remove(sink_id)
    return records


def _capture(fit, instrument="ORBI-1", filename=ORBI_FILE):
    """A *first* observation of this drift: suppression window cleared first.

    Tests of threshold and message content each want a fresh episode; the
    once-a-day window is exercised separately in
    :class:`TestDriftWarningSuppression`.
    """
    calibration_controller._drift_warned_at.clear()
    return _observe(fit, instrument, filename)


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


class TestDriftWarningSuppression:
    """Drift persists until an instrument is retuned, so it is reported on a
    schedule rather than per file. One production Orbitrap logged the warning
    172 times in 19 hours, burying unrelated errors under the repeats.
    """

    DRIFTING = {"quality": {"pre_fit_mz_error_ppm": -12.53}}

    def test_first_observation_warns(self):
        assert len(_warnings(_observe(self.DRIFTING))) == 1

    def test_repeat_within_the_window_is_suppressed(self):
        _observe(self.DRIFTING)
        assert _warnings(_observe(self.DRIFTING)) == []

    def test_the_per_file_detail_survives_suppression(self):
        # Suppression is about the monitoring event; the drill-down still
        # needs a line for every affected file, magnitude included.
        _observe(self.DRIFTING)
        records = _observe({"quality": {"pre_fit_mz_error_ppm": -14.1}})
        detail = [r for r in records if "drift detail" in r["message"]]
        assert len(detail) == 1
        assert detail[0]["level"].name == "INFO"
        assert "-14.10 ppm" in detail[0]["message"]

    def test_another_instrument_is_not_suppressed(self):
        # The window is keyed per instrument - one drifting instrument must
        # not mask another.
        _observe(self.DRIFTING, instrument="ORBI-1")
        warnings = _warnings(_observe(self.DRIFTING, instrument="ORBI-2"))
        assert len(warnings) == 1
        assert "ORBI-2" in warnings[0]["message"]

    def test_an_unnamed_instrument_is_never_suppressed(self):
        # With no name to key on, every such warning would share one entry,
        # so the first unnamed instrument to drift would hide every other one
        # for a day. Repeats are the lesser harm: monitoring groups them into
        # a single issue anyway, since the message text is identical.
        _observe(self.DRIFTING, instrument=None)
        assert len(_warnings(_observe(self.DRIFTING, instrument=None))) == 1

    def test_warning_returns_after_the_window_elapses(self):
        # Still drifting a day later is worth saying again - the reminder is
        # what keeps a needed retune from being forgotten. Backdate the
        # recorded entry rather than patching the clock: `time.monotonic` is
        # an attribute of the stdlib module, so patching it there freezes it
        # for every other thread in the interpreter too.
        assert len(_warnings(_observe(self.DRIFTING))) == 1
        assert _warnings(_observe(self.DRIFTING)) == []

        window = calibration_config.ACQUISITION_DRIFT_WARNING_INTERVAL_S
        for key in list(calibration_controller._drift_warned_at):
            calibration_controller._drift_warned_at[key] -= window
        assert len(_warnings(_observe(self.DRIFTING))) == 1

    def test_the_window_is_a_day(self):
        # A shorter window reintroduces the flood; a longer one lets a
        # retune-worthy instrument fall out of sight.
        assert calibration_config.ACQUISITION_DRIFT_WARNING_INTERVAL_S == 24 * 60 * 60


class TestDriftWarningDue:
    KEY = ("ORBI-1", 10.0)

    def test_first_call_is_due_and_records_the_time(self):
        assert _drift_warning_due(self.KEY, 1000.0)
        assert calibration_controller._drift_warned_at[self.KEY] == 1000.0

    def test_within_the_window_is_not_due(self):
        window = calibration_config.ACQUISITION_DRIFT_WARNING_INTERVAL_S
        _drift_warning_due(self.KEY, 1000.0)
        assert not _drift_warning_due(self.KEY, 1000.0 + window - 1)

    def test_at_the_window_boundary_is_due_again(self):
        window = calibration_config.ACQUISITION_DRIFT_WARNING_INTERVAL_S
        _drift_warning_due(self.KEY, 1000.0)
        assert _drift_warning_due(self.KEY, 1000.0 + window)

    def test_becoming_due_restarts_the_window(self):
        window = calibration_config.ACQUISITION_DRIFT_WARNING_INTERVAL_S
        _drift_warning_due(self.KEY, 1000.0)
        _drift_warning_due(self.KEY, 1000.0 + window)
        assert not _drift_warning_due(self.KEY, 1000.0 + window + 1)

    def test_the_same_instrument_at_another_threshold_is_independent(self):
        # The threshold is part of the key, so a TOF-class limit and an
        # Orbi-class limit never share a window.
        _drift_warning_due(self.KEY, 1000.0)
        assert _drift_warning_due(("ORBI-1", 50.0), 1000.0)
