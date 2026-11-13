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
Both release the ``PeakDetectionGuard`` slot the request held. Those answers
are only queued on the socket, and a disconnect right after them closes it
before most are sent, so the stop waits, a bounded time, for the backend to
acknowledge them. So it does for the last events the threads it joined sent:
a peak detection that was running when the stop came, a conversion finished
in the grace period. Only a real server shows that they arrive.
"""

import asyncio
import socket
import threading
import time
from contextlib import contextmanager
from queue import Queue
from threading import Event
from unittest.mock import MagicMock, patch

import socketio
import uvicorn

from mascope_backend.file_converter import peak_recompute_worker, service
from mascope_backend.file_converter.peak_guard import PeakDetectionGuard
from mascope_backend.file_converter.peak_recompute_worker import (
    STOPPED_MESSAGE,
    PeakRecomputeWorker,
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


@contextmanager
def _stop_path(
    tmp_path,
    shutdown,
    queue,
    guard,
    socket_client,
    requests,
    raw_processor=None,
    before_stop=None,
):
    """``service.run()`` with its pipeline faked and the real peak workers.

    The requests go on the queue once the stop is set and the workers have
    left their loop, as ones that arrive while a conversion drains do.
    ``raw_processor`` builds the raw processors (a mock by default), and
    ``before_stop`` runs before the stop is set.
    """

    def main(supervised):
        if before_stop is not None:
            before_stop()
        shutdown.set()
        for slot in supervised:
            slot.join()
        for request in requests:
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
        patch.object(service, "RawProcessor", raw_processor or MagicMock()),
        patch.object(service, "main", main),
    ):
        yield


def test_the_stop_answers_a_request_left_on_the_queue(tmp_path):
    shutdown = Event()
    queue = Queue()
    guard = PeakDetectionGuard()
    socket_client = MagicMock()

    with _stop_path(tmp_path, shutdown, queue, guard, socket_client, [_request()]):
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
    # Answered while the socket was still up, and waited for before it went.
    names = [c[0] for c in socket_client.mock_calls]
    assert (
        names.index("emit")
        < names.index("wait_for_acknowledgements")
        < names.index("sio.disconnect")
    )
    socket_client.wait_for_acknowledgements.assert_called_once_with(
        service.STOP_ACKNOWLEDGEMENT_SECONDS
    )


@contextmanager
def _backend():
    """A Socket.IO server on loopback that records the events it receives.

    It records a peak detection error by its process id, and any other event
    as ``(event, filename)``. It acknowledges each event as the backend does,
    with its handler's return.
    """
    received: list = []
    sio = socketio.AsyncServer(async_mode="asgi", namespaces=["/file-converter"])

    @sio.on("connect", namespace="/file-converter")
    async def connect(sid, environ, auth=None):
        return True

    @sio.on("peak_detection_error", namespace="/file-converter")
    async def peak_detection_error(sid, data):
        received.append(data["process_id"])

    for event in ("peak_detection_complete", "file_processing_success"):

        async def handler(sid, data, event=event):
            received.append((event, data["filename"]))

        sio.on(event, handler, namespace="/file-converter")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(
        uvicorn.Config(socketio.ASGIApp(sio), log_level="error", lifespan="off")
    )
    thread = threading.Thread(
        target=lambda: asyncio.run(server.serve(sockets=[sock])), daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the loopback server did not start"
        time.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}", received
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def test_the_stop_delivers_its_answers_before_it_disconnects(tmp_path):
    """On a real socket: a disconnect right after the emits loses most of them."""
    shutdown = Event()
    queue = Queue()
    guard = PeakDetectionGuard()
    requests = [_request(f"sample-{n}", f"process-{n}") for n in range(40)]

    with _backend() as (url, received):
        client = FileConverterSocketClient(
            url, peak_recompute_queue=queue, peak_guard=guard, shutdown_event=shutdown
        )
        client.connect()
        with _stop_path(tmp_path, shutdown, queue, guard, client, requests):
            service.run()

        assert not client.sio.connected
        assert sorted(received) == sorted(r["process_id"] for r in requests)


def _peak_workers_gone(timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while any(isinstance(t, PeakRecomputeWorker) for t in threading.enumerate()):
        assert time.monotonic() < deadline, "the peak workers did not stop"
        time.sleep(0.01)


class _LastConversions(threading.Thread):
    """Raw processors whose conversions end in the stop's grace period.

    The peak workers leave within a second of the stop, a conversion in
    flight can take much longer, so these send their successes once every
    peak worker is gone: right before the last join returns and the
    disconnect follows.
    """

    def __init__(self, socket_client, shutdown_event, **_):
        super().__init__(daemon=True)
        self.socket_client = socket_client
        self.shutdown_event = shutdown_event

    def run(self):
        self.shutdown_event.wait()
        _peak_workers_gone()
        for n in range(40):
            self.socket_client.emit(
                "file_processing_success",
                {"filename": f"converted-{n}", "instrument": "instrument X"},
            )


@contextmanager
def _in_flight_peaks(requests: list[dict]):
    """A backend, and a client whose peak workers are each running one of
    ``requests`` when the stop comes; each finishes once the stop is set."""
    shutdown = Event()
    queue = Queue()
    guard = PeakDetectionGuard()
    running = threading.Semaphore(0)

    def compute_peaks(filename, instrument_functions, progress_callback):
        running.release()
        shutdown.wait()

    def all_running():
        for _ in requests:
            assert running.acquire(timeout=10), "a peak detection did not start"

    for request in requests:
        guard.acquire(request["filename"])
        queue.put(request)
    with (
        _backend() as (url, received),
        patch.object(peak_recompute_worker, "is_blank_sample_file", lambda *a: False),
        patch.object(peak_recompute_worker, "fetch_instrument_functions", MagicMock()),
        patch.object(peak_recompute_worker, "compute_peaks", compute_peaks),
        patch.object(PeakRecomputeWorker, "_check_rebuilt_store"),
    ):
        client = FileConverterSocketClient(
            url, peak_recompute_queue=queue, peak_guard=guard, shutdown_event=shutdown
        )
        client.connect()
        yield client, received, shutdown, queue, guard, all_running


def test_the_stop_delivers_a_peak_detection_running_when_it_came(tmp_path):
    """Each worker sends its result and leaves at once: the disconnect would
    follow right after."""
    requests = [
        _request(f"in-flight-{n}", f"process-{n}")
        for n in range(service.PEAK_CONCURRENCY)
    ]
    with _in_flight_peaks(requests) as (client, received, *stop, all_running):
        with _stop_path(tmp_path, *stop, client, [], before_stop=all_running):
            service.run()

        assert not client.sio.connected
    assert sorted(received) == sorted(
        ("peak_detection_complete", r["filename"]) for r in requests
    )


def test_the_stop_delivers_a_conversion_finished_in_its_grace_period(tmp_path):
    with _in_flight_peaks([]) as (client, received, *stop, _):
        with _stop_path(tmp_path, *stop, client, [], raw_processor=_LastConversions):
            service.run()

        assert not client.sio.connected
    assert sorted(received) == sorted(
        ("file_processing_success", f"converted-{n}") for n in range(40)
    )


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


def _counting_client() -> FileConverterSocketClient:
    client = FileConverterSocketClient("http://backend.invalid")
    client.sio.emit = MagicMock()
    return client


def _acknowledge(client, n: int = -1, *args) -> None:
    client.sio.emit.call_args_list[n].kwargs["callback"](*args)


def test_an_event_is_waited_for_until_acknowledged():
    client = _counting_client()
    client.emit("peak_detection_error", {"filename": "sample"})
    client.emit("peak_detection_error", {"filename": "other"})

    assert client.wait_for_acknowledgements(0.05) is False
    _acknowledge(client, 0)
    assert client.wait_for_acknowledgements(0.05) is False
    # A rejection is an acknowledgement too: the event was delivered.
    _acknowledge(client, 1, {"error": "SOCKET_FORBIDDEN"})
    assert client.wait_for_acknowledgements(0.05) is True


def test_an_acknowledgement_wakes_the_wait():
    client = _counting_client()
    client.emit("peak_detection_error", {"filename": "sample"})
    timer = threading.Timer(0.1, _acknowledge, args=(client,))
    timer.start()

    started = time.monotonic()
    assert client.wait_for_acknowledgements(10) is True
    assert time.monotonic() - started < 5
    timer.join()


def test_every_event_is_waited_for():
    client = _counting_client()
    client.emit("file_processing_success", {"filename": "sample"})

    assert client.wait_for_acknowledgements(0.05) is False
    _acknowledge(client)
    assert client.wait_for_acknowledgements(0) is True


def test_an_event_that_was_not_sent_is_not_waited_for():
    client = _counting_client()
    client.sio.emit.side_effect = RuntimeError("socket gone")
    client.emit("peak_detection_error", {"filename": "sample"})

    assert client.wait_for_acknowledgements(0) is True


def test_a_disconnect_ends_the_wait_for_its_acknowledgements():
    client = _counting_client()
    client.emit("peak_detection_error", {"filename": "sample"})

    on_disconnect = client.sio.handlers["/file-converter"]["disconnect"]
    timer = threading.Timer(0.1, on_disconnect)
    timer.start()

    started = time.monotonic()
    assert client.wait_for_acknowledgements(10) is True
    assert time.monotonic() - started < 5
    timer.join()
    # The lost event's acknowledgement, arriving after all, waits for nothing
    # and leaves no debt for the next wait.
    _acknowledge(client)
    assert client.wait_for_acknowledgements(0) is True


def test_a_waited_for_event_still_has_its_acknowledgement_read():
    client = _counting_client()
    data = {"filename": "sample", "access_token": "token-1"}
    client.emit("peak_detection_error", dict(data))

    _acknowledge(client, -1, {"error": "SOCKET_FORBIDDEN"})

    # The rejected token holds the next event back, as for any event.
    client.emit("peak_detection_error", dict(data))
    assert client.sio.emit.call_count == 1
    assert client.wait_for_acknowledgements(0) is True
