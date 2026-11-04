import inspect
import logging

from mascope_backend.runtime import runtime


logger = runtime.logger.bind(method="EVENT")


class SocketLoggingHandler(logging.Handler):
    # based on https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists.
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        # python-socketio's plain WARNING/ERROR lines are transport noise (bad
        # packets, emits to closed sessions), not operator signal: clamp them
        # to INFO. Records carrying exc_info (e.g. an uncaught event-handler
        # exception) keep their native level.
        if record.exc_info is None and record.levelno > logging.INFO:
            level = "INFO"

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


#: The stdlib logger python-socketio writes to, its Redis manager included.
SOCKET_LOGGER_NAME = "sio_logger"

#: How python-socketio's Redis manager starts the lines it logs at ERROR before
#: trying again: a publish it will resend, and the listener reconnecting
#: ("... retrying in N secs", one line per backoff delay).
REDIS_RETRY_PREFIXES = (
    "Cannot publish to redis... retrying",
    "Cannot receive from redis... retrying",
)


class RedisRetryLevelFilter(logging.Filter):
    """
    Log python-socketio's Redis retry lines at INFO instead of ERROR.

    ``sentry_sdk.init`` turns on its stdlib logging integration by default,
    which files every stdlib ERROR record as an event once the logger's
    handlers have run, so the clamp in :class:`SocketLoggingHandler` never
    applied to it. Each Redis restart then filed one monitoring issue per
    retry delay. A logger filter runs before both, so the record they see is
    already INFO: a breadcrumb, not an event.

    Only the retry lines change. python-socketio's other ERROR lines, such as
    "pubsub listen() exited unexpectedly" or "Cannot publish to redis...
    giving up", mean this worker lost its cross-worker events, and are still
    reported.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.INFO and record.getMessage().startswith(
            REDIS_RETRY_PREFIXES
        ):
            record.levelno = logging.INFO
            record.levelname = logging.getLevelName(logging.INFO)
        return True


def get_socket_logger():
    wrapped_logger = logging.Logger(SOCKET_LOGGER_NAME, level=0)
    wrapped_logger.addFilter(RedisRetryLevelFilter())
    wrapped_logger.addHandler(SocketLoggingHandler())
    return wrapped_logger
