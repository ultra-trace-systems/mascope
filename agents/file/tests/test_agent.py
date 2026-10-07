"""Unit tests for the agent's life: built, started, stopped.

Hermetic: the SDK upload, the startup credential check and the status
follower's questions are monkeypatched, so nothing reaches a network. The
agent's own threads are real, and watch the test's own folder.
"""

import threading
import time

import pytest

from mascope_file_agent import (
    Agent,
    ConfigError,
    credentials,
    status,
    uploader,
    watcher,
)
from mascope_file_agent.wizard import CREDENTIAL_OK
from mascope_sdk.exceptions import MascopeConnectionError


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
