"""
What a user notification carries onto the wire.

Every Socket.IO emit is published to every backend process through Redis
pub/sub, so what an emit carries is paid for once per process, whoever the
packet is addressed to.

A dependent task's packet - one with a parent, a ``silent`` one always - only
moves or ends the progress bar its process opened. The browser displays and
dispatches packets without a parent alone, and of a child it reads the process
id, status, message and progress, nothing else. So a child goes without the
data and the error detail the decorator filled in - for a dependent
calibration fit, warning or not, that is the fit's whole table of calibrants,
and a batch calibration sends one per sample.

A packet heavier than ``USER_NOTIFICATION_BUDGET_BYTES`` is still sent, and
logged at WARNING once per notification type and
``USER_NOTIFICATION_BUDGET_WARNING_INTERVAL_S``: a composition search that
sent its whole result this way - about 14 MB - overran Redis' pub/sub buffer
and took every backend process's subscribers off it. What outgrows the budget
is mostly a progress stream, the same packet sent at every step of a task, so
its repeats are held back rather than logged one by one.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
from test_utils import captured_logs

from mascope_backend.socket.notifications import service
from mascope_backend.socket.notifications.schemas import UserNotification
from mascope_backend.socket.notifications.service import emit_user_notification
from mascope_runtime.logging import SENTRY_FINGERPRINT


_SVC = "mascope_backend.socket.notifications.service"


@pytest.fixture(autouse=True)
def _no_warning_window():
    """Each test starts with no over-budget warning on record, and leaves none."""
    service._over_budget_warned_at.clear()
    yield
    service._over_budget_warned_at.clear()


def _notification(**fields) -> UserNotification:
    """A dependent calibration fit's warning, unless ``fields`` say otherwise."""
    return UserNotification(
        **{
            "process_id": "child-process",
            "parent_id": "root-process",
            "type": "calibration_mz_fit",
            "status": "warning",
            "message": "m/z fitting sample 'x' warning: Calibration inaccurate",
            "data": {"sample_item_id": "sample-1"},
            "error": {"detail": {"data": {"stats": [{"mz": 100.0}] * 50}}},
            **fields,
        }
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
async def test_a_successful_dependent_packet_goes_without_data():
    """Not silent - a child that succeeded reports nothing its parent would -
    but still a child: the browser never displays it or hands it to a watcher."""
    packet = await _emitted(
        _notification(
            status="success",
            message="Finished to m/z fit sample 'x'.",
            data={"fit": {"mode": 0}, "stats": [{"mz": 100.0}] * 50},
            error=None,
        )
    )

    assert "data" not in packet
    assert packet["parent_id"] == "root-process"
    assert packet["status"] == "success"


@pytest.mark.asyncio
async def test_a_top_level_packet_keeps_its_data_and_error():
    """What the browser shows and its watchers read: the calibration dialog
    takes the fit and its table from here."""
    packet = await _emitted(_notification(parent_id=None))

    assert packet["data"] == {"sample_item_id": "sample-1"}
    assert len(packet["error"]["detail"]["data"]["stats"]) == 50


def _carrying(
    payload_bytes: int,
    silent: bool | None = None,
    notification_type: str = "match_compositions_by_mz",
) -> UserNotification:
    """A finished task's notification whose data weighs about ``payload_bytes``."""
    return UserNotification(
        process_id="search-process",
        type=notification_type,
        status="success",
        message="Matched 2000 potential compounds.",
        data={"rows": "x" * payload_bytes},
        silent=silent,
    )


@pytest.mark.asyncio
async def test_a_packet_over_its_budget_is_sent_and_logged_by_type():
    budget = service.USER_NOTIFICATION_BUDGET_BYTES

    with captured_logs(level="WARNING") as records:
        packet = await _emitted(_carrying(budget + 1))

    assert len(packet["data"]["rows"]) == budget + 1  # sent all the same
    assert len(records) == 1
    assert "'match_compositions_by_mz'" in records[0]["message"]
    assert records[0]["extra"][SENTRY_FINGERPRINT] == [
        "user-notification-over-budget:match_compositions_by_mz"
    ]


@pytest.mark.asyncio
async def test_a_packet_within_its_budget_is_not_logged():
    budget = service.USER_NOTIFICATION_BUDGET_BYTES

    with captured_logs(level="WARNING") as records:
        await _emitted(_carrying(budget // 2))

    assert records == []


@pytest.mark.asyncio
async def test_a_packet_is_weighed_as_it_is_sent():
    """A silent packet sheds its payload before it is weighed, so the data it
    no longer carries does not count against it."""
    budget = service.USER_NOTIFICATION_BUDGET_BYTES

    with captured_logs(level="WARNING") as records:
        packet = await _emitted(_carrying(budget * 2, silent=True))

    assert "data" not in packet
    assert records == []


@pytest.mark.asyncio
async def test_a_stream_of_heavy_packets_is_logged_once():
    """The packet a progress stream sends at every step: one record for the
    stream, and the repeats at DEBUG - every packet still sent."""
    budget = service.USER_NOTIFICATION_BUDGET_BYTES

    with captured_logs(level="DEBUG") as records:
        for _ in range(5):
            packet = await _emitted(_carrying(budget + 1))

    assert len(packet["data"]["rows"]) == budget + 1
    assert [r["level"].name for r in records if "budget" in r["message"]] == [
        "WARNING",
        "DEBUG",
        "DEBUG",
        "DEBUG",
        "DEBUG",
    ]


@pytest.mark.asyncio
async def test_each_type_is_logged_on_its_own():
    """The type names the controller to change: one does not hold back another."""
    budget = service.USER_NOTIFICATION_BUDGET_BYTES

    with captured_logs(level="WARNING") as records:
        await _emitted(_carrying(budget + 1))
        await _emitted(_carrying(budget + 1, notification_type="copy_sample_items"))

    assert [record["extra"][SENTRY_FINGERPRINT] for record in records] == [
        ["user-notification-over-budget:match_compositions_by_mz"],
        ["user-notification-over-budget:copy_sample_items"],
    ]


@pytest.mark.asyncio
async def test_a_type_is_logged_again_once_its_interval_has_passed():
    budget = service.USER_NOTIFICATION_BUDGET_BYTES
    interval = service.USER_NOTIFICATION_BUDGET_WARNING_INTERVAL_S
    service._over_budget_warned_at["match_compositions_by_mz"] = (
        time.monotonic() - interval - 1
    )

    with captured_logs(level="WARNING") as records:
        await _emitted(_carrying(budget + 1))

    assert len(records) == 1
