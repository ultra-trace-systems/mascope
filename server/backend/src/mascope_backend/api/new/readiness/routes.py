"""The backend's readiness route, ``GET /ready``, which its healthcheck asks.

The compose healthcheck of the ``backend`` service used to open a TCP
connection to the backend's port and nothing more, so a backend whose Postgres
and Redis were stopped stayed ``healthy`` (#3470). Everything that reads the
stack's health reads that check: ``mascope prod update`` waits on it after an
update, and ``mascope prod doctor`` reports it to the monitoring host. This
route is what the check asks now: ready only while this worker reaches both of
the stores it cannot work without.

- **Postgres**, through the app's own engine: a ``SELECT 1``. Not through the
  request session's admission throttle (``db_semaphore``), which a long run
  can hold; a busy backend is not a broken one.
- **Redis**, through the app's own client: a ``PING``. The backend starts
  without Redis on purpose, but its workers then share no socket state, so a
  backend without it is not ready either.

Each check gets :data:`CHECK_TIMEOUT_S` and the two run side by side, so the
route answers well inside the healthcheck's own 5 s timeout. A check that
overruns is cancelled and left to finish its clean-up on its own: a hung
database can stall the clean-up of a cancelled query as long as the query
itself, and the answer must not wait for it. While one is still winding down
the next probe does not start another beside it, so a hung store does not
pile up one stuck connection per probe; it answers not ready.

Not a public route. It sits outside ``/api/``, and nginx passes only
``/api/`` and ``/socket.io/`` to the backend, so the healthcheck inside the
container is its one caller. It says ready or not ready and nothing else: no
version, no host, no error text. Why it is not ready goes to the log, once
each time the answer changes rather than on every probe, and the access log
leaves it out (``app/fast.py``): a probe every ten seconds would otherwise
write a line of its own each time.
"""

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from mascope_backend import db
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.runtime import runtime
from mascope_backend.socket.storage import redis_storage_client


#: Where the route answers. Outside ``/api/``, which nginx passes on.
READY_PATH = "/ready"

#: Seconds each store has to answer. Both run at once, so the route answers
#: within about this long, well inside the healthcheck's 5 s timeout.
CHECK_TIMEOUT_S = 2.0

READY = "ready"
NOT_READY = "not ready"

readiness_router = APIRouter(tags=["Readiness"])


async def _database_answers() -> None:
    """Run ``SELECT 1`` through the app's own engine; raise if it cannot."""
    async with db.async_session() as session:
        await session.execute(text("SELECT 1"))


async def _redis_answers() -> None:
    """Send ``PING`` through the app's own Redis client; raise if it cannot."""
    await redis_storage_client.client.ping()


#: The stores the backend needs, by the name the log gives them.
CHECKS: dict[str, Callable[[], Awaitable[None]]] = {
    "database": _database_answers,
    "redis": _redis_answers,
}

#: A check that overran its time and is still winding down, by name. The next
#: probe answers not ready for it rather than start a second one beside it.
_overrunning: dict[str, asyncio.Task] = {}

#: The checks that failed on this worker's last probe, with the reason each
#: gave: what the log last said. Empty while ready, as a worker starts.
_last_failures: dict[str, str] = {}


def _drop_when_done(name: str, task: asyncio.Task) -> None:
    """Forget an overrunning check once it has ended, and read its outcome."""
    if _overrunning.get(name) is task:
        del _overrunning[name]
    if not task.cancelled():
        task.exception()  # read, so asyncio does not log it as never retrieved


async def _check(name: str) -> str | None:
    """
    Run one check within :data:`CHECK_TIMEOUT_S`.

    :param name: The check's key in :data:`CHECKS`.
    :return: None when the store answered, else why it did not.
    """
    if name in _overrunning:
        return "an earlier check has not finished yet"
    task = asyncio.ensure_future(CHECKS[name]())
    done, _ = await asyncio.wait({task}, timeout=CHECK_TIMEOUT_S)
    if not done:
        task.cancel()
        _overrunning[name] = task
        task.add_done_callback(lambda t: _drop_when_done(name, t))
        return f"no answer within {CHECK_TIMEOUT_S:g} s"
    error = task.exception()
    if error is None:
        return None
    return f"{type(error).__name__}: {error}"


def _log_change(failures: dict[str, str]) -> None:
    """Log the answer when it differs from this worker's last one."""
    global _last_failures
    if failures == _last_failures:
        return
    if failures:
        reasons = "; ".join(f"{name}: {why}" for name, why in failures.items())
        runtime.logger.warning(f"Backend not ready - {reasons}")
    else:
        runtime.logger.info("Backend ready again: the database and Redis answer")
    _last_failures = failures


@readiness_router.get(READY_PATH, include_in_schema=False)
@api_route(public=True)
async def get_readiness_route():
    """
    Answer 200 while this worker reaches Postgres and Redis, else 503.

    No sign-in: the container's healthcheck asks it, and it says nothing but
    ``ready`` or ``not ready``. Left out of the OpenAPI document, since no
    client of the API has any use for it.

    :return: ``ready`` with 200, or ``not ready`` with 503, as plain text.
    :rtype: PlainTextResponse
    """
    names = list(CHECKS)
    outcomes = await asyncio.gather(*(_check(name) for name in names))
    failures = {
        name: why for name, why in zip(names, outcomes, strict=True) if why is not None
    }
    _log_change(failures)
    headers = {"Cache-Control": "no-store"}
    if failures:
        return PlainTextResponse(NOT_READY, status_code=503, headers=headers)
    return PlainTextResponse(READY, headers=headers)
