import os
from collections.abc import Callable
from queue import Queue
from threading import Event, Thread
from time import monotonic

from mascope_backend.file_converter.peak_guard import PeakDetectionGuard
from mascope_backend.file_converter.peak_recompute_worker import PeakRecomputeWorker
from mascope_backend.file_converter.socket.client import FileConverterSocketClient
from mascope_backend.file_converter.watcher import FSWatcher
from mascope_thermo.processor import RawProcessor
from mascope_tofwerk.processor import H5Processor

from .runtime import runtime


def main(supervised: list["_Supervised"]):
    """Main loop of the service. Supervise the worker threads until shutdown.

    :param supervised: Thread slots to keep alive for as long as the service runs.
    """
    while not SHUTDOWN_EVENT.is_set():
        for slot in supervised:
            slot.ensure_alive()
        # Wake as soon as shutdown is requested rather than sleeping it out
        SHUTDOWN_EVENT.wait(1)


class _Supervised:
    """One worker thread slot, watched for as long as the service runs.

    A worker thread that stops leaves its queue filling and nothing else
    reports it, so the converter silently accepts uploads it will never convert
    until someone restarts the service (#1350). The workers guard their own
    loops, but a guard can only cover the failures it anticipated; this covers
    the rest by noticing the thread is gone.

    Most slots then stand up a replacement. Some cannot: see ``restartable``.
    Either way the death is reported, and keeps being reported, because being
    told is what was missing.

    The dead thread's in-flight work is handed back to its queue first, through
    the worker's own ``requeue_inflight()``. That has to be the worker's call:
    it is the only side that knows what it took off the queue, whether the item
    is still worth retrying, and what else has to be reserved before it goes
    back.
    """

    #: Give up after this many restarts inside MAX_RESTART_WINDOW_S. A thread
    #: that dies immediately on every attempt then reports and stops instead of
    #: looping forever, while a slot that recovers and runs healthily for a
    #: whole window gets its budget back - five unrelated deaths months apart
    #: are not a crash loop and must not use supervision up.
    MAX_RESTARTS = 5
    MAX_RESTART_WINDOW_S = 600
    #: Leave this long between attempts after the first. Supervision ticks once
    #: a second, so without it a failure that reproduces at construction burns
    #: the whole budget in five seconds and retires the slot over what may have
    #: been a few seconds of transient trouble.
    RESTART_BACKOFF_S = 30
    #: Once supervision has stopped trying, repeat the report this often. A slot
    #: that is dead for good is exactly the silent stall of #1350, so it has to
    #: keep saying so instead of being mentioned once and then never again.
    GIVE_UP_REPORT_INTERVAL_S = 3600

    def __init__(
        self, name: str, factory: Callable[[], Thread], restartable: bool = True
    ):
        """
        :param name: Human-readable label, also given to the thread itself so
            the supervisor's lines and the worker's own can be matched up.
        :type name: str
        :param factory: Zero-argument callable returning a fresh, unstarted thread.
        :type factory: Callable[[], Thread]
        :param restartable: False for a thread that cannot be swapped in place.
            A replacement FSWatcher starts from an empty baseline by design, so
            it would re-offer every file still in the streams folder - including
            the one a processor is converting right now, and the whole undrained
            backlog. Those slots are watched and reported, not rebuilt; the
            recovery for them is a service restart, which is safe because
            nothing is in flight.
        :type restartable: bool
        """
        self.name = name
        self._factory = factory
        self._restartable = restartable
        self._restarts = 0
        self._window_started: float | None = None
        self._last_restart_at: float | None = None
        self._gave_up_at: float | None = None
        self.thread = self._new_thread()

    def _new_thread(self) -> Thread:
        """Build a thread for this slot and label it with the slot's name.

        Workers do not name themselves usefully - ``BaseFileProcessor`` leaves
        the default ``Thread-N`` and every ``PeakRecomputeWorker`` shares one
        hardcoded name - so without this the supervisor's "X died" line cannot
        be tied to the log lines of the thread it is about.

        :return: A fresh, unstarted thread
        :rtype: Thread
        """
        thread = self._factory()
        thread.name = self.name
        return thread

    def start(self) -> None:
        """Start the initial thread."""
        self.thread.start()

    def ensure_alive(self) -> None:
        """Act on the thread having died while shutdown was not requested."""
        if self.thread.is_alive() or SHUTDOWN_EVENT.is_set():
            return

        now = monotonic()
        if self._gave_up_at is not None:
            self._report_still_dead(now)
            return

        if not self._restartable:
            self._gave_up_at = now
            runtime.logger.error(
                f"{self.name} died and cannot be replaced while the service "
                f"runs; the work it feeds has stopped moving - restart the "
                f"file converter to recover it"
            )
            return

        if (
            self._window_started is None
            or now - self._window_started > self.MAX_RESTART_WINDOW_S
        ):
            # First death, or the slot ran healthily for a whole window: not a
            # crash loop, so start counting again.
            self._window_started = now
            self._restarts = 0

        if (
            self._last_restart_at is not None
            and now - self._last_restart_at < self.RESTART_BACKOFF_S
        ):
            # Wait before trying again, so an instantly reproducing failure
            # cannot spend the budget faster than the trouble can clear.
            return

        self._restarts += 1
        if self._restarts > self.MAX_RESTARTS:
            self._gave_up_at = now
            runtime.logger.error(
                f"{self.name} died {self.MAX_RESTARTS + 1} times within "
                f"{self.MAX_RESTART_WINDOW_S} s and will not be restarted "
                f"again; work routed to it is no longer being processed"
            )
            # Still hand the work back: the queue is shared with the other
            # slots of this type, so a sibling can still pick it up.
            self._requeue_inflight()
            return

        runtime.logger.error(
            f"{self.name} died unexpectedly; restarting "
            f"({self._restarts}/{self.MAX_RESTARTS})"
        )
        self._last_restart_at = now
        self._requeue_inflight()
        try:
            replacement = self._new_thread()
            replacement.start()
        except Exception:
            # Leave the dead thread in the slot so the next tick tries again.
            # Its in-flight work has already been handed back exactly once.
            runtime.logger.exception(f"Could not restart {self.name}")
        else:
            self.thread = replacement

    def _report_still_dead(self, now: float) -> None:
        """Keep an abandoned slot visible instead of letting it fail silently."""
        if now - self._gave_up_at < self.GIVE_UP_REPORT_INTERVAL_S:
            return
        self._gave_up_at = now
        runtime.logger.error(
            f"{self.name} is still dead and no longer supervised; restart the "
            f"file converter to recover it"
        )

    def _requeue_inflight(self) -> None:
        """Hand the dead thread's in-flight work back to its queue.

        The item was taken off the queue before the thread died, so it exists
        only on the dead object. The watcher will not offer a file again - it
        computes new work as a difference against the previous walk, so a file
        still sitting in the streams folder is never re-seen. Before
        supervision existed the wedged converter forced a restart, and the
        watcher's empty baseline picked it up; now that the queue keeps
        draining, nothing would.

        The worker owns the handover so it can clear its own marker as it hands
        the item over: a slot whose replacement could not be built must not
        offer the same item again on its next attempt. Threads with no
        in-flight work to return do not implement it.
        """
        requeue = getattr(self.thread, "requeue_inflight", None)
        if requeue is None:
            return
        try:
            handed_back = requeue()
        except Exception:
            runtime.logger.exception(
                f"{self.name} died holding work that could not be re-queued; "
                f"it stays where it is until the service restarts"
            )
            return
        if handed_back is not None:
            runtime.logger.warning(
                f"{self.name} died holding {handed_back}; put it back on the queue"
            )

    def join(self) -> None:
        """Join the current thread, if it is running.

        A slot whose replacement could not be started holds a thread that was
        never started, and joining that raises. ensure_alive() has already
        reported it, so there is nothing to wait for here.
        """
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
    h5_fs_watcher = _Supervised(
        "FSWatcher *.h5",
        lambda: FSWatcher(
            path=runtime.config.source,
            pattern="*.h5",
            file_queue=h5_file_queue,
            interval=runtime.config.interval,  # default 3
            shutdown_event=SHUTDOWN_EVENT,
        ),
        restartable=False,
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
    raw_fs_watcher = _Supervised(
        "FSWatcher *.raw",
        lambda: FSWatcher(
            path=runtime.config.source,
            pattern="*.raw",
            file_queue=raw_file_queue,
            interval=runtime.config.interval,  # default 3
            shutdown_event=SHUTDOWN_EVENT,
        ),
        restartable=False,
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
        # Run main loop, watching every pipeline thread. The watchers are
        # watched too: they are the only producers, so a watcher that dies
        # stalls every upload while all the processors stay healthy and
        # idle - the same unreported stall as a dead processor, entered
        # from the other end of the pipeline. They are reported rather
        # than rebuilt, because a fresh watcher would rescan the folder.
        main(
            supervised=[
                *processors,
                *peak_workers,
                raw_fs_watcher,
                h5_fs_watcher,
            ]
        )
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
