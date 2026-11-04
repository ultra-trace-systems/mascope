"""
A TOF agent acquisition event that names no instrument is dropped with one
warning.

The instrument is the room the event's notification goes to. With none,
``emit_user_notification`` would raise, and the ``socket_auth`` wrapper would
log the ValueError at ERROR with its traceback on every such event.
"""

from unittest.mock import AsyncMock, patch

import pytest
from test_utils import captured_logs

# mascope_backend.socket.auth and mascope_backend.api.new.auth import each
# other; the application boots through the api side, so do the same here.
import mascope_backend.api.new.auth  # noqa: F401
from mascope_backend.socket.events.handlers.tof_agent import acquisition
from mascope_runtime.logging import SENTRY_FINGERPRINT


_MODULE = "mascope_backend.socket.events.handlers.tof_agent.acquisition"
_DECORATORS = "mascope_backend.socket.auth.decorators"

_EVENTS = [
    (
        acquisition.instrument_acquisition_started,
        "instrument_acquisition_started",
        {"filename": "a.h5", "polarity": "positive"},
    ),
    (
        acquisition.instrument_acquisition_progress,
        "instrument_acquisition_progress",
        {"filename": "a.h5", "progress": 50},
    ),
    (
        acquisition.instrument_acquisition_finished,
        "instrument_acquisition_finished",
        {"filename": "a.h5", "progress": 100},
    ),
]


async def _emit(handler, payload):
    """Call a handler through its socket_auth wrapper as an editor agent.

    :return: The emit_user_notification mock and the captured log records.
    """
    session = AsyncMock(return_value={"user_id": 1, "role_id": 200})  # editor
    with (
        patch(f"{_DECORATORS}.get_session_user", session),
        patch(f"{_MODULE}.emit_user_notification", new_callable=AsyncMock) as emit,
        captured_logs() as records,
    ):
        await handler("sid-agent", payload)
    return emit, records


@pytest.mark.asyncio
@pytest.mark.parametrize("instrument", ["", None, "missing"])
@pytest.mark.parametrize("handler, event, payload", _EVENTS)
async def test_an_event_with_no_instrument_warns_and_emits_nothing(
    handler, event, payload, instrument
):
    payload = dict(payload)
    if instrument != "missing":
        payload["instrument"] = instrument

    emit, records = await _emit(handler, payload)

    emit.assert_not_awaited()
    assert [r["level"].name for r in records if r["level"].no >= 30] == ["WARNING"]
    (warning,) = [r for r in records if r["level"].name == "WARNING"]
    assert event in warning["message"]
    assert warning["extra"][SENTRY_FINGERPRINT] == [
        f"tof-agent-acquisition-no-instrument:{event}"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler, event, payload", _EVENTS)
async def test_an_event_with_an_instrument_notifies_its_room(handler, event, payload):
    emit, records = await _emit(handler, {**payload, "instrument": "TOF-1"})

    emit.assert_awaited_once()
    assert emit.await_args.kwargs["room_id"] == "TOF-1"
    assert emit.await_args.kwargs["notification"].data["instrument"] == "TOF-1"
    assert not [r for r in records if r["level"].no >= 30]
