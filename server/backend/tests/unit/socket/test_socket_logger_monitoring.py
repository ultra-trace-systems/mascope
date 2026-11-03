"""
python-socketio's Redis retry lines file no monitoring event.

``sentry_sdk.init`` turns on its stdlib logging integration by default, which
files every stdlib ERROR record as an event whatever the logger's handlers do
with it. The Redis manager python-socketio runs logs "Cannot receive from
redis... retrying in N secs" at ERROR on each reconnect attempt, so every
Redis restart filed one monitoring issue per retry delay, even though
``SocketLoggingHandler`` clamps such a line to INFO. Its other ERROR lines
mean cross-worker events were lost, and must still be filed.

These run a real SDK client with a transport that records what it would send,
so they check the integration itself, not a stand-in for it.
"""

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport
from test_utils import captured_logs

from mascope_backend.socket.logging import get_socket_logger


class _RecordingTransport(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.events: list[dict] = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            if (event := item.get_event()) is not None:
                self.events.append(event)


@pytest.fixture
def sent_events():
    """A live SDK client with its default integrations; the events it sends."""
    previous = sentry_sdk.get_client()
    transport = _RecordingTransport()
    sentry_sdk.init(dsn="http://key@localhost:9/1", transport=transport)
    try:
        yield transport.events
    finally:
        sentry_sdk.flush()
        sentry_sdk.get_global_scope().set_client(previous)


def _messages(events: list[dict]) -> list[str]:
    return [(event.get("logentry") or {}).get("message", "") for event in events]


@pytest.mark.parametrize(
    "message",
    [
        "Cannot receive from redis... retrying in 1 secs",
        "Cannot receive from redis... retrying in 8 secs",
        "Cannot publish to redis... retrying",
    ],
)
def test_a_redis_retry_line_files_no_event(sent_events, message):
    logger = get_socket_logger()

    with captured_logs(level="DEBUG") as records:
        logger.error(message, extra={"redis_exception": "Connection closed by server."})
    sentry_sdk.flush()

    assert message not in _messages(sent_events)
    # Still in the logs, at INFO.
    assert [r["level"].name for r in records if r["message"] == message] == ["INFO"]


@pytest.mark.parametrize(
    "message",
    [
        "pubsub listen() exited unexpectedly",
        "Cannot publish to redis... giving up",
    ],
)
def test_a_socketio_line_that_lost_events_is_still_reported(sent_events, message):
    """Only the retry lines are downgraded, not the socket logger at large."""
    logger = get_socket_logger()

    logger.error(message, extra={"redis_exception": "Connection closed by server."})
    sentry_sdk.flush()

    assert message in _messages(sent_events)
