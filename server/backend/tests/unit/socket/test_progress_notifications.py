"""
Unit tests for progress calculation in send_progress_user_notification.

The frontend progress bar (PaneProgress) only renders a process whose
``progress`` is > 0, and emission drops ``progress`` when it is None
(model_dump(exclude_none=True)). So a notification type that is not handled here
shows no bar. These lock in the peak-assignment, batch-peak backfill and
multi-batch rematch progress behaviour.
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
async def test_batch_peak_backfill_steps_across_the_sample():
    """A backfill ticks twice per reporting sample - before its fold and after
    it - so the bar steps from item_index/N to (item_index + 1)/N. The
    before-tick carries no increment, which is not the same as carrying zero
    progress: read as a missing increment it would leave the bar wherever the
    previous sample left it, and the very first one would never open it."""

    def _tick(item_index):
        return UserNotification(
            process_id="backfill-proc",
            type="compute_batch_peaks",
            status="pending",
            message=f"Computing batch peaks, folding sample {item_index + 1}/4.",
            data={
                "sample_batch_id": "b1",
                "_room_ids": ["b1"],
                "_user_id": 1,
                "_total_samples": 4,
                "_item_index": item_index,
            },
        )

    before = await _emit_and_capture(_tick(1), increment=None)
    after = await _emit_and_capture(_tick(1), increment=1.0)

    assert before.progress == pytest.approx(25.0)
    assert after.progress == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_batch_peak_backfill_keeps_the_message_it_was_given():
    """Unlike the batch-assignment branch, this one computes the bar and nothing
    else: the packet already names which sample it is on, and rewriting it here
    would put the same sentence in two files."""
    notification = UserNotification(
        process_id="backfill-proc",
        type="compute_batch_peaks",
        status="pending",
        message="Computing batch peaks, folding sample 3/4.",
        data={"_room_ids": ["b1"], "_total_samples": 4, "_item_index": 2},
    )

    emitted = await _emit_and_capture(notification, increment=1.0)

    assert emitted.message == "Computing batch peaks, folding sample 3/4."
    assert emitted.progress == pytest.approx(75.0)


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
