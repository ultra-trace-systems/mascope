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


def _keep_out_of_sentry_logging(name: str) -> None:
    """
    Stop Sentry's stdlib logging integration from reporting a logger itself.

    ``sentry_sdk.init`` turns that integration on by default, and it reports
    every stdlib ERROR record as an event before any handler sees it - so the
    clamp in :class:`SocketLoggingHandler` never applied to it. python-socketio's
    Redis manager logs "Cannot receive from redis... retrying in N secs" at
    ERROR on every reconnect attempt, one monitoring issue per delay. Ignored
    there, the logger's records reach monitoring only through the handler and
    loguru, at the level the handler gives them: an exception keeps its level
    and stack, a plain line is INFO. A no-op without the optional sentry-sdk.

    :param name: The stdlib logger's name.
    """
    try:
        from sentry_sdk.integrations.logging import ignore_logger
    except ImportError:
        return
    ignore_logger(name)


def get_socket_logger():
    wrapped_logger = logging.Logger(SOCKET_LOGGER_NAME, level=0)
    wrapped_logger.addHandler(SocketLoggingHandler())
    _keep_out_of_sentry_logging(SOCKET_LOGGER_NAME)
    return wrapped_logger
