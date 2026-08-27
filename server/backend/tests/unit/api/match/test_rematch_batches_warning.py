"""
The batch rematch aggregate names the batches it could not rematch.

``rematch_batches`` runs each ``rematch_batch`` as a dependent task, whose own
warning is suppressed so the aggregate reports it once instead. The aggregate
reported counts only ("2 failed"), threw the reason away in its except clause,
and left the ids in a payload nothing renders - so a user was told how much had
gone wrong and nothing about what.

Most tests drive the undecorated controller through ``__wrapped__`` with the
per-batch rematch, the progress notifications and the name lookup mocked, so
neither a database nor a Socket.IO server is involved. The two that cover the
name lookup itself use the unit test database.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from test_utils import gen_test_id

from mascope_backend.api.controllers.match import match_controller
from mascope_backend.api.controllers.match.match_controller import (
    _fetch_sample_batch_names,
    rematch_batches,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.db import Dataset, SampleBatch, Workspace


_CTRL = "mascope_backend.api.controllers.match.match_controller"


@asynccontextmanager
async def _counting_session():
    """Stand in for the grouped COUNT that weights the progress bar.

    ``rematch_batches`` used to call ``get_samples`` once per batch purely to
    count rows; it now asks for one grouped count instead, so the seam these
    tests stub moved with it. Reporting no rows is deliberate: the counts only
    size each batch's share of the progress bar, and with none the aggregate
    falls back to equal shares. Nothing here asserts on progress.
    """
    session = AsyncMock()
    result = AsyncMock()
    result.all = list
    session.execute.return_value = result
    yield session


def _batch_name(sample_batch_id: str) -> str:
    """The name the UI shows, derived from the id so a test can assert either."""
    return f"Batch {sample_batch_id}"


async def _rematch(
    sample_batch_ids: list[str],
    outcomes: dict[str, str | Exception],
    batch_names: dict[str, str] | None = None,
) -> ApiException:
    """
    Rematch a selection, returning the warning (or error) it raised.

    ``outcomes`` maps a batch id to either the status ``rematch_batch``
    returns for it or the exception it raises. Anything unlisted succeeds.

    :param sample_batch_ids: The selection to rematch.
    :param outcomes: Per-batch status string or exception.
    :param batch_names: What the name lookup resolves; defaults to a name for
        every batch in the selection.
    :return: The ``ApiException`` the aggregate raised.
    """
    if batch_names is None:
        batch_names = {
            sample_batch_id: _batch_name(sample_batch_id)
            for sample_batch_id in sample_batch_ids
        }

    async def _one_batch(sample_batch_id: str, **kwargs) -> dict:
        outcome = outcomes.get(sample_batch_id, "success")
        if isinstance(outcome, Exception):
            raise outcome
        return {"status": outcome, "data": {"computed_samples_count": 1}}

    with (
        patch(f"{_CTRL}.rematch_batch", AsyncMock(side_effect=_one_batch)),
        patch(f"{_CTRL}.async_session", _counting_session),
        patch(f"{_CTRL}.send_progress_user_notification", AsyncMock()),
        patch(
            f"{_CTRL}._fetch_sample_batch_names", AsyncMock(return_value=batch_names)
        ),
        pytest.raises(ApiException) as excinfo,
    ):
        await rematch_batches.__wrapped__(
            sample_batch_ids=sample_batch_ids,
            user_id=1,
            process_id="batches",
        )
    return excinfo.value


def _warning(reason: str) -> ApiException:
    """A failure of the shape a dependent ``rematch_batch`` re-raises."""
    return ApiException(reason, {}, 200)


@pytest.mark.asyncio
async def test_warning_names_every_failed_batch_and_why():
    """A count alone leaves the user with no idea which batches to look at."""
    warning = await _rematch(
        ["b1", "b2", "b3"],
        {
            "b1": _warning("No calibration for 3 samples"),
            "b3": _warning("Target collection is empty"),
        },
    )

    assert warning.status_code == 200
    assert "Failed to rematch 2 batch(es):" in warning.user_message
    assert f"{_batch_name('b1')}: No calibration for 3 samples" in warning.user_message
    assert f"{_batch_name('b3')}: Target collection is empty" in warning.user_message
    # The one that rematched is not named as a failure.
    assert _batch_name("b2") not in warning.user_message


@pytest.mark.asyncio
async def test_an_unexpected_exception_does_not_leak_its_message():
    """
    Only an ApiException's user_message was written for a human; anything
    else can be an internal path or attribute, which stays in the log.
    """
    internal = "'NoneType' object has no attribute 'sample_batch_id'"
    warning = await _rematch(["b1", "b2"], {"b1": AttributeError(internal)})

    assert internal not in warning.user_message
    assert f"{_batch_name('b1')}: Unexpected error." in warning.user_message


@pytest.mark.asyncio
async def test_locked_batches_are_named_with_the_remedy_not_a_reason():
    """
    A lock is a routine outcome with one shared, self-explanatory cause, so
    the batches are named on one line instead of each carrying a reason.
    """
    warning = await _rematch(["b1", "b2", "b3"], {"b1": "locked", "b3": "locked"})

    assert "Failed to rematch" not in warning.user_message
    locked_line = next(
        line
        for line in warning.user_message.splitlines()
        if "rematch was locked" in line
    )
    assert _batch_name("b1") in locked_line
    assert _batch_name("b3") in locked_line
    assert _batch_name("b2") not in locked_line


@pytest.mark.asyncio
async def test_the_locked_remedy_is_singular_for_a_single_batch():
    """
    One locked batch is the common case - someone retrying the batch they
    just started - and "try these again once they finish" is not English for
    it. Only the remedy changes; the "N batch(es)" counts elsewhere keep the
    calibration aggregate's phrasing on purpose.
    """
    one = await _rematch(["b1", "b2"], {"b1": "locked"})
    one_line = next(
        line for line in one.user_message.splitlines() if "rematch was locked" in line
    )
    assert "try this one again once it finishes" in one_line
    assert one_line.endswith(f"{_batch_name('b1')}.")

    two = await _rematch(["b1", "b2"], {"b1": "locked", "b2": "locked"})
    two_line = next(
        line for line in two.user_message.splitlines() if "rematch was locked" in line
    )
    assert "try these again once they finish" in two_line


@pytest.mark.asyncio
async def test_failed_and_locked_are_reported_separately():
    """Both buckets reach the user, each in its own terms."""
    warning = await _rematch(
        ["b1", "b2", "b3"],
        {"b1": _warning("Aggregation failed"), "b2": "locked"},
    )

    assert f"{_batch_name('b1')}: Aggregation failed" in warning.user_message
    locked_line = next(
        line
        for line in warning.user_message.splitlines()
        if "rematch was locked" in line
    )
    assert locked_line.endswith(f"{_batch_name('b2')}.")


@pytest.mark.asyncio
async def test_a_long_failure_list_is_truncated():
    """One notification, not a wall of text, when a whole selection fails."""
    extra = 3
    listed = match_controller.MAX_LISTED_REMATCH_BATCHES
    sample_batch_ids = [f"b{i}" for i in range(listed + extra + 1)]
    # One batch succeeds so this stays the partial-success warning.
    outcomes = {
        sample_batch_id: _warning("Aggregation failed")
        for sample_batch_id in sample_batch_ids[1:]
    }

    warning = await _rematch(sample_batch_ids, outcomes)

    lines = warning.user_message.splitlines()
    assert lines[1] == f"Failed to rematch {listed + extra} batch(es):"
    assert len(lines) == 1 + 1 + listed + 1
    assert lines[-1] == f"...and {extra} more."


@pytest.mark.asyncio
async def test_a_long_locked_list_is_truncated():
    """The locked line is capped the same way."""
    extra = 2
    listed = match_controller.MAX_LISTED_REMATCH_BATCHES
    sample_batch_ids = [f"b{i}" for i in range(listed + extra + 1)]
    outcomes = {sample_batch_id: "locked" for sample_batch_id in sample_batch_ids[1:]}

    warning = await _rematch(sample_batch_ids, outcomes)

    locked_line = next(
        line
        for line in warning.user_message.splitlines()
        if "rematch was locked" in line
    )
    assert _batch_name(sample_batch_ids[listed]) in locked_line
    assert _batch_name(sample_batch_ids[listed + 1]) not in locked_line
    assert locked_line.endswith(f"and {extra} more.")


@pytest.mark.asyncio
async def test_an_unresolvable_name_degrades_to_the_id():
    """A batch deleted mid-run must not cost the summary its other names."""
    warning = await _rematch(
        ["b1", "b2", "b3"],
        {"b1": _warning("Aggregation failed"), "b2": _warning("Gone")},
        batch_names={"b1": _batch_name("b1")},
    )

    assert f"{_batch_name('b1')}: Aggregation failed" in warning.user_message
    assert "b2: Gone" in warning.user_message


@pytest.mark.asyncio
async def test_every_name_is_resolved_in_one_lookup():
    """N batches must not mean N name queries."""
    lookup = AsyncMock(return_value={})

    async def _one_batch(sample_batch_id: str, **kwargs) -> dict:
        if sample_batch_id == "b3":
            return {"status": "locked", "data": {}}
        if sample_batch_id == "b1":
            return {"status": "success", "data": {}}
        raise _warning("Aggregation failed")

    with (
        patch(f"{_CTRL}.rematch_batch", AsyncMock(side_effect=_one_batch)),
        patch(f"{_CTRL}.async_session", _counting_session),
        patch(f"{_CTRL}.send_progress_user_notification", AsyncMock()),
        patch(f"{_CTRL}._fetch_sample_batch_names", lookup),
        pytest.raises(ApiException),
    ):
        await rematch_batches.__wrapped__(
            sample_batch_ids=["b1", "b2", "b3"],
            user_id=1,
            process_id="batches",
        )

    lookup.assert_awaited_once_with(["b2", "b3"])


@pytest.mark.asyncio
async def test_the_error_for_a_wholly_failed_run_names_them_too():
    """Nothing processed is where naming the batches matters most."""
    error = await _rematch(
        ["b1", "b2"],
        {"b1": _warning("Aggregation failed"), "b2": "locked"},
    )

    assert error.status_code == 500
    assert error.user_message.startswith("All 2 batches failed to process")
    assert f"{_batch_name('b1')}: Aggregation failed" in error.user_message
    assert _batch_name("b2") in error.user_message


@pytest.mark.asyncio
async def test_batch_ids_stay_on_the_payload():
    """The message summarises; the id lists keep their place and shape."""
    warning = await _rematch(
        ["b1", "b2", "b3"],
        {"b1": _warning("Aggregation failed"), "b2": "locked"},
    )

    processed = warning.tech_message["data"]["processed_batches"]
    assert processed["failed_batches"] == ["b1"]
    assert processed["locked_batches"] == ["b2"]
    assert processed["success_batches"] == ["b3"]
    assert processed["failed_batches_count"] == 1
    assert processed["locked_batches_count"] == 1


# --- The name lookup itself -------------------------------------------------


@pytest_asyncio.fixture
async def named_sample_batches(async_session_factory):
    """Two committed sample batches, cleaned up afterwards."""
    workspace_id = gen_test_id()
    dataset_id = gen_test_id()
    batch_ids = [gen_test_id(), gen_test_id()]
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name="Rematch Names Workspace",
                workspace_utc_created=now,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Rematch Names Dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=now,
            )
        )
        for index, sample_batch_id in enumerate(batch_ids):
            session.add(
                SampleBatch(
                    sample_batch_id=sample_batch_id,
                    dataset_id=dataset_id,
                    sample_batch_name=f"Named Batch {index}",
                    sample_batch_utc_created=now,
                )
            )
        await session.commit()

    yield batch_ids

    async with async_session_factory() as session:
        workspace = await session.get(Workspace, workspace_id)
        if workspace:
            await session.delete(workspace)
            await session.commit()


@pytest.mark.asyncio
async def test_the_lookup_reads_the_names_the_ui_shows(named_sample_batches):
    """The name in the message is the batch's own, from the database."""
    missing = gen_test_id()

    names = await _fetch_sample_batch_names(named_sample_batches + [missing])

    assert names == {
        named_sample_batches[0]: "Named Batch 0",
        named_sample_batches[1]: "Named Batch 1",
    }
    # A batch that no longer exists is absent, not an error.
    assert missing not in names


@pytest.mark.asyncio
async def test_a_failed_lookup_does_not_cost_the_summary():
    """Naming is a courtesy; it must never take the failure report down."""
    with patch(f"{_CTRL}.async_session", side_effect=RuntimeError("pool exhausted")):
        assert await _fetch_sample_batch_names(["b1"]) == {}
