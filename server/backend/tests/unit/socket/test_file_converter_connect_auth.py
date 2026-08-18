"""
The ``/file-converter`` namespace connect handler authenticates the converter
with the shared service secret, not just the public ``X-Service-Name`` string.

Payloads emitted on this namespace carry a user's access token, so a client that
merely guessed the service name must not be able to subscribe. These drive the
connect coroutine directly (its Redis write is mocked) to pin accept/reject on
the token, and the converter-client half asserts the genuine converter presents
that token when it dials in.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# mascope_backend.socket.auth and mascope_backend.api.new.auth import each other;
# the application boots through the api side, so import it first (as the sibling
# socket tests do) to avoid hitting the cycle mid-initialization.
import mascope_backend.api.new.auth  # noqa: F401
from mascope_backend.api.new.auth.service_token import FILE_CONVERTER_SERVICE_TOKEN
from mascope_backend.socket.events.handlers.file_converter import connection


_MODULE = "mascope_backend.socket.events.handlers.file_converter.connection"
_CONVERTER_ENVIRON = {"HTTP_X_SERVICE_NAME": "file-converter"}


@pytest.mark.asyncio
async def test_valid_service_token_is_accepted():
    with patch(f"{_MODULE}.register_service", new_callable=AsyncMock) as register:
        accepted = await connection.connect(
            "sid-1",
            _CONVERTER_ENVIRON,
            {"service_token": FILE_CONVERTER_SERVICE_TOKEN},
        )

    assert accepted is True
    register.assert_awaited_once_with("file-converter", "sid-1")


@pytest.mark.asyncio
async def test_missing_token_is_rejected():
    with patch(f"{_MODULE}.register_service", new_callable=AsyncMock) as register:
        accepted = await connection.connect("sid-1", _CONVERTER_ENVIRON, {})

    assert accepted is False
    register.assert_not_awaited()


@pytest.mark.asyncio
async def test_absent_auth_is_rejected():
    """A client that opens the namespace with no auth payload at all is refused."""
    with patch(f"{_MODULE}.register_service", new_callable=AsyncMock) as register:
        accepted = await connection.connect("sid-1", _CONVERTER_ENVIRON, None)

    assert accepted is False
    register.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_token_is_rejected():
    with patch(f"{_MODULE}.register_service", new_callable=AsyncMock) as register:
        accepted = await connection.connect(
            "sid-1", _CONVERTER_ENVIRON, {"service_token": "not-the-secret"}
        )

    assert accepted is False
    register.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_service_name_is_rejected_even_with_a_valid_token():
    """The service-name gate stays: a valid token on the wrong name is refused."""
    with patch(f"{_MODULE}.register_service", new_callable=AsyncMock) as register:
        accepted = await connection.connect(
            "sid-1",
            {"HTTP_X_SERVICE_NAME": "tof-agent"},
            {"service_token": FILE_CONVERTER_SERVICE_TOKEN},
        )

    assert accepted is False
    register.assert_not_awaited()


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
