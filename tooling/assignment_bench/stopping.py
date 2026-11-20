"""How a bench command ends when something stops it from outside.

A bench command runs in its caller's session, and the caller can go away
under it: the budget watchdog stops an agent's session, a tmux window is
closed, a person presses Ctrl-C. Python's own answer to SIGTERM and SIGHUP
is to end at once, with no ``finally`` run, which left a run's backend
holding the bench's port and its database migrated to a branch (MAS-1055).

``bench.py`` installs :data:`STOPPER` for every command, so that:

- **SIGTERM, SIGHUP and SIGINT** raise :class:`Stopped` in the main thread,
  once: the command's ``finally`` blocks stop the backend, put the database
  back and free the lock, and ``main`` exits ``128 + signal``.
- **A cleanup is not cut short.** Inside :meth:`Stopper.shield` a signal is
  only recorded, and raised when the shield ends: a second signal, or a first
  one that comes while the cleanup runs, cannot leave a database half
  restored.
- **A starter that goes away is a hangup.** :func:`watch_parent` checks the
  parent process; when the process that started the command is gone, it
  sends the command SIGHUP itself, which a starter killed with SIGKILL never
  does.
- **A closed console is not an error.** Once the caller is gone, its pipe is
  too: :class:`QuietStream` drops what can no longer be printed, rather than
  fail the cleanup on the first line it says.
"""

from __future__ import annotations

import contextlib
import io
import os
import signal
import threading


#: The signals a bench command ends cleanly on.
SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)

#: Seconds between two looks at the parent process.
PARENT_POLL_S = 5.0

#: Why a command stopped, when its parent went away.
PARENT_GONE = "the process that started it went away"


class Stopped(BaseException):
    """A signal stopped the command; ``finally`` blocks clean up on the way out.

    A ``BaseException``, as ``KeyboardInterrupt`` is, so that no ``except
    Exception`` that reports a step's failure takes it for one.

    :ivar signum: The signal.
    :ivar why: What stopped it, in words: the signal's name, or that the
        starter went away.
    """

    def __init__(self, signum: int, why: str):
        super().__init__(why)
        self.signum = signum
        self.why = why

    @property
    def exit_code(self) -> int:
        return 128 + self.signum


class Stopper:
    """Turns the stopping signals into :class:`Stopped`, once, outside a shield."""

    def __init__(self):
        self._lock = threading.Lock()
        self._shields = 0
        self.pending: Stopped | None = None
        self.raised: Stopped | None = None
        #: Why the next signal is sent, when the bench sends it itself.
        self.why: str | None = None
        self._previous: dict[int, object] = {}

    @property
    def stopping(self) -> Stopped | None:
        """The stop under way, raised or pending, or None."""
        return self.raised or self.pending

    def install(self, signals=SIGNALS) -> None:
        """Take the signals over; only the main thread may, and only it is."""
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in signals:
            self._previous[signum] = signal.signal(signum, self._handle)

    def uninstall(self) -> None:
        for signum, previous in self._previous.items():
            signal.signal(signum, previous)
        self._previous.clear()
        self.reset()

    def reset(self) -> None:
        self._shields = 0
        self.pending = self.raised = None
        self.why = None

    def _handle(self, signum, frame) -> None:
        if self.stopping is not None:
            # Already on the way out: the cleanup goes on.
            return
        name = signal.Signals(signum).name
        stop = Stopped(signum, self.why or name)
        if self._shields:
            self.pending = stop
            return
        self.raised = stop
        raise stop

    @contextlib.contextmanager
    def shield(self):
        """Run a block that a signal must not cut short; a signal that comes
        meanwhile is raised when the block ends, unless it raised itself."""
        with self._lock:
            self._shields += 1
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            with self._lock:
                self._shields -= 1
                release = not self._shields and self.pending is not None
            if release and not failed:
                stop, self.pending = self.pending, None
                self.raised = stop
                raise stop


#: The one stopper of the process (signal handlers are per process).
STOPPER = Stopper()


def watch_parent(
    stopper: Stopper = STOPPER,
    poll_s: float = PARENT_POLL_S,
    getppid=os.getppid,
    kill=os.kill,
) -> threading.Event:
    """Send this process SIGHUP once the process that started it is gone.

    A process whose parent ends is handed to another one (init, or a
    subreaper), so its parent pid changes; that is the sign looked for. A
    command started with no parent of its own (parent 1) is never stopped
    by this.

    :return: An event that ends the watch when set.
    """
    parent = getppid()
    done = threading.Event()

    def loop() -> None:
        while not done.wait(poll_s):
            if getppid() != parent:
                stopper.why = PARENT_GONE
                kill(os.getpid(), signal.SIGHUP)
                return

    threading.Thread(target=loop, name="bench-parent-watch", daemon=True).start()
    return done


class QuietStream(io.TextIOBase):
    """A console that may close under the command: what it can no longer
    take is dropped, so a cleanup's lines never fail the cleanup."""

    def __init__(self, stream):
        self._stream = stream
        self.closed_under = False

    def write(self, text: str) -> int:
        if not self.closed_under:
            try:
                return self._stream.write(text)
            except (OSError, ValueError):
                self.closed_under = True
        return len(text)

    def flush(self) -> None:
        if not self.closed_under:
            try:
                self._stream.flush()
            except (OSError, ValueError):
                self.closed_under = True

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._stream.fileno()

    def isatty(self) -> bool:
        try:
            return self._stream.isatty()
        except (OSError, ValueError):
            return False

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", "utf-8")
