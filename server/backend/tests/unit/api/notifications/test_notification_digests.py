"""
Unit tests for kept notifications: the vocabulary, the digest wording, and
the writer's refusal to fail the processing it reports on.

The audience, the digests and the routes meet a real database in
``tests/integration/api/notifications/test_kept_notifications.py``.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

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


@pytest.mark.asyncio
async def test_two_workers_opening_one_digest_settle_on_it():
    """The loser of the unique index adds its file to the winner's digest."""
    written = [(True, {"notification_id": "n1", "user_id": 7})]
    add = AsyncMock(side_effect=[IntegrityError("insert", {}, Exception()), written])
    emit = AsyncMock()

    with (
        patch.object(service, "_add_to_digests", add),
        patch.object(service, "_emit", emit),
    ):
        await service.notify_processing_outcome(RECORD, ProcessingStatus.FAILED, None)

    assert add.await_count == 2
    emit.assert_awaited_once_with(written)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("no engine"),
        [IntegrityError("a", {}, Exception()), IntegrityError("b", {}, Exception())],
    ],
)
async def test_a_notification_that_cannot_be_kept_is_logged_not_raised(failure):
    add = AsyncMock(side_effect=failure)
    emit = AsyncMock()

    with (
        patch.object(service, "_add_to_digests", add),
        patch.object(service, "_emit", emit),
        patch.object(service.runtime.logger, "opt") as opt,
    ):
        await service.notify_processing_outcome(
            RECORD, ProcessingStatus.NEEDS_CHEMISTRY, None
        )

    emit.assert_not_called()
    warning = opt.return_value.warning
    warning.assert_called_once()
    assert "sf-1" in warning.call_args.args[0]


@pytest.mark.asyncio
async def test_nothing_is_emitted_where_nobody_listens():
    add = AsyncMock(return_value=[(True, {"notification_id": "n1", "user_id": 7})])
    emit = AsyncMock()

    with (
        patch.object(service, "_add_to_digests", add),
        patch.object(service, "_emit", emit),
    ):
        await service.notify_processing_outcome(
            RECORD, ProcessingStatus.FAILED, None, emit=False
        )

    add.assert_awaited_once()
    emit.assert_not_called()


@pytest.mark.asyncio
async def test_resolving_swallows_its_own_failure():
    with (
        patch.object(service, "async_session", side_effect=RuntimeError("gone")),
        patch.object(service, "_emit", AsyncMock()) as emit,
        patch.object(service.runtime.logger, "opt") as opt,
    ):
        await service.resolve_processing_notifications("Orbi")

    emit.assert_not_called()
    opt.return_value.warning.assert_called_once()
