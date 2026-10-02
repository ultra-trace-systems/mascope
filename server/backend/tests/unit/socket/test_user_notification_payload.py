"""
What a user notification carries onto the wire.

Every Socket.IO emit is published to every backend process through Redis
pub/sub, so what an emit carries is paid for once per process, whoever the
packet is addressed to.

A ``silent`` packet ends the progress bar of a dependent task whose outcome a
parent reports; the browser reads its process id, status, message and
progress, and nothing else. It goes without the data and the error detail the
decorator filled in - for a dependent calibration fit that is the fit's whole
table of calibrants, and a batch calibration sends one per sample.
"""

from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.socket.notifications.schemas import UserNotification
from mascope_backend.socket.notifications.service import emit_user_notification


_SVC = "mascope_backend.socket.notifications.service"


def _notification(**fields) -> UserNotification:
    return UserNotification(
        process_id="child-process",
        parent_id="root-process",
        type="calibration_mz_fit",
        status="warning",
        message="m/z fitting sample 'x' warning: Calibration inaccurate",
        data={"sample_item_id": "sample-1"},
        error={"detail": {"data": {"stats": [{"mz": 100.0}] * 50}}},
        **fields,
    )


async def _emitted(notification: UserNotification) -> dict:
    """The packet as it is handed to Socket.IO, for a user's own room."""
    with patch(f"{_SVC}.sio.emit", new_callable=AsyncMock) as emit:
        await emit_user_notification(notification, user_id=1)
    emit.assert_awaited_once()
    event, packet = emit.await_args.args
    assert event == "user_notification"
    return packet


@pytest.mark.asyncio
async def test_a_silent_packet_goes_without_data_and_error():
    packet = await _emitted(_notification(silent=True))

    assert "data" not in packet
    assert "error" not in packet
    # What ends the progress bar is all there.
    assert packet["process_id"] == "child-process"
    assert packet["parent_id"] == "root-process"
    assert packet["status"] == "warning"
    assert packet["silent"] is True
    assert packet["message"].startswith("m/z fitting sample")


@pytest.mark.asyncio
async def test_a_reported_packet_keeps_its_data_and_error():
    packet = await _emitted(_notification())

    assert packet["data"] == {"sample_item_id": "sample-1"}
    assert len(packet["error"]["detail"]["data"]["stats"]) == 50
