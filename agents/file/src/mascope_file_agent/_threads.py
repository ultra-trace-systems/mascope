"""Threads the agent can wait for, and still hear an interrupt while it waits.

``Thread.join()`` is the obvious way to wait for a thread, and the wrong one
for a program that is stopped with Ctrl+C:

- On Windows a wait for a lock cannot be interrupted, and a join without a
  timeout is one such wait with no end to it. An interrupt that arrives
  during it is acted on only once the thread has ended by itself.
- Elsewhere the interrupt does reach the join, and CPython 3.12 then marks
  the thread that was being waited for as stopped while it is still running
  (python/cpython#90882). ``is_alive()`` goes through the same code, so
  after an interrupt neither can be believed.

So each thread here sets an event as it ends, and waiting for it is waiting
for that event, in slices short enough for an interrupt to be acted on
between two of them.
"""

import time
from threading import Event, Thread, current_thread
from typing import Callable


#: Seconds one wait lasts at most, which is how long an interrupt can go
#: unanswered where a wait cannot be interrupted.
WAIT_SLICE = 0.5


def wait_for(event: Event, timeout: float | None = None) -> bool:
    """Wait for ``event``, without being deaf to an interrupt meanwhile.

    :param event: What to wait for
    :type event: Event
    :param timeout: Seconds to wait; None to wait until it is set
    :type timeout: float | None
    :return: Whether the event is set
    :rtype: bool
    """
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if deadline is None:
            wait = WAIT_SLICE
        else:
            wait = min(WAIT_SLICE, deadline - time.monotonic())
            if wait <= 0:
                return event.is_set()
        if event.wait(wait):
            return True


class Task:
    """A daemon thread that says when it has ended.

    :param target: What the thread runs.
    :param name: The thread's name.
    :param args: Arguments for ``target``.
    """

    def __init__(self, target: Callable, name: str, args: tuple = ()):
        self._target = target
        self._args = args
        self._ended = Event()
        self._started = False
        self.thread = Thread(target=self._run, name=name, daemon=True)

    def _run(self) -> None:
        try:
            self._target(*self._args)
        finally:
            self._ended.set()

    def start(self) -> None:
        """Start the thread."""
        self.thread.start()
        self._started = True

    @property
    def ended(self) -> bool:
        """Whether the thread has run to its end; False if it never started."""
        return self._ended.is_set()

    @property
    def is_current(self) -> bool:
        """Whether this is the thread that is asking."""
        return self.thread is current_thread()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the thread to end.

        :param timeout: Seconds to wait; None to wait until it has ended
        :type timeout: float | None
        :return: Whether nothing of it is left running: it has ended, or it
            was never started. False at once when asked from the thread
            itself, which cannot wait for its own end.
        :rtype: bool
        """
        if not self._started:
            return True
        if self.is_current:
            return False
        return wait_for(self._ended, timeout)
