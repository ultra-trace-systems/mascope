"""
Worker threads that handle peak detection requests from the backend.

When a user manually triggers peak detection from the UI, the backend
emits a peak_detection_request Socket.IO event.
The file converter socket handler enqueues the request, and PeakRecomputeWorker
threads process it.

Multiple worker threads share a single Queue and run concurrently.
The CPU-heavy fitting work is off-loaded to a ProcessPoolExecutor
inside detect_peaks.

PeakDetectionGuard rejects duplicate requests for the same sample file
at enqueue time (in the socket event handler), so the workers only see
unique filenames.
"""

from queue import Empty, Queue
from threading import Event, Thread
from typing import Any

from mascope_backend.file_converter.api import (
    fetch_instrument_functions,
    is_blank_sample_file,
    rematch_sample,
)
from mascope_backend.file_converter.errors import describe_exception
from mascope_backend.file_converter.peak_guard import PeakDetectionGuard
from mascope_backend.file_converter.runtime import runtime
from mascope_signal.peak import compute_peaks


class PeakRecomputeWorker(Thread):
    """Worker thread that pulls peak detection requests from a shared queue.

    Multiple instances can run in parallel for concurrent processing.

    :param socket_client: File converter socket client for emitting results.
    :param peak_recompute_queue: Thread-safe queue that receives request dicts.
    :param peak_guard: Shared guard for serialization / duplicate rejection.
    :param shutdown_event: Set to signal graceful shutdown.
    """

    def __init__(
        self,
        socket_client,
        peak_recompute_queue: Queue,
        peak_guard: PeakDetectionGuard,
        shutdown_event: Event,
    ):
        super().__init__(daemon=True, name="PeakRecomputeWorker")
        self.socket_client = socket_client
        self.queue = peak_recompute_queue
        self.peak_guard = peak_guard
        self.shutdown_event = shutdown_event
        # The request currently off the queue, so the service's supervisor
        # can hand it back if this thread dies while holding it.
        self._inflight: dict[str, Any] | None = None

    def _emit_blank_sample_warning(
        self,
        filename: str,
        sample_file_id: str | None,
        process_id: str | None,
        auth: dict,
    ) -> None:
        """Emit a warning when manual peak detection is requested for a blank sample."""
        self.socket_client.emit(
            "peak_detection_warning",
            {
                "filename": filename,
                "sample_file_id": sample_file_id,
                "process_id": process_id,
                "message": "No peaks found.",
            },
            auth,
        )

    @staticmethod
    def _build_auth(access_token: object, user_id: object) -> dict[str, Any]:
        """Build auth payload only when a valid access token is present.

        Returning an empty dict allows FileConverterSocketClient.emit() to
        fall back to context_manager credentials when available.
        """
        if not access_token:
            return {}

        auth = {"access_token": access_token}
        if user_id is not None:
            auth["user_id"] = user_id
        return auth

    def _process_request(self, request: dict) -> None:
        """Process a single peak-detection request.

        :param request: Queue item with filename, credentials, etc.
        """
        filename = request.get("filename")
        access_token = request.get("access_token")
        user_id = request.get("user_id")
        sample_file_id = request.get("sample_file_id")
        affected_sample_item_ids = request.get("affected_sample_item_ids", [])
        process_id = request.get("process_id")
        auth = self._build_auth(access_token=access_token, user_id=user_id)

        try:
            if not isinstance(filename, str) or not filename:
                raise ValueError("Peak detection request is missing a valid filename")
            if not isinstance(access_token, str) or not access_token:
                raise ValueError(
                    f"Peak detection request for '{filename}' is missing a "
                    "valid access token"
                )

            runtime.logger.info(
                f"PeakRecomputeWorker: processing peak detection for '{filename}'"
            )
            if is_blank_sample_file(filename, access_token):
                runtime.logger.info(
                    (
                        "PeakRecomputeWorker: skipping peak detection for "
                        f"blank sample '{filename}'"
                    )
                )
                self._emit_blank_sample_warning(
                    filename=filename,
                    sample_file_id=sample_file_id,
                    process_id=process_id,
                    auth=auth,
                )
                return

            instrument_functions = fetch_instrument_functions(
                filename,
                access_token,
            )

            def progress_callback(progress: int):
                self.socket_client.emit(
                    "peak_detection_progress",
                    {
                        "filename": filename,
                        "sample_file_id": sample_file_id,
                        "process_id": process_id,
                        "progress": progress,
                    },
                    auth,
                )

            compute_peaks(
                filename,
                instrument_functions,
                progress_callback=progress_callback,
            )

            if affected_sample_item_ids:
                for sample_item_id in affected_sample_item_ids:
                    rematch_sample(
                        sample_item_id=sample_item_id,
                        access_token=access_token,
                        full_remove=True,
                    )

            runtime.logger.info(
                f"PeakRecomputeWorker: peak detection complete for '{filename}'"
            )

            self.socket_client.emit(
                "peak_detection_complete",
                {
                    "filename": filename,
                    "sample_file_id": sample_file_id,
                    "process_id": process_id,
                },
                auth,
            )

        except Exception as e:
            runtime.logger.exception(
                f"PeakRecomputeWorker: peak detection failed for '{filename}'"
            )
            self.socket_client.emit(
                "peak_detection_error",
                {
                    "filename": filename,
                    "sample_file_id": sample_file_id,
                    "process_id": process_id,
                    "error": describe_exception(e),
                },
                auth,
            )
        finally:
            if isinstance(filename, str) and filename:
                self.peak_guard.release(filename)

    def requeue_inflight(self) -> str | None:
        """Offer the request this worker was running back to the queue.

        Called by the service's supervisor once this thread has died. Without
        it the request is simply lost: the caller never receives
        peak_detection_complete or peak_detection_error, so the UI waits on
        that process forever, and the PeakDetectionGuard slot taken when the
        request was enqueued is never released, which rejects every later
        request for the same sample file until the service restarts.

        The marker is cleared as the request is handed over, so a slot whose
        replacement could not be built does not enqueue it twice.

        The guard slot has to be re-taken here. It was acquired when the request
        was enqueued and released again by _process_request's finally as the
        exception that killed this thread unwound, so putting the request back
        without re-acquiring would let the UI enqueue a second run for the same
        file: the two would compute peaks over one filestore at the same time,
        and whichever finished first would release the other's slot. If the slot
        is already taken, that other request is doing this work, and re-queueing
        would only duplicate it.

        :return: A label for the work handed back, or None if there was none
        :rtype: str | None
        """
        request = self._inflight
        if request is None:
            return None
        self._inflight = None
        filename = request.get("filename") if isinstance(request, dict) else None
        if isinstance(filename, str) and filename:
            reserved, _ = self.peak_guard.acquire(filename)
            if not reserved:
                self._report_dropped(request, filename)
                return None
            self.queue.put(request)
            return f"peak detection for '{filename}'"
        self.queue.put(request)
        return "a peak detection request"

    def _report_dropped(self, request: dict[str, Any], filename: str) -> None:
        """Tell the caller their peak detection is not going to happen.

        Reached when the guard slot for this file was taken by a newer request
        between this worker dying and its work being handed back. That request
        will do the computation, but it carries a different process id, so
        without this the caller waiting on the dropped one watches a progress
        dialog that never resolves - the exact failure the handover exists to
        prevent.

        :param request: The queue item that is not going back on the queue.
        :type request: dict[str, Any]
        :param filename: Base filename the request was for.
        :type filename: str
        """
        runtime.logger.warning(
            f"PeakRecomputeWorker: not re-queueing peak detection for "
            f"'{filename}'; another request for it is already running"
        )
        try:
            self.socket_client.emit(
                "peak_detection_error",
                {
                    "filename": filename,
                    "sample_file_id": request.get("sample_file_id"),
                    "process_id": request.get("process_id"),
                    "error": (
                        "Peak detection stopped unexpectedly. Another request "
                        "for this file is already running - please wait for it "
                        "to finish."
                    ),
                },
                self._build_auth(
                    access_token=request.get("access_token"),
                    user_id=request.get("user_id"),
                ),
            )
        except Exception:
            runtime.logger.exception(
                f"PeakRecomputeWorker: could not report the dropped peak "
                f"detection request for '{filename}'"
            )

    def run(self) -> None:
        """Thread entry point: pull requests from the queue and process them."""
        runtime.logger.info("PeakRecomputeWorker started")
        while not self.shutdown_event.is_set():
            try:
                request = self.queue.get(timeout=1)
            except Empty:
                continue
            self._inflight = request
            try:
                self._process_request(request)
            except Exception as e:
                runtime.logger.exception(
                    "PeakRecomputeWorker: unexpected error while processing request"
                )
                try:
                    if isinstance(request, dict):
                        filename = request.get("filename", "unknown")
                        sample_file_id = request.get("sample_file_id")
                        process_id = request.get("process_id")
                        auth = self._build_auth(
                            access_token=request.get("access_token"),
                            user_id=request.get("user_id"),
                        )
                    else:
                        filename = "unknown"
                        sample_file_id = None
                        process_id = None
                        auth = {}

                    self.socket_client.emit(
                        "peak_detection_error",
                        {
                            "filename": filename,
                            "sample_file_id": sample_file_id,
                            "process_id": process_id,
                            "error": describe_exception(e),
                        },
                        auth,
                    )
                except Exception:
                    runtime.logger.exception(
                        "PeakRecomputeWorker: failed to emit error message after "
                        "processing failure"
                    )
            # Reported one way or the other: no longer in flight.
            self._inflight = None
        runtime.logger.info("PeakRecomputeWorker stopped")
