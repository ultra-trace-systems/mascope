"""
An error reaches someone who can act on it.

A paired File Agent uploads as a machine account. Nobody signs in as one, so
its personal room has no browser in it, and a background task's error
addressed to that account reaches no one. When an agent's upload failed to
auto-process, for a missing ionization token say, the file just sat there
with no samples and nobody was told (#1910).

Two routes now carry the error:

- ``handle_notifications`` addresses an *error* from a machine account's task
  to the sponsor of its device. Successes stay with the account they ran for,
  so an agent's routine uploads do not follow its sponsor around the app.
- ``auto_process_sample_file`` takes the file's instrument as an argument. A
  failed run has no result to read the instrument from, so without it the
  error never reached the room of the people watching that instrument.

The sponsor lookup itself (``mascope_backend.db.devices.device_sponsor_id``,
a real query) is covered by
``tests/integration/api/pairing/test_device_sponsor_lookup.py``; here it is
scripted.
"""

from unittest.mock import AsyncMock, patch

import pytest
from test_utils import captured_logs

from mascope_backend.socket.notifications import handle_notifications
from mascope_backend.socket.notifications.schemas import UserNotification


_SVC = "mascope_backend.socket.notifications.service"
_PROCESS = "mascope_backend.api.controllers.sample.files.process.service"

MACHINE = 7
SPONSOR = 42


@pytest.fixture
def emit(monkeypatch) -> AsyncMock:
    """Stand in for the Socket.IO emit, so no server is needed."""
    emitter = AsyncMock()
    monkeypatch.setattr(f"{_SVC}.emit_user_notification", emitter)
    return emitter


@pytest.fixture
def sponsors(monkeypatch) -> AsyncMock:
    """Script the device-sponsor lookup: the machine has a sponsor, nobody else."""
    lookup = AsyncMock(
        side_effect=lambda _session, user_id: {MACHINE: SPONSOR}.get(user_id)
    )
    monkeypatch.setattr(f"{_SVC}.device_sponsor_id", lookup)
    return lookup


def _notification(status: str) -> UserNotification:
    return UserNotification(
        process_id="p-1",
        type="auto_process_sample_file",
        status=status,
        message="Failed to auto process sample file" if status == "error" else "ok",
    )


@pytest.mark.asyncio
async def test_an_error_from_a_machine_accounts_task_reaches_the_sponsor(
    emit, sponsors
):
    await handle_notifications(
        ["instrument"],
        _notification("error"),
        {"user_id": MACHINE, "instrument": "Instr-A"},
        None,
    )

    emit.assert_awaited_once()
    assert emit.await_args.kwargs == {"room_id": "Instr-A", "user_id": SPONSOR}


@pytest.mark.asyncio
async def test_a_success_from_a_machine_accounts_task_stays_with_the_account(
    emit, sponsors
):
    await handle_notifications(
        ["instrument"],
        _notification("success"),
        {"user_id": MACHINE, "instrument": "Instr-A"},
        None,
    )

    assert emit.await_args.kwargs == {"room_id": "Instr-A", "user_id": MACHINE}
    sponsors.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_silent_error_stays_with_the_browser_that_started_the_task(
    emit, sponsors
):
    """A silent packet only ends a progress bar, in the starting user's browser."""
    notification = _notification("error")
    notification.silent = True
    await handle_notifications(
        ["instrument"],
        notification,
        {"user_id": MACHINE, "instrument": "Instr-A"},
        None,
    )

    assert emit.await_args.kwargs == {"room_id": "Instr-A", "user_id": MACHINE}
    sponsors.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_person_reads_their_own_error(emit, sponsors):
    await handle_notifications(
        ["user_id"], _notification("error"), {"user_id": 3}, None
    )

    assert emit.await_args.kwargs == {"user_id": 3}


@pytest.mark.asyncio
async def test_an_error_without_a_user_needs_no_lookup(emit, sponsors):
    await handle_notifications(
        ["instrument"], _notification("error"), {"instrument": "Instr-A"}, None
    )

    assert emit.await_args.kwargs == {"room_id": "Instr-A", "user_id": None}
    sponsors.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_lookup_keeps_the_error_with_the_account(emit, monkeypatch):
    monkeypatch.setattr(
        f"{_SVC}.device_sponsor_id", AsyncMock(side_effect=RuntimeError("db down"))
    )
    with captured_logs(level="WARNING") as records:
        await handle_notifications(
            ["instrument"],
            _notification("error"),
            {"user_id": MACHINE, "instrument": "Instr-A"},
            None,
        )

    assert emit.await_args.kwargs == {"room_id": "Instr-A", "user_id": MACHINE}
    assert [record["level"].name for record in records] == ["WARNING"]
    assert f"device sponsor for account {MACHINE}" in records[0]["message"]


@pytest.mark.asyncio
async def test_a_failed_auto_process_run_reaches_the_instrument_room_and_the_sponsor(
    emit, sponsors
):
    """End to end through the pipeline's decorator: the #1910 case."""
    from mascope_backend.api.controllers.sample.files.process.service import (
        auto_process_sample_file,
    )

    token_error = ValueError(
        "No ionization mode tokens found for file Instr-A_2026.09.18_x. "
        "Configure tokens in ionization settings"
    )
    with (
        patch(f"{_PROCESS}._delete_partial_acquisition_items", AsyncMock()),
        patch(
            f"{_PROCESS}._auto_process_sample_file",
            AsyncMock(side_effect=token_error),
        ),
    ):
        await auto_process_sample_file(
            sample_file_id="sf-1",
            independent_transaction=True,
            user_id=MACHINE,
            process_id="p-1",
            instrument="Instr-A",
        )

    emit.assert_awaited_once()
    notification = emit.await_args.args[0]
    assert emit.await_args.kwargs == {"room_id": "Instr-A", "user_id": SPONSOR}
    assert notification.status == "error"
    assert "No ionization mode tokens found" in notification.message
