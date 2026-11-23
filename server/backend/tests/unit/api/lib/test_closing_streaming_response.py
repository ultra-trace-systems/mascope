"""
``ClosingStreamingResponse`` closes the generator it sends from, however the
response ends: a synchronous generator with ``close()``, an asynchronous one
with ``aclose()``, so that what either holds open inside a ``with`` - a raw
file's reader, a database session - is let go with the response.
"""

import pytest

from mascope_backend.api.lib.api_features import ClosingStreamingResponse


_SCOPE = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}}


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _sender(hang_up_after: int | None = None):
    """An ASGI send that keeps the bodies sent, and raises as a client that
    hangs up does once ``hang_up_after`` pieces have gone out."""
    sent = []

    async def send(message):
        if message["type"] == "http.response.body" and message.get("more_body"):
            if hang_up_after is not None and len(sent) >= hang_up_after:
                raise OSError("the client hung up")
            sent.append(message["body"])

    return send, sent


def _async_body(closed: list):
    async def body():
        try:
            for piece in ("a,b\n", "1,2\n", "3,4\n"):
                yield piece
        finally:
            closed.append(True)

    return body()


def _sync_body(closed: list):
    def body():
        try:
            yield from ("a,b\n", "1,2\n", "3,4\n")
        finally:
            closed.append(True)

    return body()


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_async_body, _sync_body])
async def test_a_body_sent_in_full_is_closed(make):
    closed = []
    send, sent = _sender()

    await ClosingStreamingResponse(make(closed))(_SCOPE, _receive, send)

    assert b"".join(sent) == b"a,b\n1,2\n3,4\n"
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_async_body, _sync_body])
async def test_a_body_whose_client_hangs_up_is_closed(make):
    closed = []
    send, sent = _sender(hang_up_after=1)

    with pytest.raises(Exception):
        await ClosingStreamingResponse(make(closed))(_SCOPE, _receive, send)

    # Left suspended after its second piece, and closed all the same.
    assert sent == [b"a,b\n"]
    assert closed == [True]
