"""
Tests for the converter's worker-thread supervision (``service._Supervised``).

A processor thread that dies leaves its queue filling with files nobody will
convert, and nothing else in the service notices - the converter keeps accepting
uploads it will never process until someone restarts it (#1350). These cover the
slot that notices and stands up a replacement.
"""

from unittest.mock import MagicMock, patch

import pytest

from mascope_backend.file_converter import service


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


def _thread(alive: bool, file_to_process=None, file_queue=None) -> MagicMock:
    """A thread stand-in reporting the given liveness."""
    thread = MagicMock()
    thread.is_alive.return_value = alive
    thread.file_to_process = file_to_process
    thread.file_queue = file_queue
    return thread


def test_live_thread_is_left_alone(running_shutdown_event, logger):
    factory = MagicMock(side_effect=[_thread(alive=True)])
    slot = service._Supervised("RawProcessor #0", factory)

    slot.ensure_alive()

    # One call to build the initial thread, none to replace it.
    assert factory.call_count == 1
    logger.error.assert_not_called()


def test_dead_thread_is_replaced_and_reported(running_shutdown_event, logger):
    first, replacement = _thread(alive=False), _thread(alive=True)
    factory = MagicMock(side_effect=[first, replacement])
    slot = service._Supervised("RawProcessor #0", factory)

    slot.ensure_alive()

    assert slot.thread is replacement
    replacement.start.assert_called_once()
    # The death must be visible: this is the signal that was missing entirely.
    assert logger.error.called
    assert "RawProcessor #0" in logger.error.call_args_list[0].args[0]


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


def test_restarts_are_capped(running_shutdown_event, logger):
    threads = [
        _thread(alive=False) for _ in range(service._Supervised.MAX_RESTARTS + 2)
    ]
    factory = MagicMock(side_effect=threads)
    slot = service._Supervised("RawProcessor #0", factory)

    for _ in range(service._Supervised.MAX_RESTARTS + 3):
        slot.ensure_alive()

    # Initial construction plus MAX_RESTARTS replacements, then it gives up
    # rather than spinning on a thread that dies immediately every time.
    assert factory.call_count == service._Supervised.MAX_RESTARTS + 1


def test_failing_restart_does_not_propagate(running_shutdown_event, logger):
    factory = MagicMock(side_effect=[_thread(alive=False), RuntimeError("no threads")])
    slot = service._Supervised("RawProcessor #0", factory)

    # Supervision runs inside the service's main loop; raising here would take
    # the whole converter down instead of the one thread it failed to restart.
    slot.ensure_alive()

    assert logger.exception.called


def test_join_skips_a_thread_that_already_exited(running_shutdown_event, logger):
    dead = _thread(alive=False)
    slot = service._Supervised("RawProcessor #0", MagicMock(side_effect=[dead]))

    slot.join()

    dead.join.assert_not_called()


def test_requeues_the_file_the_dead_thread_was_holding(running_shutdown_event, logger):
    """The watcher never re-offers it, so the supervisor must."""
    queue = MagicMock()
    dead = _thread(alive=False, file_to_process="/streams/sample.raw", file_queue=queue)
    factory = MagicMock(side_effect=[dead, _thread(alive=True)])
    slot = service._Supervised("RawProcessor #0", factory)

    slot.ensure_alive()

    queue.put.assert_called_once_with("/streams/sample.raw")


def test_idle_thread_death_requeues_nothing(running_shutdown_event, logger):
    queue = MagicMock()
    dead = _thread(alive=False, file_to_process=None, file_queue=queue)
    factory = MagicMock(side_effect=[dead, _thread(alive=True)])
    slot = service._Supervised("RawProcessor #0", factory)

    slot.ensure_alive()

    queue.put.assert_not_called()


def test_requeue_failure_does_not_block_the_restart(running_shutdown_event, logger):
    queue = MagicMock()
    queue.put.side_effect = RuntimeError("queue closed")
    dead = _thread(alive=False, file_to_process="/streams/sample.raw", file_queue=queue)
    replacement = _thread(alive=True)
    factory = MagicMock(side_effect=[dead, replacement])
    slot = service._Supervised("RawProcessor #0", factory)

    slot.ensure_alive()

    assert slot.thread is replacement
    assert logger.exception.called


def test_worker_without_a_queue_is_restarted_normally(running_shutdown_event, logger):
    """PeakRecomputeWorker has no file_queue; the slot must not assume one."""
    dead = MagicMock()
    dead.is_alive.return_value = False
    del dead.file_to_process
    del dead.file_queue
    replacement = _thread(alive=True)
    factory = MagicMock(side_effect=[dead, replacement])
    slot = service._Supervised("PeakRecomputeWorker #0", factory)

    slot.ensure_alive()

    assert slot.thread is replacement
