"""
Unit tests for a batch run's terminal write - ``_close_run``, behind
``fail_run`` and ``cancel_run`` - when the database refuses a connection.

The failure that ends a pass is often that refusal itself ("too many clients
already"), and a batch run whose end is never written stays running: every
later pass on its batch is refused until the startup reaper. The write is
retried as a sample's run's is (``test_finalize_run.py``), through the same
helper, and a close that still fails never takes the place of the error that
ended the pass. No DB.
"""

from datetime import datetime

import pytest
from asyncpg.exceptions import TooManyConnectionsError
from sqlalchemy.exc import IntegrityError

from mascope_backend.api.new.peak_assignments import batch_import, batch_runs
from mascope_backend.db import retry


class _Run:
    def __init__(self, sample_batch_id="batch-1"):
        self.sample_batch_id = sample_batch_id
        self.status = "running"
        self.error = None
        self.summary = None
        self.batch_peak_run_utc_completed = None


class _Sessions:
    """An ``async_session`` stand-in that raises ``errors`` in turn, one per
    attempt, and then opens a session holding ``run``."""

    def __init__(self, *errors, run=None):
        self.errors = list(errors)
        self.run = run if run is not None else _Run()
        self.attempts = 0
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

    async def get(self, model, key):
        return self.run

    async def commit(self):
        self.commits += 1


def _refused():
    return TooManyConnectionsError("sorry, too many clients already")


def _always_refused():
    return _Sessions(
        *(_refused() for _ in range(len(retry.CONNECTION_RETRY_DELAYS_S) + 1))
    )


@pytest.fixture
def sleeps(monkeypatch):
    """The waits between attempts, recorded and not slept."""
    waited = []

    async def _sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(retry.asyncio, "sleep", _sleep)
    return waited


@pytest.fixture
def logged(monkeypatch):
    """The errors ``close_shielded`` logs."""
    errors = []
    monkeypatch.setattr(batch_runs.runtime.logger, "error", errors.append)
    return errors


@pytest.mark.asyncio
async def test_a_refused_close_is_retried_until_the_database_answers(
    monkeypatch, sleeps
):
    sessions = _Sessions(_refused(), _refused())
    monkeypatch.setattr(batch_runs, "async_session", sessions)

    await batch_runs.fail_run("batch-1", "run-1", "the engine refused", {"n": 1})

    assert sessions.attempts == 3
    assert sessions.commits == 1
    assert sleeps == list(retry.CONNECTION_RETRY_DELAYS_S[:2])
    run = sessions.run
    assert (run.status, run.error, run.summary) == (
        batch_runs.STATUS_FAILED,
        "the engine refused",
        {"n": 1},
    )


@pytest.mark.asyncio
async def test_a_cancel_is_retried_too(monkeypatch, sleeps):
    sessions = _Sessions(_refused())
    monkeypatch.setattr(batch_runs, "async_session", sessions)

    await batch_runs.cancel_run("batch-1", "run-1", reason="interrupted")

    assert sessions.attempts == 2
    assert (sessions.run.status, sessions.run.error) == (
        batch_runs.STATUS_CANCELLED,
        "interrupted",
    )


@pytest.mark.asyncio
async def test_the_run_ends_when_it_ended_not_when_the_database_answered(
    monkeypatch, sleeps
):
    sessions = _Sessions(_refused())
    monkeypatch.setattr(batch_runs, "async_session", sessions)
    stamps = iter([datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 1, 12, 1)])

    class _Clock:
        @staticmethod
        def now(tz=None):
            return next(stamps)

    monkeypatch.setattr(batch_runs, "dt", _Clock)

    await batch_runs.fail_run("batch-1", "run-1", "the engine refused")

    assert sessions.run.batch_peak_run_utc_completed == datetime(2026, 1, 1, 12, 0)


@pytest.mark.asyncio
async def test_it_gives_up_after_the_last_wait(monkeypatch, sleeps):
    sessions = _always_refused()
    monkeypatch.setattr(batch_runs, "async_session", sessions)

    with pytest.raises(TooManyConnectionsError):
        await batch_runs.fail_run("batch-1", "run-1", "the engine refused")

    assert sessions.attempts == len(retry.CONNECTION_RETRY_DELAYS_S) + 1
    assert sleeps == list(retry.CONNECTION_RETRY_DELAYS_S)
    assert sessions.run.status == "running"


@pytest.mark.asyncio
async def test_an_error_a_retry_cannot_get_past_is_raised_at_once(monkeypatch, sleeps):
    sessions = _Sessions(IntegrityError("update", {}, Exception("constraint")))
    monkeypatch.setattr(batch_runs, "async_session", sessions)

    with pytest.raises(IntegrityError):
        await batch_runs.fail_run("batch-1", "run-1", "the engine refused")

    assert sessions.attempts == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_a_step_whose_close_is_refused_raises_its_own_error(
    monkeypatch, sleeps, logged
):
    """``in_batch_run``: the step's error is the one raised, the close's is
    logged, and the run stays running for the startup reaper."""
    sessions = _always_refused()
    monkeypatch.setattr(batch_runs, "async_session", sessions)

    async def _start(*args, **kwargs):
        return "run-1"

    async def _work(run_id):
        raise RuntimeError("the step broke")

    monkeypatch.setattr(batch_runs, "start_run", _start)

    with pytest.raises(RuntimeError, match="the step broke"):
        await batch_runs.in_batch_run("batch-1", batch_runs.ACTION_SEARCH, _work)

    assert sessions.run.status == "running"
    [message] = logged
    assert "batch run 'run-1' as failed" in message
    assert "startup reaper" in message


@pytest.mark.asyncio
async def test_an_import_whose_close_is_refused_raises_its_own_error(
    monkeypatch, sleeps, logged
):
    sessions = _always_refused()
    monkeypatch.setattr(batch_runs, "async_session", sessions)

    async def _import(*args, **kwargs):
        raise ValueError("the import broke")

    monkeypatch.setattr(batch_import, "run_batch_import", _import)

    with pytest.raises(ValueError, match="the import broke"):
        await batch_import.perform_batch_import(
            "batch-1", "run-1", "peaky", 5.0, [], {}
        )

    assert sessions.run.status == "running"
    [message] = logged
    assert "import run 'run-1' as failed" in message
