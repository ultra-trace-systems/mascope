"""How ``rematch_batches`` reports a run in which nothing was processed.

A batch that is mid-processing comes back ``locked``: the walk deliberately
leaves it alone rather than interrupting it, which is what the dataset-wide
refresh promises. A batch that blew up comes back ``failed``. Both keep
``processed_batches_count`` at zero, so the "nothing was processed" branch has
to tell them apart - a dataset whose batches all happen to be busy is a "come
back later", not "all N batches failed to process".

These assert on the notification the user actually receives, since that is
where the difference shows: ``status="error"`` renders as a red failure toast
and drops the run's child processes, ``status="warning"`` does not.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.controllers.match import match_controller
from mascope_backend.api.lib import api_features


@asynccontextmanager
async def _session_yielding_counts(counts):
    """Stand in for ``async_session`` during the sample-count weighting query."""
    session = AsyncMock()
    result = AsyncMock()
    result.all = lambda: list(counts.items())
    session.execute.return_value = result
    yield session


async def _notification_for(batch_ids, statuses):
    """Walk ``batch_ids`` and return the notification the run ends on.

    ``independent_transaction=True`` is how the dataset route submits the walk,
    and it is the mode in which the outcome becomes a notification rather than
    an exception for a parent to handle.
    """
    counts = {batch_id: 1 for batch_id in batch_ids}
    emitted = []

    async def _rematch_batch(sample_batch_id, **_):
        return {"status": statuses[sample_batch_id], "data": {}}

    async def _capture(rooms, notification, kwargs, result):
        emitted.append((notification.status, notification.message))

    with (
        patch.object(
            match_controller,
            "async_session",
            lambda: _session_yielding_counts(counts),
        ),
        patch.object(match_controller, "send_progress_user_notification", AsyncMock()),
        patch.object(match_controller, "rematch_batch", _rematch_batch),
        patch.object(api_features, "handle_notifications", _capture),
        patch.object(api_features, "handle_reloads", AsyncMock()),
    ):
        await match_controller.rematch_batches(
            sample_batch_ids=batch_ids,
            independent_transaction=True,
            user_id=1,
            process_id="proc-1",
        )

    assert emitted, "the run must report something over the notification socket"
    return emitted[-1]


@pytest.mark.asyncio
async def test_an_all_locked_run_is_a_warning_not_a_failure():
    """The case the dataset menu entry makes reachable in one click."""
    status, message = await _notification_for(
        ["b1", "b2"], {"b1": "locked", "b2": "locked"}
    )

    assert status == "warning"
    assert "2 locked" in message
    assert "failed to process" not in message


@pytest.mark.asyncio
async def test_an_all_failed_run_is_still_a_failure():
    """The branch must keep firing for the case it was written for."""
    status, message = await _notification_for(
        ["b1", "b2"], {"b1": "failed", "b2": "failed"}
    )

    assert status == "error"
    assert "All 2 batches failed to process" in message


@pytest.mark.asyncio
async def test_a_locked_batch_beside_a_failed_one_is_a_warning():
    """Not every batch failed, so the "all failed" message would be untrue."""
    status, message = await _notification_for(
        ["b1", "b2"], {"b1": "locked", "b2": "failed"}
    )

    assert status == "warning"
    assert "1 locked" in message
    assert "1 failed" in message


@pytest.mark.asyncio
async def test_a_fully_successful_run_reports_plainly():
    status, message = await _notification_for(
        ["b1", "b2"], {"b1": "success", "b2": "skipped"}
    )

    assert status != "error"
    assert "2/2 batches processed" in message
