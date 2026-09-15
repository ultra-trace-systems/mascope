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

import asyncio
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
        # A sample the run fold answers None for is handed to the no-run fold,
        # which the rebuild reaches through the service; nothing there either.
        "fold_no_run": patch(
            "mascope_backend.api.new.peak_assignments.service"
            ".fold_sample_peaks_without_run",
            new_callable=AsyncMock,
        ),
        "progress": patch(
            f"{_MOD}.send_progress_user_notification", new_callable=AsyncMock
        ),
        "recompute": patch(f"{_MOD}.recompute_batch_consensus", new_callable=AsyncMock),
    }
    mocks = {key: p.start() for key, p in patches.items()}
    mocks["async_session"].return_value = _session_returning(sample_ids)
    mocks["fold_no_run"].return_value = None
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
    """A sample with no completed run, and nothing for the no-run fold to fold
    either (a blank, say), returns None. It is still one of the N the bar counts
    through."""
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
async def test_the_consensus_is_recomputed_once_after_every_sample_folded():
    """Each fold defers its consensus and the batch is recomputed once at the
    end - the per-sample recompute was O(samples x anchors) and every
    intermediate result was overwritten by the next sample's."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    order = []
    mocks = _start(["si-0", "si-1", "si-2"])
    mocks["fold"].side_effect = lambda *args, **kwargs: order.append("fold") or "sb-1"
    mocks["recompute"].side_effect = lambda *args, **kwargs: order.append("recompute")

    await backfill_sample_batch_peaks("sb-1")

    assert order == ["fold", "fold", "fold", "recompute"]
    # Every fold defers into ONE shared set, and that same set is what the
    # single pass at the end is scoped to - it must never be handed the batch.
    deferred = {
        id(call.kwargs["defer_consensus_to"]) for call in mocks["fold"].await_args_list
    }
    assert len(deferred) == 1
    for call in mocks["fold"].await_args_list:
        assert set(call.kwargs) == {"defer_consensus_to"}
    mocks["recompute"].assert_awaited_once_with("sb-1", set())
    assert id(mocks["recompute"].await_args.args[1]) in deferred


@pytest.mark.asyncio
async def test_the_consensus_pass_runs_even_when_a_fold_raised():
    """A sample whose fold raised is skipped; the anchors the others touched
    still need their consensus, so the pass is unconditional on a fold that
    raised (the loop's own except arms catch it and carry on)."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0", "si-1"], fold=[RuntimeError("fold blew up"), "sb-1"])

    folded, failed = await backfill_sample_batch_peaks("sb-1")

    assert (folded, failed) == (1, 1)
    mocks["recompute"].assert_awaited_once_with("sb-1", set())


@pytest.mark.asyncio
async def test_the_consensus_pass_runs_when_the_loop_is_left_altogether():
    """Stronger than the last one: not a fold the loop caught, but the loop
    being LEFT. Deferring the consensus makes that the dangerous case - the
    folds before it have COMMITTED occurrences whose anchors nothing else
    recomputes - so the pass is in a finally, and the exception still
    propagates. Without it the recompute is never awaited at all."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0", "si-1"])
    mocks["progress"].side_effect = RuntimeError("progress emit blew up")

    with pytest.raises(RuntimeError, match="progress emit blew up"):
        await backfill_sample_batch_peaks("sb-1", process_id="proc-1")

    mocks["recompute"].assert_awaited_once_with("sb-1", set())


@pytest.mark.asyncio
async def test_a_cancelled_backfill_still_runs_the_consensus_pass():
    """The load-bearing vector: a CancelledError is a BaseException, so it walks
    past both of the loop's except arms. It must still reach the caller as a
    cancellation, and the committed folds must still get their consensus."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0", "si-1"], fold=[asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await backfill_sample_batch_peaks("sb-1")

    mocks["recompute"].assert_awaited_once_with("sb-1", set())


@pytest.mark.asyncio
async def test_a_failing_consensus_pass_does_not_mask_the_cancellation():
    """The guard on the finally. A recompute that raises while the loop is
    already unwinding must not REPLACE the exception that interrupted it - a
    cancellation reported as a database error is the wrong story entirely."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0"], fold=[asyncio.CancelledError()])
    mocks["recompute"].side_effect = RuntimeError("pool timeout")

    with pytest.raises(asyncio.CancelledError):
        await backfill_sample_batch_peaks("sb-1")

    mocks["recompute"].assert_awaited_once()


@pytest.mark.asyncio
async def test_a_consensus_pass_that_fails_on_the_ordinary_path_still_raises():
    """The other side of that guard: nothing is unwinding, so a failed pass is
    the only news there is. Swallowed, `backfill_outcome` would report a green
    'Computed batch peaks from N assigned sample(s)' over anchors it never
    wrote."""
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        backfill_sample_batch_peaks,
    )

    mocks = _start(["si-0", "si-1"])
    mocks["recompute"].side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await backfill_sample_batch_peaks("sb-1")


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
