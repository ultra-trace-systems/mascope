"""
How the socket storage logs a failed Redis call.

Every function in ``mascope_backend.socket.storage`` catches its Redis
failures and rides them out: it refuses the call, retries on the next tick,
or leaves a key to lapse on its own. A Redis restart, an idle connection the
server closed or a host name that does not resolve yet during a restart used
to be logged with ``logger.exception``, so each blip filed error events in
monitoring from every worker at once.

What must hold:

- a Redis ``ConnectionError`` or ``TimeoutError`` is a warning with no stack,
  kept out of monitoring, and attributed to the storage function that failed;
- any other exception is still an ERROR with its stack, and reaches
  monitoring, redis-py's ``AuthenticationError`` and ``AuthorizationError``
  included: they subclass its ``ConnectionError``, but a wrong password is a
  misconfiguration no wait heals;
- callers that raise ``SocketSessionError`` still raise it.

The functions are driven directly against a Redis stub whose every command
raises; the records go through the real monitoring sink with the SDK's two
capture calls recorded, so "kept out" means the sink sent nothing.
"""

import asyncio
from types import SimpleNamespace

import pytest
import sentry_sdk
from redis.exceptions import AuthenticationError as RedisAuthenticationError
from redis.exceptions import AuthorizationError as RedisAuthorizationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from test_utils import captured_logs

from mascope_backend.runtime import runtime
from mascope_backend.socket.storage import rooms, services, sessions, workers
from mascope_backend.socket.storage.exceptions import SocketSessionError
from mascope_backend.socket.storage.rooms import RoomTracker
from mascope_runtime import logging as runtime_logging


class _FailingRedis:
    """A Redis client whose every command raises the given error."""

    def __init__(self, error: Exception):
        self._error = error

    def __getattr__(self, name):
        async def _command(*args, **kwargs):
            raise self._error

        return _command


@pytest.fixture
def failing_redis(monkeypatch):
    """Point every storage module at a Redis stub that raises ``error``."""

    def _install(error: Exception) -> None:
        stub = SimpleNamespace(client=_FailingRedis(error))
        for module in (rooms, services, sessions, workers):
            monkeypatch.setattr(module, "redis_storage_client", stub)
        # A fresh run of failed beats: the cases here are one beat each, and
        # must not add up to the outage report (test_storage_redis_outage.py).
        monkeypatch.setattr(workers, "_redis_outage", workers.RedisOutage())

    return _install


@pytest.fixture
def monitoring(monkeypatch):
    """
    The real monitoring sink, with what it would send recorded.

    :return: ``("exc" | "msg", ...)`` per event the sink would have sent.
    """
    sent: list[tuple] = []
    monkeypatch.setattr(
        sentry_sdk, "capture_exception", lambda error=None, **_: sent.append(("exc",))
    )
    monkeypatch.setattr(
        sentry_sdk, "capture_message", lambda message, **_: sent.append(("msg",))
    )
    sink_id = runtime.logger.add(
        runtime_logging._sentry_sink, level="WARNING", enqueue=False, catch=False
    )
    try:
        yield sent
    finally:
        runtime.logger.remove(sink_id)


async def _stop_worker_heartbeat(monkeypatch) -> None:
    # Only a worker that started its heartbeat has a key to remove.
    monkeypatch.setattr(workers, "_heartbeat", asyncio.create_task(asyncio.sleep(0)))
    await workers.stop_worker_heartbeat()


_USER = SimpleNamespace(id=1, username="someone", role_id=2)

#: (the function whose failure is logged, a call that reaches it, the
#: exception the call raises or None). A call takes ``monkeypatch``.
_SITES = [
    (
        "get_session_user",
        lambda mp: sessions.get_session_user("sid-1"),
        SocketSessionError,
    ),
    (
        "save_user_session",
        lambda mp: sessions.save_user_session("sid-1", _USER),
        SocketSessionError,
    ),
    ("clear_user_session", lambda mp: sessions.clear_user_session("sid-1"), None),
    (
        "_unindex_session",
        lambda mp: sessions._unindex_session("session-key", "sid-1"),
        None,
    ),
    ("join", lambda mp: RoomTracker.join(1, "room-1"), "original"),
    ("leave", lambda mp: RoomTracker.leave(1, "room-1"), None),
    ("is_in_room", lambda mp: RoomTracker.is_in_room(1, "room-1"), None),
    ("leave_all", lambda mp: RoomTracker.leave_all(1), None),
    ("release", lambda mp: RoomTracker.release(1, "sid-1", "room-1"), None),
    ("release_socket", lambda mp: RoomTracker.release_socket(1, "sid-1"), None),
    ("forget_socket", lambda mp: RoomTracker.forget_socket("sid-1"), None),
    (
        "_write_presence_key",
        lambda mp: services.register_service("file-converter", "sid-1"),
        None,
    ),
    (
        "unregister_service",
        lambda mp: services.unregister_service("file-converter", "sid-1"),
        None,
    ),
    (
        "is_service_connected",
        lambda mp: services.is_service_connected("file-converter"),
        None,
    ),
    ("_beat", lambda mp: workers._beat(), None),
    ("renew_socket_claim", lambda mp: workers.renew_socket_claim("sid-1"), None),
    ("release_socket_claim", lambda mp: workers.release_socket_claim("sid-1"), None),
    ("stop_worker_heartbeat", _stop_worker_heartbeat, None),
]

_SITE_PARAMS = [pytest.param(*site, id=site[0]) for site in _SITES]


async def _call(call, monkeypatch, error: Exception, raises) -> list[dict]:
    """Run one call against the failing stub; return everything it logged."""
    with captured_logs(level="DEBUG") as records:
        if raises is None:
            await call(monkeypatch)
        else:
            expected = type(error) if raises == "original" else raises
            with pytest.raises(expected):
                await call(monkeypatch)
    return records


def _monitored(records: list[dict]) -> list[dict]:
    threshold = runtime.logger.level("WARNING").no
    return [record for record in records if record["level"].no >= threshold]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(RedisConnectionError("Connection closed by server."), id="conn"),
        pytest.param(RedisTimeoutError("Timeout reading from socket"), id="timeout"),
        pytest.param(ConnectionResetError("reset by peer"), id="builtin-conn"),
        pytest.param(TimeoutError(), id="builtin-timeout"),
    ],
)
@pytest.mark.parametrize("function, call, raises", _SITE_PARAMS)
@pytest.mark.asyncio
async def test_a_redis_outage_is_a_warning_kept_out_of_monitoring(
    monkeypatch, failing_redis, monitoring, error, function, call, raises
):
    failing_redis(error)

    records = await _call(call, monkeypatch, error, raises)

    logged = _monitored(records)
    assert logged, "the failure must still be logged"
    for record in logged:
        assert record["level"].name == "WARNING", record["message"]
        assert record["exception"] is None, record["message"]
        assert record["extra"].get(runtime_logging.SENTRY_SKIP) is True
    # Attributed to the storage function, not to the logging helper.
    assert function in {record["function"] for record in logged}
    assert monitoring == []


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(RuntimeError("not a connection problem"), id="runtime"),
        pytest.param(RedisAuthenticationError("invalid password"), id="auth"),
        pytest.param(RedisAuthorizationError("NOPERM"), id="authz"),
    ],
)
@pytest.mark.parametrize("function, call, raises", _SITE_PARAMS)
@pytest.mark.asyncio
async def test_any_other_failure_keeps_its_stack_and_reaches_monitoring(
    monkeypatch, failing_redis, monitoring, error, function, call, raises
):
    failing_redis(error)

    records = await _call(call, monkeypatch, error, raises)

    own = [record for record in _monitored(records) if record["function"] == function]
    assert own, "the failure must still be logged by its own function"
    for record in own:
        assert runtime_logging.SENTRY_SKIP not in record["extra"]
        if function == "_unindex_session":
            # Best effort by design: a warning, no stack, but monitored.
            assert record["level"].name == "WARNING"
        else:
            assert record["level"].name == "ERROR"
            assert record["exception"] is not None
    assert monitoring, "a fault must reach monitoring"
