"""
Telling Redis out of reach from a fault, for every user of the shared client.

The backend's one Redis connection (``socket/storage/client.py``) is used by the
web socket storage, the socket handlers and sign-out around it, the login rate
limits and MFA. A Redis restart fails all of them at once, for a second or for
as long as it lasts. Each says so in its log, and what it should file in error
monitoring depends on what failed:

- **The socket plumbing** rides an outage out: a refused call, a retry on the
  next tick, a key that lapses. A transient failure there is a warning with no
  stack, kept out of monitoring (:func:`log_storage_failure`,
  :func:`log_socket_failure`). A Redis that stays down is reported once by the
  worker heartbeat (``socket/storage/workers.py``).
- **A security control** (a rate limit, an MFA check) that cannot read Redis
  fails open or closed, and that is worth an error even in a short outage: a
  limit that is off is a signal. A transient failure there is still an error,
  but without a stack, and its message says Redis was out of reach
  (:func:`log_control_failure`).

Any other exception is a fault, and is logged with its stack wherever it
happens.
"""

from redis.exceptions import AuthenticationError as RedisAuthenticationError
from redis.exceptions import AuthorizationError as RedisAuthorizationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from mascope_backend.runtime import runtime
from mascope_runtime.logging import SENTRY_SKIP


#: Failures of the connection to Redis rather than of the code using it: a
#: Redis restart, an idle connection the server closed, a host name that does
#: not resolve yet while the stack restarts, a stall past the socket timeout.
#: redis-py raises its own classes for these; the built-in ones are listed for
#: a socket error it lets through unwrapped.
TRANSIENT_REDIS_ERRORS = (
    RedisConnectionError,
    RedisTimeoutError,
    ConnectionError,
    TimeoutError,
)

#: redis-py's ``ConnectionError`` subclasses that no wait heals: a wrong or
#: rotated password, an account the server refuses a command. They are a
#: misconfiguration to report, not Redis out of reach.
REDIS_CONFIGURATION_ERRORS = (RedisAuthenticationError, RedisAuthorizationError)

#: The part of :data:`TRANSIENT_REDIS_ERRORS` only Redis raises.
_REDIS_PY_TRANSIENT_ERRORS = (RedisConnectionError, RedisTimeoutError)

#: The message's ending for a transient failure, the same in every log line.
OUT_OF_REACH = "(Redis out of reach)"


def is_transient_redis_error(error: BaseException) -> bool:
    """
    Whether a failed Redis call met Redis briefly out of reach, not a fault.

    For an exception a Redis command itself raised. One that reached the caller
    through other code goes to :func:`redis_out_of_reach`.

    :param error: The exception a Redis call raised.
    :return: True for :data:`TRANSIENT_REDIS_ERRORS`, except the
        :data:`REDIS_CONFIGURATION_ERRORS` that subclass them.
    """
    return isinstance(error, TRANSIENT_REDIS_ERRORS) and not isinstance(
        error, REDIS_CONFIGURATION_ERRORS
    )


def redis_out_of_reach(error: BaseException) -> bool:
    """
    Whether an exception, or one it was raised from, is Redis out of reach.

    For an exception that reached the caller through other code: the socket
    storage's ``SocketSessionError`` raised from the Redis error, or a socket
    handler's exception, which may come from the database as well. So only
    redis-py's own classes count here, not the built-in ``ConnectionError`` and
    ``TimeoutError`` :func:`is_transient_redis_error` takes: the database
    driver raises those too, and a database outage is not this one.

    :param error: The exception being handled.
    :return: True when ``error`` or an exception in its ``__cause__`` chain is
        a redis-py ``ConnectionError`` or ``TimeoutError`` other than the
        :data:`REDIS_CONFIGURATION_ERRORS`.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, REDIS_CONFIGURATION_ERRORS):
            return False
        if isinstance(current, _REDIS_PY_TRANSIENT_ERRORS):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


def _log_quietly_or_with_stack(message: str, transient: bool) -> None:
    """Log for the caller's caller: a quiet warning, or an error with a stack."""
    if transient:
        runtime.logger.bind(**{SENTRY_SKIP: True}).opt(depth=2).warning(
            f"{message} {OUT_OF_REACH}"
        )
    else:
        runtime.logger.opt(depth=2, exception=True).error(message)


def log_storage_failure(message: str, error: BaseException) -> None:
    """
    Log a failed socket storage call, from inside its ``except`` block.

    A transient failure (:func:`is_transient_redis_error`) is Redis being
    briefly out of reach, which every caller already rides out (a refused call,
    a retry on the next tick, a key that lapses on its own). It is logged as a
    warning with no stack, and kept out of error monitoring. Any other
    exception is a fault and is logged with its stack, as before.

    The record is attributed to the caller, so the log line and the monitoring
    group name the storage function that failed, not this helper.

    :param message: What failed, naming the socket or service.
    :param error: The exception being handled.
    """
    _log_quietly_or_with_stack(message, is_transient_redis_error(error))


def log_socket_failure(message: str, error: BaseException) -> None:
    """
    Log a failure that reached socket code above the storage, from inside its
    ``except`` block.

    For socket sign-in, the socket event handlers and sign-out, which ride a
    Redis outage out as the storage does: the socket is refused, or the event
    dropped, and the client tries again. When the failure is Redis out of reach
    (:func:`redis_out_of_reach`) it is a warning with no stack, kept out of
    error monitoring; any other exception is an error with its stack.

    The record is attributed to the caller, as :func:`log_storage_failure`'s.

    :param message: What failed, naming the socket or account.
    :param error: The exception being handled.
    """
    _log_quietly_or_with_stack(message, redis_out_of_reach(error))


def log_control_failure(message: str, error: BaseException) -> None:
    """
    Log a security control that could not consult Redis, from inside its
    ``except`` block.

    For the login rate limits and MFA: a limit skipped or a check refused while
    Redis is down is a security signal, so it stays an error in monitoring,
    even when the failure is transient (:func:`is_transient_redis_error`). Then
    it has no stack, which would show only the client's connection code, and
    its message ends in "(Redis out of reach)", so it reads as the outage it
    is. Any other exception is an error with its stack.

    The record is attributed to the caller, as :func:`log_storage_failure`'s.

    :param message: What failed and what the control did instead, e.g.
        "failed open".
    :param error: The exception being handled.
    """
    if is_transient_redis_error(error):
        runtime.logger.opt(depth=1).error(f"{message} {OUT_OF_REACH}")
    else:
        runtime.logger.opt(depth=1, exception=True).error(message)
