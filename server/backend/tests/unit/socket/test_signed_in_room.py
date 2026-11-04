"""
Tests: which sockets authentication puts in the signed-in room.

A record-sync event emitted without a room goes to the signed-in room, not to
every socket on the namespace (:mod:`mascope_backend.socket.signed_in`). A web
socket enters it when it authenticates and nothing holds its account; a socket
that fails to authenticate leaves it, since the attempt has just cleared the
session it may have been in the room under; a service socket, on a namespace
of its own, never enters it.

The token check and the session store are mocked: what is under test is which
way each outcome sends the socket.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import order matters, as in test_subscription_password_gate: entering the
# graph through socket.auth leaves that package half-built.
import mascope_backend.api.new.auth  # noqa: F401
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.socket.auth.exceptions import SocketUnauthenticatedError
from mascope_backend.socket.auth.service import authenticate_socket_connection
from mascope_backend.socket.records.service import emit_record_reload
from mascope_backend.socket.signed_in import SIGNED_IN_ROOM


_SERVICE = "mascope_backend.socket.auth.service"
_ROOM = "mascope_backend.socket.signed_in"

#: Comfortably above any configured role threshold.
_ROLE_ID = 1000

#: The token's issue time, and an expiry a day away.
_ISSUED_AT = 1
_EXPIRES_AT = int(time.time()) + 86400


def _user(**overrides):
    fields = dict(
        id=42,
        username="someone",
        role_id=_ROLE_ID,
        must_change_password=False,
        is_active=True,
        sessions_revoked_at=None,
        password_changed_at=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def save_session():
    with patch(f"{_SERVICE}.save_user_session", AsyncMock()) as save:
        yield save


@pytest.fixture
def sio(save_session):
    server = MagicMock()
    server.enter_room = AsyncMock()
    server.leave_room = AsyncMock()
    tracker = MagicMock()
    tracker.join_socket = AsyncMock()
    tracker.leave_socket = AsyncMock()
    with (
        patch(f"{_ROOM}.sio", server),
        patch(f"{_ROOM}.room_tracker", tracker),
        patch(f"{_SERVICE}.clear_user_session", AsyncMock()),
    ):
        yield server


async def _authenticate(**kwargs) -> None:
    await authenticate_socket_connection(
        sid="sid-a", token="a-token", minimum_role="guest", **kwargs
    )


@pytest.mark.asyncio
async def test_a_web_socket_that_signs_in_enters(sio):
    with patch(
        f"{_SERVICE}.validate_jwt_token",
        AsyncMock(return_value=(_user(), _ISSUED_AT, _EXPIRES_AT)),
    ):
        await _authenticate()

    sio.enter_room.assert_awaited_once_with("sid-a", SIGNED_IN_ROOM)
    sio.leave_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_web_socket_s_session_keeps_its_token_s_times(sio, save_session):
    """Subscribe and the renewal read them later, without the token."""
    user = _user()
    with patch(
        f"{_SERVICE}.validate_jwt_token",
        AsyncMock(return_value=(user, _ISSUED_AT, _EXPIRES_AT)),
    ):
        await _authenticate()

    save_session.assert_awaited_once_with(
        "sid-a", user, namespace="/", issued_at=_ISSUED_AT, expires_at=_EXPIRES_AT
    )


@pytest.mark.asyncio
async def test_a_web_socket_whose_token_has_expired_leaves(sio):
    """Its token expired between being read and the room being entered."""
    with patch(
        f"{_SERVICE}.validate_jwt_token",
        AsyncMock(return_value=(_user(), _ISSUED_AT, int(time.time()) - 1)),
    ):
        await _authenticate()

    sio.enter_room.assert_not_awaited()
    sio.leave_room.assert_awaited_once_with("sid-a", SIGNED_IN_ROOM)


@pytest.mark.asyncio
async def test_a_held_web_socket_that_signs_in_leaves(sio):
    """A tab that was in the room as an account now held is taken out."""
    held = _user(must_change_password=True)
    with patch(
        f"{_SERVICE}.validate_jwt_token",
        AsyncMock(return_value=(held, _ISSUED_AT, _EXPIRES_AT)),
    ):
        await _authenticate()

    sio.enter_room.assert_not_awaited()
    sio.leave_room.assert_awaited_once_with("sid-a", SIGNED_IN_ROOM)


@pytest.mark.asyncio
async def test_a_service_socket_never_enters(sio):
    with patch(
        f"{_SERVICE}.validate_service_access_token", AsyncMock(return_value=_user())
    ):
        await _authenticate(service_name="file-converter")

    sio.enter_room.assert_not_awaited()
    sio.leave_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_web_socket_with_an_invalid_token_leaves(sio):
    with (
        patch(
            f"{_SERVICE}.validate_jwt_token",
            AsyncMock(side_effect=InvalidTokenException()),
        ),
        pytest.raises(SocketUnauthenticatedError),
    ):
        await _authenticate()

    sio.enter_room.assert_not_awaited()
    sio.leave_room.assert_awaited_once_with("sid-a", SIGNED_IN_ROOM)


@pytest.mark.asyncio
async def test_a_web_socket_failing_otherwise_leaves(sio):
    """A role too low, a store fault: the same way out."""
    with (
        patch(
            f"{_SERVICE}.validate_jwt_token",
            AsyncMock(return_value=(_user(role_id=-1), _ISSUED_AT, _EXPIRES_AT)),
        ),
        pytest.raises(SocketUnauthenticatedError),
    ):
        await _authenticate()

    sio.enter_room.assert_not_awaited()
    sio.leave_room.assert_awaited_once_with("sid-a", SIGNED_IN_ROOM)


@pytest.mark.asyncio
async def test_a_service_socket_failing_touches_no_web_room(sio):
    with (
        patch(
            f"{_SERVICE}.validate_service_access_token",
            AsyncMock(side_effect=InvalidTokenException()),
        ),
        pytest.raises(SocketUnauthenticatedError),
    ):
        await _authenticate(service_name="file-converter")

    sio.leave_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_service_socket_failing_otherwise_touches_no_web_room(sio):
    with (
        patch(
            f"{_SERVICE}.validate_service_access_token",
            AsyncMock(return_value=_user(role_id=-1)),
        ),
        pytest.raises(SocketUnauthenticatedError),
    ):
        await _authenticate(service_name="file-converter")

    sio.leave_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_room_less_record_event_goes_to_the_signed_in_room():
    server = MagicMock()
    server.emit = AsyncMock()
    with patch("mascope_backend.socket.records.service.sio", server):
        await emit_record_reload(record_type="workspace")

    server.emit.assert_awaited_once()
    assert server.emit.await_args.kwargs["room"] == SIGNED_IN_ROOM
    assert server.emit.await_args.kwargs["namespace"] == "/"
