"""One peak-detection request gives its user one progress bar, ended once.

The frontend's notification store (``stores/ui/notification.js``) ends a
process on the first packet for its process id that is not pending: it
removes the progress bar, shows a toast and files a row in the log. A packet
of a later stage for the same id then opens a second bar, or contradicts the
row already filed.

A request's packets come from three places under one process id: the request
itself (``request_peak_detection``, pending), the background-task decorator
around ``compute_sample_file_peaks`` (its outcome), and the file converter's
``peak_detection_*`` socket handlers (progress and the outcome of the
detection). These tests run the route, the task it schedules and
``request_peak_detection`` for real, stub only the converter's socket and the
database, answer as the converter would, and record every packet the user
gets, in order.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks

from mascope_backend.api.routes.sample.files import sample_files_routes
from mascope_backend.socket.events.handlers.file_converter import conversion


_ROUTES = "mascope_backend.api.routes.sample.files.sample_files_routes"
_CONTROLLER = "mascope_backend.api.controllers.sample.files.sample_files_controller"
_SERVICE = "mascope_backend.socket.notifications.service"
_CONVERSION = "mascope_backend.socket.events.handlers.file_converter.conversion"

USER_ID = 42


def _user():
    return SimpleNamespace(
        id=USER_ID,
        username="analyst",
        role_id=3,
        to_dict=lambda: {"id": USER_ID},
    )


class _Recorder:
    """Every packet sent to a user, from any sender, in the order sent."""

    def __init__(self):
        self.packets = []

    async def __call__(self, notification, room_id=None, user_id=None):
        self.packets.append((notification.model_copy(), user_id))


async def _request_peaks(converter_connected: bool):
    """Run the route and its task; return the packets and the converter's events."""
    packets = _Recorder()
    socket = AsyncMock()
    background_tasks = BackgroundTasks()
    with (
        patch(f"{_ROUTES}.check_sample_file_instrument_access", AsyncMock()),
        patch(f"{_ROUTES}.get_access_token", AsyncMock(return_value="token")),
        patch(
            f"{_CONTROLLER}.get_sample_file",
            AsyncMock(return_value={"data": {"filename": "sample.raw"}}),
        ),
        patch(
            f"{_CONTROLLER}.is_service_connected",
            AsyncMock(return_value=converter_connected),
        ),
        patch(
            f"{_CONTROLLER}.fetch_affected_sample_data",
            AsyncMock(return_value=(["item-1"], [])),
        ),
        patch(f"{_CONTROLLER}.event_emitter.emit", socket),
        patch(f"{_CONTROLLER}.emit_user_notification", packets),
        patch(f"{_SERVICE}.emit_user_notification", packets),
        patch(f"{_SERVICE}.error_recipient", AsyncMock(side_effect=lambda uid: uid)),
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
    return packets, socket


async def _converter_answers(packets: _Recorder, request: dict, progress=(40, 80)):
    """Answer the request as the converter does: progress, then the outcome.

    The handlers are called past their token check, which is not what is
    under test here.
    """
    reply = {key: request[key] for key in ("user_id", "filename", "sample_file_id")} | {
        "process_id": request["process_id"]
    }
    with patch(f"{_CONVERSION}.emit_user_notification", packets):
        for percent in progress:
            await conversion.peak_detection_progress.__wrapped__(
                "sid", reply | {"progress": percent}
            )
        await conversion.peak_detection_complete.__wrapped__("sid", dict(reply))


@pytest.mark.asyncio
async def test_the_bar_stays_open_until_the_converter_reports_the_outcome():
    packets, socket = await _request_peaks(converter_connected=True)

    socket.assert_awaited_once()
    event, request = socket.await_args.args
    assert event == "file-converter.peak_detection_request"
    process_id = request["process_id"]
    assert process_id
    assert request["user_id"] == USER_ID

    # Accepted and handed over: one pending packet, and nothing that ends it.
    assert [(p.status, p.process_id, uid) for p, uid in packets.packets] == [
        ("pending", process_id, USER_ID)
    ]

    await _converter_answers(packets, request)

    for_this_process = [
        (p.status, p.progress)
        for p, uid in packets.packets
        if p.process_id == process_id and uid == USER_ID
    ]
    assert for_this_process == [
        ("pending", 5),
        ("pending", 40),
        ("pending", 80),
        ("success", 100),
    ]
    # Every packet the user got was this request's.
    assert len(for_this_process) == len(packets.packets)


@pytest.mark.asyncio
async def test_a_request_with_no_converter_tells_the_user_it_failed():
    packets, socket = await _request_peaks(converter_connected=False)

    # Refused before the hand-over: nothing reaches the converter, no bar opens.
    socket.assert_not_awaited()
    assert len(packets.packets) == 1
    notification, user_id = packets.packets[0]
    assert user_id == USER_ID
    assert notification.type == "compute_sample_file_peaks"
    assert notification.status == "error"
    assert notification.process_id
