"""Peak detection requests the stopping converter will not run are answered.

Once the converter is told to stop, its ``PeakRecomputeWorker`` threads leave
their loop within a second, but the socket stays up until every other thread
has been joined, which can take the whole stop grace period while a conversion
finishes. The backend still counts the converter as connected all that time and
can still send it a peak detection request. Its caller was already told the
request is pending, so a request nobody runs has to be answered with a
``peak_detection_error``, or the caller's progress stays pending for good
(#2911). Two places do it: ``run()`` empties the queue before it disconnects,
and the socket handler refuses a request that arrives once the stop is set.
Both release the ``PeakDetectionGuard`` slot the request held.
"""

from queue import Queue
from threading import Event
from unittest.mock import MagicMock, patch

from mascope_backend.file_converter import service
from mascope_backend.file_converter.peak_guard import PeakDetectionGuard
from mascope_backend.file_converter.peak_recompute_worker import (
    STOPPED_MESSAGE,
    drain_on_stop,
)
from mascope_backend.file_converter.socket.client import FileConverterSocketClient


def _request(filename: str = "sample", process_id: str = "process-1") -> dict:
    return {
        "filename": filename,
        "sample_file_id": f"{filename}-id",
        "process_id": process_id,
        "access_token": "token-1",
        "user_id": 7,
    }


def _errors(socket_client) -> list[tuple[dict, dict]]:
    return [
        (c.args[1], c.args[2])
        for c in socket_client.emit.call_args_list
        if c.args[0] == "peak_detection_error"
    ]


def test_the_stop_answers_a_request_left_on_the_queue(tmp_path):
    """The real shutdown path with the real peak workers.

    The request goes on the queue once the stop is set and the workers have
    left their loop, as one that arrives while a conversion drains does.
    """
    shutdown = Event()
    queue = Queue()
    guard = PeakDetectionGuard()
    socket_client = MagicMock()

    def main(supervised):
        shutdown.set()
        for slot in supervised:
            slot.join()
        request = _request()
        guard.acquire(request["filename"])
        queue.put(request)

    fake_runtime = MagicMock()
    fake_runtime.config.source = str(tmp_path)
    fake_runtime.config.h5_threads = 1
    fake_runtime.config.raw_threads = 1
    fake_runtime.config.interval = 3

    with (
        patch.object(service, "SHUTDOWN_EVENT", shutdown),
        patch.object(service, "PEAK_RECOMPUTE_QUEUE", queue),
        patch.object(service, "PEAK_GUARD", guard),
        patch.object(service, "SOCKET_CLIENT", socket_client),
        patch.object(service, "runtime", fake_runtime),
        # run() makes SIGTERM its own stop request; installed here, it would
        # be pytest's SIGTERM handler for the rest of the session.
        patch.object(service, "install_stop_signal", lambda: None),
        patch.object(service, "wait_for_backend", lambda: True),
        patch.object(service, "FSWatcher", MagicMock()),
        patch.object(service, "H5Processor", MagicMock()),
        patch.object(service, "RawProcessor", MagicMock()),
        patch.object(service, "main", main),
    ):
        service.run()

    ((payload, auth),) = _errors(socket_client)
    assert payload == {
        "filename": "sample",
        "sample_file_id": "sample-id",
        "process_id": "process-1",
        "error": STOPPED_MESSAGE,
    }
    assert auth == {"access_token": "token-1", "user_id": 7}
    assert queue.empty()
    assert guard.acquire("sample") == (True, None), "the guard slot was kept"
    # Answered while the socket was still up.
    names = [c[0] for c in socket_client.mock_calls]
    assert names.index("emit") < names.index("sio.disconnect")


def test_the_drain_answers_every_request_and_survives_a_failed_emit():
    queue = Queue()
    guard = PeakDetectionGuard()
    socket_client = MagicMock()
    socket_client.emit.side_effect = [RuntimeError("socket gone"), None]
    for n in (1, 2):
        guard.acquire(f"sample-{n}")
        queue.put(_request(f"sample-{n}", f"process-{n}"))
    queue.put("not a request")

    assert drain_on_stop(socket_client, queue, guard) == 2

    assert [p["process_id"] for p, _ in _errors(socket_client)] == [
        "process-1",
        "process-2",
    ]
    assert queue.empty()
    assert guard.acquire("sample-1") == (True, None)
    assert guard.acquire("sample-2") == (True, None)


def test_the_drain_of_an_empty_queue_reports_nothing():
    socket_client = MagicMock()

    assert drain_on_stop(socket_client, Queue(), PeakDetectionGuard()) == 0

    socket_client.emit.assert_not_called()


def _client(
    shutdown: Event,
) -> tuple[FileConverterSocketClient, Queue, PeakDetectionGuard]:
    queue = Queue()
    guard = PeakDetectionGuard()
    client = FileConverterSocketClient(
        "http://backend.invalid",
        peak_recompute_queue=queue,
        peak_guard=guard,
        shutdown_event=shutdown,
    )
    client.sio.emit = MagicMock()
    return client, queue, guard


def _on_request(client):
    return client.sio.handlers["/file-converter"]["peak_detection_request"]


def test_a_request_after_the_stop_is_refused_with_an_error():
    shutdown = Event()
    shutdown.set()
    client, queue, guard = _client(shutdown)

    _on_request(client)(_request())

    ((event, payload),) = [
        (c.args[0], c.args[1]) for c in client.sio.emit.call_args_list
    ]
    assert event == "peak_detection_error"
    assert payload["process_id"] == "process-1"
    assert payload["error"] == STOPPED_MESSAGE
    assert payload["access_token"] == "token-1"
    assert queue.empty()
    assert guard.acquire("sample") == (True, None), "the guard slot was taken"


def test_a_request_before_the_stop_is_queued():
    client, queue, guard = _client(Event())

    _on_request(client)(_request())

    assert queue.get_nowait()["process_id"] == "process-1"
    client.sio.emit.assert_not_called()


def test_the_service_hands_its_stop_to_the_socket_handler():
    handler = service.SOCKET_CLIENT.event_handler
    assert handler._shutdown_event is service.SHUTDOWN_EVENT
