"""A peak-detection request reports its outcome to the user who asked for it.

``compute_sample_file_peaks`` notifies the room ``user_id``, and the
background-task decorator reads that only from the task's kwargs or from the
result's ``_notification_data``, never from the ``user`` object the task also
takes. The route passed only ``user``, so every request ended with "Cannot
emit notification: no room_id for 'user_id'" at WARNING - one monitoring event
per file - and the success or error packet was dropped.

These tests drive the route itself and run the task it schedules, so a route
that stops passing the id fails them, not only a controller that stops
accepting it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.routes.sample.files import sample_files_routes


_ROUTES = "mascope_backend.api.routes.sample.files.sample_files_routes"
_CONTROLLER = "mascope_backend.api.controllers.sample.files.sample_files_controller"
_SERVICE = "mascope_backend.socket.notifications.service"

USER_ID = 42


def _user():
    return SimpleNamespace(id=USER_ID, to_dict=lambda: {"id": USER_ID})


async def _request_peaks(converter_available=AsyncMock()):
    """Call the route, run what it scheduled, and return the packets sent."""
    background_tasks = BackgroundTasks()
    with (
        patch(f"{_ROUTES}.check_sample_file_instrument_access", AsyncMock()),
        patch(f"{_ROUTES}.get_access_token", AsyncMock(return_value="token")),
        patch(
            f"{_CONTROLLER}.get_sample_file",
            AsyncMock(return_value={"data": {"filename": "sample.raw"}}),
        ),
        patch(f"{_CONTROLLER}.ensure_converter_available", converter_available),
        patch(f"{_CONTROLLER}.request_peak_detection", AsyncMock()),
        patch(f"{_SERVICE}.error_recipient", AsyncMock(side_effect=lambda uid: uid)),
        patch(f"{_SERVICE}.emit_user_notification", AsyncMock()) as emit,
        patch(f"{_SERVICE}.runtime.logger.warning") as warning,
    ):
        response = await sample_files_routes.compute_sample_file_peaks_route(
            sample_file_id="file-1",
            background_tasks=background_tasks,
            user=_user(),
        )
        assert response.status_code == 202
        await background_tasks()

    assert not warning.called, warning.call_args
    return emit


@pytest.mark.asyncio
async def test_a_requested_detection_tells_the_user_it_was_requested():
    emit = await _request_peaks()

    emit.assert_awaited_once()
    notification = emit.await_args.args[0]
    assert emit.await_args.kwargs == {"user_id": USER_ID}
    assert notification.type == "compute_sample_file_peaks"
    assert notification.status == "success"


@pytest.mark.asyncio
async def test_a_failed_request_tells_the_user_it_failed():
    unavailable = AsyncMock(
        side_effect=ApiException(
            "The file converter is not connected", "no converter", 503
        )
    )

    emit = await _request_peaks(converter_available=unavailable)

    emit.assert_awaited_once()
    notification = emit.await_args.args[0]
    assert emit.await_args.kwargs == {"user_id": USER_ID}
    assert notification.type == "compute_sample_file_peaks"
    assert notification.status == "error"
