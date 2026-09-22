"""
Unit tests for kept notifications: the vocabulary, the digest wording, and
the writer's refusal to fail the processing it reports on.

The audience, the digests and the routes meet a real database in
``tests/integration/api/notifications/test_kept_notifications.py``.
"""

from unittest.mock import AsyncMock, patch

import pytest
from test_utils import captured_logs

from mascope_backend.api.models.sample.files.config import (
    IN_PROGRESS,
    ProcessingStatus,
)
from mascope_backend.api.new.notifications import service
from mascope_backend.api.new.notifications.config import (
    PROCESSING_NOTIFICATIONS,
    NotificationKind,
)
from mascope_backend.socket.notifications.schemas import UserNotification


RECORD = {
    "sample_file_id": "sf-1",
    "filename": "Orbi_x.raw",
    "instrument": "Orbi",
    "datetime_utc": None,
    "uploaded_by_user_id": 7,
}


def test_every_outcome_that_needs_someone_is_kept():
    """Everything but the stages on the way and plain success."""
    kept = set(ProcessingStatus) - IN_PROGRESS - {ProcessingStatus.DONE}

    assert set(PROCESSING_NOTIFICATIONS) == kept


def test_kept_notifications_read_like_live_ones():
    """A severity the live notification knows, and a kind that fits its column."""
    for kind, severity in PROCESSING_NOTIFICATIONS.values():
        UserNotification(type=kind.value, message="m", status=severity)
        assert len(kind.value) <= 32


@pytest.mark.parametrize(
    ("kind", "one", "many"),
    [
        (
            NotificationKind.NEEDS_CHEMISTRY,
            "1 file from Orbi needs a chemistry: no ionization mode could be "
            "bound, so it has no samples.",
            "3 files from Orbi need a chemistry: no ionization mode could be "
            "bound, so they have no samples.",
        ),
        (
            NotificationKind.CALIBRATION_FAILED,
            "The m/z calibration failed for 1 file from Orbi, so it was not matched.",
            "The m/z calibration failed for 3 files from Orbi, so they were not "
            "matched.",
        ),
        (
            NotificationKind.PROCESSING_FAILED,
            "Processing failed for 1 file from Orbi.",
            "Processing failed for 3 files from Orbi.",
        ),
    ],
)
def test_a_digest_counts_its_files_in_its_sentence(kind, one, many):
    assert service.digest_message(kind, 1, "Orbi") == one
    assert service.digest_message(kind, 3, "Orbi") == many


class _Session:
    """An async session that holds nothing: the digests are mocked."""

    def __init__(self):
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin_nested(self):
        return self


def _lines(records: list) -> list[tuple[str, str]]:
    return [(r["level"].name, r["message"]) for r in records]


def test_an_instruments_key_ignores_case_and_stray_whitespace():
    """What the workspace is found by, so its digests are too."""
    assert service.instrument_key(" Orbi-Lab2 ") == "orbi-lab2"
    assert service.instrument_key(None) is None


@pytest.mark.asyncio
async def test_a_notification_that_cannot_be_kept_is_logged_not_raised():
    """The file is named at INFO, and the WARNING, which monitoring groups
    issues by, names none: an outage fails this for every file in flight."""
    add = AsyncMock(side_effect=RuntimeError("no engine"))
    emit = AsyncMock()

    with (
        patch.object(service, "async_session", side_effect=_Session),
        patch.object(service, "add_to_digests", add),
        patch.object(service, "emit_notification_changes", emit),
        captured_logs() as records,
    ):
        await service.notify_processing_outcome(
            RECORD, ProcessingStatus.NEEDS_CHEMISTRY, None
        )

    emit.assert_not_called()
    lines = _lines(records)
    warnings = [message for level, message in lines if level == "WARNING"]
    assert len(warnings) == 1 and "sf-1" not in warnings[0], lines
    assert any(level == "INFO" and "sf-1" in message for level, message in lines)


@pytest.mark.asyncio
async def test_nothing_is_emitted_where_nobody_listens():
    add = AsyncMock(return_value=[(True, {"notification_id": "n1", "user_id": 7})])
    emit = AsyncMock()

    with (
        patch.object(service, "async_session", side_effect=_Session),
        patch.object(service, "add_to_digests", add),
        patch.object(service, "emit_notification_changes", emit),
    ):
        await service.notify_processing_outcome(
            RECORD, ProcessingStatus.FAILED, None, emit=False
        )

    add.assert_awaited_once()
    emit.assert_not_called()


@pytest.mark.asyncio
async def test_a_digest_that_cannot_be_kept_leaves_the_status_alone():
    """Each part runs under a savepoint, and a failed one is only logged."""
    session = _Session()
    resolved = [(False, {"notification_id": "n2", "user_id": 7})]

    with (
        patch.object(
            service, "add_to_digests", AsyncMock(side_effect=RuntimeError("down"))
        ),
        patch.object(service, "resolve_digests", AsyncMock(return_value=resolved)),
        captured_logs() as records,
    ):
        changes = await service.keep_processing_outcome(
            session, RECORD, ProcessingStatus.FAILED, None
        )

    # The resolve still ran and reported its change.
    assert changes == resolved
    warnings = [m for level, m in _lines(records) if level == "WARNING"]
    assert len(warnings) == 1 and "sf-1" not in warnings[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "kept", "resolved"),
    [
        (ProcessingStatus.FAILED, True, True),
        (ProcessingStatus.DONE, False, True),
        (ProcessingStatus.BOUND, False, False),
    ],
)
async def test_a_status_keeps_and_resolves_what_it_should(status, kept, resolved):
    """Outcomes that need someone are kept; anything settled may resolve."""
    add, resolve = AsyncMock(return_value=[]), AsyncMock(return_value=[])

    with (
        patch.object(service, "add_to_digests", add),
        patch.object(service, "resolve_digests", resolve),
    ):
        await service.keep_processing_outcome(_Session(), RECORD, status, None)

    assert add.await_count == int(kept)
    assert resolve.await_count == int(resolved)
    if resolved:
        assert resolve.await_args.args[1] == {"orbi"}


@pytest.mark.asyncio
async def test_resolving_swallows_its_own_failure():
    with (
        patch.object(service, "async_session", side_effect=RuntimeError("gone")),
        patch.object(service, "emit_notification_changes", AsyncMock()) as emit,
        patch.object(service.runtime.logger, "opt") as opt,
    ):
        await service.resolve_processing_notifications("Orbi")

    emit.assert_not_called()
    opt.return_value.warning.assert_called_once()
