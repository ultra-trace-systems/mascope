"""
Unit tests for ``_finalize_run`` - a run's terminal write - when the database
refuses a connection.

The failure that ends a run is often that refusal itself ("too many clients
already"), and a run whose end is never written stays non-terminal: it blocks
its sample until the startup reaper or retention's grace, and a client waiting
on it reads 'running' all the while. The write is retried for as long as such a
refusal usually lasts, and only for that kind of error.
"""

from datetime import datetime

import pytest
from asyncpg.exceptions import (
    CannotConnectNowError,
    ConnectionDoesNotExistError,
    TooManyConnectionsError,
)
from sqlalchemy.exc import (
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from mascope_backend.api.new.peak_assignments import service
from mascope_backend.db import retry


class _Sessions:
    """An ``async_session`` stand-in that raises ``errors`` in turn, one per
    attempt, and then opens a session that records what it was asked to write.
    """

    def __init__(self, *errors):
        self.errors = list(errors)
        self.attempts = 0
        self.statements = []
        self.commits = 0

    def __call__(self):
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, statement):
        self.statements.append(statement)

    async def commit(self):
        self.commits += 1


def _refused():
    return TooManyConnectionsError("sorry, too many clients already")


@pytest.fixture
def sleeps(monkeypatch):
    """The waits between attempts, recorded and not slept."""
    waited = []

    async def _sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(retry.asyncio, "sleep", _sleep)
    return waited


def _values(statement) -> dict:
    return {
        column.key: getattr(value, "value", value)
        for column, value in statement._values.items()
    }


@pytest.mark.asyncio
async def test_a_refused_write_is_retried_until_the_database_answers(
    monkeypatch, sleeps
):
    sessions = _Sessions(_refused(), _refused())
    monkeypatch.setattr(service, "async_session", sessions)

    await service._finalize_run("run-1", "failed", error="engine refused")

    assert sessions.attempts == 3
    assert sessions.commits == 1
    assert sleeps == list(retry.CONNECTION_RETRY_DELAYS_S[:2])
    values = _values(sessions.statements[0])
    assert (values["status"], values["error"]) == ("failed", "engine refused")


@pytest.mark.asyncio
async def test_the_run_ends_when_it_ended_not_when_the_database_answered(
    monkeypatch, sleeps
):
    """The timestamp is taken before the first attempt: the time a run took is
    read off it, and a minute of refusals is not the run's."""
    sessions = _Sessions(_refused())
    monkeypatch.setattr(service, "async_session", sessions)
    stamps = iter([datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 1, 12, 1)])

    class _Clock:
        @staticmethod
        def now(tz=None):
            return next(stamps)

    monkeypatch.setattr(service, "dt", _Clock)

    await service._finalize_run("run-1", "completed")

    completed = _values(sessions.statements[0])["peak_assignment_run_utc_completed"]
    assert completed == datetime(2026, 1, 1, 12, 0)


@pytest.mark.asyncio
async def test_it_gives_up_after_the_last_wait(monkeypatch, sleeps):
    attempts = len(retry.CONNECTION_RETRY_DELAYS_S) + 1
    sessions = _Sessions(*(_refused() for _ in range(attempts)))
    monkeypatch.setattr(service, "async_session", sessions)

    with pytest.raises(TooManyConnectionsError):
        await service._finalize_run("run-1", "failed", error="engine refused")

    assert sessions.attempts == attempts
    assert sleeps == list(retry.CONNECTION_RETRY_DELAYS_S)


@pytest.mark.asyncio
async def test_an_error_a_retry_cannot_get_past_is_raised_at_once(monkeypatch, sleeps):
    sessions = _Sessions(IntegrityError("update", {}, Exception("constraint")))
    monkeypatch.setattr(service, "async_session", sessions)

    with pytest.raises(IntegrityError):
        await service._finalize_run("run-1", "failed", error="engine refused")

    assert sessions.attempts == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "error, retried",
    [
        (TooManyConnectionsError("sorry, too many clients already"), True),
        (CannotConnectNowError("the database system is starting up"), True),
        (ConnectionDoesNotExistError("connection was closed"), True),
        (ConnectionRefusedError("connection refused"), True),
        (OperationalError("update", {}, Exception("server closed")), True),
        (InterfaceError("update", {}, Exception("connection is closed")), True),
        (PoolTimeoutError("QueuePool limit reached"), True),
        (
            ProgrammingError(
                "update", {}, Exception("gone"), connection_invalidated=True
            ),
            True,
        ),
        (ProgrammingError("update", {}, Exception("syntax")), False),
        (IntegrityError("update", {}, Exception("constraint")), False),
        (ValueError("not the database"), False),
    ],
)
def test_only_a_database_it_could_not_reach_is_retried(error, retried):
    assert retry.is_connection_error(error) is retried
