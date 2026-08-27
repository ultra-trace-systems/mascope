"""
A silent notification with nobody to send it to costs nothing.

A ``silent`` packet is pure frontend plumbing: the backend suppressed the
user-facing copy of a nested task's warning because a parent handler reports
it, and sends this one only so the progress entry that process opened ends
now instead of waiting out its fallback timeout. It carries nothing to read.

``handle_notifications`` used to log at WARNING whenever it could resolve
neither a room nor a user, and every WARNING record is exported to error
monitoring (``mascope_runtime.logging._SENTRY_LEVELS``). On a pipeline that
runs without a user - tests, background reprocessing - that turned each
suppressed warning into a monitoring event: up to fourteen for a single
sample that will not calibrate (7 attempts x calibration_mz_fit +
calibration_mz_calibrate_sample).

The two halves that must not move:

- with an audience the silent packet is still emitted, because it is the only
  non-pending packet the child's process id sends and therefore the thing
  that ends its progress bar;
- without one, an *ordinary* notification that cannot be delivered is still a
  WARNING - a message meant for a user was lost, and that diagnostic stays.
"""

from unittest.mock import AsyncMock

import pytest

from mascope_backend.api.lib import api_features
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    raise_api_warning,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import handle_notifications
from mascope_backend.socket.notifications.schemas import UserNotification


@pytest.fixture
def emit(monkeypatch) -> AsyncMock:
    """Stand in for the Socket.IO emit, so no server is needed."""
    emitter = AsyncMock()
    monkeypatch.setattr(
        "mascope_backend.socket.notifications.service.emit_user_notification", emitter
    )
    return emitter


class _Monitored:
    """Collects the records error monitoring would receive (WARNING+)."""

    def __init__(self):
        self.records = []

    def __enter__(self):
        self._sink_id = runtime.logger.add(
            lambda message: self.records.append(message.record), level="WARNING"
        )
        return self

    def __exit__(self, *exc_info):
        runtime.logger.remove(self._sink_id)
        return False

    @property
    def levels(self) -> list[str]:
        return [record["level"].name for record in self.records]

    @property
    def messages(self) -> list[str]:
        return [record["message"] for record in self.records]


def _warning(silent: bool | None) -> UserNotification:
    return UserNotification(
        process_id="child",
        parent_id="root",
        type="calibration_mz_fit",
        status="warning",
        message="careful",
        silent=silent,
    )


@pytest.mark.asyncio
async def test_silent_packet_without_an_audience_is_free(emit):
    """No emit attempt, and nothing for error monitoring to pick up."""
    with _Monitored() as monitored:
        await handle_notifications(["user_id"], _warning(silent=True), {}, None)

    assert monitored.records == []
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_silent_packet_with_a_user_is_still_emitted(emit):
    """The progress bar it exists to end is in that user's browser."""
    with _Monitored() as monitored:
        await handle_notifications(
            ["user_id"], _warning(silent=True), {"user_id": 7}, None
        )

    emit.assert_awaited_once()
    assert emit.await_args.kwargs["user_id"] == 7
    assert emit.await_args.args[0].silent is True
    assert monitored.records == []


@pytest.mark.asyncio
async def test_silent_packet_with_a_room_is_still_emitted(emit):
    """A room is an audience too, even with no user id to route by."""
    with _Monitored() as monitored:
        await handle_notifications(
            ["sample_batch_id"],
            _warning(silent=True),
            {"sample_batch_id": "sb-1"},
            None,
        )

    emit.assert_awaited_once()
    assert emit.await_args.kwargs["room_id"] == "sb-1"
    assert monitored.records == []


@pytest.mark.asyncio
async def test_undeliverable_user_facing_notification_still_warns(emit):
    """The diagnostic that matters is not blinded: this one lost a message."""
    with _Monitored() as monitored:
        await handle_notifications(["user_id"], _warning(silent=None), {}, None)

    assert monitored.levels == ["WARNING"]
    assert "Cannot emit notification" in monitored.messages[0]
    emit.assert_not_awaited()


@api_controller_background_task(error_notification_rooms=["user_id"])
async def warn_about_something(
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    raise_api_warning("careful", {})


@pytest.mark.asyncio
async def test_userless_nested_warning_files_no_monitoring_event(emit):
    """
    End to end, the way the cost was measured: a nested dependent task warns
    on a pipeline with no user. The warning still reaches the parent; error
    monitoring hears nothing.
    """
    with _Monitored() as monitored, pytest.raises(ApiException) as excinfo:
        await warn_about_something(
            independent_transaction=False,
            user_id=None,
            process_id="child",
            parent_id="root",
        )

    assert excinfo.value.status_code == 200
    assert excinfo.value.user_message == "careful"
    assert monitored.records == []
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_userless_reported_warning_still_files_one(emit):
    """
    Same pipeline, but the warning is this level's to report and nobody can
    hear it. That is the case the WARNING exists for, and it survives.
    """
    with _Monitored() as monitored:
        await warn_about_something(independent_transaction=True, user_id=None)

    assert monitored.levels == ["WARNING"]
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_decorator_still_reaches_the_service(monkeypatch):
    """
    Guards the two tests above against becoming vacuous.

    They assert on an *absence*, so they would pass just as well if the
    decorator had stopped calling ``handle_notifications`` at all. It has
    not: the same call that emits nothing with no audience emits the silent
    packet as soon as there is one.
    """
    emitter = AsyncMock()
    monkeypatch.setattr(
        "mascope_backend.socket.notifications.service.emit_user_notification", emitter
    )
    assert api_features.handle_notifications is handle_notifications

    with pytest.raises(ApiException):
        await warn_about_something(
            independent_transaction=False,
            user_id=7,
            process_id="child",
            parent_id="root",
        )

    emitter.assert_awaited_once()
    assert emitter.await_args.args[0].silent is True
