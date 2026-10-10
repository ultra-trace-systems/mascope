"""Unit tests for the agent's life: built, started, stopped.

Hermetic: the SDK upload, the startup credential check, the status
follower's questions and the question of what the server can do are
monkeypatched, so nothing reaches a network. The agent's own threads are
real, and watch the test's own folder.
"""

import ctypes
import functools
import operator
import os
import queue
import signal
import threading
import time

import pytest

from mascope_file_agent import (
    Agent,
    ConfigError,
    Repair,
    _threads,
    capabilities,
    credentials,
    status,
    uploader,
    watcher,
)
from mascope_file_agent.wizard import CREDENTIAL_OK
from mascope_sdk.exceptions import AuthenticationError, MascopeConnectionError


class RecordingLogger:
    def __init__(self):
        self.lines = []

    def _log(self, level):
        return lambda message: self.lines.append((level, message))

    def __getattr__(self, level):
        if level in ("debug", "info", "warning", "error", "exception"):
            return self._log(level)
        raise AttributeError(level)

    def said(self, level, text):
        return any(lvl == level and text in message for lvl, message in self.lines)


def wait_for(condition, timeout=10):
    """Whether ``condition`` came true within ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class Uploads:
    """Stands in for the SDK's upload: records each call, and can hold it."""

    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.error = None

    def hold(self):
        self.release.clear()

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        self.release.wait(30)
        if self.error is not None:
            raise self.error


@pytest.fixture
def uploads(monkeypatch):
    uploads = Uploads()
    monkeypatch.setattr(uploader, "api_post_file_tus", uploads)
    yield uploads
    # Whatever a test left held, let go of, so no worker outlives it waiting.
    uploads.release.set()


@pytest.fixture
def make_agent(monkeypatch, make_settings, uploads):
    """Agents that upload any file at once, and are stopped after the test."""
    monkeypatch.setattr(watcher, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(uploader, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(
        credentials, "check_credential", lambda host, token, verify: (CREDENTIAL_OK, "")
    )
    monkeypatch.setattr(status.StatusFollower, "poll_due", lambda self: None)
    # A server that announces nothing: uploads go as they did before a server
    # could keep anything with them.
    monkeypatch.setattr(capabilities.ServerCapabilities, "ask", lambda self: {})
    made = []

    def make(**settings):
        agent = Agent(
            make_settings(**{"timeout": 0, **settings}), logger=RecordingLogger()
        )
        made.append(agent)
        return agent

    yield make
    uploads.release.set()
    for agent in made:
        agent.stop(timeout=10)


@pytest.fixture
def sample(tmp_path):
    sample = tmp_path / "x.raw"
    sample.write_bytes(b"data")
    return str(sample)


# ---------------------------------------------------------------------------
# Built
# ---------------------------------------------------------------------------


def test_an_agent_takes_its_server_from_the_settings(make_agent):
    assert make_agent().url == "https://mascope.example.com"
    # An explicit scheme is kept: a plain-HTTP server stays plain HTTP.
    assert make_agent(host="http://localhost:8090").url == "http://localhost:8090"


def test_a_given_url_is_where_everything_is_asked(make_agent, monkeypatch):
    """Uploads, the renewal, the check at start and a pairing: one address.

    A program trying the agent against a local server names it once, and is
    not told that an address it never named could not be reached.
    """
    asked = []
    monkeypatch.setattr(
        credentials,
        "check_credential",
        lambda host, token, verify: (
            asked.append(("check", host)) or (CREDENTIAL_OK, "")
        ),
    )
    monkeypatch.setattr(
        credentials,
        "run_pairing",
        lambda host, verify, instrument=None: asked.append(("pair", host)),
    )
    agent = make_agent()
    local = Agent(agent.settings, "http://localhost:8090", logger=RecordingLogger())

    for built in (agent, local):
        built.credentials.check_at_start()
        built.credentials._pair()

    assert asked == [
        ("check", "https://mascope.example.com"),
        ("pair", "https://mascope.example.com"),
        ("check", "http://localhost:8090"),
        ("pair", "http://localhost:8090"),
    ]
    assert local.uploader.url == local.status_follower._url == "http://localhost:8090"


@pytest.mark.parametrize(
    "settings, complaint",
    [
        ({"instrument": ""}, "needs one"),
        ({"instrument": "orbi lab 2"}, "letters, digits and hyphens"),
        ({"source": "no-such-folder-anywhere"}, "watched folder does not exist"),
        ({"host": ""}, "No Mascope server"),
    ],
)
def test_an_agent_is_not_built_on_settings_it_cannot_run_on(
    make_agent, settings, complaint
):
    """Refused where the program that builds it can say so, not at an upload."""
    with pytest.raises(ConfigError, match=complaint):
        make_agent(**settings)


# ---------------------------------------------------------------------------
# Started
# ---------------------------------------------------------------------------


def test_a_started_agent_uploads_a_file_that_appears(make_agent, uploads, tmp_path):
    agent = make_agent()
    agent.start()
    assert agent.running

    sample = tmp_path / "x.raw"
    sample.write_bytes(b"data")

    assert wait_for(lambda: uploads.calls)
    assert uploads.calls[0]["filepath"] == str(sample)
    assert uploads.calls[0]["url"] == "https://mascope.example.com"
    assert uploads.calls[0]["instrument"] == "Orbi-Lab2"
    assert wait_for(
        lambda: agent.logger.said("info", "File upload of file x.raw succeeded!")
    )


def test_start_does_not_wait_for_the_credential_check(
    make_agent, monkeypatch, uploads, sample
):
    """A program that embeds the agent gets its thread back at once.

    The check asks the server, which can take as long as a connection timeout,
    and the files that appear meanwhile wait for it rather than for nothing.
    """
    answer = threading.Event()

    def slow_check(host, token, verify):
        answer.wait(30)
        return CREDENTIAL_OK, ""

    monkeypatch.setattr(credentials, "check_credential", slow_check)
    agent = make_agent()

    began = time.monotonic()
    agent.start()
    assert time.monotonic() - began < 5  # not the 30 s the check is out for

    agent.watcher.on_filesystem_object_created(sample)
    # Long enough for an upload that did not wait for the check to have begun.
    assert not uploads.started.wait(0.5)

    answer.set()
    assert wait_for(lambda: uploads.calls)


def test_a_second_start_is_refused(make_agent):
    agent = make_agent()
    agent.start()

    with pytest.raises(RuntimeError, match="started or stopped before"):
        agent.start()
    with pytest.raises(RuntimeError, match="started or stopped before"):
        agent.run_until_complete()

    # The refusal leaves the running agent as it was.
    assert agent.running


def test_a_start_that_fails_leaves_nothing_running(make_agent, tmp_path):
    """The watched folder can go between building the agent and starting it."""
    folder = tmp_path / "watched"
    folder.mkdir()
    agent = make_agent(source=str(folder))
    before = set(threading.enumerate())
    folder.rmdir()

    with pytest.raises(OSError):
        agent.start()

    assert not agent.running
    assert wait_for(lambda: not (set(threading.enumerate()) - before))


def test_a_stopped_agent_does_not_start_again(make_agent):
    """An agent runs once: its watcher's thread cannot be started twice."""
    agent = make_agent()
    agent.start()
    assert agent.stop(timeout=10)

    with pytest.raises(RuntimeError, match="started or stopped before"):
        agent.start()


def test_run_until_complete_returns_once_interrupted(make_agent, monkeypatch):
    """What the console program does: block until Ctrl+C, then stop the rest."""
    agent = make_agent()
    look = agent.uploader.jobs.get_nowait
    pressed = []

    def interrupted():
        if not pressed:
            pressed.append("Ctrl+C")
            raise KeyboardInterrupt
        return look()

    monkeypatch.setattr(agent.uploader.jobs, "get_nowait", interrupted)

    agent.run_until_complete()

    assert agent.logger.said("info", "Shutdown requested by user.")
    assert agent.logger.said("info", "File system watcher stopped")
    assert not agent.running
    assert not agent.watcher.observer.is_alive()


def test_an_interrupt_taken_on_the_upload_loops_own_jump_is_handled(
    make_agent, monkeypatch
):
    """A Ctrl+C that arrives between two looks at the queue, not during one.

    The interpreter acts on a pending signal where it next looks for one,
    and a loop's backward jump is such a place. CPython 3.12 hands what the
    signal raises there to the handler of the instruction before the jump's
    target, which for a loop that opens a ``try`` block is no handler of
    that block.

    So the interrupt here is a real one, placed on the ``continue`` of an
    empty queue: one C call raises the signal and then ``Empty``, and since
    a call that ends in an exception does not look for signals, the
    ``continue`` is the first instruction that does. The signal goes
    through the C library, because ``signal.raise_signal`` acts on it
    before it returns.
    """
    agent = make_agent()
    libc = ctypes.CDLL("ucrtbase" if os.name == "nt" else None)
    interrupt = functools.partial(getattr(libc, "raise"), int(signal.SIGINT))
    empty = queue.SimpleQueue().get_nowait
    looks = []

    def interrupted_as_it_looks():
        looks.append(len(looks))
        if len(looks) > 3:
            # The interrupt went nowhere: end the loop, and fail below.
            agent.shutdown_event.set()
        list(map(operator.call, [interrupt, empty]))

    monkeypatch.setattr(agent.uploader.jobs, "get_nowait", interrupted_as_it_looks)
    # Python's own handler, whatever the suite was started with.
    before = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        agent.uploader.run_until_complete()
    except KeyboardInterrupt:
        pytest.fail("the interrupt went past the handlers of the upload loop")
    finally:
        signal.signal(signal.SIGINT, before)

    assert len(looks) == 1
    assert agent.logger.said("info", "Shutdown requested by user.")
    assert agent.shutdown_event.is_set()


# ---------------------------------------------------------------------------
# Interrupted with a lock in hand
# ---------------------------------------------------------------------------


class Presses:
    """Ctrl+C for the main thread, at as many of its calls as are ``due``."""

    def __init__(self, ctrl_c, due=0):
        self._ctrl_c = ctrl_c
        self.due = due

    def __call__(self):
        if self.due and threading.current_thread() is threading.main_thread():
            self.due -= 1
            self._ctrl_c()


class InterruptedLock:
    """Stands in for the lock inside an event or a queue, and interrupts its taker.

    ``Event`` and ``Queue`` are written in Python around a lock, which they
    take through ``Condition.__enter__``: a call that takes the lock and only
    then returns to the ``with`` statement that will release it. What an
    interrupt raises in between leaves the lock held, with nothing left to
    release it. This takes the real lock and then has the main thread sent a
    real SIGINT, which Python acts on with the lock in hand and the ``with``
    block not yet entered.

    It also tells a lock that was left so. The thread that left it would wait
    for ever on coming for it again; here that is counted in ``left_held``
    and let through, so that a test fails where the agent would hang.

    :param lock: The lock to stand in for.
    :param presses: Called with the lock taken, to interrupt the taker.
    """

    def __init__(self, lock, presses):
        self._lock = lock
        self._presses = presses
        self._owner = None
        self.left_held = 0

    def acquire(self, blocking=True, timeout=-1):
        if blocking and self._owner == threading.get_ident():
            self.left_held += 1
            return True
        taken = self._lock.acquire(blocking, timeout)
        if taken:
            self._owner = threading.get_ident()
        return taken

    def release(self):
        self._owner = None
        self._lock.release()

    def locked(self):
        return self._lock.locked()

    def __enter__(self):
        self.acquire()
        self._presses()
        return True

    def __exit__(self, *exc):
        self.release()


def interrupted_event(presses, event=None):
    """An event whose lock has its taker interrupted; the lock is its ``lock``."""
    event = event or threading.Event()
    event.lock = event._cond._lock = InterruptedLock(event._cond._lock, presses)
    return event


def interrupted_queue(waiting, presses):
    """Have the lock of ``waiting`` interrupt its taker; that lock."""
    lock = InterruptedLock(waiting.mutex, presses)
    waiting.mutex = lock
    for condition in (waiting.not_empty, waiting.not_full, waiting.all_tasks_done):
        condition._lock = lock
    return lock


@pytest.mark.parametrize("held", ["the event the loop waits on", "the queue it reads"])
def test_an_interrupt_taken_with_a_lock_in_hand_does_not_leave_it_held(
    make_agent, ctrl_c, held
):
    """A Ctrl+C that arrives just as the upload loop has taken a lock.

    The loop takes two every time round: the lock of the event that stops
    it, and the lock of the queue of waiting files. An interrupt raised then
    and there left the lock held, and the agent waiting for it for ever at
    its next use - the event's when the loop sets it on its way out, the
    queue's when the stop empties it.
    """
    agent = make_agent()
    presses = Presses(ctrl_c, due=1)
    if held == "the event the loop waits on":
        lock = interrupted_event(presses, agent.shutdown_event).lock
    else:
        lock = interrupted_queue(agent.uploader.jobs, presses)

    try:
        agent.run_until_complete()
    except KeyboardInterrupt:
        pytest.fail("the interrupt went past the agent")

    assert presses.due == 0  # it was sent
    assert not lock.left_held
    assert not lock.locked()
    assert agent.logger.said("info", "Shutdown requested by user.")
    assert agent.logger.said("info", "File system watcher stopped")
    assert not agent.running
    # Interrupts are Python's to raise again.
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_three_interrupts_each_taken_with_a_lock_in_hand_are_answered_in_turn(
    make_agent, monkeypatch, uploads, sample, ctrl_c
):
    """Ctrl+C three times with an upload under way, each as a lock is taken.

    The first in the upload loop and the other two in the waits of the stop,
    each of which waits for the event a thread sets as it ends. No lock is
    left held, and the three are answered as ``run_until_complete`` says:
    stop, keep waiting, stop without the upload.
    """
    monkeypatch.setattr(_threads, "WAIT_SLICE", 0.05)
    in_the_loop, in_the_stop = Presses(ctrl_c), Presses(ctrl_c)
    ended = []

    def ends_interrupted():
        ended.append(interrupted_event(in_the_stop))
        return ended[-1]

    monkeypatch.setattr(_threads, "Event", ends_interrupted)
    agent = make_agent()
    stopping = interrupted_event(in_the_loop, agent.shutdown_event)

    def begun(**kwargs):
        # Only once a worker has the file, which is then one to wait for.
        in_the_loop.due, in_the_stop.due = 1, 2
        uploads(**kwargs)

    monkeypatch.setattr(uploader, "api_post_file_tus", begun)
    uploads.hold()
    agent.uploader.enqueue(sample)

    try:
        agent.run_until_complete()
    except KeyboardInterrupt:
        pytest.fail("an interrupt went past the agent")

    assert in_the_loop.due == in_the_stop.due == 0  # all three were sent
    said = [message for _, message in agent.logger.lines]
    first = said.index("Shutdown requested by user.")
    second = next(i for i, line in enumerate(said) if "Still waiting" in line)
    third = next(i for i, line in enumerate(said) if "x.raw: its upload had" in line)
    assert first < second < third
    assert len(ended) > 3, "the threads the agent waits for end with an event"
    locks = [event.lock for event in (stopping, *ended)]
    assert not any(lock.left_held for lock in locks)
    assert not any(lock.locked() for lock in locks)


def test_an_interrupt_during_the_check_at_start_stops_the_agent(
    make_agent, monkeypatch, ctrl_c
):
    """Ctrl+C while the server is being asked about the credential.

    Nothing around that question handles an interrupt, so one raised there
    ended ``run_until_complete`` with the ``KeyboardInterrupt`` itself, and
    the console program with a traceback. Counted, it is answered by the
    upload loop, as an interrupt at any other time is.
    """

    def asked(host, token, verify):
        ctrl_c()
        return CREDENTIAL_OK, ""

    monkeypatch.setattr(credentials, "check_credential", asked)
    agent = make_agent()

    try:
        agent.run_until_complete()
    except KeyboardInterrupt:
        pytest.fail("the interrupt went past the agent")

    assert agent.logger.said("info", "Shutdown requested by user.")
    assert agent.logger.said("info", "File system watcher stopped")
    assert not agent.running


def test_a_repair_offered_at_the_start_is_interrupted_as_any_prompt_is(
    make_agent, monkeypatch, ctrl_c
):
    """Ctrl+C at "Pair this machine again now?" gets out of the question.

    The check at start runs on the thread that runs the agent, and a refused
    credential is offered a repair there, at the console. Whoever is asked
    answers with Ctrl+C as at any prompt: it is raised in the offer, and it
    is not a request to stop the agent as well.
    """
    monkeypatch.setattr(
        credentials,
        "check_credential",
        lambda host, token, verify: (credentials.CREDENTIAL_REJECTED, "Revoked."),
    )
    agent = make_agent()
    heard = []

    class AsksAtTheConsole(Repair):
        def offer(self, reason, pair):
            try:
                ctrl_c()
                heard.append("nothing")
            except KeyboardInterrupt:
                heard.append("KeyboardInterrupt")
            # Nothing else ends the run: the agent goes on without a repair.
            threading.Timer(0.2, agent.shutdown_event.set).start()
            return None

    agent.credentials.repair = AsksAtTheConsole()

    try:
        agent.run_until_complete()
    except KeyboardInterrupt:
        pytest.fail("the interrupt went past the offer")

    assert heard == ["KeyboardInterrupt"]
    assert agent.logger.said("error", "This machine's Mascope credential was refused")
    assert not agent.logger.said("info", "Shutdown requested by user.")


def test_run_until_complete_off_the_main_thread_leaves_interrupts_alone(make_agent):
    """A program that runs the agent on a thread of its own keeps its Ctrl+C.

    Only the main thread is handed an interrupt, and only it can say what
    one does. Run anywhere else the agent replaces nothing, and is ended
    with ``stop()`` as it always was.
    """
    agent = make_agent()
    handler = signal.getsignal(signal.SIGINT)
    handlers = []

    def run():
        handlers.append(signal.getsignal(signal.SIGINT))
        agent.run_until_complete()

    running = threading.Thread(target=run)
    running.start()
    assert wait_for(lambda: len(agent._helpers) == 2)
    handlers.append(signal.getsignal(signal.SIGINT))

    assert agent.stop(timeout=10) is True
    running.join(10)

    assert not running.is_alive()
    assert handlers == [handler, handler]
    assert signal.getsignal(signal.SIGINT) is handler
    assert agent.logger.said("info", "File system watcher stopped")


# ---------------------------------------------------------------------------
# Stopped
# ---------------------------------------------------------------------------


def test_stop_before_start_has_nothing_to_wait_for(make_agent):
    agent = make_agent()

    assert agent.stop() is True

    with pytest.raises(RuntimeError, match="started or stopped before"):
        agent.start()


def test_stop_ends_every_thread_the_agent_started(make_agent):
    agent = make_agent()
    before = set(threading.enumerate())
    agent.start()
    assert wait_for(lambda: len(agent._helpers) == 2)

    assert agent.stop(timeout=10) is True

    assert not agent.running
    assert wait_for(lambda: not (set(threading.enumerate()) - before))
    # Stopping again finds nothing left to stop.
    assert agent.stop(timeout=1) is True


def test_stop_waits_for_an_upload_under_way(make_agent, uploads, sample):
    """A file a worker has begun is uploaded before the agent is gone."""
    uploads.hold()
    agent = make_agent()
    agent.start()
    agent.uploader.enqueue(sample)
    assert uploads.started.wait(10)

    stopped = []
    stopping = threading.Thread(target=lambda: stopped.append(agent.stop()))
    stopping.start()
    stopping.join(0.3)
    assert stopping.is_alive()  # still waiting for the upload

    uploads.release.set()
    stopping.join(10)

    assert stopped == [True]
    assert agent.logger.said("info", "File upload of file x.raw succeeded!")


def test_stop_stops_waiting_when_its_time_runs_out(make_agent, uploads, sample):
    """A deadline is kept, and the log names what was still being uploaded."""
    uploads.hold()
    agent = make_agent()
    agent.start()
    agent.uploader.enqueue(sample)
    assert uploads.started.wait(10)

    began = time.monotonic()
    assert agent.stop(timeout=0.3) is False

    assert time.monotonic() - began < 5
    assert agent.logger.said(
        "warning", "x.raw: its upload had not ended when the agent stopped."
    )


def test_stop_names_a_file_it_did_not_get_to(make_agent, uploads, sample):
    """A file still waiting to be left alone is not uploaded, and is named."""
    agent = make_agent(timeout=3600)
    agent.start()
    agent.uploader.enqueue(sample)

    assert agent.stop(timeout=10) is True

    assert not uploads.calls
    assert agent.logger.said(
        "warning", "x.raw: not uploaded, as the agent is stopping."
    )


def test_a_deadline_ends_the_wait_for_a_retry(
    make_agent, monkeypatch, uploads, sample, tmp_path
):
    """A failing upload does not hold a stopping agent for its retries.

    The file is left where it is rather than set aside: it did not fail for
    good, the agent stopped trying.
    """
    monkeypatch.setattr(uploader, "RETRY_DELAY", 3600)
    uploads.error = MascopeConnectionError("refused")
    agent = make_agent()
    agent.start()
    agent.uploader.enqueue(sample)
    assert wait_for(lambda: agent.logger.said("info", "Retrying upload in"))

    agent.stop(timeout=0.2)

    assert wait_for(
        lambda: agent.logger.said(
            "warning", "x.raw: not uploaded, as the agent is stopping."
        )
    )
    assert len(uploads.calls) == 1
    assert not (tmp_path / "failed_uploads").exists()
    assert wait_for(lambda: all(w.ended for w in agent.uploader._workers))
    # One line for the file, and the one that is true of it.
    assert not agent.logger.said("warning", "x.raw: its upload had not ended")


def test_without_a_deadline_stop_lets_the_retries_run(
    make_agent, monkeypatch, uploads, sample, tmp_path
):
    """Stopping with no deadline gives up on nothing a worker has begun."""
    # Long enough that the nine retries are still going on once stop() is
    # well into its wait for them.
    monkeypatch.setattr(uploader, "RETRY_DELAY", 0.05)
    uploads.error = MascopeConnectionError("refused")
    uploads.hold()
    agent = make_agent()
    agent.start()
    agent.uploader.enqueue(sample)
    assert uploads.started.wait(10)

    # Asked to stop while the first attempt is still out, and let go of only
    # once stop() is waiting for the workers, so that the other nine attempts
    # are made under a stop that is already in progress.
    stopped = []
    stopping = threading.Thread(target=lambda: stopped.append(agent.stop()))
    stopping.start()
    assert wait_for(lambda: agent.uploader._finishing)
    assert len(uploads.calls) == 1
    uploads.release.set()
    stopping.join(10)

    assert stopped == [True]
    assert len(uploads.calls) == 10
    assert (tmp_path / "failed_uploads" / "x.raw").exists()


# ---------------------------------------------------------------------------
# What runs when a file is complete
# ---------------------------------------------------------------------------


def test_on_ready_steps_run_in_order_before_the_uploader_has_the_file(
    make_agent, sample
):
    agent = make_agent()
    seen = []

    def step(name):
        def run(path):
            # The uploader has not been handed the file yet.
            seen.append((name, path, agent.uploader.jobs.qsize()))

        return run

    agent.on_ready(step("first"))
    agent.on_ready(step("second"))

    agent.watcher.on_filesystem_object_created(sample)

    assert seen == [("first", sample, 0), ("second", sample, 0)]
    assert agent.uploader.jobs.get(timeout=5) == sample


def test_a_failing_on_ready_step_does_not_keep_the_file_from_the_uploader(
    make_agent, sample
):
    """The upload matters more than what a step would have added to it."""
    agent = make_agent()

    def broken(path):
        raise OSError("disk full")

    agent.on_ready(broken)

    agent.watcher.on_filesystem_object_created(sample)

    assert agent.uploader.jobs.get(timeout=5) == sample
    assert agent.logger.said("exception", "Unexpected error handling the new file")


# ---------------------------------------------------------------------------
# A file that is gone when its turn comes
# ---------------------------------------------------------------------------


def test_a_waiting_file_that_disappears_does_not_stop_the_agent(
    make_agent, uploads, tmp_path, sample
):
    """Acquisition software renames and tidies files; the agent has to outlive it.

    A file deleted or renamed between appearing and being uploaded used to end
    the upload loop, and the agent with it: nothing was uploaded from then on
    until somebody noticed and started it again.
    """
    agent = make_agent()
    agent.start()

    agent.uploader.enqueue(str(tmp_path / "gone.raw"))

    assert wait_for(lambda: agent.logger.said("warning", "gone.raw: not uploaded"))
    assert agent.running
    # And the next file is uploaded as if nothing had happened.
    agent.uploader.enqueue(sample)
    assert wait_for(lambda: uploads.calls)
    assert uploads.calls[0]["filepath"] == sample


def test_a_waiting_file_that_cannot_be_read_just_now_is_looked_at_again(
    make_agent, monkeypatch, uploads, sample
):
    """A network folder drops out, a scanner holds a file: it is still there.

    Only a file that is not found is gone. Anything else the look at it raises
    says nothing of the file, so it keeps its place in the queue - and the log
    says so once, not once a second for as long as it lasts.
    """
    agent = make_agent()
    look = agent.uploader.seconds_since_last_access
    failures = [PermissionError("in use"), OSError("the network name is gone")]

    def unreadable_twice(fname):
        if failures:
            raise failures.pop(0)
        return look(fname)

    monkeypatch.setattr(agent.uploader, "seconds_since_last_access", unreadable_twice)
    agent.start()

    agent.uploader.enqueue(sample)

    assert wait_for(lambda: uploads.calls)
    assert uploads.calls[0]["filepath"] == sample
    assert agent.running
    said = [
        message
        for level, message in agent.logger.lines
        if level == "warning" and "could not be looked at" in message
    ]
    assert said == [
        "x.raw: could not be looked at just now (in use). It stays in the queue "
        "and is uploaded once it can be."
    ]


# ---------------------------------------------------------------------------
# Stopping at an awkward moment
# ---------------------------------------------------------------------------


def test_a_deadline_is_kept_while_an_on_ready_step_is_running(make_agent, tmp_path):
    """A step that takes its time does not take the deadline with it.

    The step runs on the watcher's own thread. On the observer's it would
    hold the lock the observer needs in order to stop, and stop() would wait
    for the step however short its deadline - so the file has to come the
    way files do, through the observer.
    """
    agent = make_agent()
    entered = threading.Event()
    leave = threading.Event()

    def slow_step(path):
        entered.set()
        leave.wait(30)

    agent.on_ready(slow_step)
    agent.start()
    (tmp_path / "x.raw").write_bytes(b"data")
    assert entered.wait(10)

    began = time.monotonic()
    try:
        assert agent.stop(timeout=0.3) is False
        assert time.monotonic() - began < 5
    finally:
        leave.set()

    # The file the step was holding reaches an uploader that has stopped
    # taking files, and is named rather than left in a queue nobody reads.
    assert wait_for(
        lambda: agent.logger.said(
            "warning", "x.raw: not uploaded, as the agent is stopping."
        )
    )


def test_a_file_still_being_written_at_a_stop_is_named(
    make_agent, monkeypatch, uploads, sample
):
    """The agent saw it, so the log says what became of it."""

    def held_by_its_writer(source, target):
        raise PermissionError("in use")

    monkeypatch.setattr(watcher.os, "rename", held_by_its_writer)
    agent = make_agent()
    agent.start()
    agent.watcher.seen(sample)
    assert wait_for(lambda: agent.logger.said("debug", "is not ready"))

    assert agent.stop(timeout=10) is True

    assert not uploads.calls
    assert agent.logger.said(
        "warning", "x.raw: not uploaded, as the agent is stopping."
    )


def test_a_file_that_arrives_after_the_stop_is_named(make_agent, uploads, sample):
    agent = make_agent()
    agent.start()
    assert agent.stop(timeout=10) is True

    agent.uploader.enqueue(sample)

    assert not uploads.calls
    assert agent.logger.said(
        "warning", "x.raw: not uploaded, as the agent is stopping."
    )
    assert agent.uploader.jobs.empty()


def test_a_stop_during_a_start_waits_for_it_and_then_stops_it(make_agent, monkeypatch):
    """Stopped from another thread mid-start, the agent is not left half-running."""
    agent = make_agent()
    before = set(threading.enumerate())
    starting_workers = threading.Event()
    go_on = threading.Event()
    start_workers = agent.uploader.start

    def slow_start():
        starting_workers.set()
        go_on.wait(30)
        start_workers()

    monkeypatch.setattr(agent.uploader, "start", slow_start)
    starting = threading.Thread(target=agent.start)
    starting.start()
    assert starting_workers.wait(10)

    stopped = []
    stopping = threading.Thread(target=lambda: stopped.append(agent.stop(timeout=10)))
    stopping.start()
    stopping.join(0.3)
    assert stopping.is_alive()  # waiting for the start to be over

    go_on.set()
    starting.join(10)
    stopping.join(15)

    assert stopped == [True]
    assert not agent.running
    assert wait_for(lambda: not (set(threading.enumerate()) - before))


def test_an_agent_that_dies_at_its_start_says_why(make_agent, monkeypatch):
    """Embedded, nobody is waiting for what the agent's thread raises."""

    def broken_check(host, token, verify):
        raise RuntimeError("the check itself broke")

    monkeypatch.setattr(credentials, "check_credential", broken_check)
    agent = make_agent()
    before = set(threading.enumerate())

    agent.start()

    assert wait_for(
        lambda: agent.logger.said("exception", "The agent stopped on an unexpected")
    )
    assert wait_for(lambda: not agent.running)
    assert wait_for(lambda: not (set(threading.enumerate()) - before))


def test_the_console_program_keeps_waiting_through_a_second_interrupt(
    make_agent, monkeypatch
):
    """Ctrl+C three times, as before: stop, keep waiting, stop without them."""
    agent = make_agent()
    asked = []

    def interrupted_twice(timeout=None):
        asked.append(timeout)
        if len(asked) < 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(agent, "stop", interrupted_twice)

    agent._stop_patiently()

    assert asked == [None, None, 0]
    assert agent.logger.said("warning", "Interrupt once more to stop without them")


def test_the_steps_of_a_start_keep_their_order(make_agent, monkeypatch):
    """Watching begins before the credential is checked, and uploading after.

    A file that appears while the server is being asked is not missed, and
    none is uploaded on a credential the operator is about to be asked about.
    """
    agent = make_agent()
    order = []
    watch = agent.watcher.start

    def watching():
        order.append("watching")
        watch()

    def checked(host, token, verify):
        order.append("credential")
        return CREDENTIAL_OK, ""

    monkeypatch.setattr(agent.watcher, "start", watching)
    monkeypatch.setattr(credentials, "check_credential", checked)
    monkeypatch.setattr(
        agent.credentials, "renewal_loop", lambda stop: order.append("renewal")
    )
    monkeypatch.setattr(
        agent.status_follower, "run", lambda stop: order.append("status")
    )
    monkeypatch.setattr(
        agent.uploader, "run_until_complete", lambda: order.append("uploading")
    )

    agent.run_until_complete()

    assert order[:2] == ["watching", "credential"]
    assert sorted(order[2:]) == ["renewal", "status", "uploading"]


def test_an_uploader_that_has_finished_starts_no_workers(make_agent):
    """Workers started after their release would wait for it for ever."""
    agent = make_agent()
    before = set(threading.enumerate())

    assert agent.uploader.finish(timeout=1) is True
    agent.uploader.start()

    assert agent.uploader._workers == []
    assert set(threading.enumerate()) == before


# ---------------------------------------------------------------------------
# What stop() says of files and threads it cannot simply wait for
# ---------------------------------------------------------------------------


def test_a_file_waiting_for_a_free_worker_at_a_deadline_is_named(
    make_agent, uploads, tmp_path
):
    """Three workers, four files: the fourth has been handed over and not begun."""
    uploads.hold()
    agent = make_agent()
    agent.start()
    names = {"a.raw", "b.raw", "c.raw", "d.raw"}
    for name in sorted(names):
        (tmp_path / name).write_bytes(b"data")
        agent.uploader.enqueue(str(tmp_path / name))
    assert wait_for(lambda: len(uploads.calls) == 3 and agent.uploader.jobs.empty())

    assert agent.stop(timeout=0.3) is False

    begun = {os.path.basename(call["filepath"]) for call in uploads.calls}
    (waiting,) = names - begun
    assert agent.logger.said(
        "warning", f"{waiting}: not uploaded, as the agent is stopping."
    )
    for name in begun:
        assert agent.logger.said("warning", f"{name}: its upload had not ended")
    # And it is not begun once a worker comes free.
    uploads.release.set()
    assert wait_for(lambda: all(w.ended for w in agent.uploader._workers))
    assert len(uploads.calls) == 3


def test_a_worker_that_comes_free_after_the_deadline_begins_nothing(
    make_agent, uploads, sample
):
    """The file it would have taken next is named instead."""
    agent = make_agent()
    agent.uploader._abort.set()
    agent.uploader._uploads.put(sample)
    agent.uploader._uploads.put(None)

    agent.uploader._work()

    assert not uploads.calls
    assert agent.logger.said(
        "warning", "x.raw: not uploaded, as the agent is stopping."
    )


def test_stop_from_an_on_ready_step_does_not_wait_for_its_own_thread(
    make_agent, sample
):
    """False at once, as the docstring says: the thread ends after the step."""
    agent = make_agent()
    stops = []

    def stop_the_agent(path):
        began = time.monotonic()
        stops.append((agent.stop(timeout=5), time.monotonic() - began))

    agent.on_ready(stop_the_agent)
    agent.start()

    agent.watcher.seen(sample)

    assert wait_for(lambda: stops)
    ((stopped, took),) = stops
    assert stopped is False
    assert took < 2
    assert wait_for(lambda: agent.watcher._handling.ended)
    assert not agent.logger.said("exception", "Unexpected error")
    # The file the step was handed reaches an uploader that takes no more.
    assert agent.logger.said(
        "warning", "x.raw: not uploaded, as the agent is stopping."
    )


def test_a_repair_that_stops_the_agent_does_not_wait_for_its_own_worker(
    make_settings, monkeypatch, uploads, sample
):
    """A program that answers a refused credential by stopping the agent.

    Its offer runs on the worker whose upload was refused, and that worker
    cannot wait for its own end. The other workers are waited for, and the
    stop is not counted as having run out of time.
    """
    monkeypatch.setattr(uploader, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(
        credentials, "check_credential", lambda host, token, verify: (CREDENTIAL_OK, "")
    )
    monkeypatch.setattr(status.StatusFollower, "poll_due", lambda self: None)
    monkeypatch.setattr(capabilities.ServerCapabilities, "ask", lambda self: {})
    stops = []

    class StopsTheAgent(Repair):
        def offer(self, reason, pair):
            began = time.monotonic()
            stops.append((agent.stop(timeout=5), time.monotonic() - began))
            return None

    agent = Agent(
        make_settings(timeout=0), logger=RecordingLogger(), repair=StopsTheAgent()
    )
    uploads.error = AuthenticationError("Credential refused", status_code=401)
    agent.start()

    agent.uploader.enqueue(sample)

    assert wait_for(lambda: stops)
    ((stopped, took),) = stops
    assert stopped is True
    assert took < 2
    assert wait_for(lambda: all(w.ended for w in agent.uploader._workers))
    assert not agent.logger.said("warning", "its upload had not ended")
    assert not agent.logger.said("exception", "Unexpected error")


def test_an_agent_stopped_during_its_start_does_not_ask_the_server(
    make_agent, monkeypatch
):
    """The check at start can take a connection timeout; a stop does not wait
    for a question whose answer nobody will use."""
    asked = []
    monkeypatch.setattr(
        credentials,
        "check_credential",
        lambda host, token, verify: asked.append(host) or (CREDENTIAL_OK, ""),
    )
    agent = make_agent()
    watch = agent.watcher.start

    def stopped_while_starting():
        watch()
        agent.shutdown_event.set()

    monkeypatch.setattr(agent.watcher, "start", stopped_while_starting)

    agent.start()

    assert wait_for(lambda: agent._thread.ended)
    assert asked == []
