"""How a background task's own outcome reaches the user's notification.

A controller that isolates per-item failures finishes its run and reports what
happened in the ``status`` of the dict it returns. Announcing every such run as
a success told users a batch had been refreshed while its message said the
refresh had failed, so the outcome is translated into the narrower notification
vocabulary here instead.
"""

from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.lib.api_features import (
    RESULT_STATUS_NOTIFICATION,
    api_controller_background_task,
    notification_status,
)
from mascope_backend.socket.notifications import UserNotification


_MOD = "mascope_backend.api.lib.api_features"


def _controller(result):
    """A decorated background task that simply returns ``result``."""

    @api_controller_background_task(success_notification_rooms=["sample_batch_id"])
    async def run_task(
        sample_batch_id: str = "batch-1",
        independent_transaction: bool = False,
        user_id: int | None = None,
        process_id: str | None = None,
        parent_id: str | None = None,
    ) -> dict:
        return result

    return run_task


async def _announce(result) -> UserNotification:
    """Run a controller returning ``result`` and capture its notification."""
    with (
        patch(f"{_MOD}.handle_notifications", new_callable=AsyncMock) as notify,
        patch(f"{_MOD}.handle_reloads", new_callable=AsyncMock),
    ):
        await _controller(result)(
            sample_batch_id="batch-1", independent_transaction=True
        )
    assert notify.await_count == 1, "the run is announced exactly once"
    return notify.await_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("success", "success"),
        ("skipped", "success"),
        ("partial", "warning"),
        ("locked", "warning"),
        ("failed", "error"),
    ],
)
async def test_the_outcome_decides_how_the_run_is_announced(outcome, expected):
    notification = await _announce({"status": outcome, "message": "done"})

    assert notification.status == expected
    assert notification.message == "done"


@pytest.mark.asyncio
async def test_a_failed_run_carries_its_counts_for_the_drawer():
    """The toast shows only the message; the detail is what remains afterwards."""
    counts = {"failed_samples_count": 7, "total_samples_count": 213}

    notification = await _announce(
        {"status": "failed", "message": "refresh failed", "data": counts}
    )

    assert notification.error == {"detail": counts}


@pytest.mark.asyncio
async def test_a_successful_run_carries_no_error_detail():
    notification = await _announce({"status": "success", "message": "done"})

    assert notification.error is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"message": "done"},  # the majority: controllers with no status of their own
        {"status": "something-new", "message": "done"},
        None,
    ],
    ids=["no-status", "unknown-status", "no-result"],
)
async def test_a_run_that_reports_no_outcome_stays_a_success(result):
    """Most controllers report nothing, and an unknown word is not a failure."""
    notification = await _announce(result)

    assert notification.status == "success"


def test_every_translation_is_a_status_notifications_accept():
    """The schema validates on construction only, so nothing catches a typo here."""
    for status in set(RESULT_STATUS_NOTIFICATION.values()) | {
        notification_status(None)
    }:
        UserNotification(type="run_task", message="done", status=status)
