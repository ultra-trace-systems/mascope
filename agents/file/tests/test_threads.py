"""Unit tests for the counting of interrupts, and for the wait that raises them.

Python raises ``KeyboardInterrupt`` at whichever instruction the main thread
has reached, which leaves a lock held when that instruction is just past the
taking of one (``test_agent.py`` places an interrupt there). So an agent that
runs on the main thread has its interrupts counted, and raises each one
itself where no lock is in hand. These hold what that rests on: what is
counted, on which thread, in place of which handler, and where it is raised.

The interrupts are real ones, sent to the test's own thread.
"""

import signal
import threading
import time
from contextlib import contextmanager

import pytest

from mascope_file_agent import _threads
from mascope_file_agent._threads import interrupts, wait_for


@contextmanager
def nothing_raised_where_it_arrived():
    """Fail the test, not the test run, on an interrupt that was not counted."""
    try:
        yield
    except KeyboardInterrupt:
        pytest.fail("an interrupt was raised where it arrived")


def test_an_interrupt_is_counted_and_raised_once_when_asked_for(ctrl_c):
    with interrupts.recorded():
        with nothing_raised_where_it_arrived():
            ctrl_c()
            ctrl_c()

        # Two came, so two are raised, and no third.
        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()
        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()
        interrupts.raise_pending()


def test_the_handler_is_pythons_again_once_the_block_ends(ctrl_c):
    with pytest.raises(RuntimeError, match="however it ends"):
        with interrupts.recorded():
            assert signal.getsignal(signal.SIGINT) is not signal.default_int_handler
            with nothing_raised_where_it_arrived():
                ctrl_c()
            raise RuntimeError("however it ends")

    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    # What was counted and never asked for went with the block.
    interrupts.raise_pending()
    with pytest.raises(KeyboardInterrupt):
        ctrl_c()


def test_a_block_inside_another_goes_on_counting_for_it(ctrl_c):
    with interrupts.recorded():
        with interrupts.recorded():
            with nothing_raised_where_it_arrived():
                ctrl_c()
        # The inner block ending did not end the counting, or lose the count.
        with nothing_raised_where_it_arrived():
            ctrl_c()
        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()
        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()
        interrupts.raise_pending()


@pytest.mark.parametrize("ignored", [False, True])
def test_a_handler_that_is_not_pythons_own_is_left_in_place(ctrl_c, ignored):
    """A program's own handler, and a process started with interrupts ignored."""
    calls = []
    handler = signal.SIG_IGN if ignored else lambda signum, frame: calls.append(signum)
    signal.signal(signal.SIGINT, handler)

    with interrupts.recorded():
        assert signal.getsignal(signal.SIGINT) is handler
        ctrl_c()
        interrupts.raise_pending()

    assert signal.getsignal(signal.SIGINT) is handler
    assert calls == ([] if ignored else [signal.SIGINT])


def test_nothing_is_replaced_off_the_main_thread():
    """Python hands an interrupt to the main thread, and takes a handler from it."""
    handler = signal.getsignal(signal.SIGINT)
    found = []

    def elsewhere():
        with interrupts.recorded():
            found.append(signal.getsignal(signal.SIGINT))
            interrupts.raise_pending()

    thread = threading.Thread(target=elsewhere)
    thread.start()
    thread.join(10)

    assert found == [handler]


def test_a_counted_interrupt_is_raised_on_the_thread_that_was_interrupted(
    ctrl_c, monkeypatch
):
    """A stop asked from another thread is not the one Ctrl+C was meant for."""
    monkeypatch.setattr(_threads, "WAIT_SLICE", 0.01)
    raised = []

    def elsewhere():
        try:
            interrupts.raise_pending()
            wait_for(threading.Event(), 0.05)
        except KeyboardInterrupt:
            raised.append(threading.current_thread().name)

    with interrupts.recorded():
        with nothing_raised_where_it_arrived():
            ctrl_c()
        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join(10)

        assert raised == []
        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()


def test_the_first_interrupt_of_an_immediate_block_is_raised_where_it_arrives(ctrl_c):
    """As at a prompt, which is written for Ctrl+C to get out of it."""
    with interrupts.recorded():
        with interrupts.immediate():
            with pytest.raises(KeyboardInterrupt):
                ctrl_c()
            # The next one is the agent's again, here and after the block.
            with nothing_raised_where_it_arrived():
                ctrl_c()
        with nothing_raised_where_it_arrived():
            ctrl_c()

        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()
        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()
        interrupts.raise_pending()


def test_an_immediate_block_that_ends_uninterrupted_leaves_them_counted(ctrl_c):
    with interrupts.recorded():
        with interrupts.immediate():
            pass
        with nothing_raised_where_it_arrived():
            ctrl_c()
        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()


def test_a_wait_raises_a_counted_interrupt_between_two_slices(ctrl_c, monkeypatch):
    monkeypatch.setattr(_threads, "WAIT_SLICE", 0.01)
    with interrupts.recorded():
        with nothing_raised_where_it_arrived():
            ctrl_c()
        began = time.monotonic()

        with pytest.raises(KeyboardInterrupt):
            wait_for(threading.Event(), 30)

        assert time.monotonic() - began < 5
        # It was the one interrupt: the wait runs out as it would have.
        assert wait_for(threading.Event(), 0.05) is False


def test_a_wait_with_nothing_left_to_wait_for_raises_nothing(ctrl_c):
    """An interrupt asks for less waiting, and here there is none to cut short."""
    happened = threading.Event()
    happened.set()
    with interrupts.recorded():
        with nothing_raised_where_it_arrived():
            ctrl_c()

            assert wait_for(happened) is True
            # No time to wait is what the third interrupt asks of a stop.
            assert wait_for(threading.Event(), 0) is False

        with pytest.raises(KeyboardInterrupt):
            interrupts.raise_pending()
