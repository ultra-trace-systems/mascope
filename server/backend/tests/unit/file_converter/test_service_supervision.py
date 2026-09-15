"""
Tests for the converter's worker-thread supervision (``service._Supervised``).

A worker thread that dies leaves its queue filling with files nobody will
convert, and nothing else in the service notices - the converter keeps accepting
uploads it will never process until someone restarts it (#1350). These cover the
slot that notices, the replacement it stands up, and the handover of whatever
the dead thread was holding.

The handover is checked against the real ``RawProcessor`` and
``PeakRecomputeWorker`` rather than stand-ins: the supervisor asks the worker for
it by calling ``requeue_inflight()``, and a mock answers to whatever name the
test writer typed, so a mock-only suite cannot tell whether the two sides still
agree.
"""

import inspect
from queue import Queue
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from mascope_backend.file_converter import base_processor, service
from mascope_backend.file_converter.peak_recompute_worker import PeakRecomputeWorker
from mascope_thermo.processor import RawProcessor
from mascope_tofwerk.processor import H5Processor


MAX_RESTARTS = service._Supervised.MAX_RESTARTS
BACKOFF = service._Supervised.RESTART_BACKOFF_S


@pytest.fixture()
def running_shutdown_event():
    """A SHUTDOWN_EVENT stand-in that is never set."""
    event = MagicMock()
    event.is_set.return_value = False
    with patch.object(service, "SHUTDOWN_EVENT", event):
        yield event


@pytest.fixture()
def logger():
    """Capture the module's logger calls."""
    fake = MagicMock()
    runtime = MagicMock()
    runtime.logger = fake
    with patch.object(service, "runtime", runtime):
        yield fake


@pytest.fixture()
def clock():
    """Drive ``service.monotonic`` from the test.

    A test supplies the death times it cares about; the last value is then
    repeated, so the fixture never turns an extra clock read into a confusing
    StopIteration. Tests therefore assert on outcomes - restart counts and log
    lines - rather than on how often the clock was read.
    """

    def _clock(*times):
        remaining = list(times)

        def _tick():
            return remaining.pop(0) if len(remaining) > 1 else remaining[0]

        return patch.object(service, "monotonic", side_effect=_tick)

    return _clock


def _errors(logger, needle):
    """Every ERROR line containing `needle`."""
    return [
        call.args[0] for call in logger.error.call_args_list if needle in call.args[0]
    ]


def _thread(alive: bool) -> MagicMock:
    """A thread stand-in reporting the given liveness, with no in-flight work."""
    thread = MagicMock()
    thread.is_alive.return_value = alive
    # The supervisor probes for the handover method; a bare MagicMock would
    # invent one and return another MagicMock, which is not a useful default.
    del thread.requeue_inflight
    return thread


def _holding(path: str) -> MagicMock:
    """A dead thread stand-in that hands `path` back when asked, exactly once."""
    thread = _thread(alive=False)
    thread.requeue_inflight = MagicMock(side_effect=[path] + [None] * 10)
    return thread


def _deaths(count: int):
    """Clock readings for `count` deaths, spaced so the backoff never blocks."""
    return [n * BACKOFF for n in range(count)]


def test_live_thread_is_left_alone(running_shutdown_event, logger):
    factory = MagicMock(side_effect=[_thread(alive=True)])
    slot = service._Supervised("RawProcessor #0", factory)

    slot.ensure_alive()

    # One call to build the initial thread, none to replace it.
    assert factory.call_count == 1
    logger.error.assert_not_called()


def test_dead_thread_is_replaced_and_reported(running_shutdown_event, logger, clock):
    first, replacement = _thread(alive=False), _thread(alive=True)
    factory = MagicMock(side_effect=[first, replacement])
    slot = service._Supervised("RawProcessor #0", factory)

    with clock(0):
        slot.ensure_alive()

    assert slot.thread is replacement
    replacement.start.assert_called_once()
    # The death must be visible: this is the signal that was missing entirely.
    assert _errors(logger, "RawProcessor #0")


def test_no_restart_once_shutdown_is_requested(logger):
    event = MagicMock()
    event.is_set.return_value = True
    factory = MagicMock(side_effect=[_thread(alive=False)])

    with patch.object(service, "SHUTDOWN_EVENT", event):
        slot = service._Supervised("RawProcessor #0", factory)
        slot.ensure_alive()

    # A thread that exited because we asked it to is not a failure.
    assert factory.call_count == 1
    logger.error.assert_not_called()


def test_the_slot_names_the_thread_it_supervises(running_shutdown_event, logger, clock):
    """Otherwise "RawProcessor #0 died" cannot be matched to the thread's own lines."""
    first, replacement = _thread(alive=False), _thread(alive=True)
    factory = MagicMock(side_effect=[first, replacement])

    slot = service._Supervised("RawProcessor #0", factory)
    assert first.name == "RawProcessor #0"

    with clock(0):
        slot.ensure_alive()

    # The replacement carries the same label, not a fresh Thread-N.
    assert replacement.name == "RawProcessor #0"


def test_restarts_are_spaced_out(running_shutdown_event, logger, clock):
    """A failure that reproduces at once must not spend the budget in seconds."""
    factory = MagicMock(side_effect=[_thread(alive=False) for _ in range(5)])
    slot = service._Supervised("RawProcessor #0", factory)

    # Supervision ticks once a second; the second tick is far inside the backoff.
    with clock(0, 1):
        slot.ensure_alive()
        slot.ensure_alive()

    assert factory.call_count == 2  # the initial thread plus one replacement
    assert len(_errors(logger, "died unexpectedly")) == 1


def test_restarts_are_capped_within_the_window(running_shutdown_event, logger, clock):
    factory = MagicMock(
        side_effect=[_thread(alive=False) for _ in range(MAX_RESTARTS + 2)]
    )
    slot = service._Supervised("RawProcessor #0", factory)

    # One death more than the budget allows, spaced past the backoff each time
    # and still well inside the window, so it gives up rather than spinning.
    with clock(*_deaths(MAX_RESTARTS + 1)):
        for _ in range(MAX_RESTARTS + 1):
            slot.ensure_alive()

    assert factory.call_count == MAX_RESTARTS + 1
    assert len(_errors(logger, "will not be restarted")) == 1


def test_budget_is_restored_after_a_healthy_window(
    running_shutdown_event, logger, clock
):
    """Five unrelated deaths months apart are not a crash loop."""
    factory = MagicMock(
        side_effect=[_thread(alive=False) for _ in range(MAX_RESTARTS + 3)]
    )
    slot = service._Supervised("RawProcessor #0", factory)

    window = service._Supervised.MAX_RESTART_WINDOW_S
    # The budget is spent inside one window; the next death comes long after the
    # slot has been running healthily, so it must still be restarted.
    with clock(*_deaths(MAX_RESTARTS), 10 * window):
        for _ in range(MAX_RESTARTS + 1):
            slot.ensure_alive()

    assert factory.call_count == MAX_RESTARTS + 2
    assert not _errors(logger, "will not be restarted")


def test_abandoned_slot_keeps_reporting(running_shutdown_event, logger, clock):
    """A slot that is dead for good is the #1350 stall; it must keep saying so."""
    factory = MagicMock(
        side_effect=[_thread(alive=False) for _ in range(MAX_RESTARTS + 2)]
    )
    slot = service._Supervised("RawProcessor #0", factory)

    gave_up_at = MAX_RESTARTS * BACKOFF
    interval = service._Supervised.GIVE_UP_REPORT_INTERVAL_S
    ticks = _deaths(MAX_RESTARTS + 1) + [gave_up_at + 1, gave_up_at + interval + 1]
    with clock(*ticks):
        for _ in range(len(ticks)):
            slot.ensure_alive()

    still_dead = _errors(logger, "still dead and no longer supervised")
    # Silent on the tick right after giving up, loud again once the interval passed.
    assert len(still_dead) == 1
    assert "RawProcessor #0" in still_dead[0]


def test_giving_up_still_hands_the_work_back(running_shutdown_event, logger, clock):
    """The queue is shared, so a sibling slot can still convert the file."""
    threads = [_thread(alive=False) for _ in range(MAX_RESTARTS)]
    last = _holding("/streams/sample.raw")
    factory = MagicMock(side_effect=threads + [last])
    slot = service._Supervised("RawProcessor #0", factory)

    with clock(*_deaths(MAX_RESTARTS + 1)):
        for _ in range(MAX_RESTARTS):
            slot.ensure_alive()
        # The budget is now spent, and this last thread died holding a file.
        slot.ensure_alive()

    assert _errors(logger, "will not be restarted")
    last.requeue_inflight.assert_called_once_with()


def test_failing_restart_does_not_propagate(running_shutdown_event, logger, clock):
    factory = MagicMock(side_effect=[_thread(alive=False), RuntimeError("no threads")])
    slot = service._Supervised("RawProcessor #0", factory)

    # Supervision runs inside the service's main loop; raising here would take
    # the whole converter down instead of the one thread it failed to restart.
    with clock(0):
        slot.ensure_alive()

    assert logger.exception.called


def test_a_replacement_that_cannot_start_is_not_adopted(
    running_shutdown_event, logger, clock
):
    """The slot must keep the dead thread, not a thread that never ran.

    Adopting it would make the slot look freshly restarted while nothing is
    running, and the next tick would read its empty in-flight marker instead of
    the dead thread's.
    """
    dead = _holding("/streams/sample.raw")
    stillborn = _thread(alive=False)
    stillborn.start.side_effect = RuntimeError("can't start new thread")
    factory = MagicMock(side_effect=[dead, stillborn])
    slot = service._Supervised("RawProcessor #0", factory)

    with clock(0):
        slot.ensure_alive()

    assert slot.thread is dead
    assert logger.exception.called


def test_failing_restart_does_not_requeue_the_same_work_twice(
    running_shutdown_event, logger, clock
):
    """The slot keeps the dead thread when the factory fails; it must not re-ask.

    The worker clears its own marker as it hands the item over, so the second
    tick gets nothing back. Without that, a factory that keeps failing enqueues
    one file MAX_RESTARTS times and the user is told a converted upload failed.
    """
    dead = _holding("/streams/sample.raw")
    factory = MagicMock(
        side_effect=[dead, RuntimeError("no threads"), RuntimeError("no threads")]
    )
    slot = service._Supervised("RawProcessor #0", factory)

    with clock(0, BACKOFF):
        slot.ensure_alive()
        slot.ensure_alive()

    assert slot.thread is dead
    assert dead.requeue_inflight.call_count == 2
    # Asked twice, but the worker only had it to give once.
    handed_back = [
        call.args[0]
        for call in logger.warning.call_args_list
        if "put it back on the queue" in call.args[0]
    ]
    assert len(handed_back) == 1


def test_requeue_failure_does_not_block_the_restart(
    running_shutdown_event, logger, clock
):
    dead = _thread(alive=False)
    dead.requeue_inflight = MagicMock(side_effect=RuntimeError("queue closed"))
    replacement = _thread(alive=True)
    factory = MagicMock(side_effect=[dead, replacement])
    slot = service._Supervised("RawProcessor #0", factory)

    with clock(0):
        slot.ensure_alive()

    assert slot.thread is replacement
    assert logger.exception.called


def test_thread_without_inflight_work_is_restarted_normally(
    running_shutdown_event, logger, clock
):
    """A thread that holds nothing off its queue must not be probed clumsily."""
    dead = _thread(alive=False)
    replacement = _thread(alive=True)
    factory = MagicMock(side_effect=[dead, replacement])
    slot = service._Supervised("PeakRecomputeWorker #0", factory)

    with clock(0):
        slot.ensure_alive()

    assert slot.thread is replacement
    # Absence of the handover is expected, not an error to report or trip over.
    logger.warning.assert_not_called()
    logger.exception.assert_not_called()


def test_unrestartable_slot_is_reported_and_left_alone(
    running_shutdown_event, logger, clock
):
    """A replacement FSWatcher would rescan and re-offer files already in flight.

    So the watchers are watched and reported, never rebuilt: the death is the
    thing that was invisible, and a service restart is the safe recovery.
    """
    dead = _thread(alive=False)
    factory = MagicMock(side_effect=[dead, _thread(alive=True)])
    slot = service._Supervised("FSWatcher *.raw", factory, restartable=False)

    with clock(0):
        slot.ensure_alive()

    assert slot.thread is dead
    assert factory.call_count == 1
    assert _errors(logger, "cannot be replaced while the service runs")


def test_join_skips_a_thread_that_already_exited(running_shutdown_event, logger):
    dead = _thread(alive=False)
    slot = service._Supervised("RawProcessor #0", MagicMock(side_effect=[dead]))

    slot.join()

    dead.join.assert_not_called()


def test_join_waits_for_a_running_thread(running_shutdown_event, logger):
    """The shutdown path must still wait for a processor mid-conversion."""
    live = _thread(alive=True)
    slot = service._Supervised("RawProcessor #0", MagicMock(side_effect=[live]))

    slot.join()

    live.join.assert_called_once()


def test_requeues_the_work_the_dead_thread_was_holding(
    running_shutdown_event, logger, clock
):
    """The watcher never re-offers it, so the supervisor must ask for it back."""
    dead = _holding("/streams/sample.raw")
    factory = MagicMock(side_effect=[dead, _thread(alive=True)])
    slot = service._Supervised("RawProcessor #0", factory)

    with clock(0):
        slot.ensure_alive()

    dead.requeue_inflight.assert_called_once_with()
    assert any(
        "/streams/sample.raw" in call.args[0] for call in logger.warning.call_args_list
    )


# --- the handover contract, against the real workers ------------------------


def _raw_processor(queue: Queue) -> RawProcessor:
    """A real processor, constructed the way the service constructs it."""
    return RawProcessor(
        socket_client=MagicMock(), file_queue=queue, shutdown_event=Event()
    )


def _peak_worker(queue: Queue, guard=None) -> PeakRecomputeWorker:
    """A real peak worker, constructed the way the service constructs it."""
    if guard is None:
        guard = MagicMock()
        guard.acquire.return_value = (True, None)
    return PeakRecomputeWorker(
        socket_client=MagicMock(),
        peak_recompute_queue=queue,
        peak_guard=guard,
        shutdown_event=Event(),
    )


def test_processor_hands_back_a_file_it_was_converting(tmp_path):
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"")
    queue = Queue()
    processor = _raw_processor(queue)
    processor.file_to_process = str(raw)

    assert processor.requeue_inflight() == str(raw)

    assert queue.get_nowait() == str(raw)
    # Handed over, so a slot that has to ask again gets nothing.
    assert processor.file_to_process is None
    assert processor.requeue_inflight() is None
    assert queue.empty()


def test_processor_does_not_hand_back_a_file_it_already_finished(tmp_path):
    """file_to_process outlives the conversion, so the path alone is not enough.

    A processor is idle most of the time, and if the attribute still named the
    last file it converted - which has been deleted from the streams folder -
    handing that back would make the replacement fail and report a processing
    error for an upload the user was already told succeeded.
    """
    queue = Queue()
    processor = _raw_processor(queue)
    processor.file_to_process = str(tmp_path / "already-converted.raw")

    reporter = MagicMock()
    with patch.object(base_processor, "runtime", reporter):
        assert processor.requeue_inflight() is None

    assert queue.empty()
    assert processor.file_to_process is None
    # Dropping it silently would hide a file that is missing for some other
    # reason, such as an unmounted streams share.
    assert reporter.logger.warning.called
    assert "already-converted.raw" in reporter.logger.warning.call_args.args[0]


def test_processor_with_no_file_hands_back_nothing():
    queue = Queue()
    processor = _raw_processor(queue)

    assert processor.file_to_process is None
    assert processor.requeue_inflight() is None
    assert queue.empty()


def test_processor_clears_the_marker_once_a_file_is_dealt_with(tmp_path, monkeypatch):
    """Drives the real loop: after an item is handled, nothing is in flight.

    This is what makes the marker mean "in flight" rather than "last touched",
    and it is the reason an idle thread's death re-queues nothing. The failure
    path is asserted explicitly - reported to the user, moved aside, marker
    cleared - so that the loop reaching the catch-all handler instead would not
    pass for the same reason.
    """
    # Pin the DLL-free reader, so this runs the same path everywhere.
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "opentfraw")
    monkeypatch.delenv("MASCOPE_THERMO_DLL_DIR", raising=False)

    # A real file that is not a real .raw, so one iteration runs the failure
    # path end to end rather than tripping over a missing path.
    raw = tmp_path / "sample.raw"
    raw.write_bytes(b"not a raw file")
    queue = Queue()
    queue.put(str(raw))
    processor = _raw_processor(queue)

    # Let the loop run exactly one iteration, then leave.
    iterations = iter([False])
    shutdown = MagicMock()
    shutdown.is_set.side_effect = lambda: next(iterations, True)
    processor.shutdown_event = shutdown

    processor.run()

    # The inner handler ran: the user was told, and the file was moved aside.
    events = [call.args[0] for call in processor.socket_client.emit.call_args_list]
    assert "file_processing_error" in events
    assert not raw.exists()
    assert (tmp_path / "failed_files" / "sample.raw").exists()
    # And nothing is left in flight for a later death to hand back.
    assert processor.file_to_process is None
    assert processor.requeue_inflight() is None


def test_peak_worker_hands_back_the_request_it_was_running():
    """Otherwise the caller waits forever for a completion event nobody sends."""
    queue = Queue()
    guard = MagicMock()
    guard.acquire.return_value = (True, None)
    worker = _peak_worker(queue, guard)
    request = {"filename": "sample.raw", "process_id": "abc"}
    worker._inflight = request

    handed_back = worker.requeue_inflight()

    assert "sample.raw" in handed_back
    assert queue.get_nowait() is request
    # The slot released as the thread died has to be taken again, or the UI can
    # start a second run over the same filestore.
    guard.acquire.assert_called_once_with("sample.raw")
    assert worker.requeue_inflight() is None
    assert queue.empty()


def test_peak_worker_does_not_duplicate_a_request_already_running():
    """If the slot is taken, that other request is doing this work.

    It carries a different process id though, so the caller of the dropped one
    still has to be told - otherwise this branch recreates the hanging progress
    dialog that the handover exists to prevent.
    """
    queue = Queue()
    guard = MagicMock()
    guard.acquire.return_value = (False, "already in progress")
    worker = _peak_worker(queue, guard)
    worker._inflight = {"filename": "sample.raw", "process_id": "abc"}

    assert worker.requeue_inflight() is None

    assert queue.empty()
    event, payload = worker.socket_client.emit.call_args.args[:2]
    assert event == "peak_detection_error"
    assert payload["process_id"] == "abc"


def test_peak_worker_with_no_request_hands_back_nothing():
    assert _peak_worker(Queue()).requeue_inflight() is None


def test_every_supervised_worker_implements_the_handover():
    """The supervisor probes by name and calls with no arguments.

    A rename or a new required parameter would silently turn the handover into
    a no-op, dropping in-flight work without a word, and no other test would
    notice because the rest of the suite drives the slot with stand-ins.
    """
    for worker_type in (RawProcessor, H5Processor, PeakRecomputeWorker):
        handover = getattr(worker_type, "requeue_inflight", None)
        assert callable(handover), (
            f"{worker_type.__name__} no longer implements requeue_inflight, so the "
            f"supervisor would drop its in-flight work without a word"
        )
        required = [
            name
            for name, param in inspect.signature(handover).parameters.items()
            if name != "self" and param.default is inspect.Parameter.empty
        ]
        assert not required, (
            f"{worker_type.__name__}.requeue_inflight now requires {required}; the "
            f"supervisor calls it with no arguments"
        )


def test_the_service_wires_the_watchers_as_unrestartable(tmp_path):
    """The slot honours restartable=False; run() has to actually pass it.

    Without this the whole reason the watchers are not rebuilt lives in one
    keyword argument that no test would notice the loss of.
    """
    created = []
    real_slot = service._Supervised

    class _Recording(real_slot):
        def __init__(self, name, factory, restartable=True):
            created.append((name, restartable))
            super().__init__(name, factory, restartable)

    shutdown = MagicMock()
    shutdown.is_set.return_value = True  # main() returns on its first check

    fake_runtime = MagicMock()
    fake_runtime.config.source = str(tmp_path)
    fake_runtime.config.h5_threads = 2
    fake_runtime.config.raw_threads = 2
    fake_runtime.config.interval = 3

    with (
        patch.object(service, "_Supervised", _Recording),
        patch.object(service, "SHUTDOWN_EVENT", shutdown),
        patch.object(service, "runtime", fake_runtime),
        patch.object(service, "wait_for_backend", lambda: True),
        patch.object(service, "FSWatcher", MagicMock()),
        patch.object(service, "H5Processor", MagicMock()),
        patch.object(service, "RawProcessor", MagicMock()),
        patch.object(service, "PeakRecomputeWorker", MagicMock()),
    ):
        service.run()

    watchers = [(n, r) for n, r in created if n.startswith("FSWatcher")]
    workers = [(n, r) for n, r in created if not n.startswith("FSWatcher")]

    assert len(watchers) == 2, created
    assert all(restartable is False for _, restartable in watchers), watchers
    # Everything else is still replaced in place.
    assert workers
    assert all(restartable is True for _, restartable in workers), workers
