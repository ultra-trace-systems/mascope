"""Unit tests for the batch-run startup reconciliation.

A ``BatchPeakRun`` is opened ``running`` and closed by the operation that opened
it. No path survives the process dying underneath it, and ``start_run`` refuses a
second operation while one is in flight - so a batch whose run was interrupted
answers 409 to every rebuild, search, import and curation from then on, with no
way out from the app. This reset is the way out, and these pin what it may touch.

Hermetic: the session is a stand-in and the statement is inspected rather than
executed, so this runs on every CI job rather than only where a database is up.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from mascope_backend.db.admin.peak_assignments.reset_running_runs import (
    STUCK_BATCH_RUN_ERROR,
    reset_running_batch_peak_runs,
)


_MOD = "mascope_backend.db.admin.peak_assignments.reset_running_runs"


def _session_factory(recorded: list, rowcount: int = 0, error: Exception | None = None):
    """A stand-in for `async_session`, recording the statements it is given."""

    async def execute(statement):
        if error is not None:
            raise error
        recorded.append(statement)
        return SimpleNamespace(rowcount=rowcount)

    async def commit():
        return None

    session = SimpleNamespace(execute=execute, commit=commit)

    @asynccontextmanager
    async def factory():
        yield session

    return factory


@pytest.mark.asyncio
async def test_a_running_batch_run_is_failed_and_counted():
    recorded: list = []

    with patch(f"{_MOD}.async_session", _session_factory(recorded, rowcount=2)):
        result = await reset_running_batch_peak_runs()

    assert result["status"] == "success"
    assert result["data"]["reset_count"] == 2
    assert "2 interrupted batch peak run(s)" in result["message"]

    compiled = recorded[0].compile(dialect=postgresql.dialect())
    values = list(compiled.params.values())
    assert "UPDATE batch_peak_run SET" in str(compiled)
    # Exactly the one non-terminal state, closed the way `fail_run` closes a run.
    assert "running" in values
    assert "failed" in values
    assert STUCK_BATCH_RUN_ERROR in values


@pytest.mark.asyncio
async def test_the_reset_leaves_the_current_run_alone():
    """`is_current` is never written: an interrupted run never held it, so the
    batch keeps the ledger and the snapshot its last good run left. Writing it
    here would blank the run selector for a batch whose ledger is intact."""
    recorded: list = []

    with patch(f"{_MOD}.async_session", _session_factory(recorded, rowcount=1)):
        await reset_running_batch_peak_runs()

    assert "is_current" not in str(recorded[0].compile(dialect=postgresql.dialect()))


@pytest.mark.asyncio
async def test_a_failure_is_swallowed_so_startup_continues():
    """Housekeeping on a table a recent migration added: a database not yet
    upgraded to that head is not a reason to refuse to boot."""
    with patch(
        f"{_MOD}.async_session",
        _session_factory(
            [], error=RuntimeError('relation "batch_peak_run" does not exist')
        ),
    ):
        result = await reset_running_batch_peak_runs()

    assert result["status"] == "skipped"
    assert result["data"]["reset_count"] == 0


@pytest.mark.asyncio
async def test_nothing_to_reset_is_reported_as_such():
    with patch(f"{_MOD}.async_session", _session_factory([], rowcount=0)):
        result = await reset_running_batch_peak_runs()

    assert result["status"] == "success"
    assert result["message"] == "No interrupted batch peak runs found"
