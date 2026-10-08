"""Unit test: the server takes a request whose headers hold a full record.

An upload's acquisition record travels in the headers of the request that
creates the upload. The HTTP parser's default gives up on a request whose
headers have not all arrived within 16 KB, which is how a network delivers
ones this long - and only a network: sent in one piece over loopback, or
through an in-process test client, the same request passes with the limit
not raised. So no test of the upload can see the setting, and a server
without it would lose a record now and then and no upload.

What can be held is that the server is started with a limit, and that it is
large enough for the largest record there can be.
"""

import math
from unittest.mock import MagicMock

from mascope_backend.app import uvicorn as launcher
from mascope_sdk import acquisition


def test_the_server_is_started_with_room_for_a_whole_record(monkeypatch):
    started = MagicMock()
    monkeypatch.setattr(launcher.uvicorn, "run", started)
    monkeypatch.setattr(launcher, "init_main_process", MagicMock())
    monkeypatch.setattr(launcher.asyncio, "run", lambda coroutine: None)

    launcher.run()

    # A record of the size limit, base64 in Upload-Metadata, beside the
    # upload's other metadata and the rest of a request's headers.
    record = math.ceil(acquisition.MAX_BYTES / 3) * 4
    assert started.call_args.kwargs["h11_max_incomplete_event_size"] >= record + 8192
