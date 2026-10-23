"""
Unit tests for the socket auth decorators' logging levels.

``connect`` accepts unauthenticated sockets, so every guarded event such a
client emits reaches the decorator with no session in Redis. That is routine
and must stay below the error-monitoring threshold (records at WARNING and
above are exported; see mascope_runtime.logging), while a genuine bug inside a
handler must still be recorded with its traceback.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_utils import captured_logs

# mascope_backend.socket.auth and mascope_backend.api.new.auth import each
# other; importing the socket side first hits the cycle mid-initialization.
# The application boots through the api side, so do the same here.
import mascope_backend.api.new.auth  # noqa: F401
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.runtime import runtime
from mascope_backend.socket.auth.decorators import (
    file_converter_socket_auth,
    socket_auth,
)
from mascope_backend.socket.auth.exceptions import SocketForbiddenError
from mascope_backend.socket.storage import SocketSessionError


_DECORATORS = "mascope_backend.socket.auth.decorators"


async def _call_guarded_event(session, handler=None) -> list:
    """Invoke a socket_auth-guarded handler, returning the captured records.

    ``session`` is either the session dict get_session_user returns or the
    exception it raises.
    """

    @socket_auth(minimum_role="guest")
    async def handle_event(sid, *args, **kwargs):
        if handler is not None:
            return await handler(sid, *args, **kwargs)
        return "ok"

    get_session_user = (
        AsyncMock(side_effect=session)
        if isinstance(session, Exception)
        else AsyncMock(return_value=session)
    )
    with (
        captured_logs() as records,
        patch(f"{_DECORATORS}.get_session_user", new=get_session_user),
    ):
        await handle_event("sid-001")
    return records


def _monitored(records: list) -> list:
    """Records that would reach error monitoring (WARNING and above)."""
    return [r for r in records if r["level"].no >= runtime.logger.level("WARNING").no]


@pytest.mark.asyncio
async def test_missing_session_is_not_monitored():
    """The common case: an event from a socket that never authenticated."""
    records = await _call_guarded_event(SocketSessionError("No session found"))

    assert _monitored(records) == []
    assert any("No session found" in r["message"] for r in records)


@pytest.mark.asyncio
async def test_corrupted_session_is_not_re_reported():
    """get_session_user already logs the underlying fault at ERROR itself."""
    records = await _call_guarded_event(SocketSessionError("Corrupted session data"))

    assert _monitored(records) == []


@pytest.mark.asyncio
async def test_insufficient_role_is_not_monitored():
    records = await _call_guarded_event(SocketForbiddenError())

    assert _monitored(records) == []


@pytest.mark.asyncio
async def test_handler_bug_is_still_monitored():
    """The broad catch swallows handler exceptions, so they must be recorded."""

    async def _boom(sid, *args, **kwargs):
        raise KeyError("sample_item_id")

    session = {"user_id": 1, "role_id": 400}  # owner, clears any minimum_role
    records = await _call_guarded_event(session, handler=_boom)

    monitored = _monitored(records)
    assert len(monitored) == 1
    assert monitored[0]["level"].name == "ERROR"
    assert monitored[0]["exception"] is not None


# file_converter_socket_auth: the converter sends its token in every event's
# payload. A rejection must not propagate out of the wrapper: python-socketio
# runs each handler as a task nothing awaits, so a raised rejection surfaced
# as "Task exception was never retrieved" at ERROR, in error monitoring.
# The wrapper returns {"error": <code>} instead, which python-socketio sends
# as the acknowledgement to a client that asked for one.


async def _call_converter_event(data: dict, user=None, minimum_role="editor"):
    """Invoke a file_converter_socket_auth-guarded handler.

    ``user`` is either the user validate_service_access_token returns or the
    exception it raises. Returns the captured records, the handler's calls and
    the wrapper's return value; awaiting the wrapper raises if anything
    propagates out of it.
    """
    calls = []

    @file_converter_socket_auth(minimum_role=minimum_role)
    async def handle_event(sid, event_data):
        calls.append(event_data)
        return "ok"

    validate = (
        AsyncMock(side_effect=user)
        if isinstance(user, Exception)
        else AsyncMock(return_value=user)
    )
    with (
        captured_logs() as records,
        patch(f"{_DECORATORS}.validate_service_access_token", new=validate),
    ):
        result = await handle_event("sid-001", data)
    return records, calls, result


_EDITOR = SimpleNamespace(username="converter", role_id=200)
_GUEST = SimpleNamespace(username="converter", role_id=100)


@pytest.mark.asyncio
async def test_converter_missing_token_is_dropped_unmonitored():
    records, calls, result = await _call_converter_event({"filename": "a.raw"})

    assert calls == []
    assert result == {"error": "SOCKET_UNAUTHENTICATED"}
    assert _monitored(records) == []
    assert any("Missing access token" in r["message"] for r in records)


@pytest.mark.asyncio
async def test_converter_invalid_token_is_dropped_unmonitored():
    records, calls, result = await _call_converter_event(
        {"access_token": "expired"}, user=InvalidTokenException("Token expired")
    )

    assert calls == []
    assert result == {"error": "SOCKET_UNAUTHENTICATED"}
    assert _monitored(records) == []
    assert any("Token expired" in r["message"] for r in records)


@pytest.mark.asyncio
async def test_converter_insufficient_role_is_dropped_unmonitored():
    records, calls, result = await _call_converter_event(
        {"access_token": "token"}, user=_GUEST
    )

    assert calls == []
    assert result == {"error": "SOCKET_FORBIDDEN"}
    assert _monitored(records) == []


@pytest.mark.asyncio
async def test_converter_role_configuration_error_is_still_monitored():
    records, calls, result = await _call_converter_event(
        {"access_token": "token"}, user=_EDITOR, minimum_role="no-such-role"
    )

    assert calls == []
    assert result == {"error": "SOCKET_AUTH_CONFIG_ERROR"}
    monitored = _monitored(records)
    assert len(monitored) == 1
    assert "Invalid role configuration" in monitored[0]["message"]


@pytest.mark.asyncio
async def test_converter_authenticated_event_reaches_the_handler():
    records, calls, result = await _call_converter_event(
        {"access_token": "token", "filename": "a.raw"}, user=_EDITOR
    )

    assert calls == [{"filename": "a.raw"}]
    assert result == "ok"
    assert _monitored(records) == []


@pytest.mark.asyncio
async def test_converter_handler_bug_acknowledges_nothing():
    """Only an auth rejection answers with an error payload."""

    @file_converter_socket_auth(minimum_role="editor")
    async def handle_event(sid, event_data):
        raise KeyError("user_id")

    with (
        captured_logs() as records,
        patch(
            f"{_DECORATORS}.validate_service_access_token",
            new=AsyncMock(return_value=_EDITOR),
        ),
    ):
        result = await handle_event("sid-001", {"access_token": "token"})

    assert result is None
    assert len(_monitored(records)) == 1
