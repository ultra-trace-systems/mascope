"""
Unit tests for progress calculation in send_progress_user_notification.

The frontend progress bar (PaneProgress) only renders a process whose
``progress`` is > 0, and emission drops ``progress`` when it is None
(model_dump(exclude_none=True)). So a notification type that is not handled here
shows no bar. These lock in the peak-assignment progress behaviour.
"""

from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.socket.notifications.schemas import UserNotification


_SVC = "mascope_backend.socket.notifications.service"


async def _emit_and_capture(notification, increment):
    """Run send_progress_user_notification with emission mocked; return the
    (single) notification object that would have been emitted."""
    from mascope_backend.socket.notifications.service import (
        send_progress_user_notification,
    )

    with patch(f"{_SVC}.emit_user_notification", new_callable=AsyncMock) as emit:
        await send_progress_user_notification(notification, increment)
    assert emit.call_count >= 1
    return emit.call_args.args[0]


@pytest.mark.asyncio
async def test_batch_assign_sets_progress():
    """A batch assignment tick sets progress from item_index / total_samples."""
    notification = UserNotification(
        process_id="batch-proc",
        type="assign_sample_batch_peaks",
        status="pending",
        message="Assigning peaks.",
        data={
            "sample_batch_id": "b1",
            "_room_ids": ["b1"],
            "_user_id": 1,
            "_total_samples": 4,
            "_item_index": 1,
        },
    )
    emitted = await _emit_and_capture(notification, increment=1.0)
    # (item_index + increment) / total * 100 = (1 + 1) / 4 * 100
    assert emitted.progress == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_standalone_single_assign_sets_progress():
    """A standalone single-sample assignment (no parent) drives its own bar."""
    notification = UserNotification(
        process_id="assign-proc",
        parent_id=None,
        type="assign_sample_peaks",
        status="pending",
        message="Assigning peaks for sample.",
        data={"_user_id": 1},
    )
    emitted = await _emit_and_capture(notification, increment=0.4)
    assert emitted.progress == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_nested_single_assign_stays_quiet():
    """When nested under a batch (parent_id set), the per-sample stream carries
    no progress so only the batch-level bar renders."""
    notification = UserNotification(
        process_id="assign-proc",
        parent_id="batch-proc",
        type="assign_sample_peaks",
        status="pending",
        message="Assigning peaks for sample.",
        data={"_user_id": 1},
    )
    emitted = await _emit_and_capture(notification, increment=0.4)
    assert emitted.progress is None
