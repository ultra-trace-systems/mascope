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

Where the interrupt is raised matters as much as when. Python raises
``KeyboardInterrupt`` on the main thread at whichever instruction that
thread has reached, and ``Event``, ``Condition`` and ``Queue`` are written
in Python around a lock: raised once the lock is taken and before the
``with`` block that releases it is entered, the interrupt leaves the lock
held, and whatever comes for it next waits for ever. An agent's main thread
takes such a lock several times a second. So while an agent runs on the main
thread its interrupts are counted instead (:data:`interrupts`), and the
thread raises each one itself between two waits, with no lock in hand.
"""

import signal
import time
from contextlib import contextmanager
from threading import Event, Thread, current_thread, get_ident, main_thread
from typing import Callable, Iterator


#: Seconds one wait lasts at most, which is how long an interrupt can go
#: unanswered where a wait cannot be interrupted, or where it is only counted.
WAIT_SLICE = 0.5


class Interrupts:
    """Ctrl+C, counted where it arrives and raised where that does no harm.

    Inside :meth:`recorded`, the handler here stands in for Python's own: it
    counts the interrupt and does nothing else. The thread raises it for
    itself with :meth:`raise_pending`, at a place of its choosing. Each is
    raised once, in the order they came, so three of them are still three.
    """

    def __init__(self) -> None:
        # The handler writes the first and the thread it interrupts writes
        # the second. Neither needs a lock, since the handler runs on that
        # same thread, between two of its instructions - and the handler
        # must not take one, since the thread may be holding it.
        self._recorded = 0
        self._answered = 0
        # The thread whose interrupts are counted; None while none are.
        self._listener: int | None = None
        self._immediate = False

    def _record(self, signum, frame) -> None:
        """Handle SIGINT inside :meth:`recorded`."""
        if self._immediate:
            # Counted again from here on, and said before it is raised: what
            # this cuts short may be the step that would have said so.
            self._immediate = False
            raise KeyboardInterrupt
        self._recorded += 1

    @contextmanager
    def recorded(self) -> Iterator[None]:
        """Count the interrupts of the block instead of raising them.

        Only on the main thread, the one Python hands an interrupt to, and
        only in place of Python's own handler, which is put back as the
        block ends. A program that has set a handler of its own has said
        what an interrupt does, and a process started with interrupts
        ignored goes on ignoring them. Wherever nothing is replaced, the
        block runs as it would have without this.
        """
        if (
            current_thread() is not main_thread()
            or signal.getsignal(signal.SIGINT) is not signal.default_int_handler
        ):
            yield
            return
        self._recorded = self._answered = 0
        self._immediate = False
        handler = self._record
        try:
            signal.signal(signal.SIGINT, handler)
        except ValueError:
            # A main thread that cannot be given a handler, as that of an
            # interpreter embedded in another program may be.
            yield
            return
        self._listener = get_ident()
        try:
            yield
        finally:
            self._listener = None
            if signal.getsignal(signal.SIGINT) is handler:
                signal.signal(signal.SIGINT, signal.default_int_handler)

    def raise_pending(self) -> None:
        """Raise the oldest interrupt that was counted and not yet raised.

        For the thread whose interrupts are counted to call where an
        exception can pass: between two waits, not inside one. On any other
        thread, and while none are counted, this does nothing.

        :raises KeyboardInterrupt: For an interrupt that was counted
        """
        if self._listener == get_ident() and self._answered < self._recorded:
            self._answered += 1
            raise KeyboardInterrupt

    @contextmanager
    def immediate(self) -> Iterator[None]:
        """Have the block interrupted the way Python does it: there and then.

        For a step that talks to whoever is at the console and is written
        for Ctrl+C to cut it short, such as a prompt, or a wait that says
        "Ctrl+C to cancel". The first interrupt of the block is raised
        where it arrives, and the ones after it are counted again. On a
        thread whose interrupts are not counted, the block runs as it would
        have without this.
        """
        if self._listener != get_ident():
            yield
            return
        self._immediate = True
        try:
            yield
        finally:
            self._immediate = False


#: The interrupts of the process: one of these, as there is one SIGINT.
interrupts = Interrupts()


def wait_for(event: Event, timeout: float | None = None) -> bool:
    """Wait for ``event``, without being deaf to an interrupt meanwhile.

    :param event: What to wait for
    :type event: Event
    :param timeout: Seconds to wait; None to wait until it is set
    :type timeout: float | None
    :return: Whether the event is set
    :rtype: bool
    :raises KeyboardInterrupt: Between two waits, for an interrupt that was
        counted (:class:`Interrupts`) while there is still something to
        wait for
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
        interrupts.raise_pending()


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
