"""
Token-bearing file-converter payloads go to the converter room, never to the
namespace at large.

``file_context`` and ``peak_detection_request`` both carry a user's access
token. Only authenticated converter connections are members of
``FILE_CONVERTER_ROOM`` (the connect handler joins them after verifying the
service secret), so a room-targeted emit can never reach a socket that merely
connected to the namespace, and it reaches every converter regardless of which
worker holds its connection.
"""

from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.socket.events.emitters import file_converter as emitters


_MODULE = "mascope_backend.socket.events.emitters.file_converter"

_FILE_CONTEXT = {"filename": "sample.raw", "user_id": 1, "access_token": "secret-token"}
_PEAK_REQUEST = {
    "filename": "sample.raw",
    "sample_file_id": "sf-1",
    "user_id": 1,
    "access_token": "secret-token",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler, event, payload",
    [
        (emitters.send_file_context_to_converter, "file_context", _FILE_CONTEXT),
        (
            emitters.send_peak_detection_request_to_converter,
            "peak_detection_request",
            _PEAK_REQUEST,
        ),
    ],
)
async def test_payload_targets_the_converter_room(handler, event, payload):
    with patch(f"{_MODULE}.sio.emit", new_callable=AsyncMock) as emit:
        await handler(payload)

    emit.assert_awaited_once_with(
        event,
        payload,
        namespace="/file-converter",
        room=emitters.FILE_CONVERTER_ROOM,
    )
