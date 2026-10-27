import threading

import socketio

from mascope_backend.file_converter.socket.events import SocketEventHandler
from mascope_backend.file_converter.socket.session import FileContextManager
from mascope_backend.runtime import runtime
from mascope_backend.service_token import FILE_CONVERTER_SERVICE_TOKEN
from mascope_runtime.logging import SENTRY_FINGERPRINT


class FileConverterSocketClient:
    """Socket.IO client for file converter service"""

    def __init__(self, url: str, peak_recompute_queue=None, peak_guard=None):
        self.url = url
        self.sio = socketio.Client(
            logger=False,
            ssl_verify=False,
            # Send no Origin. This is a service-to-service client, not a page, so
            # it has no origin to speak for - but websocket-client synthesises
            # one from the URL it dials unless told not to, and the server side
            # rejects a handshake whose Origin is not the deployment's own (see
            # mascope_backend.socket.server). Suppressing it keeps the converter
            # outside that check entirely rather than relying on the synthesised
            # value happening to match whatever host it connected to.
            websocket_extra_options={"suppress_origin": True},
        )
        self.context_manager = FileContextManager()
        # The access token the backend last rejected for each file (by its
        # normalized name): an event for that file carrying that token would
        # only be dropped again, so it is not sent. A later context or peak
        # request brings a fresh token, which is sent as usual. Written from
        # the Socket.IO thread that runs acknowledgement callbacks, read from
        # the processing threads that emit.
        self._rejected_tokens: dict[str | None, str | None] = {}
        self._rejected_lock = threading.Lock()
        self.event_handler = SocketEventHandler(
            self,
            peak_recompute_queue=peak_recompute_queue,
            peak_guard=peak_guard,
        )
        self._print_registered_events()

    def _print_registered_events(self):
        """Print all registered event handlers in a single line"""
        all_events = [
            f"{event}"
            for namespace in self.sio.handlers.values()
            for event in namespace.keys()
        ]
        runtime.logger.debug(
            f"Registered socket events: {', '.join(sorted(set(all_events)))}"
        )

    def connect(self):
        """Connect to Mascope server.

        Raises on failure; the caller owns retry and logging (during a
        stack restart the backend is expected to be unreachable for a
        while, so a failed attempt here is not an incident by itself).
        """
        # Guard check if already connected (multiple workers race condition)
        if self.sio.connected:
            runtime.logger.debug("Already connected, skipping reconnect")
            return

        self.sio.connect(
            self.url,
            headers={"X-Service-Name": "file-converter"},
            # Prove this is the genuine converter, not a client that guessed the
            # public X-Service-Name string; the backend refuses the namespace
            # connect without it (see the /file-converter connect handler).
            auth={"service_token": FILE_CONVERTER_SERVICE_TOKEN},
            namespaces=["/file-converter"],
            # Websocket only: a single persistent connection stays pinned to one
            # backend worker. The default polling handshake issues several HTTP
            # requests that a multi-worker backend (workers="auto") load-balances
            # across processes, so the Engine.IO session is never found on the
            # worker handling the next request and the namespace never connects.
            # The file-converter reaches the backend directly (no nginx ip_hash
            # stickiness), so it must avoid polling itself.
            transports=["websocket"],
        )

    def emit(self, event: str, data: dict, auth: dict = {}):
        """Emit an event to the server, with optional auth.
        If auth is not provided, will try to fill from file context
        (if filename is in data and context exists).

        :param event: Event name to emit
        :type event: str
        :param data: Event data dict
        :type data: dict
        :param auth: Optional auth dict with 'access_token' and 'user_id'.
        :type auth: dict | None, optional
        """
        try:
            file_context = None
            if "filename" in data:
                # If filename is provided, try to get file context (for auth fallback)
                file_context = self.context_manager.get_context(data["filename"])
            if auth:
                # If auth is provided, it takes precedence over file context
                data.update(auth)
            else:
                # If no auth provided, try to fill from file context (if exists)
                if file_context is not None:
                    data.update(
                        {
                            "access_token": file_context.access_token,
                            "user_id": file_context.user_id,
                        }
                    )
            file_key = self._file_key(data)
            access_token = data.get("access_token")
            if access_token and self._is_rejected(file_key, access_token):
                runtime.logger.debug(
                    f"Not emitting {event} for {data.get('filename')}: "
                    "the backend rejected its access token"
                )
                return
            self.sio.emit(
                event,
                data,
                namespace="/file-converter",
                callback=self._acknowledgement_handler(event, file_key, access_token),
            )
        except Exception:
            runtime.logger.exception(f"Failed to emit {event}")

    def _file_key(self, data: dict) -> str | None:
        """The name rejections are kept under: the file's normalized name, so
        a polarity's events and its file's share one (None for no file)."""
        filename = data.get("filename")
        if not isinstance(filename, str):
            return None
        return self.context_manager._normalize_filename(filename)

    def _is_rejected(self, file_key: str | None, access_token: str) -> bool:
        with self._rejected_lock:
            return self._rejected_tokens.get(file_key) == access_token

    def _acknowledgement_handler(
        self, event: str, file_key: str | None, access_token: str | None
    ):
        """The callback that reads the backend's acknowledgement of ``event``.

        The backend acknowledges an event it rejects for auth with
        ``{"error": <code>}`` and drops it (``file_converter_socket_auth``); an
        accepted event's acknowledgement is its handler's return, today
        nothing. A rejection is logged once per file and token, and the
        token is remembered so ``emit`` stops sending that file's events with
        it. Events already sent before the first acknowledgement came back
        are dropped by the backend too, and logged no further. An event sent
        with no token is never held back: ``emit`` compares only a token it
        is about to send.
        """

        def on_acknowledgement(*args):
            payload = args[0] if args else None
            if not isinstance(payload, dict) or "error" not in payload:
                return
            code = payload["error"]
            with self._rejected_lock:
                if (
                    file_key in self._rejected_tokens
                    and self._rejected_tokens[file_key] == access_token
                ):
                    return
                self._rejected_tokens[file_key] = access_token
            # One issue for every such rejection: the file name in the
            # message would otherwise split it into one per file
            runtime.logger.bind(
                **{SENTRY_FINGERPRINT: ["file-converter-event-rejected", str(code)]}
            ).warning(
                f"The backend rejected file converter event {event} for "
                f"{file_key}: {code}; not sending further events for it with "
                "this access token"
            )

        return on_acknowledgement
