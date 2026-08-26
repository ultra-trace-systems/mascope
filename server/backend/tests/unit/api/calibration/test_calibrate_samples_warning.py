"""
The batch m/z calibration warning names the samples it could not calibrate.

``calibration_mz_calibrate_samples`` collects a record per failed sample, but
the notification pane renders only type, status and message, and nothing in
the tree consumes the ``samples_calibrate_failed`` payload - so a bare count
left the user with no way to tell which samples came back uncalibrated.

The tests drive the undecorated controller through ``__wrapped__`` and mock
the per-sample calibration, so neither a database nor a Socket.IO server is
involved.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.controllers.calibration import calibration_controller
from mascope_backend.api.controllers.calibration.calibration_controller import (
    calibration_mz_calibrate_samples,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    MzCalibrationParams,
)


_CTRL = "mascope_backend.api.controllers.calibration.calibration_controller"


def _fetched_sample(sample_item_id: str) -> SimpleNamespace:
    """The fields the controller reads off a sample it failed to calibrate."""
    return SimpleNamespace(
        sample_item_id=sample_item_id,
        sample_item_name=f"sample {sample_item_id}",
        filename=f"{sample_item_id}.raw",
    )


async def _calibrate_batch(
    sample_item_ids: list[str], failing: set[str]
) -> ApiException:
    """Calibrate a batch in which ``failing`` fails, returning the warning."""

    async def _one_sample(sample_item_id: str, **kwargs) -> dict:
        if sample_item_id in failing:
            raise ApiException(
                f"Not enough calibration peaks for {sample_item_id}", {}, 200
            )
        return {"_notification_data": {"affected_sample_item_ids": [sample_item_id]}}

    with (
        patch(
            f"{_CTRL}.calibration_mz_calibrate_sample",
            AsyncMock(side_effect=_one_sample),
        ),
        patch(
            f"{_CTRL}.fetch_sample",
            AsyncMock(
                side_effect=lambda sample_item_id: _fetched_sample(sample_item_id)
            ),
        ),
        patch(
            f"{_CTRL}.fetch_affected_sample_data",
            AsyncMock(return_value=(None, ["sb-1"], None, None)),
        ),
        patch(f"{_CTRL}.send_progress_user_notification", AsyncMock()),
        pytest.raises(ApiException) as excinfo,
    ):
        await calibration_mz_calibrate_samples.__wrapped__(
            sample_item_ids=sample_item_ids,
            mz_calibration_params=MzCalibrationParams(refine_window=100),
            user_id=1,
            process_id="batch",
        )
    return excinfo.value


@pytest.mark.asyncio
async def test_warning_names_every_failed_sample():
    """A count alone leaves the user with no idea which samples to look at."""
    warning = await _calibrate_batch(["a", "b", "c"], failing={"a", "c"})

    assert warning.status_code == 200
    assert "Failed to calibrate 2 sample(s)." in warning.user_message
    for sample_item_id in ("a", "c"):
        assert f"sample {sample_item_id}" in warning.user_message
        assert (
            f"Not enough calibration peaks for {sample_item_id}" in warning.user_message
        )
    # The one that calibrated is not named as a failure.
    assert "sample b" not in warning.user_message


@pytest.mark.asyncio
async def test_per_sample_detail_stays_on_the_payload():
    """The message summarises; the structured records are still attached."""
    warning = await _calibrate_batch(["a", "b"], failing={"a"})

    failed = warning.tech_message["samples_calibrate_failed"]
    assert [record["sample_item"]["sample_item_id"] for record in failed] == ["a"]


@pytest.mark.asyncio
async def test_a_long_failure_list_is_truncated():
    """One notification, not a wall of text, when a whole batch fails."""
    extra = 3
    listed = calibration_controller.MAX_LISTED_CALIBRATION_FAILURES
    sample_item_ids = [f"s{i}" for i in range(listed + extra)]

    warning = await _calibrate_batch(sample_item_ids, failing=set(sample_item_ids))

    lines = warning.user_message.splitlines()
    assert lines[0] == f"Failed to calibrate {len(sample_item_ids)} sample(s)."
    assert len(lines) == listed + 2
    assert lines[-1] == f"...and {extra} more."
