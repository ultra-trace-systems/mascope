"""
A run's terminal write, retried while the database refuses a connection.

The failure that ends a run is often that refusal itself ("too many clients
already"), and the write recording it is then refused too. A run whose end is
never written stays non-terminal: a per-sample run blocks its sample, a batch
run its batch, until the startup reaper or retention's grace (hours). The
terminal writes of both, ``_finalize_run`` of a sample's run and
``_close_run`` and ``complete_run`` of a batch run, go through
:func:`retry_on_connection_error`, so they cannot drift apart.
"""

import asyncio
from typing import Awaitable, Callable, TypeVar

from asyncpg.exceptions import (
    CannotConnectNowError,
    InsufficientResourcesError,
    PostgresConnectionError,
)
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from mascope_backend.runtime import runtime


T = TypeVar("T")

#: The waits, in seconds, between attempts at a terminal write while the
#: database refuses a connection. The refusals seen in practice ("too many
#: clients already") pass within a minute, so the write is tried for about
#: that long.
CONNECTION_RETRY_DELAYS_S = (1, 2, 4, 8, 15, 30)


def is_connection_error(exc: BaseException) -> bool:
    """Whether ``exc`` says the database could not be reached, not that the
    write was wrong: the one kind of failure a later attempt can get past.

    A refusal at connect surfaces as asyncpg's own exception, not wrapped by
    SQLAlchemy (``TooManyConnectionsError`` is an
    ``InsufficientResourcesError``); one on a live connection is SQLAlchemy's.
    """
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return isinstance(
        exc,
        (
            InsufficientResourcesError,
            CannotConnectNowError,
            PostgresConnectionError,
            OperationalError,
            InterfaceError,
            PoolTimeoutError,
            OSError,
        ),
    )


async def retry_on_connection_error(write: Callable[[], Awaitable[T]], what: str) -> T:
    """Await ``write()``, again after each of :data:`CONNECTION_RETRY_DELAYS_S`
    while it fails on :func:`is_connection_error`.

    ``write`` opens its own session on each call, so a failed attempt is rolled
    back whole before the next. Any other error is raised at once, and the
    refusal itself once the last wait is spent.

    :param what: What the write does, for the log ("mark run 'x' failed").
    """
    for attempt, delay in enumerate((*CONNECTION_RETRY_DELAYS_S, None), start=1):
        try:
            return await write()
        except Exception as exc:
            if delay is None or not is_connection_error(exc):
                raise
            runtime.logger.warning(
                f"Could not {what} (attempt {attempt}): {exc!r}; retrying in {delay} s"
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable: the last attempt returns or raises")
