"""The converter reads the backend's acknowledgement of the events it emits.

The backend drops a ``/file-converter`` event whose access token it rejects
and acknowledges it with ``{"error": <code>}`` (``file_converter_socket_auth``).
Without a callback the converter never learned of it and kept sending a
rejected file's progress and result events for nothing. These tests drive
``FileConverterSocketClient.emit`` with ``sio.emit`` replaced, and answer each
event as the backend would by calling the callback it was given.
"""

from unittest.mock import MagicMock

import pytest
from test_utils import captured_logs

from mascope_backend.file_converter.socket.client import FileConverterSocketClient
from mascope_backend.file_converter.socket.session import FileContext
from mascope_runtime.logging import SENTRY_FINGERPRINT


REJECTED = {"error": "SOCKET_UNAUTHENTICATED"}


def _context(filename: str, access_token: str) -> FileContext:
    return FileContext(
        filename=filename,
        user_id=7,
        username="uploader",
        role_id=2,
        access_token=access_token,
    )


@pytest.fixture
def client():
    client = FileConverterSocketClient("http://backend.invalid")
    client.sio.emit = MagicMock()
    return client


def _sent(client) -> list[tuple[str, dict]]:
    return [(c.args[0], c.args[1]) for c in client.sio.emit.call_args_list]


def _acknowledge(client, *args, index: int = -1):
    """Answer an emitted event as the backend does."""
    client.sio.emit.call_args_list[index].kwargs["callback"](*args)


def _rejection_warnings(records):
    return [
        r
        for r in records
        if r["level"].name == "WARNING" and "rejected" in r["message"]
    ]


def test_emit_asks_for_the_acknowledgement(client):
    client.context_manager.register_file(_context("sample.raw", "token-1"))

    client.emit("file_processing_progress", {"filename": "sample", "progress": 10})

    call = client.sio.emit.call_args
    assert call.kwargs["namespace"] == "/file-converter"
    assert callable(call.kwargs["callback"])
    assert call.args[1]["access_token"] == "token-1"


@pytest.mark.parametrize("ack", [(), (None,), ({"ok": True},)])
def test_an_accepted_event_changes_nothing(client, ack):
    client.context_manager.register_file(_context("sample.raw", "token-1"))
    client.emit("file_processing_progress", {"filename": "sample", "progress": 10})

    with captured_logs("WARNING") as records:
        _acknowledge(client, *ack)
    client.emit("file_processing_success", {"filename": "sample"})

    assert records == []
    assert [event for event, _ in _sent(client)] == [
        "file_processing_progress",
        "file_processing_success",
    ]


def test_a_rejection_is_logged_once_and_stops_the_files_events(client):
    client.context_manager.register_file(_context("sample.raw", "token-1"))
    client.emit("file_processing_progress", {"filename": "sample_+", "progress": 10})
    # Sent before the first acknowledgement came back
    client.emit("file_processing_progress", {"filename": "sample_+", "progress": 20})

    with captured_logs() as records:
        _acknowledge(client, REJECTED, index=0)
        _acknowledge(client, REJECTED, index=1)
        # The other polarity, and the file itself, share the rejected token
        client.emit(
            "file_processing_progress", {"filename": "sample_-", "progress": 30}
        )
        client.emit("file_processing_error", {"filename": "sample", "error": "x"})

    warnings = _rejection_warnings(records)
    assert len(warnings) == 1
    assert "file_processing_progress" in warnings[0]["message"]
    assert "SOCKET_UNAUTHENTICATED" in warnings[0]["message"]
    assert warnings[0]["extra"][SENTRY_FINGERPRINT] == [
        "file-converter-event-rejected",
        "SOCKET_UNAUTHENTICATED",
    ]
    assert len(_sent(client)) == 2


def test_a_forbidden_token_is_held_back_too(client):
    client.context_manager.register_file(_context("sample.raw", "token-1"))
    client.emit("file_processing_progress", {"filename": "sample", "progress": 10})
    _acknowledge(client, {"error": "SOCKET_FORBIDDEN"})

    client.emit("file_processing_success", {"filename": "sample"})

    assert [event for event, _ in _sent(client)] == ["file_processing_progress"]


@pytest.mark.parametrize(
    "code", ["SOCKET_AUTH_UNAVAILABLE", "SOCKET_AUTH_CONFIG_ERROR"]
)
def test_a_rejection_not_about_the_token_holds_nothing_back(client, code):
    """The token could not be checked (the backend's database failed for a
    moment), or the backend's role configuration is broken: the next event
    may pass, and a file's final event above all must still be sent."""
    client.context_manager.register_file(_context("sample.raw", "token-1"))
    client.emit("file_processing_progress", {"filename": "sample", "progress": 10})
    client.emit("file_processing_progress", {"filename": "sample", "progress": 20})

    with captured_logs() as records:
        _acknowledge(client, {"error": code}, index=0)
        _acknowledge(client, {"error": code}, index=1)
        client.emit("file_processing_success", {"filename": "sample"})

    warnings = _rejection_warnings(records)
    assert len(warnings) == 1
    assert code in warnings[0]["message"]
    assert "later events are still sent" in warnings[0]["message"]
    assert warnings[0]["extra"][SENTRY_FINGERPRINT] == [
        "file-converter-event-rejected",
        code,
    ]
    assert [event for event, _ in _sent(client)][-1] == "file_processing_success"

    # The token itself refused afterwards is logged, and holds back
    with captured_logs() as records:
        _acknowledge(client, REJECTED)
    client.emit("file_processing_success", {"filename": "sample"})

    assert len(_rejection_warnings(records)) == 1
    assert len(_sent(client)) == 3


def test_a_fresh_token_for_the_file_is_sent(client):
    client.context_manager.register_file(_context("sample.raw", "token-1"))
    client.emit("file_processing_progress", {"filename": "sample", "progress": 10})
    _acknowledge(client, REJECTED)

    # The file uploaded again: its new context carries a new token
    client.context_manager.register_file(_context("sample.raw", "token-2"))
    client.emit("file_processing_progress", {"filename": "sample", "progress": 10})

    assert len(_sent(client)) == 2
    assert _sent(client)[-1][1]["access_token"] == "token-2"


def test_an_explicit_auth_is_suppressed_by_its_own_token(client):
    # The peak recompute worker passes its request's token, not a context's
    auth = {"access_token": "token-1", "user_id": 7}
    client.emit("peak_detection_progress", {"filename": "sample"}, auth)
    _acknowledge(client, REJECTED)

    client.emit("peak_detection_complete", {"filename": "sample"}, dict(auth))
    client.emit(
        "peak_detection_complete",
        {"filename": "sample"},
        {"access_token": "token-2", "user_id": 7},
    )

    assert [payload["access_token"] for _, payload in _sent(client)] == [
        "token-1",
        "token-2",
    ]


def test_other_files_are_unaffected(client):
    client.context_manager.register_file(_context("sample.raw", "token-1"))
    client.context_manager.register_file(_context("other.raw", "token-1"))
    client.emit("file_processing_progress", {"filename": "sample", "progress": 10})
    _acknowledge(client, REJECTED)

    client.emit("file_processing_progress", {"filename": "other", "progress": 10})

    assert [payload["filename"] for _, payload in _sent(client)] == [
        "sample",
        "other",
    ]


def test_an_event_without_a_token_is_never_suppressed(client):
    # upload_lost names a file with no upload context, so it carries no token
    client.emit("upload_lost", {"filename": "lost.raw"})
    with captured_logs("WARNING") as records:
        _acknowledge(client, REJECTED)
    client.emit("upload_lost", {"filename": "lost.raw"})
    with captured_logs("WARNING") as again:
        _acknowledge(client, REJECTED)

    assert len(_rejection_warnings(records)) == 1
    assert _rejection_warnings(again) == []
    assert len(_sent(client)) == 2


def test_the_peak_guards_warning_goes_through_emit(client):
    """The duplicate-request warning used to call ``sio.emit`` directly,
    without a callback, so a rejection of it went unread."""
    guard = MagicMock()
    guard.acquire.return_value = (False, "already running")
    client.event_handler._peak_guard = guard
    client.event_handler._peak_recompute_queue = MagicMock()
    on_request = client.sio.handlers["/file-converter"]["peak_detection_request"]

    on_request({"filename": "sample", "access_token": "token-1", "user_id": 7})
    _acknowledge(client, REJECTED)
    on_request({"filename": "sample", "access_token": "token-1", "user_id": 7})

    ((event, payload),) = _sent(client)
    assert event == "peak_detection_warning"
    assert payload["access_token"] == "token-1"
    assert payload["user_id"] == 7
    assert payload["message"] == "already running"
