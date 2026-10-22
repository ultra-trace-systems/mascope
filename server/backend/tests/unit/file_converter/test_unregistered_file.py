"""A file the converter is offered with no upload registered for it.

The upload registers who sent the file in the converter's memory, and every
record of the file is written with that uploader's credential. A converter
restarted after the upload has lost it, while its watcher still offers the
file it finds left in the streams folder at startup. Such a file cannot be
stored, so the run loop fails it before reading it, and reports it as one
line pinned to one monitoring issue rather than one issue per file name.
"""

import os
import time
from queue import Empty
from threading import Event

import pytest
from test_utils import captured_logs

from mascope_backend.file_converter.errors import (
    UNREGISTERED_FILE_MESSAGE,
    UnregisteredFileError,
)
from mascope_runtime.logging import _SENTRY_LEVELS, SENTRY_FINGERPRINT
from mascope_tofwerk.processor import H5Processor


SAMPLE = "TOF-1_sample.h5"


class _OneShotQueue:
    """Hands out one file, then ends the run loop."""

    def __init__(self, item, shutdown):
        self._item = item
        self._shutdown = shutdown

    def get(self, timeout=None):  # noqa: ARG002
        if self._item is None:
            self._shutdown.set()
            raise Empty
        item, self._item = self._item, None
        return item


class _ContextManager:
    """Holds the ``registered`` names, each after ``late_by`` lookups miss."""

    def __init__(self, registered, late_by=0):
        self.registered = registered
        self.late_by = late_by
        self.lookups = 0
        self.cleared = []

    def get_context(self, name):
        self.lookups += 1
        if self.lookups <= self.late_by:
            return None
        return object() if os.path.basename(name) in self.registered else None

    def clear_context(self, name):
        self.cleared.append(name)


class _Socket:
    def __init__(self, registered, late_by=0):
        self.emitted = []
        self.context_manager = _ContextManager(registered, late_by)

    def emit(self, event, payload):
        self.emitted.append((event, payload))


class _PropsRead(Exception):
    """Stands for everything past the check: the file was read."""


def _run_once(path, registered=(), late_by=0):
    """Run one file through the loop.

    Reading the file's properties raises ``_PropsRead``, so a file that got
    past the context check is told apart from one that did not. The wait for
    a late context is cut to a fraction of a second.

    :return: (log records, socket, processor) captured from the iteration.
    """
    shutdown = Event()
    socket = _Socket(registered, late_by)
    processor = H5Processor(
        socket_client=socket,
        file_queue=_OneShotQueue(path, shutdown),
        shutdown_event=shutdown,
    )
    processor.CONTEXT_GRACE_WAIT = 0.2
    processor.CONTEXT_GRACE_POLL = 0.01
    processor.props_read = []

    def _read_props():
        processor.props_read.append(path)
        raise _PropsRead("read")

    processor._get_sample_file_props = _read_props
    processor._finalize = lambda: None
    processor.moved_aside = []
    processor._handle_failed_file = processor.moved_aside.append

    with captured_logs() as records:
        processor.run()
    return records, socket, processor


def _failure_records(records):
    return [r for r in records if "Failed to process file" in r["message"]]


@pytest.fixture
def stale_file(tmp_path):
    """A file left in the streams folder three days ago."""
    path = tmp_path / SAMPLE
    path.write_bytes(b"x" * 10)
    three_days_ago = time.time() - 3 * 24 * 3600
    os.utime(path, (three_days_ago, three_days_ago))
    return str(path)


@pytest.fixture
def fresh_file(tmp_path):
    """A file the watcher queued moments after it was written."""
    path = tmp_path / SAMPLE
    path.write_bytes(b"x" * 10)
    return str(path)


class TestUnregisteredFile:
    def test_it_fails_before_the_file_is_read(self, stale_file):
        _, _, processor = _run_once(stale_file)

        assert processor.props_read == []

    def test_a_registered_file_is_read(self, stale_file):
        # The check passes what it should: without this, a check that always
        # raised would pass the test above as well.
        _, _, processor = _run_once(stale_file, registered={SAMPLE})

        assert processor.props_read == [stale_file]

    def test_it_fails_without_waiting(self, stale_file):
        # A leftover from before a restart gets no grace: a backlog of them
        # must not hold the queue up
        _, socket, _ = _run_once(stale_file, registered={SAMPLE}, late_by=1)

        assert socket.context_manager.lookups == 2

    def test_the_error_says_to_upload_it_again(self, stale_file):
        # The notification carries this; without a context the backend
        # refuses it (#2535), so the converter's log is where it is read.
        _, socket, _ = _run_once(stale_file)

        errors = [p for event, p in socket.emitted if event == "file_processing_error"]
        assert len(errors) == 1
        assert errors[0]["filename"] == SAMPLE
        assert errors[0]["error"] == UNREGISTERED_FILE_MESSAGE
        assert "Upload the file again" in errors[0]["error"]

    def test_the_file_is_moved_aside_and_its_context_cleared(self, stale_file):
        _, socket, processor = _run_once(stale_file)

        assert processor.moved_aside == [stale_file]
        assert socket.context_manager.cleared == [SAMPLE]

    def test_it_is_still_reported_to_monitoring(self, stale_file):
        # An upload is lost, and its uploader cannot be told: the notification
        # needs the credential that is missing. Monitoring is the only report.
        records, _, _ = _run_once(stale_file)

        failures = _failure_records(records)
        assert len(failures) == 1
        assert failures[0]["level"].name in _SENTRY_LEVELS

    def test_every_such_file_is_one_monitoring_issue(self, stale_file):
        records, _, _ = _run_once(stale_file)

        failure = _failure_records(records)[0]
        assert failure["extra"][SENTRY_FINGERPRINT] == [
            "file-converter-unregistered-file"
        ]
        assert failure["exception"] is None

    def test_the_line_says_how_long_the_file_waited(self, stale_file):
        # What tells a restart (days) from a context that came late (seconds)
        records, _, _ = _run_once(stale_file)

        message = _failure_records(records)[0]["message"]
        assert SAMPLE in message
        assert "last written 2592" in message
        assert UNREGISTERED_FILE_MESSAGE in message


class TestLateContext:
    """A context still crossing Redis when its fresh file is dequeued."""

    def test_a_context_that_arrives_shortly_after_is_waited_for(self, fresh_file):
        _, socket, processor = _run_once(fresh_file, registered={SAMPLE}, late_by=3)

        assert processor.props_read == [fresh_file]
        assert socket.context_manager.lookups > 3

    def test_a_context_that_never_arrives_still_fails_the_file(self, fresh_file):
        records, socket, processor = _run_once(fresh_file)

        assert processor.props_read == []
        assert socket.context_manager.lookups > 2
        assert len(_failure_records(records)) == 1

    def test_shutdown_ends_the_wait(self, fresh_file):
        shutdown = Event()
        shutdown.set()
        socket = _Socket(registered=())
        processor = H5Processor(
            socket_client=socket, file_queue=None, shutdown_event=shutdown
        )
        processor.file_to_process = fresh_file

        started = time.monotonic()
        with pytest.raises(UnregisteredFileError):
            processor._await_file_context()
        assert time.monotonic() - started < 1


def test_the_lookup_raises_the_dedicated_error(stale_file):
    socket = _Socket(registered=())
    processor = H5Processor(
        socket_client=socket, file_queue=None, shutdown_event=Event()
    )
    processor.file_to_process = stale_file

    with pytest.raises(UnregisteredFileError, match="Upload the file again"):
        processor._get_file_context()


def test_it_is_still_a_runtime_error():
    # The orphan check re-raises RuntimeError rather than reading it as "no
    # orphan", so a file with no context still fails there instead of being
    # taken for a duplicate.
    assert issubclass(UnregisteredFileError, RuntimeError)
