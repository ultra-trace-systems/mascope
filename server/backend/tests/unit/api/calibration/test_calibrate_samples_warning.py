"""
The batch m/z calibration warning names the samples it could not calibrate.

``calibration_mz_calibrate_samples`` collects a record per failed sample, but
the notification pane renders only type, status and message, and nothing in
the tree consumes the ``samples_calibrate_failed`` payload - so a bare count
left the user with no way to tell which samples came back uncalibrated.

The payload carries the records the message names and the counts behind them,
not one per sample: like every notification it is published to every backend
process through Redis pub/sub.

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


async def _run_batch(
    sample_item_ids: list[str],
    failing: set[str],
    progress: AsyncMock | None = None,
) -> dict:
    """Calibrate a batch in which ``failing`` fails; what the controller returns
    or raises is passed on. ``progress`` stands in for the progress emitter."""

    async def _one_sample(sample_item_id: str, **kwargs) -> dict:
        # A failed fit names the samples it touched too, as the real one does.
        affected = {
            "_notification_data": {"affected_sample_item_ids": [sample_item_id]}
        }
        if sample_item_id in failing:
            raise ApiException(
                f"Not enough calibration peaks for {sample_item_id}", affected, 200
            )
        return affected

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
        patch(f"{_CTRL}.send_progress_user_notification", progress or AsyncMock()),
    ):
        return await calibration_mz_calibrate_samples.__wrapped__(
            sample_item_ids=sample_item_ids,
            mz_calibration_params=MzCalibrationParams(refine_window=100),
            user_id=1,
            process_id="batch",
        )


async def _calibrate_batch(
    sample_item_ids: list[str], failing: set[str]
) -> ApiException:
    """Calibrate a batch in which ``failing`` fails, returning the warning."""
    with pytest.raises(ApiException) as excinfo:
        await _run_batch(sample_item_ids, failing)
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


@pytest.mark.asyncio
async def test_the_payload_carries_the_records_the_message_names():
    """A notification is published to every backend process through Redis
    pub/sub: a record per sample would make it the size of the batch."""
    listed = calibration_controller.MAX_LISTED_CALIBRATION_FAILURES
    sample_item_ids = [f"s{i}" for i in range(listed * 5)]

    warning = await _calibrate_batch(sample_item_ids, failing=set(sample_item_ids))

    failed = warning.tech_message["samples_calibrate_failed"]
    assert [record["sample_item"]["sample_item_id"] for record in failed] == (
        sample_item_ids[:listed]
    )
    assert warning.tech_message["summary"] == {
        "failed": len(sample_item_ids),
        "below_bar": 0,
        "total": len(sample_item_ids),
    }
    # What the reloads are addressed to, and nothing per sample.
    assert warning.tech_message["_notification_data"] == {
        "affected_sample_batch_ids": ["sb-1"]
    }


@pytest.mark.asyncio
async def test_a_calibrated_batch_reports_its_batches_not_its_samples():
    result = await _run_batch([f"s{i}" for i in range(50)], failing=set())

    assert result["_notification_data"] == {"affected_sample_batch_ids": ["sb-1"]}


@pytest.mark.asyncio
async def test_the_progress_packet_counts_the_samples_and_lists_none():
    """The packet that opens the batch's progress bar: its message gives the
    count, and the ids would make it the size of the batch."""
    progress = AsyncMock()

    await _run_batch([f"s{i}" for i in range(50)], failing=set(), progress=progress)

    progress.assert_awaited_once()
    packet = progress.await_args.args[0]
    assert packet.type == "calibration_mz_calibrate_samples"
    assert packet.message == "m/z calibrating 50 samples."
    assert packet.data == {"_user_id": 1}
