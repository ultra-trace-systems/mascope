from typing import TYPE_CHECKING

from mascope_backend.file_converter.peak_guard import PeakDetectionGuard
from mascope_backend.file_converter.socket.session import FileContext
from mascope_backend.runtime import runtime


if TYPE_CHECKING:
    from queue import Queue

    from .client import FileConverterSocketClient


class SocketEventHandler:
    def __init__(
        self,
        client: "FileConverterSocketClient",
        peak_recompute_queue: "Queue | None" = None,
        peak_guard: "PeakDetectionGuard | None" = None,
    ):
        self.client = client
        self._peak_recompute_queue = peak_recompute_queue
        self._peak_guard = peak_guard
        self.register_events()

    def register_events(self):
        @self.client.sio.event(namespace="/file-converter")
        def connect():
            runtime.logger.info(f"Connected to mascope server at {self.client.url}")

        @self.client.sio.event(namespace="/file-converter")
        def disconnect():
            runtime.logger.info("Disconnected from mascope server")

        @self.client.sio.on("file_context", namespace="/file-converter")
        def on_file_context(data):
            context = _build_file_context(data)
            self.client.context_manager.register_file(context)

        @self.client.sio.on("peak_detection_request", namespace="/file-converter")
        def on_peak_detection_request(data):
            """Handles peak detection requests from the backend.

            Acquires the peak detection guard before enqueuing so that
            duplicate requests for the same file are rejected immediately
            instead of sitting in the queue.

            Auth credentials (access_token, user_id) are carried inside
            the queue item.
            """
            filename = data["filename"]
            runtime.logger.info(f"Received peak detection request for {filename}")

            if self._peak_recompute_queue is None:
                runtime.logger.error(
                    "Peak detection queue not available - ignoring request"
                )
                return

            if self._peak_guard is not None:
                is_acquired, failure_reason = self._peak_guard.acquire(filename)
                if not is_acquired:
                    # The guard already logged the rejection
                    self.client.sio.emit(
                        "peak_detection_warning",
                        {
                            "filename": filename,
                            "sample_file_id": data.get("sample_file_id"),
                            "process_id": data.get("process_id"),
                            "message": failure_reason,
                            "access_token": data.get("access_token"),
                            "user_id": data.get("user_id"),
                        },
                        namespace="/file-converter",
                    )
                    return

            self._peak_recompute_queue.put(data)


def _build_file_context(data: dict) -> FileContext:
    """Helper to build FileContext from incoming socket data"""
    return FileContext(
        filename=data["filename"],
        user_id=data["user_id"],
        username=data["username"],
        role_id=data["role_id"],
        access_token=data["access_token"],
        device_id=data.get("device_id"),
        instrument_timezone=data.get("instrument_timezone"),
    )
