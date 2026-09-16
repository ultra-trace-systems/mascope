"""Unit tests for the quality bar a fit must clear to be stored ``verified``."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.calibration.calibration_controller import (
    calibration_mz_apply,
    is_unfitted_record,
    previous_fit_moved_the_axis,
    stamp_quality_verdict,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_quality_issues,
    fit_quality,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    AffectedSampleData,
)
from mascope_backend.api.models.calibration.config import calibration_config


ORBI_FILE = "ORBI-1_file.raw"
TOF_FILE = "TOF-1_file.h5"

_CTRL = "mascope_backend.api.controllers.calibration.calibration_controller"

GOOD = {
    "n_points": 5,
    "n_ions": 3,
    "pre_fit_mz_error_ppm": 3.3,
    "post_fit_mz_error_ppm": 0.22,
    "calibrant_to_tic": 0.1,
}


def _codes(quality, filename=ORBI_FILE):
    return [issue["code"] for issue in calibration_quality_issues(quality, filename)]


class TestQualityIssues:
    def test_a_healthy_fit_clears_the_bar(self):
        assert _codes(GOOD) == []
        assert _codes(GOOD, TOF_FILE) == []

    def test_two_disagreeing_calibrants_are_not_a_calibration(self):
        # The reported Orbitrap case: two calibrants 2.5 ppm apart, the median
        # splits the difference and the residual never moves.
        quality = {
            **GOOD,
            "n_points": 2,
            "n_ions": 2,
            "pre_fit_mz_error_ppm": 1.26,
            "post_fit_mz_error_ppm": 1.26,
        }

        assert _codes(quality) == ["residual"]

    def test_a_few_points_are_trusted_with_a_small_correction(self):
        # Narrow-range and EasyIC modes routinely match one or two calibrants.
        for n_points in (1, 2):
            quality = {
                **GOOD,
                "n_points": n_points,
                "n_ions": n_points,
                "pre_fit_mz_error_ppm": 0.9,
                "post_fit_mz_error_ppm": 0.0,
            }
            assert _codes(quality) == []

    def test_a_single_calibrant_moving_the_axis_far_is_caught(self):
        # Anchored to the wrong peak: the fit moves the axis tens of ppm and
        # zeroes its own residual.
        quality = {
            **GOOD,
            "n_points": 1,
            "n_ions": 1,
            "pre_fit_mz_error_ppm": 78.4,
            "post_fit_mz_error_ppm": 0.0,
        }

        (issue,) = calibration_quality_issues(quality, ORBI_FILE)
        assert issue["code"] == "points"
        assert "78.40 ppm" in issue["message"]

    def test_few_points_with_an_unknown_shift_are_not_trusted(self):
        quality = {**GOOD, "n_points": 2, "pre_fit_mz_error_ppm": None}

        assert _codes(quality) == ["points"]

    def test_a_fit_on_no_points_is_caught(self):
        assert _codes({**GOOD, "n_points": 0}) == ["points"]

    def test_many_points_from_one_ion_are_caught(self):
        assert _codes({**GOOD, "n_points": 3, "n_ions": 1}) == ["ions"]

    def test_the_signal_bar(self):
        assert _codes({**GOOD, "calibrant_to_tic": 1.3e-4}) == []
        assert _codes({**GOOD, "calibrant_to_tic": 9.6e-5}) == ["signal"]

    def test_the_residual_bound_follows_the_instrument_class(self):
        quality = {**GOOD, "post_fit_mz_error_ppm": 2.0}

        assert _codes(quality, ORBI_FILE) == ["residual"]
        assert _codes(quality, TOF_FILE) == []
        assert _codes({**GOOD, "post_fit_mz_error_ppm": 3.5}, TOF_FILE) == ["residual"]

    def test_the_bound_itself_passes(self):
        quality = {
            **GOOD,
            "post_fit_mz_error_ppm": calibration_config.ORBI_MAX_POST_FIT_MZ_ERROR_PPM,
        }

        assert _codes(quality) == []

    def test_the_signal_bar_is_not_applied_to_tof(self):
        assert _codes({**GOOD, "calibrant_to_tic": 1e-6}, TOF_FILE) == []

    def test_values_the_block_does_not_carry_are_not_held_against_it(self):
        # Records written before n_ions was recorded, and a TIC share that
        # could not be computed.
        quality = {**GOOD}
        del quality["n_ions"]
        quality["calibrant_to_tic"] = None

        assert _codes(quality) == []

    def test_an_unknown_residual_is_not_let_through(self):
        assert _codes({**GOOD, "post_fit_mz_error_ppm": None}) == ["residual_unknown"]

    def test_a_fit_without_statistics_cannot_be_judged(self):
        assert _codes(None) == ["no_quality"]
        assert _codes({}) == ["no_quality"]

    def test_messages_name_the_measured_value_and_the_limit(self):
        (issue,) = calibration_quality_issues(
            {**GOOD, "post_fit_mz_error_ppm": -1.26}, ORBI_FILE
        )

        assert issue["message"] == (
            "Mean m/z error after calibration is 1.26 ppm (limit 1 ppm)."
        )


class TestFitQualityCountsIons:
    def test_distinct_ions_are_counted(self):
        stats = [
            {"mz": 79.0, "target_ion_id": "br"},
            {"mz": 81.0, "target_ion_id": "br"},
            {"mz": 159.0, "target_ion_id": "br2"},
            {"match_mz_error": 3.3, "calibration_mz_error": 0.2},
        ]

        assert fit_quality(stats, None)["n_ions"] == 2

    def test_rows_without_ion_ids_leave_the_count_unknown(self):
        assert fit_quality([{"mz": 79.0}], None)["n_ions"] is None


POOR = {**GOOD, "n_points": 1, "pre_fit_mz_error_ppm": 78.0}


class TestStampQualityVerdict:
    def test_a_fit_that_clears_the_bar_is_verified(self):
        fit = {"quality": dict(GOOD)}

        assert stamp_quality_verdict(fit, ORBI_FILE) == []
        assert fit["status"] == "ok"
        assert fit["verified"] is True
        assert fit["quality_issues"] == []
        assert "accepted_at" not in fit

    def test_a_fit_below_the_bar_is_stored_poor_and_unverified(self):
        fit = {"quality": dict(POOR)}

        issues = stamp_quality_verdict(fit, ORBI_FILE)

        assert [issue["code"] for issue in issues] == ["points"]
        assert fit["status"] == "poor"
        assert fit["verified"] is False
        assert fit["quality_issues"] == issues

    def test_an_accepted_fit_is_verified_but_keeps_its_issues(self):
        fit = {"quality": dict(POOR)}

        stamp_quality_verdict(fit, ORBI_FILE, accept=True, accepted_by=7)

        assert fit["status"] == "poor"
        assert fit["verified"] is True
        assert fit["quality_issues"][0]["code"] == "points"
        assert fit["accepted_by"] == 7
        assert fit["accepted_at"]

    def test_accepting_a_good_fit_records_no_acceptance(self):
        fit = {"quality": dict(GOOD)}

        stamp_quality_verdict(fit, ORBI_FILE, accept=True, accepted_by=7)

        assert fit["verified"] is True
        assert "accepted_by" not in fit

    def test_a_request_body_cannot_claim_the_verdict(self):
        # /mz_apply persists the body's fit dict verbatim otherwise.
        fit = {
            "quality": dict(POOR),
            "status": "ok",
            "verified": True,
            "quality_issues": [],
            "accepted_at": "2026-01-01T00:00:00+00:00",
            "accepted_by": 1,
        }

        stamp_quality_verdict(fit, ORBI_FILE)

        assert fit["status"] == "poor"
        assert fit["verified"] is False
        assert "accepted_at" not in fit
        assert "accepted_by" not in fit


class TestRecordKinds:
    CONVERTER = {"mode": 0, "par": [1.0, 2.0]}

    def test_converter_records_are_unfitted(self):
        assert is_unfitted_record({**self.CONVERTER, "status": "unfitted"})
        # Registered before the stamp.
        assert is_unfitted_record(self.CONVERTER)

    def test_fits_and_markers_are_not_unfitted(self):
        assert not is_unfitted_record(None)
        assert not is_unfitted_record({"status": "ok", "verified": True})
        assert not is_unfitted_record({"status": "poor", "verified": False})
        assert not is_unfitted_record({"status": "failed", "verified": False})
        # Applied before the status field existed.
        assert not is_unfitted_record({"mode": "one-point", "verified": True})
        assert not is_unfitted_record({"mode": 2, "par": [1.0], "verified": True})
        assert not is_unfitted_record({"mode": 2, "par": [1.0], "quality": {}})
        # Not the converter's shape at all.
        assert not is_unfitted_record({"acquisition_drift": True})

    def test_a_poor_fit_moved_the_axis(self):
        assert previous_fit_moved_the_axis({"status": "poor", "verified": False})
        assert previous_fit_moved_the_axis({"status": "poor", "verified": True})

    def test_a_converter_record_did_not_move_the_axis(self):
        assert not previous_fit_moved_the_axis(
            {**self.CONVERTER, "status": "unfitted", "verified": False}
        )
        assert not previous_fit_moved_the_axis(self.CONVERTER)


async def _apply(fit: dict, accept: bool = False) -> tuple[dict, dict]:
    """Apply ``fit`` to a file with no previous record.

    :return: The persisted record and the controller's result.
    """
    sample_file = SimpleNamespace(
        sample_file_id="sf-1",
        filename=ORBI_FILE,
        instrument="ORBI-1",
        mz_calibration=None,
        range=None,
        to_dict=dict,
    )
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
        result = await calibration_mz_apply.__wrapped__(
            fit=fit,
            filename=ORBI_FILE,
            manual=True,
            accept_quality_issues=accept,
            user_id=7,
        )
    return sample_file.mz_calibration, result


class TestApply:
    @pytest.mark.asyncio
    async def test_a_good_fit_is_announced_as_a_success(self):
        stored, result = await _apply({"quality": dict(GOOD)})

        assert stored["verified"] is True
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_a_poor_fit_is_applied_unverified_and_announced_as_a_warning(self):
        stored, result = await _apply({"quality": dict(POOR)})

        assert stored["status"] == "poor"
        assert stored["verified"] is False
        assert result["status"] == "partial"
        assert "excluded from matching" in result["message"]
        assert "Fitted on 1 calibration point" in result["message"]

    @pytest.mark.asyncio
    async def test_an_accepted_poor_fit_is_stored_verified(self):
        stored, result = await _apply({"quality": dict(POOR)}, True)

        assert stored["status"] == "poor"
        assert stored["verified"] is True
        assert stored["accepted_by"] == 7
        assert result["status"] == "success"
