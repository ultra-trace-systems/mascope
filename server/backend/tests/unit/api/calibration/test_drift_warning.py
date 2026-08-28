"""Unit tests for the acquisition-drift warning."""

import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.calibration import calibration_controller
from mascope_backend.api.controllers.calibration.calibration_controller import (
    _drift_warning_due,
    acquisition_drift_limit_ppm,
    acquisition_drift_ppm,
    calibration_mz_apply,
    calibration_mz_calibrate_sample,
    carry_acquisition_drift,
    clear_drift_suppression,
    previous_fit_moved_the_axis,
    warn_on_acquisition_drift,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    AffectedSampleData,
)
from mascope_backend.api.models.calibration.config import calibration_config
from mascope_backend.runtime import runtime


ORBI_FILE = "ORBI-1_file.raw"
TOF_FILE = "TOF-1_file.h5"

_CTRL = "mascope_backend.api.controllers.calibration.calibration_controller"


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
        # The automatic pipeline's recalibration: it runs on the axis the
        # previous fit already corrected, so its near-zero pre-fit error says
        # nothing about how the file was acquired and must not clear the
        # marker. (The manual path deliberately differs - see
        # TestManualRecalibrationClearsTheMarker.)
        fit = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
        previous = {"acquisition_drift": True, "acquisition_drift_ppm": 12.6}
        cleared = carry_acquisition_drift(fit, previous, ORBI_FILE)

        assert cleared is False
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


class TestManualRecalibrationClearsTheMarker:
    """A human recalibrating asserts the previous fit was wrong.

    The carry assumes the fit that raised the flag was correct. When it was
    not - a mis-calibration inflates ``pre_fit_mz_error_ppm`` - the marker
    describes that bad fit rather than the instrument, and carrying it pins a
    bogus ppm value to the badge for the life of the file. Manual
    recalibration is the one signal the backend has that the assumption
    failed, so it drops the carried marker.

    It also stops trusting this fit's own pre-fit error: once a fit has been
    applied, a later one measures the axis that fit set, not the acquisition
    axis, so what it sees is the overruled fit's residual.
    """

    CLEAN = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
    FLAGGED = {"status": "ok", "acquisition_drift": True, "acquisition_drift_ppm": 12.6}

    def test_carried_marker_is_dropped(self):
        fit = dict(self.CLEAN)
        cleared = carry_acquisition_drift(
            fit, self.FLAGGED, ORBI_FILE, manual=True, cleared_by=7
        )

        assert cleared is True
        assert "acquisition_drift" not in fit
        assert "acquisition_drift_ppm" not in fit

    def test_clearing_is_recorded_for_the_audit_trail(self):
        fit = dict(self.CLEAN)
        carry_acquisition_drift(fit, self.FLAGGED, ORBI_FILE, manual=True, cleared_by=7)

        assert fit["drift_cleared_by"] == 7
        # An aware UTC timestamp, JSON-serializable for the mz_calibration
        # column it is persisted into.
        assert datetime.fromisoformat(fit["drift_cleared_at"]).tzinfo is not None

    def test_an_unknown_operator_still_records_the_clear(self):
        fit = dict(self.CLEAN)
        carry_acquisition_drift(fit, self.FLAGGED, ORBI_FILE, manual=True)

        assert fit["drift_cleared_by"] is None
        assert "drift_cleared_at" in fit

    def test_a_legacy_marker_without_magnitude_is_dropped(self):
        # Records predating acquisition_drift_ppm are carried blind by the
        # automatic path; a manual recalibration overrules them the same way.
        fit = dict(self.CLEAN)
        cleared = carry_acquisition_drift(
            fit, {"acquisition_drift": True}, ORBI_FILE, manual=True
        )

        assert cleared is True
        assert "acquisition_drift" not in fit

    def test_a_displaced_axis_does_not_re_raise_the_marker(self):
        # The reported case, and the one a clean-fit test cannot reach: a
        # mis-calibration moved the axis by the bogus correction it applied,
        # so the recalibration that retracts it measures that displacement as
        # its own pre-fit error. Attributing it to the instrument would
        # re-raise the flag on the calibration meant to clear it - quoting the
        # operator's own bad correction - and leave the badge amber until a
        # second recalibration finally ran on a correct axis.
        fit = {"quality": {"pre_fit_mz_error_ppm": 12.53}}
        cleared = carry_acquisition_drift(fit, self.FLAGGED, ORBI_FILE, manual=True)

        assert cleared is True
        assert "acquisition_drift" not in fit
        assert "acquisition_drift_ppm" not in fit
        assert "drift_cleared_at" in fit

    def test_genuine_drift_on_the_acquisition_axis_is_still_flagged(self):
        # Nothing has moved this file's axis, so the pre-fit error means what
        # it says: a manual first calibration flags a drifting instrument
        # exactly as the automatic pipeline does.
        fit = {"quality": {"pre_fit_mz_error_ppm": -12.53}}
        cleared = carry_acquisition_drift(fit, None, ORBI_FILE, manual=True)

        assert cleared is False
        assert fit["acquisition_drift"] is True
        assert fit["acquisition_drift_ppm"] == pytest.approx(-12.53)
        assert "drift_cleared_at" not in fit

    def test_drift_after_a_given_up_calibration_is_still_flagged(self):
        # A calibration the pipeline gave up on leaves a marker record but
        # never touches the axis, so the file is still on the one it was
        # acquired with and this fit observes the instrument.
        fit = {"quality": {"pre_fit_mz_error_ppm": -12.53}}
        failed = {"status": "failed", "verified": False, "error": "no calibrants"}
        carry_acquisition_drift(fit, failed, ORBI_FILE, manual=True)

        assert fit["acquisition_drift"] is True

    def test_nothing_to_clear_leaves_no_audit_trail(self):
        fit = dict(self.CLEAN)
        cleared = carry_acquisition_drift(
            fit, {"status": "ok", "verified": True}, ORBI_FILE, manual=True
        )

        assert cleared is False
        assert "drift_cleared_at" not in fit
        assert "drift_cleared_by" not in fit


class TestPreviousFitMovedTheAxis:
    """Only an applied fit displaces the axis a later fit measures against."""

    def test_an_applied_fit_moved_it(self):
        assert previous_fit_moved_the_axis({"status": "ok", "verified": True})

    def test_no_record_at_all_leaves_the_acquisition_axis(self):
        assert not previous_fit_moved_the_axis(None)
        assert not previous_fit_moved_the_axis({})

    def test_a_given_up_calibration_leaves_the_acquisition_axis(self):
        # The pipeline records the give-up so the sample browser can show it,
        # but it never touched the file.
        assert not previous_fit_moved_the_axis({"status": "failed", "verified": False})

    def test_a_record_that_says_neither_is_treated_as_applied(self):
        # Being wrong this way costs one drift observation; the other way
        # costs a badge that blames the instrument for a bad fit.
        assert previous_fit_moved_the_axis({"acquisition_drift": True})


class TestTheIncomingFitCannotSetItsOwnMarker:
    """``/mz_apply`` persists the body's fit dict with unknown keys intact.

    The marker and its audit fields are the backend's verdict on the file, so
    they are dropped from whatever arrives and re-derived here - otherwise a
    request could pin an amber badge on a clean file, or forge a clear.
    """

    SPOOFED = {
        "quality": {"pre_fit_mz_error_ppm": 0.35},
        "acquisition_drift": True,
        "acquisition_drift_ppm": 99.9,
        "drift_cleared_at": "2020-01-01T00:00:00+00:00",
        "drift_cleared_by": 1,
    }

    def test_a_supplied_marker_is_not_persisted(self):
        fit = dict(self.SPOOFED)
        carry_acquisition_drift(fit, None, ORBI_FILE)

        assert "acquisition_drift" not in fit
        assert "acquisition_drift_ppm" not in fit

    def test_a_supplied_audit_trail_is_not_persisted(self):
        fit = dict(self.SPOOFED)
        carry_acquisition_drift(fit, None, ORBI_FILE)

        assert "drift_cleared_at" not in fit
        assert "drift_cleared_by" not in fit

    def test_the_carried_marker_still_wins(self):
        # Dropping the supplied keys does not drop the record's own history.
        fit = dict(self.SPOOFED)
        carry_acquisition_drift(
            fit, {"acquisition_drift": True, "acquisition_drift_ppm": 12.6}, ORBI_FILE
        )

        assert fit["acquisition_drift_ppm"] == pytest.approx(12.6)

    def test_a_fit_without_quality_is_not_flagged_by_the_body(self):
        fit = {"acquisition_drift": True, "acquisition_drift_ppm": 99.9}
        carry_acquisition_drift(fit, None, ORBI_FILE)

        assert "acquisition_drift" not in fit


class TestClearDriftSuppression:
    def test_every_threshold_for_the_instrument_is_forgotten(self):
        # The stale entry may have been recorded under either class limit, and
        # the next observation need not use the same one.
        calibration_controller._drift_warned_at.update(
            {("ORBI-1", 10.0): 1000.0, ("ORBI-1", 50.0): 1000.0}
        )
        clear_drift_suppression("ORBI-1")

        assert calibration_controller._drift_warned_at == {}

    def test_other_instruments_are_left_alone(self):
        calibration_controller._drift_warned_at.update(
            {("ORBI-1", 10.0): 1000.0, ("ORBI-2", 10.0): 1000.0}
        )
        clear_drift_suppression("ORBI-1")

        assert list(calibration_controller._drift_warned_at) == [("ORBI-2", 10.0)]

    def test_a_missing_instrument_name_is_a_no_op(self):
        # Unattributable warnings are never recorded as a window, so there is
        # nothing keyed under an empty name to clear - and clearing "all
        # unnamed" would be indiscriminate.
        calibration_controller._drift_warned_at.update({("ORBI-1", 10.0): 1000.0})
        clear_drift_suppression(None)
        clear_drift_suppression("")

        assert list(calibration_controller._drift_warned_at) == [("ORBI-1", 10.0)]

    def test_the_instrument_warns_again_afterwards(self):
        drifting = {"quality": {"pre_fit_mz_error_ppm": -12.53}}
        assert len(_warnings(_observe(drifting))) == 1
        assert _warnings(_observe(drifting)) == []

        clear_drift_suppression("ORBI-1")

        assert len(_warnings(_observe(drifting))) == 1


def _sample_file(previous: dict | None) -> SimpleNamespace:
    """The fields ``calibration_mz_apply`` reads off the sample file."""
    return SimpleNamespace(
        sample_file_id="sf-1",
        filename=ORBI_FILE,
        instrument="ORBI-1",
        mz_calibration=previous,
        range=None,
        to_dict=dict,
    )


async def _apply(fit: dict, previous: dict | None, manual: bool) -> dict:
    """Apply a fit over ``previous``, returning the record that was persisted.

    Drives the undecorated controller with the file, database and Socket.IO
    calls mocked out - the point of interest is which drift marker ends up on
    ``sample_file.mz_calibration``.
    """
    sample_file = _sample_file(previous)
    handler = SimpleNamespace(apply=AsyncMock())
    sum_signal = SimpleNamespace(mz=SimpleNamespace(values=[100.0, 200.0]))
    with (
        patch(f"{_CTRL}.fetch_sample_file", AsyncMock(return_value=sample_file)),
        patch(
            f"{_CTRL}.fetch_affected_sample_data",
            AsyncMock(return_value=AffectedSampleData([], [], [], [])),
        ),
        patch(f"{_CTRL}.update_sample_batch_status", AsyncMock()),
        patch(f"{_CTRL}.send_progress_user_notification", AsyncMock()),
        patch(f"{_CTRL}.get_calibration_handler", MagicMock(return_value=handler)),
        patch(f"{_CTRL}.get_sum_signal", MagicMock(return_value=sum_signal)),
        patch(f"{_CTRL}.update_sample_file", AsyncMock()),
        patch(f"{_CTRL}.SampleFileUpdate", MagicMock()),
    ):
        await calibration_mz_apply.__wrapped__(
            fit=fit, filename=ORBI_FILE, manual=manual, user_id=7
        )
    return sample_file.mz_calibration


class TestApplyRoutesTheMarkerByPath:
    """``calibration_mz_apply`` is the single carry site both paths share.

    The routes behind the calibration dialog pass ``manual=True``; the
    automatic pipeline (``calibrate_with_retry``) leaves the default. These
    tests pin that the flag survives the trip to the persisted record.
    """

    CLEAN = {"quality": {"pre_fit_mz_error_ppm": 0.35}}
    FLAGGED = {"acquisition_drift": True, "acquisition_drift_ppm": 12.6}
    WINDOW = ("ORBI-1", 10.0)

    @pytest.mark.asyncio
    async def test_manual_apply_persists_a_cleared_marker(self):
        stored = await _apply(dict(self.CLEAN), self.FLAGGED, manual=True)

        assert "acquisition_drift" not in stored
        assert stored["drift_cleared_by"] == 7
        assert stored["verified"] is True

    @pytest.mark.asyncio
    async def test_automatic_apply_persists_the_carried_marker(self):
        stored = await _apply(dict(self.CLEAN), self.FLAGGED, manual=False)

        assert stored["acquisition_drift"] is True
        assert stored["acquisition_drift_ppm"] == pytest.approx(12.6)
        assert "drift_cleared_at" not in stored

    @pytest.mark.asyncio
    async def test_manual_apply_retires_the_stale_suppression_window(self):
        # The warning that opened the window came from the fit the operator
        # has just overruled, so leaving it would hide a genuine drift on this
        # instrument for the rest of the day.
        calibration_controller._drift_warned_at[self.WINDOW] = time.monotonic()

        await _apply(dict(self.CLEAN), self.FLAGGED, manual=True)

        assert self.WINDOW not in calibration_controller._drift_warned_at

    @pytest.mark.asyncio
    async def test_automatic_apply_keeps_the_suppression_window(self):
        calibration_controller._drift_warned_at[self.WINDOW] = time.monotonic()

        await _apply(dict(self.CLEAN), self.FLAGGED, manual=False)

        assert self.WINDOW in calibration_controller._drift_warned_at

    @pytest.mark.asyncio
    async def test_manual_apply_on_a_displaced_axis_clears_and_retires_the_window(self):
        # The mis-calibration case end to end: the recalibration's own pre-fit
        # error is the displacement the bad fit left, so the record comes out
        # unflagged and the window that fit's warning opened is retired with
        # it - a genuine drift on this instrument can be reported again.
        calibration_controller._drift_warned_at[self.WINDOW] = time.monotonic()

        stored = await _apply(
            {"quality": {"pre_fit_mz_error_ppm": 12.53}}, self.FLAGGED, manual=True
        )

        assert "acquisition_drift" not in stored
        assert stored["drift_cleared_by"] == 7
        assert self.WINDOW not in calibration_controller._drift_warned_at

    @pytest.mark.asyncio
    async def test_automatic_apply_on_a_displaced_axis_keeps_flagging(self):
        # Nobody has overruled the previous fit here, so a pre-fit error this
        # far out is still reported as it always was.
        stored = await _apply(
            {"quality": {"pre_fit_mz_error_ppm": 12.53}}, self.FLAGGED, manual=False
        )

        assert stored["acquisition_drift"] is True
        assert stored["acquisition_drift_ppm"] == pytest.approx(12.53)

    @pytest.mark.asyncio
    async def test_a_manual_apply_that_clears_nothing_keeps_the_window(self):
        # Recalibrating a genuinely drifting batch by hand runs this per
        # sample. Resetting the window on each of them would restore the flood
        # of one warning per file that the window exists to collapse.
        calibration_controller._drift_warned_at[self.WINDOW] = time.monotonic()

        await _apply({"quality": {"pre_fit_mz_error_ppm": -12.53}}, None, manual=True)

        assert self.WINDOW in calibration_controller._drift_warned_at


async def _calibrate_sample(fit: dict, previous: dict | None, manual: bool) -> list:
    """Fit and apply one sample through the controller, returning the logs.

    The apply step is stubbed down to the only part the warning depends on -
    the verdict ``carry_acquisition_drift`` writes onto the fit - so the test
    exercises the real decision without a database or a raw file.
    """
    sample = SimpleNamespace(
        sample_item_id="si-1",
        sample_item_name="sample 1",
        sample_file_id="sf-1",
        filename=ORBI_FILE,
        instrument="ORBI-1",
    )

    async def _apply_stub(**kwargs):
        carry_acquisition_drift(
            kwargs["fit"], previous, ORBI_FILE, manual=kwargs["manual"]
        )

    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        with (
            patch(f"{_CTRL}.fetch_sample", AsyncMock(return_value=sample)),
            patch(
                f"{_CTRL}.fetch_affected_sample_data",
                AsyncMock(return_value=AffectedSampleData([], [], [], [])),
            ),
            patch(f"{_CTRL}.send_progress_user_notification", AsyncMock()),
            patch(
                f"{_CTRL}.calibration_mz_fit",
                AsyncMock(return_value={"data": {"fit": fit}}),
            ),
            patch(f"{_CTRL}.calibration_mz_apply", AsyncMock(side_effect=_apply_stub)),
        ):
            await calibration_mz_calibrate_sample.__wrapped__(
                sample_item_id="si-1",
                mz_calibration_params=None,
                manual=manual,
                user_id=7,
            )
    finally:
        runtime.logger.remove(sink_id)
    return records


class TestTheWarningFollowsTheMarker:
    """One verdict serves the badge and the monitoring warning alike.

    A manual recalibration measures its pre-fit error on the axis the fit it
    overrules already moved. Reporting that as instrument drift would tell the
    operator to retune over their own bad correction - and burn the once-a-day
    warning slot the clear has just freed on it.
    """

    DISPLACED = {"quality": {"pre_fit_mz_error_ppm": 12.53}}
    FLAGGED = {"status": "ok", "acquisition_drift": True, "acquisition_drift_ppm": 12.6}

    @pytest.mark.asyncio
    async def test_a_manual_recalibration_does_not_blame_the_instrument(self):
        records = await _calibrate_sample(
            dict(self.DISPLACED), self.FLAGGED, manual=True
        )

        assert _warnings(records) == []

    @pytest.mark.asyncio
    async def test_drift_on_the_acquisition_axis_still_warns(self):
        records = await _calibrate_sample(dict(self.DISPLACED), None, manual=True)

        assert len(_warnings(records)) == 1

    @pytest.mark.asyncio
    async def test_the_automatic_pipeline_still_warns(self):
        records = await _calibrate_sample(dict(self.DISPLACED), None, manual=False)

        assert len(_warnings(records)) == 1


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
