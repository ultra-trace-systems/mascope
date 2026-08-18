"""
The ``/file-converter`` namespace connect handler authenticates the converter
with the shared service secret, not just the public ``X-Service-Name`` string.

Payloads emitted on this namespace carry a user's access token, so a client that
merely guessed the service name must not be able to subscribe. These drive the
connect coroutine directly (its Redis write and room join are mocked) to pin
accept/reject on the token, and the converter-client half asserts the genuine
converter presents that token when it dials in.

The auth payload is client-controlled JSON of any shape, so the malformed-shape
cases (non-dict auth, non-string or non-ASCII token) must reject like any bad
token - as a WARNING-level refusal, not an unhandled error.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Importing the handlers package below pulls in the default-namespace handlers,
# whose mascope_backend.socket.auth import cycles with mascope_backend.api.new.auth;
# the application boots through the api side, so import it first (as the sibling
# socket tests do) to avoid hitting the cycle mid-initialization.
import mascope_backend.api.new.auth  # noqa: F401
from mascope_backend.service_token import FILE_CONVERTER_SERVICE_TOKEN
from mascope_backend.socket.events.emitters.file_converter import FILE_CONVERTER_ROOM
from mascope_backend.socket.events.handlers.file_converter import connection


_MODULE = "mascope_backend.socket.events.handlers.file_converter.connection"
_CONVERTER_ENVIRON = {"HTTP_X_SERVICE_NAME": "file-converter"}


@contextmanager
def _connect_mocks(register_ok: bool = True):
    """Mock the connect handler's side effects: room join/leave, presence, renewal.

    ``start_presence_renewal`` spawns a real background task, so it is stubbed
    out here; the accept test asserts it was called once.
    """
    with (
        patch(
            f"{_MODULE}.register_service", new=AsyncMock(return_value=register_ok)
        ) as register,
        patch(f"{_MODULE}.sio.enter_room", new_callable=AsyncMock) as enter_room,
        patch(f"{_MODULE}.sio.leave_room", new_callable=AsyncMock) as leave_room,
        patch(f"{_MODULE}.start_presence_renewal") as start_renewal,
    ):
        yield register, enter_room, leave_room, start_renewal


@pytest.mark.asyncio
async def test_valid_service_token_is_accepted():
    with _connect_mocks() as (register, enter_room, leave_room, start_renewal):
        accepted = await connection.connect(
            "sid-1",
            _CONVERTER_ENVIRON,
            {"service_token": FILE_CONVERTER_SERVICE_TOKEN},
        )

    assert accepted is True
    register.assert_awaited_once_with("file-converter", "sid-1")
    enter_room.assert_awaited_once_with(
        "sid-1", FILE_CONVERTER_ROOM, namespace="/file-converter"
    )
    leave_room.assert_not_awaited()
    # Presence is kept alive for the life of the connection.
    start_renewal.assert_called_once_with("file-converter", "sid-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth",
    [
        pytest.param({}, id="missing-token"),
        pytest.param(None, id="absent-auth"),
        pytest.param({"service_token": "not-the-secret"}, id="wrong-token"),
        pytest.param("not-a-dict", id="non-dict-auth"),
        pytest.param([FILE_CONVERTER_SERVICE_TOKEN], id="list-auth"),
        pytest.param({"service_token": 12345}, id="non-string-token"),
        pytest.param({"service_token": ""}, id="empty-token"),
        pytest.param({"service_token": "töken"}, id="non-ascii-token"),
    ],
)
async def test_bad_auth_is_rejected(auth):
    """Every malformed or wrong auth shape is refused without side effects."""
    with _connect_mocks() as (register, enter_room, leave_room, start_renewal):
        accepted = await connection.connect("sid-1", _CONVERTER_ENVIRON, auth)

    assert accepted is False
    register.assert_not_awaited()
    enter_room.assert_not_awaited()
    leave_room.assert_not_awaited()
    start_renewal.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_service_name_is_rejected_even_with_a_valid_token():
    """The service-name gate stays: a valid token on the wrong name is refused."""
    with _connect_mocks() as (register, enter_room, leave_room, start_renewal):
        accepted = await connection.connect(
            "sid-1",
            {"HTTP_X_SERVICE_NAME": "tof-agent"},
            {"service_token": FILE_CONVERTER_SERVICE_TOKEN},
        )

    assert accepted is False
    register.assert_not_awaited()
    enter_room.assert_not_awaited()
    leave_room.assert_not_awaited()
    start_renewal.assert_not_called()


@pytest.mark.asyncio
async def test_failed_registration_refuses_the_connection():
    """
    A connection whose presence write failed is refused, not accepted: an
    accepted-but-unregistered converter would read as absent to the
    availability gates for the socket's whole lifetime, while a refused client
    reconnects and registers afresh.

    The room is joined before presence is registered, so a refusal here must
    also leave it - a rejected connect never runs the disconnect handler.
    """
    with _connect_mocks(register_ok=False) as (
        register,
        enter_room,
        leave_room,
        start_renewal,
    ):
        accepted = await connection.connect(
            "sid-1",
            _CONVERTER_ENVIRON,
            {"service_token": FILE_CONVERTER_SERVICE_TOKEN},
        )

    assert accepted is False
    register.assert_awaited_once_with("file-converter", "sid-1")
    enter_room.assert_awaited_once_with(
        "sid-1", FILE_CONVERTER_ROOM, namespace="/file-converter"
    )
    leave_room.assert_awaited_once_with(
        "sid-1", FILE_CONVERTER_ROOM, namespace="/file-converter"
    )
    start_renewal.assert_not_called()


def test_converter_client_presents_the_service_token():
    """The genuine converter sends the shared secret in its connect auth."""
    from mascope_backend.file_converter.socket.client import FileConverterSocketClient

    client = FileConverterSocketClient("http://localhost:8090")
    # Swap the real Socket.IO client for a stub: connect() early-returns when
    # already connected, so it must read False before it dials.
    client.sio = MagicMock()
    client.sio.connected = False

    client.connect()

    client.sio.connect.assert_called_once()
    assert client.sio.connect.call_args.kwargs["auth"] == {
        "service_token": FILE_CONVERTER_SERVICE_TOKEN
    }
