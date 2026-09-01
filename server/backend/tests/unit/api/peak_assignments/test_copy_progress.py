"""
Unit tests for the assignment copy's progress stream.

A copy fans out over the batch's other samples, and each destination re-scores
the copied formulas against its own peak file - the slow part - so the copy ran
for a long time with nothing to read. These lock in the packets it emits: that
they ride the fan-out's own channel (so the terminal packet the decorator sends
ends the same bar), that every destination advances the bar whatever became of
it, that the phases WITHIN a destination move it too, and that the fractions
they carry render as a bar that climbs to full.

That last one is why these go through the real calculator rather than assert
the emitted fractions alone: the calculator dispatches on notification TYPE, so
a fan-out can emit a perfectly good stream that draws no bar at all because its
type reaches no branch. Both halves have to meet.

No DB or Socket.IO - every dependency is mocked (mirrors
test_batch_peaks_progress.py).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.new.peak_assignments.copy_service import (
    CopyCandidate,
    CopyPartition,
)
from mascope_backend.socket.notifications.service import (
    send_progress_user_notification,
)


_MOD = "mascope_backend.api.new.peak_assignments.copy_service"
_SVC = "mascope_backend.socket.notifications.service"

BANDS = {"assigned": 0.8, "candidate": 0.5}

#: The fractions the real ``_copy_to_destination`` reports its phases at, so a
#: test drives the same contract the production reporter does.
PHASES = (0.05, 0.15, 0.65, 0.8)

SOURCE = SimpleNamespace(
    sample_item_id="si-source",
    sample_item_name="Curated Sample",
    sample_batch_id="sb-1",
)


def _partition(*candidates, run_id="run-1"):
    return CopyPartition(
        source_run_id=run_id,
        source_engine="mascope",
        tier_bands=dict(BANDS),
        destinations=tuple(candidates),
    )


def _eligible(index):
    return CopyCandidate(
        sample_item_id=f"si-{index}", sample_item_name=f"Sibling {index}", reason=None
    )


def _skipped(index, reason="blank sample (no peaks)"):
    return CopyCandidate(
        sample_item_id=f"si-{index}", sample_item_name=f"Sibling {index}", reason=reason
    )


def _start(*, copy=None, report_phases=True):
    """Patch the fan-out's world; return the started mocks.

    ``copy`` is what one destination's copy does - pass a list to vary it per
    destination, whose entries are raised if they are exceptions and returned
    otherwise (so a list entry replaces the stand-in rather than feeding it).
    The stand-in drives ``report_progress`` with the same phase fractions the
    real one does, so the stream under test is production's.
    """
    patches = {
        "rows": patch(f"{_MOD}._source_run_and_rows", new_callable=AsyncMock),
        "copy": patch(f"{_MOD}._copy_to_destination", new_callable=AsyncMock),
        "progress": patch(
            f"{_MOD}.send_progress_user_notification", new_callable=AsyncMock
        ),
    }
    mocks = {key: p.start() for key, p in patches.items()}

    run = SimpleNamespace(
        peak_assignment_run_id="run-1",
        engine="mascope",
        engine_version="0.2.0",
        tier_bands=dict(BANDS),
        status="completed",
    )
    mocks["rows"].return_value = (run, [{"mz_error_ppm": 1.2}])

    async def _copy(**kwargs):
        if report_phases and kwargs.get("report_progress") is not None:
            for fraction in PHASES:
                await kwargs["report_progress"](fraction, "Working")
        return {
            "sample_item_id": kwargs["destination_sample_item_id"],
            "sample_item_name": "Sibling",
            "status": "copied",
            "peak_assignment_run_id": "copied-run",
        }

    if isinstance(copy, list):
        mocks["copy"].side_effect = copy
    else:
        mocks["copy"].side_effect = _copy
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


async def _rendered(progress_mock):
    """The bar each emitted packet draws, through the real calculator."""
    drawn = []
    with patch(f"{_SVC}.emit_user_notification", new_callable=AsyncMock) as emit:
        for notification, increment in _packets(progress_mock):
            await send_progress_user_notification(notification, increment)
            drawn.append(emit.call_args.args[0].progress)
    return drawn


@pytest.fixture(autouse=True)
def _stop_all_patches():
    yield
    patch.stopall()


async def _fanout(partition, **kwargs):
    from mascope_backend.api.new.peak_assignments.copy_service import _run_copy_fanout

    return await _run_copy_fanout(
        source=SOURCE,
        partition=partition,
        rescore=True,
        user_id=kwargs.get("user_id"),
        process_id=kwargs.get("process_id", "proc-1"),
        parent_id=kwargs.get("parent_id"),
    )


@pytest.mark.asyncio
async def test_packets_are_pending_ones_on_the_fanouts_channel():
    """Typed for the process the browser is already tracking, and addressed to
    the batch's room and to whoever asked for the copy."""
    mocks = _start()

    await _fanout(_partition(_eligible(1)), user_id=7, parent_id="parent-1")

    notification, _ = _packets(mocks["progress"])[0]
    assert notification.type == "copy_assignments_to_batch"
    assert notification.status == "pending"
    assert notification.process_id == "proc-1"
    assert notification.parent_id == "parent-1"
    assert notification.data["sample_batch_id"] == "sb-1"
    assert notification.data["_room_ids"] == ["sb-1"]
    assert notification.data["_user_id"] == 7


@pytest.mark.asyncio
async def test_every_destination_opens_and_closes_its_share_of_the_bar():
    """One tick as a destination starts, one once it is done - the phases in
    between are extra, not a replacement."""
    mocks = _start(report_phases=False)

    await _fanout(_partition(_eligible(1), _eligible(2)))

    assert _ticks(mocks["progress"]) == [
        (0, 2, None),
        (0, 2, 1.0),
        (1, 2, None),
        (1, 2, 1.0),
    ]


@pytest.mark.asyncio
async def test_a_destination_closes_on_one_whole_item_not_the_whole_fanout():
    """``increment`` is the fraction of the CURRENT item. Passing the overall
    fraction here advanced the bar by a fraction of a fraction, so a long
    fan-out crept toward a fraction of full and never reached it."""
    mocks = _start(report_phases=False)

    await _fanout(_partition(_eligible(1), _eligible(2), _eligible(3)))

    closing = [increment for _, _, increment in _ticks(mocks["progress"])[1::2]]
    assert closing == [1.0, 1.0, 1.0]


@pytest.mark.asyncio
async def test_a_skipped_destination_still_advances_the_bar():
    """The bar tracks how much of the batch has been dealt with, not how much
    of it was copied: a bar that stalls on the blanks reads as a hang."""
    mocks = _start()

    result = await _fanout(_partition(_skipped(1), _eligible(2)))

    assert result["data"]["skipped_count"] == 1
    assert [item for item, _, _ in _ticks(mocks["progress"])][-1] == 1
    assert await _rendered(mocks["progress"]) == pytest.approx(
        [0.0, 50.0, 50.0, 52.5, 57.5, 82.5, 90.0, 100.0]
    )


@pytest.mark.asyncio
async def test_a_failing_destination_still_advances_the_bar():
    """A destination the fan-out isolated is one of the N the bar counts
    through, so the run does not appear to stop on it."""
    mocks = _start(
        copy=[
            RuntimeError("peak file unreadable"),
            {
                "sample_item_id": "si-2",
                "sample_item_name": "Sibling 2",
                "status": "copied",
                "peak_assignment_run_id": "copied-run",
            },
        ]
    )

    result = await _fanout(_partition(_eligible(1), _eligible(2)))

    assert result["data"]["failed_count"] == 1
    drawn = await _rendered(mocks["progress"])
    assert drawn[-1] == pytest.approx(100.0)
    assert drawn == sorted(drawn)


@pytest.mark.asyncio
async def test_the_phases_within_a_destination_move_the_bar():
    """The reason this exists at all: one destination is minutes of re-scoring
    and publishing, so a bar that only steps per destination sits still through
    the slow part. On a two-sample batch that is the whole bar."""
    mocks = _start()

    await _fanout(_partition(_eligible(1), _eligible(2)))

    drawn = await _rendered(mocks["progress"])
    # Strictly inside the first destination's half of the bar, so a viewer sees
    # movement before any destination has finished.
    within_first = [value for value in drawn[:6] if 0.0 < value < 50.0]
    assert within_first == pytest.approx([2.5, 7.5, 32.5, 40.0])
    assert drawn == sorted(drawn)


@pytest.mark.asyncio
async def test_the_emitted_stream_fills_one_bar_from_empty_to_full():
    """End to end over the real progress calculator: the keys the fan-out sends
    are the ones the calculator reads, and together they draw a bar that climbs
    to full. Asserted over the pair because either half can be right while the
    two do not meet - the calculator dispatches on TYPE, so before this type
    reached a branch the fan-out emitted a flawless stream that drew nothing."""
    mocks = _start(report_phases=False)

    await _fanout(_partition(_eligible(1), _eligible(2), _eligible(3), _eligible(4)))

    drawn = await _rendered(mocks["progress"])
    assert drawn == pytest.approx([0.0, 25.0, 25.0, 50.0, 50.0, 75.0, 75.0, 100.0])
    assert drawn == sorted(drawn)


@pytest.mark.asyncio
async def test_no_destinations_means_no_stream():
    """Nothing to copy to, so nothing to report - and no N to divide by."""
    mocks = _start()

    await _fanout(_partition())

    mocks["progress"].assert_not_awaited()
