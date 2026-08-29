"""
Unit tests for the batch-peak backfill's progress stream.

``backfill_sample_batch_peaks`` folds a batch one sample at a time and used to
report only once it was done, so the button that launched it spun for as long as
the whole batch took with nothing to read. These lock in the packets it now
emits: that they ride the controller's own channel (so the terminal packet the
decorator sends ends the same bar), their order, that a sample whose fold raised
still advances the bar, that a large batch is sampled rather than reported
per sample, and that the fractions they carry render as a bar that climbs to
full.

No DB or Socket.IO - every dependency is mocked (mirrors test_batch.py).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.socket.notifications.service import (
    send_progress_user_notification,
)


_MOD = "mascope_backend.api.new.peak_assignments.batch_peaks_controller"
_SVC = "mascope_backend.socket.notifications.service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_returning(sample_ids):
    """Build an async_session() context manager whose query yields ``sample_ids``."""
    scalars = MagicMock()
    scalars.all.return_value = sample_ids
    exec_result = MagicMock()
    exec_result.scalars.return_value = scalars

    session = AsyncMock()
    session.execute = AsyncMock(return_value=exec_result)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _start(sample_ids, fold=None):
    """Patch the backfill's world; return the started mocks.

    ``fold`` is what one sample's fold does - pass a list to vary it per sample.
    It defaults to a batch id, which is what a sample that folded returns.
    """
    patches = {
        "async_session": patch(f"{_MOD}.async_session"),
        "fold": patch(f"{_MOD}.fold_sample_into_batch_peaks", new_callable=AsyncMock),
        "progress": patch(
            f"{_MOD}.send_progress_user_notification", new_callable=AsyncMock
        ),
    }
    mocks = {key: p.start() for key, p in patches.items()}
    mocks["async_session"].return_value = _session_returning(sample_ids)
    if isinstance(fold, list):
        mocks["fold"].side_effect = fold
    else:
        mocks["fold"].return_value = "sb-1" if fold is None else fold
    return mocks


def _packets(progress_mock):
    """Every emitted packet, in order, as its (notification, increment) pair."""
    packets = []
    for call in progress_mock.await_args_list:
        notification = call.args[0]
        increment = call.args[1] if len(call.args) > 1 else call.kwargs.get("increment")
        packets.append((notification, increment))
    return packets


def _ticks(progress_mock):
    """Every packet as (item_index, total_samples, increment)."""
    return [
        (n.data["_item_index"], n.data["_total_samples"], increment)
        for n, increment in _packets(progress_mock)
    ]


@pytest.fixture(autouse=True)
def _stop_all_patches():
    yield
    patch.stopall()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reports_before_and_after_every_sample():
    """Each sample of an ordinary batch ticks twice - as it starts, and once it
    has been folded."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0", "si-1", "si-2"])

    folded, failed = await backfill_sample_batch_peaks(
        "sb-1", user_id=7, process_id="proc-1"
    )

    assert (folded, failed) == (3, 0)
    assert _ticks(mocks["progress"]) == [
        (0, 3, None),
        (0, 3, 1.0),
        (1, 3, None),
        (1, 3, 1.0),
        (2, 3, None),
        (2, 3, 1.0),
    ]


@pytest.mark.asyncio
async def test_packets_are_pending_ones_on_the_controllers_channel():
    """The stream is typed for the process the browser is already tracking, and
    addressed to the batch's room and to whoever asked for the backfill."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0"])

    await backfill_sample_batch_peaks(
        "sb-1", user_id=7, process_id="proc-1", parent_id="parent-1"
    )

    notification, _ = _packets(mocks["progress"])[0]
    assert notification.type == "compute_batch_peaks"
    assert notification.status == "pending"
    assert notification.process_id == "proc-1"
    assert notification.parent_id == "parent-1"
    assert notification.data["sample_batch_id"] == "sb-1"
    assert notification.data["_room_ids"] == ["sb-1"]
    assert notification.data["_user_id"] == 7
    assert "1/1" in notification.message


@pytest.mark.asyncio
async def test_a_sample_whose_fold_raised_still_advances_the_bar():
    """The bar tracks how much of the batch has been dealt with, not how much of
    it succeeded: a bar that stalls on the one failure the run isolated reads as
    a hang."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(
        ["si-0", "si-1", "si-2"], fold=["sb-1", RuntimeError("fold blew up"), "sb-1"]
    )

    folded, failed = await backfill_sample_batch_peaks("sb-1", process_id="proc-1")

    assert (folded, failed) == (2, 1)
    assert _ticks(mocks["progress"]) == [
        (0, 3, None),
        (0, 3, 1.0),
        (1, 3, None),
        (1, 3, 1.0),
        (2, 3, None),
        (2, 3, 1.0),
    ]


@pytest.mark.asyncio
async def test_a_sample_with_nothing_to_fold_still_advances_the_bar():
    """A sample with no completed run folds nothing and returns None. It is
    still one of the N the bar counts through - the whole point of the button is
    a batch mostly made of those."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0", "si-1"], fold=[None, "sb-1"])

    folded, failed = await backfill_sample_batch_peaks("sb-1", process_id="proc-1")

    assert (folded, failed) == (1, 0)
    assert len(_ticks(mocks["progress"])) == 4


@pytest.mark.asyncio
async def test_a_large_batch_is_sampled_rather_than_reported_per_sample():
    """Past the step count the batch is sampled, so the packet count stays
    bounded however large it is - and the last sample reports whatever the
    stride, so the bar still ends full rather than a stride short of it."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        _BACKFILL_PROGRESS_STEPS,
        backfill_sample_batch_peaks,
    )

    total = _BACKFILL_PROGRESS_STEPS * 10
    mocks = _start([f"si-{i}" for i in range(total)])

    await backfill_sample_batch_peaks("sb-1", process_id="proc-1")

    ticks = _ticks(mocks["progress"])
    reporting = sorted({item_index for item_index, _, _ in ticks})
    assert len(ticks) == 2 * len(reporting)
    assert len(reporting) <= 2 * _BACKFILL_PROGRESS_STEPS
    assert reporting[0] == 0
    assert reporting[-1] == total - 1
    # Still in order, so the bar only ever climbs.
    assert [item_index for item_index, _, _ in ticks] == sorted(
        item_index for item_index, _, _ in ticks
    )


@pytest.mark.asyncio
async def test_no_process_means_no_stream():
    """A backfill nobody is watching (no process id) folds silently: a generated
    id would open a new progress bar per packet rather than fill one."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0", "si-1"])

    folded, failed = await backfill_sample_batch_peaks("sb-1")

    assert (folded, failed) == (2, 0)
    mocks["progress"].assert_not_awaited()


@pytest.mark.asyncio
async def test_an_empty_batch_reports_nothing():
    """Nothing to fold, so nothing to report - and no N to divide the bar by."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start([])

    folded, failed = await backfill_sample_batch_peaks("sb-1", process_id="proc-1")

    assert (folded, failed) == (0, 0)
    mocks["progress"].assert_not_awaited()


@pytest.mark.asyncio
async def test_the_emitted_stream_fills_one_bar_from_empty_to_full():
    """End to end over the real progress calculator: the keys the controller
    sends are the ones the calculator reads, and together they draw a bar that
    climbs to full. Asserted over the pair because either half can be right
    while the two do not meet."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0", "si-1", "si-2", "si-3"])

    await backfill_sample_batch_peaks("sb-1", user_id=7, process_id="proc-1")

    emitted = []
    with patch(f"{_SVC}.emit_user_notification", new_callable=AsyncMock) as emit:
        for notification, increment in _packets(mocks["progress"]):
            await send_progress_user_notification(notification, increment)
            emitted.append(emit.call_args.args[0].progress)

    assert emitted == pytest.approx([0.0, 25.0, 25.0, 50.0, 50.0, 75.0, 75.0, 100.0])
    assert emitted == sorted(emitted)
