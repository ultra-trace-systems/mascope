"""
How the users of the shared Redis client above the socket storage log an
outage.

The socket storage logs a Redis connection failure as a warning kept out of
monitoring (``socket/test_storage_redis_failures.py``). Its callers, and the
security controls that share its client, used to log the same failure again
with ``logger.exception``, so a Redis restart still filed error events. What
must hold:

- socket sign-in, the socket event decorator and sign-out log Redis out of
  reach as a warning with no stack, kept out of monitoring, attributed to
  their own function, also when it reaches them raised from another exception
  (the storage's ``SocketSessionError``);
- there, any other exception is still an ERROR with its stack that reaches
  monitoring: a wrong Redis password, a bug, and a built-in
  ``ConnectionError``, which the database driver raises too;
- the login rate limits and MFA log Redis out of reach as an ERROR that
  reaches monitoring, with no stack, and a message that says Redis was out of
  reach; any other exception is an ERROR with its stack;
- what each caller does on the failure (fail open, fail closed, refuse the
  socket, drop the event) is as before.

The callers are driven against a Redis stub whose every command raises, or
with the call they make replaced by one that raises; the records go through
the real monitoring sink with the SDK's two capture calls recorded.
"""

from types import SimpleNamespace

import pytest
import sentry_sdk
from redis.exceptions import AuthenticationError as RedisAuthenticationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from test_utils import captured_logs

from mascope_backend import redis_failures
from mascope_backend.api.lib import rate_limit as rl
from mascope_backend.api.new.auth.mfa import pending, reauth
from mascope_backend.api.new.auth.mfa.exceptions import MfaReauthRequiredException
from mascope_backend.runtime import runtime
from mascope_backend.socket import eviction
from mascope_backend.socket.auth import decorators, service
from mascope_backend.socket.auth.exceptions import SocketUnauthenticatedError
from mascope_backend.socket.storage import rooms, sessions
from mascope_backend.socket.storage.exceptions import SocketSessionError
from mascope_backend.socket.storage.rooms import RoomTracker
from mascope_runtime import logging as runtime_logging


_OUT_OF_REACH = [
    pytest.param(RedisConnectionError("Connection refused."), id="conn"),
    pytest.param(RedisTimeoutError("Timeout reading from socket"), id="timeout"),
]

_FAULTS = [
    pytest.param(RuntimeError("not a connection problem"), id="runtime"),
    pytest.param(RedisAuthenticationError("invalid password"), id="auth"),
]


class _FailingRedis:
    """A Redis client whose every command raises a copy of the given error."""

    def __init__(self, error: Exception):
        self._error = error

    def __getattr__(self, name):
        async def _command(*args, **kwargs):
            # A fresh one each time: sign-in calls the stub again while
            # handling the first failure, and one instance raised inside its
            # own handler grows a traceback the log formatter stalls on.
            raise type(self._error)(*self._error.args)

        return _command


@pytest.fixture
def monitoring(monkeypatch):
    """
    The real monitoring sink, with what it would send recorded.

    :return: ``("exc" | "msg",)`` per event the sink would have sent.
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


def _own(records: list[dict], function: str) -> list[dict]:
    """The WARNING+ records the given function logged itself."""
    threshold = runtime.logger.level("WARNING").no
    return [
        record
        for record in records
        if record["level"].no >= threshold and record["function"] == function
    ]


async def _noop(*args, **kwargs):
    return None


def _raising(error: Exception):
    async def _call(*args, **kwargs):
        raise error

    return _call


# --- redis_out_of_reach -------------------------------------------------------


def _raised_from(error: Exception) -> SocketSessionError:
    try:
        try:
            raise error
        except Exception as cause:
            raise SocketSessionError("Failed to save session") from cause
    except SocketSessionError as wrapped:
        return wrapped


@pytest.mark.parametrize(
    "error, expected",
    [
        pytest.param(RedisConnectionError("refused"), True, id="conn"),
        pytest.param(RedisTimeoutError("timeout"), True, id="timeout"),
        pytest.param(_raised_from(RedisConnectionError("refused")), True, id="cause"),
        pytest.param(RedisAuthenticationError("password"), False, id="auth"),
        pytest.param(
            _raised_from(RedisAuthenticationError("password")), False, id="auth-cause"
        ),
        pytest.param(ConnectionRefusedError("database"), False, id="builtin-conn"),
        pytest.param(TimeoutError(), False, id="builtin-timeout"),
        pytest.param(SocketSessionError("No session found"), False, id="no-cause"),
        pytest.param(RuntimeError("bug"), False, id="runtime"),
    ],
)
def test_redis_out_of_reach_follows_the_cause_and_takes_only_redis_errors(
    error, expected
):
    assert redis_failures.redis_out_of_reach(error) is expected


def test_redis_out_of_reach_ends_on_a_cause_cycle():
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__cause__, second.__cause__ = second, first

    assert redis_failures.redis_out_of_reach(first) is False


# --- the socket callers: a warning kept out of monitoring ----------------------


async def _authenticate(monkeypatch, error: Exception) -> None:
    """Sign a web socket in while saving its session meets ``error``."""
    monkeypatch.setattr(
        sessions, "redis_storage_client", SimpleNamespace(client=_FailingRedis(error))
    )
    user = SimpleNamespace(id=1, username="someone", role_id=400)

    async def _validate(token):
        return user, 1000, 2000

    monkeypatch.setattr(service, "validate_jwt_token", _validate)
    monkeypatch.setattr(service, "hold_from_broadcasts", _noop)
    with pytest.raises(SocketUnauthenticatedError):
        await service.authenticate_socket_connection("sid-1", "token", "guest")


async def _hold_quietly(monkeypatch, error: Exception) -> None:
    monkeypatch.setattr(service, "hold_from_broadcasts", _raising(error))
    await service._hold_quietly("sid-1")


async def _subscribe(monkeypatch, error: Exception) -> None:
    """Run a decorated event whose room cannot be recorded."""
    monkeypatch.setattr(
        rooms, "redis_storage_client", SimpleNamespace(client=_FailingRedis(error))
    )

    async def _session(sid, namespace="/"):
        return {"user_id": 1, "role_id": 400}

    monkeypatch.setattr(decorators, "get_session_user", _session)

    @decorators.socket_auth("guest")
    async def subscribe(sid):
        await RoomTracker.join(1, "room-1")
        return "joined"

    # The event is dropped, as before.
    assert await subscribe("sid-1") is None


async def _evict(monkeypatch, error: Exception) -> None:
    monkeypatch.setattr(eviction, "get_user_socket_sids", _raising(error))
    assert await eviction.evict_ended_sessions(1) == 0


async def _sign_out_socket(monkeypatch, error: Exception) -> None:
    async def _session(sid, namespace="/"):
        return {"user_id": 1}

    monkeypatch.setattr(eviction, "get_session_user", _session)
    monkeypatch.setattr(eviction, "_sign_out", _raising(error))
    assert await eviction.sign_out_socket("sid-1", 1) is False


async def _sign_out_session_list(monkeypatch, error: Exception) -> None:
    monkeypatch.setattr(eviction, "get_user_socket_sids", _raising(error))
    assert await eviction.sign_out_session(1, 1000) == 0


async def _sign_out_session_each(monkeypatch, error: Exception) -> None:
    async def _sids(user_id):
        return {"sid-1"}

    monkeypatch.setattr(eviction, "get_user_socket_sids", _sids)
    monkeypatch.setattr(eviction, "peek_session_user", _raising(error))
    assert await eviction.sign_out_session(1, 1000) == 0


async def _sign_out_expired(monkeypatch, error: Exception) -> None:
    monkeypatch.setattr(eviction, "peek_session_user", _raising(error))
    assert await eviction.sign_out_expired_socket("sid-1", 1) is False


#: (the function whose failure is logged, a call that reaches it with an error)
_SOCKET_SITES = [
    pytest.param("authenticate_socket_connection", _authenticate, id="sign-in"),
    pytest.param("_hold_quietly", _hold_quietly, id="hold"),
    pytest.param("wrapper", _subscribe, id="socket_auth"),
    pytest.param("evict_ended_sessions", _evict, id="evict"),
    pytest.param("sign_out_socket", _sign_out_socket, id="sign-out-socket"),
    pytest.param("sign_out_session", _sign_out_session_list, id="sign-out-list"),
    pytest.param("sign_out_session", _sign_out_session_each, id="sign-out-each"),
    pytest.param("sign_out_expired_socket", _sign_out_expired, id="sign-out-expiry"),
]


@pytest.mark.parametrize("error", _OUT_OF_REACH)
@pytest.mark.parametrize("function, call", _SOCKET_SITES)
@pytest.mark.asyncio
async def test_a_socket_caller_logs_redis_out_of_reach_as_a_quiet_warning(
    monkeypatch, monitoring, error, function, call
):
    with captured_logs(level="DEBUG") as records:
        await call(monkeypatch, error)

    own = _own(records, function)
    assert own, "the failure must still be logged by its own function"
    for record in own:
        assert record["level"].name == "WARNING", record["message"]
        assert record["exception"] is None, record["message"]
        assert record["extra"].get(runtime_logging.SENTRY_SKIP) is True
        assert record["message"].endswith(redis_failures.OUT_OF_REACH)
    assert monitoring == []


@pytest.mark.parametrize("error", _FAULTS)
@pytest.mark.parametrize("function, call", _SOCKET_SITES)
@pytest.mark.asyncio
async def test_a_socket_caller_keeps_the_stack_of_any_other_failure(
    monkeypatch, monitoring, error, function, call
):
    with captured_logs(level="DEBUG") as records:
        await call(monkeypatch, error)

    own = _own(records, function)
    assert own, "the failure must still be logged by its own function"
    for record in own:
        assert record["level"].name == "ERROR", record["message"]
        assert record["exception"] is not None, record["message"]
        assert runtime_logging.SENTRY_SKIP not in record["extra"]
    assert ("exc",) in monitoring


@pytest.mark.asyncio
async def test_socket_sign_in_reports_a_database_outage(monkeypatch, monitoring):
    """
    A built-in ``ConnectionError`` reaching sign-in is not Redis's: reading
    the account from the database raises one too.
    """
    monkeypatch.setattr(
        service, "validate_jwt_token", _raising(ConnectionRefusedError("database"))
    )
    monkeypatch.setattr(service, "clear_user_session", _noop)
    monkeypatch.setattr(service, "hold_from_broadcasts", _noop)

    with captured_logs(level="DEBUG") as records:
        with pytest.raises(SocketUnauthenticatedError):
            await service.authenticate_socket_connection("sid-1", "token", "guest")

    (record,) = _own(records, "authenticate_socket_connection")
    assert record["level"].name == "ERROR"
    assert record["exception"] is not None
    assert monitoring == [("exc",)]


# --- the security controls: still an error ------------------------------------


_REQUEST = SimpleNamespace(headers={}, client=SimpleNamespace(host="192.0.2.1"))


async def _ip_limit():
    # Fails open: the request goes through.
    dependency = rl.rate_limit(times=1, seconds=60, scope="test")
    assert await dependency(_REQUEST) is None


async def _account_limit():
    await rl.enforce_login_rate_limit("someone@test.com", times=1, seconds=60)


async def _user_limit():
    await rl.enforce_user_rate_limit(1, times=1, seconds=60, scope="test")


async def _clear_user_limit():
    await rl.clear_user_rate_limit(1, scope="test")


async def _refund():
    await rl.refund_ip_rate_limit(_REQUEST, scope="test")


async def _clear_login_limit():
    await rl.clear_login_rate_limit("someone@test.com")


async def _burn_check():
    # Fails open: the token is not refused.
    assert await pending._is_burned("jti") is False


async def _burn():
    await pending.burn_pending_token("jti")


async def _attempt():
    await pending.register_failed_attempt("jti")


async def _mark_verified():
    await reauth.mark_recently_verified(1)


async def _step_up():
    # Fails closed: the action is refused.
    with pytest.raises(MfaReauthRequiredException):
        await reauth.require_recent_mfa(SimpleNamespace(id=1, mfa_enabled=True))


async def _clear_verified():
    await reauth.clear_recent_verification(1)


#: (the function whose failure is logged, a call that reaches it, the words
#: its message must carry)
_CONTROL_SITES = [
    pytest.param("dependency", _ip_limit, "failed open", id="ip-limit"),
    pytest.param(
        "enforce_login_rate_limit", _account_limit, "failed open", id="account-limit"
    ),
    pytest.param(
        "enforce_user_rate_limit", _user_limit, "failed open", id="user-limit"
    ),
    pytest.param("clear_user_rate_limit", _clear_user_limit, "clear", id="clear-user"),
    pytest.param("refund_ip_rate_limit", _refund, "refund", id="refund"),
    pytest.param("clear_login_rate_limit", _clear_login_limit, "clear", id="clear"),
    pytest.param("_is_burned", _burn_check, "failed open", id="burn-check"),
    pytest.param("burn_pending_token", _burn, "burn", id="burn"),
    pytest.param("register_failed_attempt", _attempt, "attempt", id="attempt"),
    pytest.param("mark_recently_verified", _mark_verified, "record", id="mark"),
    pytest.param("require_recent_mfa", _step_up, "failed closed", id="step-up"),
    pytest.param("clear_recent_verification", _clear_verified, "clear", id="unmark"),
]


@pytest.fixture
def failing_redis(monkeypatch):
    """Point the shared client the controls use at a stub that raises."""
    client = rl.redis_storage_client

    def _install(error: Exception) -> None:
        monkeypatch.setattr(client, "_client", _FailingRedis(error))

    return _install


@pytest.mark.parametrize("error", _OUT_OF_REACH)
@pytest.mark.parametrize("function, call, words", _CONTROL_SITES)
@pytest.mark.asyncio
async def test_a_control_logs_redis_out_of_reach_as_an_error_without_a_stack(
    failing_redis, monitoring, error, function, call, words
):
    failing_redis(error)

    with captured_logs(level="DEBUG") as records:
        await call()

    (record,) = _own(records, function)
    assert record["level"].name == "ERROR"
    assert record["exception"] is None
    assert runtime_logging.SENTRY_SKIP not in record["extra"]
    assert words in record["message"]
    assert record["message"].endswith(redis_failures.OUT_OF_REACH)
    assert monitoring == [("msg",)]


@pytest.mark.parametrize("error", _FAULTS)
@pytest.mark.parametrize("function, call, words", _CONTROL_SITES)
@pytest.mark.asyncio
async def test_a_control_keeps_the_stack_of_any_other_failure(
    failing_redis, monitoring, error, function, call, words
):
    failing_redis(error)

    with captured_logs(level="DEBUG") as records:
        await call()

    (record,) = _own(records, function)
    assert record["level"].name == "ERROR"
    assert record["exception"] is not None
    assert words in record["message"]
    assert not record["message"].endswith(redis_failures.OUT_OF_REACH)
    assert monitoring == [("exc",)]
