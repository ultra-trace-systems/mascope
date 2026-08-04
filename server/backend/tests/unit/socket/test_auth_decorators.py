"""
Unit tests for the socket_auth decorator's logging levels.

``connect`` accepts unauthenticated sockets, so every guarded event such a
client emits reaches the decorator with no session in Redis. That is routine
and must stay below the error-monitoring threshold (records at WARNING and
above are exported; see mascope_runtime.logging), while a genuine bug inside a
handler must still be recorded with its traceback.
"""

from unittest.mock import AsyncMock, patch

import pytest

# mascope_backend.socket.auth and mascope_backend.api.new.auth import each
# other; importing the socket side first hits the cycle mid-initialization.
# The application boots through the api side, so do the same here.
import mascope_backend.api.new.auth  # noqa: F401
from mascope_backend.runtime import runtime
from mascope_backend.socket.auth.decorators import socket_auth
from mascope_backend.socket.auth.exceptions import SocketForbiddenError
from mascope_backend.socket.storage import SocketSessionError


_DECORATORS = "mascope_backend.socket.auth.decorators"


async def _call_guarded_event(session, handler=None) -> list:
    """Invoke a socket_auth-guarded handler, returning the captured records.

    ``session`` is either the session dict get_session_user returns or the
    exception it raises.
    """
    records = []
    sink_id = runtime.logger.add(
        lambda message: records.append(message.record), level="TRACE"
    )

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
    try:
        with patch(f"{_DECORATORS}.get_session_user", new=get_session_user):
            await handle_event("sid-001")
    finally:
        runtime.logger.remove(sink_id)
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
