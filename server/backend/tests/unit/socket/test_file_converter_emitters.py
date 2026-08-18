"""
Token-bearing file-converter payloads reach the converter's own connection only,
never a namespace broadcast.

``file_context`` and ``peak_detection_request`` both carry a user's access token.
These lock in that the emitter targets the registered converter sid (``to=``) and
drops the payload - rather than broadcasting it - when no converter is connected.
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
async def test_payload_is_sent_only_to_the_registered_converter(
    handler, event, payload
):
    with (
        patch(
            f"{_MODULE}.get_service_sid", new=AsyncMock(return_value="converter-sid")
        ),
        patch(f"{_MODULE}.sio.emit", new_callable=AsyncMock) as emit,
    ):
        await handler(payload)

    emit.assert_awaited_once_with(
        event, payload, namespace="/file-converter", to="converter-sid"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler, payload",
    [
        (emitters.send_file_context_to_converter, _FILE_CONTEXT),
        (emitters.send_peak_detection_request_to_converter, _PEAK_REQUEST),
    ],
)
async def test_payload_is_dropped_when_no_converter_is_connected(handler, payload):
    with (
        patch(f"{_MODULE}.get_service_sid", new=AsyncMock(return_value=None)),
        patch(f"{_MODULE}.sio.emit", new_callable=AsyncMock) as emit,
    ):
        await handler(payload)

    emit.assert_not_awaited()
