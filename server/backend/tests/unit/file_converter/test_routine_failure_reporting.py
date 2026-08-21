"""What the run loop actually logs when a file fails, and at what level.

The level is not cosmetic: the error-monitoring sink subscribes at WARNING
(``mascope_runtime.logging._SENTRY_LEVELS``), so it alone decides whether a
failure becomes a monitoring event. A routine data-side failure logged at
WARNING is worse than no fix at all - the record carries no exception, so it
is captured as a *message* keyed on text that includes the filename, turning
one grouped issue into one issue per file. That is the exact flood these
failures were reclassified to stop.

These tests drive ``BaseFileProcessor.run`` itself rather than the
classification helper, because the helper being right is not the property
that matters - what reaches the sink is.
"""

from queue import Empty
from threading import Event

import pytest

from mascope_backend.file_converter.errors import EmptyAcquisitionError
from mascope_backend.runtime import runtime
from mascope_runtime.logging import _SENTRY_LEVELS
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
    def __init__(self):
        self.cleared = []

    def clear_context(self, name):
        self.cleared.append(name)


class _Socket:
    def __init__(self):
        self.emitted = []
        self.context_manager = _ContextManager()

    def emit(self, event, payload):
        self.emitted.append((event, payload))


def _run_once(failure):
    """Run one file through the loop, failing it with ``failure``.

    :return: (log records, socket) captured from the iteration.
    """
    shutdown = Event()
    socket = _Socket()
    processor = H5Processor(
        socket_client=socket,
        file_queue=_OneShotQueue(SAMPLE, shutdown),
        shutdown_event=shutdown,
    )

    def _raise():
        raise failure

    processor._get_sample_file_props = _raise
    processor._finalize = lambda: None
    processor.moved_aside = []
    processor._handle_failed_file = processor.moved_aside.append

    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )
    try:
        processor.run()
    finally:
        runtime.logger.remove(sink_id)
    return records, socket, processor


def _failure_records(records):
    return [r for r in records if "Failed to process file" in r["message"]]


def _at_or_above_warning(records):
    return [r for r in records if r["level"].name in _SENTRY_LEVELS]


class TestRoutineFailureStaysOutOfMonitoring:
    FAILURE = EmptyAcquisitionError(
        "The file contains no scans; the acquisition is empty or was aborted."
    )

    def test_it_is_logged_below_the_monitoring_threshold(self):
        records, _, _ = _run_once(self.FAILURE)

        failures = _failure_records(records)
        assert len(failures) == 1
        assert failures[0]["level"].name not in _SENTRY_LEVELS

    def test_nothing_in_the_whole_iteration_reaches_the_sink(self):
        # Not just the failure line: any WARNING+ record naming the file would
        # be captured, and the Orbitrap path had exactly that problem in an
        # earlier property.
        records, _, _ = _run_once(self.FAILURE)

        assert _at_or_above_warning(records) == []

    def test_it_carries_no_traceback(self):
        # A record without an exception is grouped by its message text, which
        # includes the filename - one issue per file rather than one issue.
        records, _, _ = _run_once(self.FAILURE)

        assert _failure_records(records)[0]["exception"] is None

    def test_the_message_still_names_the_file_and_the_reason(self):
        records, _, _ = _run_once(self.FAILURE)

        message = _failure_records(records)[0]["message"]
        assert SAMPLE in message
        assert "contains no scans" in message

    def test_the_user_is_still_notified(self):
        # Quieting monitoring must not quieten the person who uploaded it.
        _, socket, _ = _run_once(self.FAILURE)

        errors = [p for event, p in socket.emitted if event == "file_processing_error"]
        assert len(errors) == 1
        assert errors[0]["filename"] == SAMPLE
        assert errors[0]["error"] == str(self.FAILURE)

    def test_the_file_is_still_moved_aside(self):
        _, _, processor = _run_once(self.FAILURE)

        assert processor.moved_aside == [SAMPLE]

    def test_the_context_is_still_cleared(self):
        _, socket, _ = _run_once(self.FAILURE)

        assert socket.context_manager.cleared == [SAMPLE]


class TestRealFaultIsStillReported:
    FAILURE = OSError("I/O error: Invalid argument (os error 22)")

    def test_it_is_logged_at_a_level_the_sink_subscribes_to(self):
        records, _, _ = _run_once(self.FAILURE)

        failures = _failure_records(records)
        assert len(failures) == 1
        assert failures[0]["level"].name in _SENTRY_LEVELS

    def test_it_carries_its_traceback(self):
        # An exception record groups by type and stack, not by filename, so a
        # recurring fault stays one issue - and is diagnosable.
        records, _, _ = _run_once(self.FAILURE)

        assert _failure_records(records)[0]["exception"] is not None


class TestMonitoringThresholdAssumption:
    def test_the_sink_subscribes_from_warning_upward(self):
        # The reclassification above is only correct while this holds. If the
        # sink ever subscribes at INFO, routine failures start minting events
        # again and this test says so.
        assert set(_SENTRY_LEVELS) == {"WARNING", "ERROR", "CRITICAL"}

    @pytest.mark.parametrize("level", ["TRACE", "DEBUG", "INFO", "SUCCESS"])
    def test_levels_below_warning_are_not_forwarded(self, level):
        assert level not in _SENTRY_LEVELS
