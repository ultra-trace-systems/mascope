"""
A nested, dependent background task does not report its own warning.

The warning is re-raised to the parent handler, which reports it with its own
context - so reporting here as well filed one notification per nesting level
per attempt. A sample that would not calibrate produced fourteen identical
entries (7 attempts x calibration_mz_fit + calibration_mz_calibrate_sample).

The packet itself is still emitted, flagged ``silent``: on the failure path it
is the only non-pending notification the child's process id sends, and that is
what ends the progress bar the task opened. The frontend takes a silent packet
as progress termination alone - no log entry, no badge count, no toast.

The user-facing copy is suppressed under exactly the condition that re-raises,
so a warning is reported either here or by the parent - never both, never
neither. Note the patch target: the decorator binds ``handle_notifications``
into its own module namespace, so patching
``mascope_backend.socket.notifications.handle_notifications`` would be a no-op.
"""

from unittest.mock import AsyncMock

import pytest

from mascope_backend.api.lib import api_features
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    raise_api_warning,
)


@api_controller_background_task(error_notification_rooms=["user_id"])
async def warn_about_something(
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    raise_api_warning("careful", {})


@pytest.fixture
def emitted(monkeypatch) -> AsyncMock:
    emit = AsyncMock()
    monkeypatch.setattr(api_features, "handle_notifications", emit)
    return emit


@pytest.mark.asyncio
async def test_top_level_warning_is_reported(emitted):
    """Nobody upstream to report it: this is the only chance to say it."""
    await warn_about_something(independent_transaction=True, user_id=1)

    emitted.assert_awaited_once()
    assert emitted.await_args.args[1].status == "warning"
    # Reported, so the drawer and the badge must see it.
    assert not emitted.await_args.args[1].silent


@pytest.mark.asyncio
async def test_nested_dependent_warning_is_re_raised_instead_of_reported(emitted):
    with pytest.raises(ApiException) as excinfo:
        await warn_about_something(
            independent_transaction=False,
            user_id=1,
            process_id="child",
            parent_id="root",
        )

    # Not reported here, but the parent still receives it verbatim.
    assert excinfo.value.status_code == 200
    assert excinfo.value.user_message == "careful"
    # The packet still goes out, flagged silent: it is the only non-pending
    # notification this process id sends, so the frontend needs it to end the
    # progress bar the task opened. Silent keeps it out of the log, off the
    # badge and away from the toast.
    emitted.assert_awaited_once()
    assert emitted.await_args.args[1].silent is True


@pytest.mark.asyncio
async def test_dependent_warning_without_a_parent_is_reported(emitted):
    """No parent_id means no re-raise, so silencing it would lose it entirely."""
    await warn_about_something(
        independent_transaction=False, user_id=1, process_id="orphan"
    )

    emitted.assert_awaited_once()
    assert not emitted.await_args.args[1].silent


@pytest.mark.asyncio
async def test_independent_nested_warning_is_still_reported(emitted):
    """Protects the rematch_batch special case: independent despite a parent."""
    await warn_about_something(
        independent_transaction=True,
        user_id=1,
        process_id="child",
        parent_id="root",
    )

    emitted.assert_awaited_once()
    assert not emitted.await_args.args[1].silent
