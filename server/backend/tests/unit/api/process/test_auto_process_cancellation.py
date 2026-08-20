"""
Unit tests for how auto-processing survives (and reports) cancellation.

A pipeline that stops mid-run leaves the sample_file and its sample_item
committed, no match rows, and the batch still settling ``ready`` - and because
``asyncio.CancelledError`` derives from ``BaseException``, every
``except Exception`` in the pipeline misses it, so it used to happen with
nothing in the log to say why. Three things cover it now: the pipeline runs
detached rather than inside the triggering request's ASGI call, a cancellation
that reaches it is reported before it propagates, and shutdown drains the
detached tasks instead of closing the loop on them.

Note the pipeline is not in fact cancelled by a client disconnect - uvicorn does
not cancel an ASGI call when its client goes away - so the cancellation these
tests cover is overwhelmingly the drain's own, at shutdown.

All external dependencies are mocked - no DB, file I/O, or Socket.IO required.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_SVC = "mascope_backend.api.controllers.sample.files.process.service"
_NOTIF = "mascope_backend.socket.notifications"
_UTILS = "mascope_backend.api.lib.utils"


@pytest.fixture(autouse=True)
def isolated_background_state():
    """Give each test its own task set and a cleared shutdown flag.

    Both are module-global and the backend suite runs on a single session-wide
    event loop (``asyncio_default_test_loop_scope = session``), so without this
    a task one test leaves running is visible to - and cancellable by - the
    drain in the next, and a test that sets ``_shutdown`` silently changes how
    every later backoff behaves.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    original_tasks = service._background_tasks
    service._background_tasks = set()
    service._shutdown.clear()
    try:
        yield service._background_tasks
    finally:
        for task in service._background_tasks:
            task.cancel()
        service._background_tasks = original_tasks
        service._shutdown.clear()


@pytest.fixture
def mock_runtime():
    """Patch the module's runtime and hand back its logger."""
    from mascope_backend.api.controllers.sample.files.process import service

    runtime = MagicMock()
    with patch.object(service, "runtime", runtime):
        yield runtime


def _messages(mock_logger_method):
    return [call.args[0] for call in mock_logger_method.call_args_list]


async def _settle_into_the_backoff(body, max_passes=200):
    """Run a pipeline task until its first attempt has failed.

    The only suspension point after that failure is the backoff itself, so the
    caller can act on it as soon as this returns.

    Bounded so a pipeline that never reaches its body fails the test in
    milliseconds instead of spinning the suite to a standstill - which is
    exactly what a detached task dying before it starts looks like.
    """
    for _ in range(max_passes):
        await asyncio.sleep(0)
        if body.await_count:
            break
    else:
        raise AssertionError(
            "the pipeline body was never reached - the task finished or died "
            "before its first attempt"
        )
    await asyncio.sleep(0)


# --------------------------------------------------------------------------
# Reporting a cancellation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_is_logged_before_it_propagates(mock_runtime):
    from mascope_backend.api.controllers.sample.files.process import service

    with (
        patch(
            f"{_SVC}._auto_process_sample_file",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        patch(f"{_SVC}._delete_partial_acquisition_items", AsyncMock()),
    ):
        # Cancellation must still propagate - swallowing it would break task
        # teardown - but it must not pass through silently.
        with pytest.raises(asyncio.CancelledError):
            await service.auto_process_sample_file(sample_file_id="sf-001")

    messages = _messages(mock_runtime.logger.error)
    assert messages, "a cancelled pipeline must not vanish silently"
    assert "sf-001" in messages[0]
    assert "cancelled" in messages[0].lower()
    assert "no matched peaks" in messages[0]


@pytest.mark.asyncio
async def test_cancellation_during_a_drain_is_logged_below_the_error_level(
    mock_runtime,
):
    """One error for the whole drain, not one error-monitoring issue per file.

    The monitoring sink groups by formatted message and takes everything from
    WARNING up, so a message carrying the sample file id has to drop to INFO to
    avoid opening an issue for every file an interrupted burst had queued.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    service._shutdown.set()

    with (
        patch(
            f"{_SVC}._auto_process_sample_file",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        patch(f"{_SVC}._delete_partial_acquisition_items", AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await service.auto_process_sample_file(sample_file_id="sf-001")

    mock_runtime.logger.error.assert_not_called()
    mock_runtime.logger.warning.assert_not_called()
    assert any("sf-001" in m for m in _messages(mock_runtime.logger.info))


@pytest.mark.asyncio
async def test_cancellation_is_not_retried(mock_runtime):
    """Cancellation is not a recoverable error - retrying it would hang teardown."""
    from mascope_backend.api.controllers.sample.files.process import service

    body = AsyncMock(side_effect=asyncio.CancelledError())

    with (
        patch(f"{_SVC}._auto_process_sample_file", body),
        patch(f"{_SVC}._delete_partial_acquisition_items", AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await service.auto_process_sample_file(sample_file_id="sf-001")

    assert body.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_during_the_retry_backoff_is_logged(mock_runtime):
    """The backoff is the pipeline's longest-lived state - up to 210s per file."""
    from mascope_backend.api.controllers.sample.files.process import service

    # Fail once with a recoverable error so the loop reaches the backoff, then
    # cancel it there the way a hard shutdown would.
    body = AsyncMock(side_effect=ConnectionError("503"))

    with (
        patch(f"{_SVC}._auto_process_sample_file", body),
        patch(f"{_SVC}._delete_partial_acquisition_items", AsyncMock()),
        patch(f"{_SVC}._is_recoverable_error", lambda _e: True),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (600, 600, 600)),
    ):
        pipeline = asyncio.create_task(
            service.auto_process_sample_file(sample_file_id="sf-001")
        )
        await _settle_into_the_backoff(body)

        pipeline.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pipeline

    messages = _messages(mock_runtime.logger.error)
    assert any("sf-001" in m and "retry" in m.lower() for m in messages), messages


@pytest.mark.asyncio
async def test_backoff_stands_down_as_soon_as_shutdown_starts(mock_runtime):
    """A pipeline asleep in its backoff must not hold the drain's whole budget.

    The retry delays are minutes; the drain's budget is 60s. Waiting one out
    would spend the entire budget on a pipeline doing nothing, leaving none for
    the ones doing real work.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    body = AsyncMock(side_effect=ConnectionError("503"))

    with (
        patch(f"{_SVC}._auto_process_sample_file", body),
        patch(f"{_SVC}._delete_partial_acquisition_items", AsyncMock()),
        patch(f"{_SVC}._is_recoverable_error", lambda _e: True),
        # A backoff far longer than the test could afford to wait out.
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (600, 600, 600)),
    ):
        pipeline = asyncio.create_task(
            service.auto_process_sample_file(sample_file_id="sf-001")
        )
        await _settle_into_the_backoff(body)

        service._shutdown.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pipeline, timeout=2)

    assert body.await_count == 1, "it must not have taken the retry"
    assert pipeline.cancelled(), "standing down reports as cancelled, not failed"
    # Reported at INFO because the shutdown flag is set - see the drain test.
    assert any(
        "sf-001" in m and "shutting down" in m
        for m in _messages(mock_runtime.logger.info)
    ), _messages(mock_runtime.logger.info)


# --------------------------------------------------------------------------
# Detaching the pipeline
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_detaches_the_pipeline_from_its_caller(
    isolated_background_state,
):
    """Cancelling the caller's scope must not take the pipeline with it."""
    from mascope_backend.api.controllers.sample.files.process import service

    started = asyncio.Event()
    release = asyncio.Event()
    finished = []

    async def slow_pipeline(**_kwargs):
        started.set()
        await release.wait()
        finished.append(True)
        return {"done": True}

    async def request_scope():
        await service.spawn_auto_process_sample_file(sample_file_id="sf-001")
        # The request goes on doing its own work after scheduling; this is the
        # scope a teardown would cancel.
        await asyncio.Event().wait()

    with patch.object(service, "auto_process_sample_file", slow_pipeline):
        caller = asyncio.create_task(request_scope())
        await asyncio.wait_for(started.wait(), timeout=1)

        # The request is torn down while the pipeline it spawned is running.
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        spawned = [t for t in isolated_background_state if not t.done()]
        assert spawned, "pipeline should be tracked so it cannot be collected"

        release.set()
        await asyncio.gather(*spawned)

    assert finished == [True], "the pipeline must outlive the caller that spawned it"


@pytest.mark.asyncio
async def test_spawned_task_is_tracked_and_released(isolated_background_state):
    """A strong reference is held while running and dropped once finished."""
    from mascope_backend.api.controllers.sample.files.process import service

    async def pipeline(**_kwargs):
        return {"done": True}

    with patch.object(service, "auto_process_sample_file", pipeline):
        await service.spawn_auto_process_sample_file(sample_file_id="sf-001")
        assert len(isolated_background_state) == 1, (
            "the running task must be strongly referenced"
        )
        await asyncio.gather(*isolated_background_state)

    # done_callback runs on the next loop pass
    await asyncio.sleep(0)
    assert not isolated_background_state, "reference must be released"


@pytest.mark.asyncio
async def test_spawn_forwards_every_argument(isolated_background_state):
    """The signature mirrors the pipeline's rather than swallowing **kwargs."""
    from mascope_backend.api.controllers.sample.files.process import service

    seen = {}

    async def pipeline(**kwargs):
        seen.update(kwargs)

    with patch.object(service, "auto_process_sample_file", pipeline):
        await service.spawn_auto_process_sample_file(
            sample_file_id="sf-001",
            independent_transaction=True,
            user_id=7,
            process_id="p-1",
            parent_id="p-0",
        )
        await asyncio.gather(*isolated_background_state)

    assert seen == {
        "sample_file_id": "sf-001",
        "independent_transaction": True,
        "user_id": 7,
        "process_id": "p-1",
        "parent_id": "p-0",
    }


@pytest.mark.asyncio
async def test_spawn_omits_an_absent_process_id_rather_than_passing_none(
    isolated_background_state,
):
    """An absent process id must stay absent, not arrive as an explicit None.

    ``api_controller_background_task`` reads it as
    ``kwargs.get("process_id", gen_id(8))``, so a forwarded ``None`` skips the
    generated default and reaches ``UserNotification``, whose ``process_id`` is
    a required ``str``. That ValidationError is raised outside the decorator's
    try block, so the pipeline dies before its first attempt and the detached
    task just looks like it finished.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    seen = {}

    async def pipeline(**kwargs):
        seen.update(kwargs)

    with patch.object(service, "auto_process_sample_file", pipeline):
        await service.spawn_auto_process_sample_file(sample_file_id="sf-001")
        await asyncio.gather(*isolated_background_state)

    assert "process_id" not in seen
    assert seen["parent_id"] is None


# --------------------------------------------------------------------------
# Draining at shutdown
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_waits_for_running_pipelines(
    isolated_background_state, mock_runtime
):
    """Detaching the pipeline removed uvicorn's wait; the drain restores it."""
    from mascope_backend.api.controllers.sample.files.process import service

    finished = []

    async def pipeline():
        await asyncio.sleep(0)
        finished.append(True)

    task = asyncio.create_task(pipeline())
    isolated_background_state.add(task)

    await service.drain_auto_process_tasks(timeout=5)

    assert finished == [True]
    assert task.done() and not task.cancelled()


@pytest.mark.asyncio
async def test_drain_waits_for_tasks_spawned_while_it_is_draining(
    isolated_background_state, mock_runtime
):
    """A pipeline's last act is to spawn a rematch task into the same set.

    A drain that snapshots the set once would return before that task exists,
    leaving it abandoned when the loop closes - unwaited, uncancelled and
    unreported, the exact failure the drain is here to stop.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    finished = []

    async def followup():
        await asyncio.sleep(0)
        finished.append("followup")

    async def pipeline():
        await asyncio.sleep(0)
        finished.append("pipeline")
        # Spawned after the drain has already taken its first look.
        isolated_background_state.add(asyncio.create_task(followup()))

    isolated_background_state.add(asyncio.create_task(pipeline()))

    await service.drain_auto_process_tasks(timeout=5)

    assert finished == ["pipeline", "followup"]


@pytest.mark.asyncio
async def test_drain_cancels_what_overruns_so_it_still_reports(
    isolated_background_state, mock_runtime
):
    """A task abandoned by the closing loop reports nothing; a cancelled one does."""
    from mascope_backend.api.controllers.sample.files.process import service

    async def forever():
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    isolated_background_state.add(task)

    await service.drain_auto_process_tasks(timeout=0.01)

    assert task.cancelled()
    errors = _messages(mock_runtime.logger.error)
    assert len(errors) == 1, f"one error for the whole drain, got {errors}"
    assert "1 background task(s)" in errors[0]


@pytest.mark.asyncio
async def test_drain_does_not_hang_on_a_task_that_resists_cancellation(
    isolated_background_state, mock_runtime
):
    """Cancelled pipelines unwind through a rollback, which can block.

    Draining is a courtesy to in-flight work and must never be the reason a
    worker fails to shut down, so the post-cancel wait is bounded too.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    async def stubborn():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # A cleanup that outlives its own cancellation, e.g. a rollback on
            # the saturated pool that caused the backoff.
            await asyncio.sleep(0.5)
            raise

    task = asyncio.create_task(stubborn())
    isolated_background_state.add(task)

    with patch.object(service, "_DRAIN_CANCEL_GRACE_S", 0.05):
        await asyncio.wait_for(
            service.drain_auto_process_tasks(timeout=0.01), timeout=2
        )

    assert not task.done(), "the task really did resist - the drain gave up on it"
    task.cancel()


@pytest.mark.asyncio
async def test_drain_signals_shutdown_even_with_nothing_running(
    isolated_background_state,
):
    """The flag has to be set before the early return, not after it.

    A worker can be draining an empty set while a pipeline is a microsecond
    from entering its backoff; the flag is what that pipeline reads.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    await service.drain_auto_process_tasks(timeout=0.01)

    assert service._shutdown.is_set()
    assert not isolated_background_state


@pytest.mark.asyncio
async def test_drain_ignores_tasks_from_another_loop(
    isolated_background_state, mock_runtime
):
    """The tracking set outlives any one loop; a foreign task must not raise here.

    Awaiting a task that belongs to another loop raises, and a raise in a
    shutdown hook takes the whole teardown with it - which is how this broke
    every fixture-heavy integration test the first time round.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    # A stand-in rather than a real task on a real second loop: creating one
    # would leave a pending task behind when that loop is closed, and asyncio
    # complains about it for the rest of the session.
    foreign = MagicMock(spec=asyncio.Task)
    foreign.done.return_value = False
    foreign.get_loop.return_value = asyncio.new_event_loop()
    isolated_background_state.add(foreign)
    try:
        # Must return promptly and without raising, despite the foreign task.
        await asyncio.wait_for(service.drain_auto_process_tasks(timeout=5), timeout=2)
    finally:
        foreign.get_loop.return_value.close()
        isolated_background_state.discard(foreign)

    mock_runtime.logger.exception.assert_not_called()
    foreign.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_spawned_pipeline_runs_the_real_retry_loop(
    isolated_background_state, mock_runtime
):
    """The spawn path must reach the pipeline body, not just a stubbed stand-in.

    Every other spawn test replaces ``auto_process_sample_file`` wholesale, so
    none of them would notice a detached task that died before the pipeline
    started - the failure would look exactly like a task that finished.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    body = AsyncMock(side_effect=ConnectionError("503"))

    with (
        patch(f"{_SVC}._auto_process_sample_file", body),
        patch(f"{_SVC}._delete_partial_acquisition_items", AsyncMock()),
        patch(f"{_SVC}._is_recoverable_error", lambda _e: True),
        patch.object(service, "_AUTO_PROCESS_RETRY_DELAYS_S", (600, 600, 600)),
        patch(f"{_NOTIF}.handle_notifications", new_callable=AsyncMock),
        patch(f"{_UTILS}.handle_reloads", new_callable=AsyncMock),
    ):
        await service.spawn_auto_process_sample_file(
            sample_file_id="sf-001", independent_transaction=True
        )
        await _settle_into_the_backoff(body)

        task = next(iter(isolated_background_state))
        assert body.await_count == 1, "the detached task must reach the pipeline"
        assert not task.done(), "and park in its backoff rather than fall through"

        # And the drain gets it back out of there.
        await service.drain_auto_process_tasks(timeout=5)
        assert task.done()


@pytest.mark.asyncio
async def test_a_cancelled_rematch_names_itself_too(mock_runtime):
    """Rematch tasks share the task set, so the drain cancels them as well.

    They had the pipeline's silence for the same reason - CancelledError is a
    BaseException, and ``_observe_background_task`` returns early for anything
    cancelled - so the drain's promise that the log names what it stopped used
    to hold for pipelines and quietly fail for these.
    """
    from mascope_backend.api.controllers.sample.files.process import service

    with patch(
        f"{_SVC}.rematch_samples",
        AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await service._rematch_when_slot_free(sample_item_ids={"si-1", "si-2"})

    messages = _messages(mock_runtime.logger.error)
    assert any("Rematch of 2 affected sample(s)" in m for m in messages), messages
    # And the gate is handed back, or the next pipeline waits on a dead slot.
    assert service._auto_process_gate._value == service._AUTO_PROCESS_CONCURRENCY
