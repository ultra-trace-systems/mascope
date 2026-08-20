"""
Unit tests for how auto-processing survives (and reports) cancellation.

A pipeline that is cancelled leaves exactly the state #1844 described: the
sample_file and its sample_item committed, no match rows, and the batch still
settling ``ready`` - with nothing in the log to say why. Two things prevent it:
the pipeline no longer runs inside the triggering request's ASGI call, and a
cancellation that still reaches it is reported before it propagates.

All external dependencies are mocked - no DB, file I/O, or Socket.IO required.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_SVC = "mascope_backend.api.controllers.sample.files.process.service"


@pytest.mark.asyncio
async def test_cancellation_is_logged_before_it_propagates():
    from mascope_backend.api.controllers.sample.files.process import service

    logger = MagicMock()
    runtime = MagicMock()
    runtime.logger = logger

    with (
        patch.object(service, "runtime", runtime),
        patch(
            f"{_SVC}._auto_process_sample_file",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
    ):
        # Cancellation must still propagate - swallowing it would break task
        # teardown - but it must not pass through silently.
        with pytest.raises(asyncio.CancelledError):
            await service.auto_process_sample_file(sample_file_id="sf-001")

    assert logger.error.called, "a cancelled pipeline must not vanish silently"
    message = logger.error.call_args_list[0].args[0]
    assert "sf-001" in message
    assert "cancelled" in message.lower()
    assert "no matched peaks" in message


@pytest.mark.asyncio
async def test_cancellation_is_not_retried():
    """Cancellation is not a recoverable error - retrying it would hang teardown."""
    from mascope_backend.api.controllers.sample.files.process import service

    body = AsyncMock(side_effect=asyncio.CancelledError())
    runtime = MagicMock()

    with (
        patch.object(service, "runtime", runtime),
        patch(f"{_SVC}._auto_process_sample_file", body),
        patch(f"{_SVC}.asyncio.sleep", AsyncMock()) as slept,
    ):
        with pytest.raises(asyncio.CancelledError):
            await service.auto_process_sample_file(sample_file_id="sf-001")

    assert body.await_count == 1
    slept.assert_not_awaited()


@pytest.mark.asyncio
async def test_spawn_detaches_the_pipeline_from_its_caller():
    """The spawned pipeline must outlive the request that scheduled it."""
    from mascope_backend.api.controllers.sample.files.process import service

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_pipeline(**_kwargs):
        started.set()
        await release.wait()
        return {"done": True}

    with patch.object(service, "auto_process_sample_file", slow_pipeline):
        # Cancelling the caller's scope must not take the pipeline with it.
        caller = asyncio.create_task(
            service.spawn_auto_process_sample_file(sample_file_id="sf-001")
        )
        await caller
        await asyncio.wait_for(started.wait(), timeout=1)

        spawned = [t for t in service._background_tasks if not t.done()]
        assert spawned, "pipeline should be tracked so it cannot be collected"

        release.set()
        await asyncio.gather(*spawned)


@pytest.mark.asyncio
async def test_spawned_task_is_tracked_and_released():
    """A strong reference is held while running and dropped once finished."""
    from mascope_backend.api.controllers.sample.files.process import service

    async def pipeline(**_kwargs):
        return {"done": True}

    before = set(service._background_tasks)

    with patch.object(service, "auto_process_sample_file", pipeline):
        await service.spawn_auto_process_sample_file(sample_file_id="sf-001")
        added = set(service._background_tasks) - before
        assert len(added) == 1, "the running task must be strongly referenced"
        await asyncio.gather(*added)

    # done_callback runs on the next loop pass
    await asyncio.sleep(0)
    assert not (set(service._background_tasks) - before), "reference must be released"


@pytest.mark.asyncio
async def test_cancellation_during_the_retry_backoff_is_logged():
    """The backoff is the pipeline's longest-lived state and was the last silent one."""
    from mascope_backend.api.controllers.sample.files.process import service

    logger = MagicMock()
    runtime = MagicMock()
    runtime.logger = logger

    # Fail once with a recoverable error so the loop reaches the backoff, then
    # cancel the sleep the way a shutdown would.
    body = AsyncMock(side_effect=ConnectionError("503"))

    with (
        patch.object(service, "runtime", runtime),
        patch(f"{_SVC}._auto_process_sample_file", body),
        patch(f"{_SVC}._is_recoverable_error", lambda _e: True),
        patch(
            f"{_SVC}.asyncio.sleep",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await service.auto_process_sample_file(sample_file_id="sf-001")

    messages = [c.args[0] for c in logger.error.call_args_list]
    assert any("sf-001" in m and "retry" in m.lower() for m in messages), messages


@pytest.mark.asyncio
async def test_drain_waits_for_running_pipelines():
    """Detaching the pipeline removed uvicorn's wait; the drain restores it."""
    from mascope_backend.api.controllers.sample.files.process import service

    finished = []

    async def pipeline():
        await asyncio.sleep(0)
        finished.append(True)

    task = asyncio.create_task(pipeline())
    service._background_tasks.add(task)
    try:
        await service.drain_auto_process_tasks(timeout=5)
        assert finished == [True]
        assert task.done()
    finally:
        service._background_tasks.discard(task)


@pytest.mark.asyncio
async def test_drain_cancels_what_overruns_so_it_still_reports():
    """A task abandoned by the closing loop reports nothing; a cancelled one does."""
    from mascope_backend.api.controllers.sample.files.process import service

    async def forever():
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    service._background_tasks.add(task)
    runtime = MagicMock()
    try:
        with patch.object(service, "runtime", runtime):
            await service.drain_auto_process_tasks(timeout=0.01)
        assert task.cancelled()
    finally:
        service._background_tasks.discard(task)


@pytest.mark.asyncio
async def test_drain_is_a_noop_with_nothing_running():
    from mascope_backend.api.controllers.sample.files.process import service

    before = set(service._background_tasks)
    await service.drain_auto_process_tasks(timeout=0.01)
    assert set(service._background_tasks) == before


@pytest.mark.asyncio
async def test_drain_ignores_tasks_from_a_dead_loop():
    """The tracking set outlives any one loop; a foreign task must not raise here.

    Awaiting a task that belongs to another loop raises, and a raise in a shutdown
    hook takes the whole teardown with it - which is how this broke every
    fixture-heavy integration test the first time round.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    other_loop = asyncio.new_event_loop()
    try:
        foreign = other_loop.create_task(asyncio.sleep(60))
    finally:
        pass

    service._background_tasks.add(foreign)
    runtime = MagicMock()
    try:
        with patch.object(service, "runtime", runtime):
            # Must return promptly and without raising, despite the foreign task.
            await asyncio.wait_for(
                service.drain_auto_process_tasks(timeout=5), timeout=2
            )
        runtime.logger.exception.assert_not_called()
    finally:
        service._background_tasks.discard(foreign)
        foreign.cancel()
        other_loop.close()
