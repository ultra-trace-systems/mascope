import os
from queue import Queue
from threading import Event
from time import sleep

from mascope_backend.file_converter.peak_guard import PeakDetectionGuard
from mascope_backend.file_converter.peak_recompute_worker import PeakRecomputeWorker
from mascope_backend.file_converter.socket.client import FileConverterSocketClient
from mascope_backend.file_converter.watcher import FSWatcher
from mascope_thermo.processor import RawProcessor
from mascope_tofwerk.processor import H5Processor

from .runtime import runtime


def main(supervised: list["_Supervised"] | None = None):
    """Main loop of the service. Supervise the worker threads until shutdown.

    :param supervised: Thread slots to keep alive; None disables supervision.
    """
    while not SHUTDOWN_EVENT.is_set():
        if supervised:
            for slot in supervised:
                slot.ensure_alive()
        # Wait for shutdown event
        sleep(1)


class _Supervised:
    """One worker thread slot, restarted if the thread ever dies.

    A processor thread that stops leaves its queue filling and nothing else
    reports it, so the converter silently accepts uploads it will never convert
    until someone restarts the service (#1350). The processors guard their own
    loops, but a guard can only cover the failures it anticipated; this covers
    the rest by noticing the thread is gone and standing up a replacement.
    """

    #: Give up after this many restarts, so a thread that dies immediately on
    #: every attempt reports once instead of looping forever.
    MAX_RESTARTS = 5

    def __init__(self, name: str, factory):
        """
        :param name: Human-readable label used in log messages.
        :param factory: Zero-argument callable returning a fresh, unstarted thread.
        """
        self.name = name
        self._factory = factory
        self._restarts = 0
        self.thread = factory()

    def start(self) -> None:
        """Start the initial thread."""
        self.thread.start()

    def ensure_alive(self) -> None:
        """Replace the thread if it has died while shutdown was not requested."""
        if self.thread.is_alive() or SHUTDOWN_EVENT.is_set():
            return
        if self._restarts >= self.MAX_RESTARTS:
            return
        self._restarts += 1
        runtime.logger.error(
            f"{self.name} died unexpectedly; restarting "
            f"({self._restarts}/{self.MAX_RESTARTS})"
        )
        if self._restarts == self.MAX_RESTARTS:
            runtime.logger.error(
                f"{self.name} has now been restarted {self.MAX_RESTARTS} times; "
                f"this is the last attempt, further deaths will not be recovered"
            )
        self._requeue_orphaned_file()
        try:
            self.thread = self._factory()
            self.thread.start()
        except Exception:
            runtime.logger.exception(f"Could not restart {self.name}")

    def _requeue_orphaned_file(self) -> None:
        """Put the dead thread's in-flight file back on the queue.

        The file was already taken off the queue when the thread died, and the
        watcher will not offer it again - it computes new work as a difference
        against the previous walk, so a file still sitting in the streams folder
        is never re-seen. Before supervision existed the wedged converter forced
        a restart, and the watcher's empty baseline picked it up; now that the
        queue keeps draining, nothing would.

        Re-queueing is safe: the processor already treats a half-converted file
        as an orphaned filestore and retries it once.
        """
        path = getattr(self.thread, "file_to_process", None)
        queue = getattr(self.thread, "file_queue", None)
        if not path or queue is None:
            return
        try:
            queue.put(path)
            runtime.logger.warning(
                f"{self.name} died holding {path}; re-queued it for the replacement"
            )
        except Exception:
            runtime.logger.exception(
                f"{self.name} died holding {path} and it could not be re-queued; "
                f"the file stays in the streams folder until the service restarts"
            )

    def join(self) -> None:
        """Join the current thread, if it was ever started."""
        if self.thread.is_alive():
            self.thread.join()


def wait_for_backend() -> bool:
    """Connect the socket, waiting for the backend to come up first.

    The converter starts alongside the backend (stack start, update
    restart), so the backend refusing connections is the normal case at
    startup: retry with backoff, logging each attempt at INFO. Escalate
    to a single WARNING once the wait stops looking like a startup, then
    keep retrying quietly - the converter cannot do anything useful
    without the backend, so there is nothing better to do than wait.

    :return: True once connected, False when shutdown was requested first
    :rtype: bool
    """
    runtime.logger.info(f"Waiting for the backend at {URL}...")
    delay = 1
    waited = 0
    warned = False
    while not SHUTDOWN_EVENT.is_set():
        try:
            SOCKET_CLIENT.connect()
        except Exception as e:
            runtime.logger.info(f"Backend not ready yet ({e}), retrying in {delay} s")
            SHUTDOWN_EVENT.wait(delay)
            waited += delay
            if not warned and waited >= CONNECT_WARN_AFTER_S:
                warned = True
                runtime.logger.warning(
                    f"Backend at {URL} still unreachable after {waited} s; "
                    "the file converter keeps retrying but cannot process "
                    "files until the backend is up"
                )
            delay = min(delay * 2, CONNECT_RETRY_MAX_S)
        else:
            runtime.logger.info(f"Connected to the backend at {URL}")
            return True
    return False


# Global variables
SHUTDOWN_EVENT = Event()
# Startup wait tuning: retry delays double from 1 s up to this cap, and a
# single WARNING (= one monitored GlitchTip event) fires once the backend
# has been unreachable for longer than any normal startup takes.
CONNECT_RETRY_MAX_S = 30
CONNECT_WARN_AFTER_S = 120
HOST = runtime.config.server if runtime.mode == "prod" else "localhost"
URL = f"http://{HOST}:{runtime.meta.api_port}"
# Maximum number of peak detection requests that can run in parallel.
# Controls the number of PeakRecomputeWorker threads.
PEAK_CONCURRENCY = 3
PEAK_GUARD = PeakDetectionGuard()
PEAK_RECOMPUTE_QUEUE = Queue()
SOCKET_CLIENT = FileConverterSocketClient(
    URL, peak_recompute_queue=PEAK_RECOMPUTE_QUEUE, peak_guard=PEAK_GUARD
)


def run():
    """Run the service

    :raises Exception: Parsing command line arguments failed
    """

    if not os.path.exists(runtime.config.source):
        runtime.logger.info(
            f"Creating missing source directory {runtime.config.source}"
        )
        os.makedirs(runtime.config.source)

    # Everything the converter produces flows through the socket, so connect
    # before starting any watcher or worker threads: starting them earlier
    # only yields emit errors while the backend is still booting.
    if not wait_for_backend():
        return

    # Initialize streamer thread(s)
    # tof streamers
    h5_file_queue = Queue()
    h5_streamers = [
        _Supervised(
            f"H5Processor #{n}",
            lambda: H5Processor(
                socket_client=SOCKET_CLIENT,
                file_queue=h5_file_queue,
                shutdown_event=SHUTDOWN_EVENT,
                peak_guard=PEAK_GUARD,
            ),
        )
        for n in range(runtime.config.h5_threads)
    ]
    h5_fs_watcher = FSWatcher(
        path=runtime.config.source,
        pattern="*.h5",
        file_queue=h5_file_queue,
        interval=runtime.config.interval,  # default 3
        shutdown_event=SHUTDOWN_EVENT,
    )
    h5_fs_watcher.start()

    # orbi file processors
    raw_file_queue = Queue()
    raw_processors = [
        _Supervised(
            f"RawProcessor #{n}",
            lambda: RawProcessor(
                socket_client=SOCKET_CLIENT,
                file_queue=raw_file_queue,
                shutdown_event=SHUTDOWN_EVENT,
                peak_guard=PEAK_GUARD,
            ),
        )
        for n in range(runtime.config.raw_threads)
    ]
    raw_fs_watcher = FSWatcher(
        path=runtime.config.source,
        pattern="*.raw",
        file_queue=raw_file_queue,
        interval=runtime.config.interval,  # default 3
        shutdown_event=SHUTDOWN_EVENT,
    )
    raw_fs_watcher.start()

    processors = [*raw_processors, *h5_streamers]

    # Start processor thread(s)
    for processor in processors:
        processor.start()

    # Peak detection workers (handle peak detection requests from backend)
    peak_workers = [
        _Supervised(
            f"PeakRecomputeWorker #{n}",
            lambda: PeakRecomputeWorker(
                socket_client=SOCKET_CLIENT,
                peak_recompute_queue=PEAK_RECOMPUTE_QUEUE,
                peak_guard=PEAK_GUARD,
                shutdown_event=SHUTDOWN_EVENT,
            ),
        )
        for n in range(PEAK_CONCURRENCY)
    ]
    for worker in peak_workers:
        worker.start()

    try:
        # Run main loop, keeping the queue-consuming threads alive
        main(supervised=[*processors, *peak_workers])
    except Exception:
        # Shutdown gracefully on exception
        SHUTDOWN_EVENT.set()
    finally:
        # Wait for all threads to finish
        for processor in processors:
            processor.join()
        raw_fs_watcher.join()
        h5_fs_watcher.join()
        for worker in peak_workers:
            worker.join()


if __name__ == "__main__":
    # Run the service
    run()
