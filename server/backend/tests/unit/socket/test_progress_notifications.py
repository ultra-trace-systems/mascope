"""
Unit tests for progress calculation in send_progress_user_notification.

The frontend progress bar (PaneProgress) only renders a process whose
``progress`` is > 0, and emission drops ``progress`` when it is None
(model_dump(exclude_none=True)). So a notification type that is not handled here
shows no bar. These lock in the peak-assignment and multi-batch rematch
progress behaviour.
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


@pytest.mark.asyncio
async def test_rematch_batches_bar_climbs_across_unequal_batches():
    """Rematching several batches fills one bar, in size order of the work.

    Each batch reports twice - starting and finished - and carries the share
    of the run already behind it, so the bar climbs through the whole run.
    Weighting by the batch's own share alone restarted it at every batch,
    which sent the bar backwards whenever a large batch preceded a small one.
    """
    weights = [0.9, 0.1]  # a 90-sample batch, then a 10-sample one

    progress = []
    completed = 0.0
    for weight in weights:
        for increment in (0.2, 0.8):
            notification = UserNotification(
                process_id="rematch-proc",
                type="rematch_batches",
                status="pending",
                message="Rematching sample batch.",
                data={
                    "sample_batch_id": "b1",
                    "_user_id": 1,
                    "_batch_weight": weight,
                    "_batch_base": completed,
                },
            )
            emitted = await _emit_and_capture(notification, increment=increment)
            progress.append(emitted.progress)
        completed += weight

    assert progress == pytest.approx([18.0, 72.0, 92.0, 98.0])
    assert progress == sorted(progress)
