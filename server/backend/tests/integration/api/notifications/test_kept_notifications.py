"""
Integration tests: processing outcomes kept as notifications.

A file of an instrument that fails, needs a chemistry or cannot be calibrated
is kept as a notification for the people answerable for the instrument: the
uploader - for a paired agent's upload, the device's sponsor - and the owners
of the instrument's acquisition workspace. One unread digest per person, kind
and instrument takes every such file until it is read, and is marked resolved
once no file is left in that state. Here the audience, the digests, the
routes and the hook in the status writer meet a real database; nothing is
sent to a browser.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.controllers.sample.files import sample_files_controller
from mascope_backend.api.controllers.sample.files.process import status
from mascope_backend.api.models.sample.files.config import ProcessingStatus
from mascope_backend.api.new.notifications import service
from mascope_backend.db import (
    AccessToken,
    AgentDevice,
    Notification,
    SampleFile,
    User,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.admin.sample_file.reset_interrupted_processing import (
    INTERRUPTED_DETAIL,
    reset_interrupted_processing,
)
from mascope_backend.db.id import gen_id


INSTRUMENT = "kept-orbi"
WORKSPACE = f"Acquisitions {INSTRUMENT}"


@pytest_asyncio.fixture(autouse=True)
async def clean_state(async_session_factory):
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(Notification).where(Notification.instrument_key == INSTRUMENT)
        )
        await session.execute(
            delete(SampleFile).where(func.lower(SampleFile.instrument) == INSTRUMENT)
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_name == WORKSPACE)
        )
        await session.execute(
            delete(AccessToken).where(AccessToken.service_name == "file-agent")
        )
        await session.execute(
            delete(User).where(User.account_type == ACCOUNT_TYPE_MACHINE)
        )
        await session.execute(
            delete(AgentDevice).where(AgentDevice.service_name == "file-agent")
        )
        await session.execute(delete(User).where(User.email.like("kept-%@test.com")))
        await session.commit()


@pytest.fixture(autouse=True)
def emitted(monkeypatch) -> dict[str, AsyncMock]:
    """Record what would be sent to browsers instead of sending it."""
    created, updated = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "emit_record_created", created)
    monkeypatch.setattr(service, "emit_record_updated", updated)
    monkeypatch.setattr(status, "emit_record_updated", AsyncMock())
    monkeypatch.setattr(sample_files_controller, "emit_record_deleted", AsyncMock())
    return {"created": created, "updated": updated}


async def _workspace(async_session_factory, owner_ids: list[int]) -> None:
    """The instrument's acquisition workspace, owned by ``owner_ids``."""
    async with async_session_factory() as session:
        workspace = Workspace(
            workspace_id=gen_id(16),
            workspace_name=WORKSPACE,
            workspace_status="active",
            is_system=True,
        )
        session.add(workspace)
        await session.flush()
        for user_id in owner_ids:
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(16),
                    workspace_id=workspace.workspace_id,
                    user_id=user_id,
                    workspace_role="owner",
                )
            )
        await session.commit()


async def _person(async_session_factory, name: str, active: bool = True) -> int:
    async with async_session_factory() as session:
        user = User(
            email=f"kept-{name}@test.com",
            username=f"kept_{name}",
            hashed_password="123456",
            is_active=active,
        )
        session.add(user)
        await session.commit()
        return user.id


async def _file(
    async_session_factory,
    name: str,
    uploaded_by: int | None = None,
    processing_status: str | None = None,
    instrument: str = INSTRUMENT,
) -> dict:
    async with async_session_factory() as session:
        row = SampleFile(
            sample_file_id=gen_id(),
            filename=f"{instrument}_{name}.raw",
            instrument=instrument,
            instrument_type="orbi",
            datetime=datetime(2026, 9, 1, 12, 0, 0),
            datetime_utc=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
            length=60.0,
            range=[50.0, 500.0],
            polarity="-",
            uploaded_by_user_id=uploaded_by,
            processing_status=processing_status,
        )
        session.add(row)
        await session.commit()
        return row.to_dict()


async def _set_status(async_session_factory, record: dict, value: str) -> None:
    async with async_session_factory() as session:
        await session.execute(
            update(SampleFile)
            .where(SampleFile.sample_file_id == record["sample_file_id"])
            .values(processing_status=value)
        )
        await session.commit()


def _seen(row: Notification) -> dict:
    """A notification as a reader saw it, for marking it read."""
    return {
        "notification_id": row.notification_id,
        "updated_utc": row.updated_utc.isoformat(),
    }


async def _digests(async_session_factory, user_id: int | None = None) -> list:
    async with async_session_factory() as session:
        stmt = select(Notification).where(Notification.instrument_key == INSTRUMENT)
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)
        return (await session.scalars(stmt.order_by(Notification.created_utc))).all()


# ---------------------------------------------------------------------------
# Who is told
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_agent_upload_is_kept_for_its_sponsor_and_the_owners(
    async_session_factory, test_users, provision_device
):
    sponsor, owner = test_users["editor"].id, test_users["owner"].id
    _device_id, machine, _token = await provision_device(sponsor)
    await _workspace(async_session_factory, [owner, machine.id])
    record = await _file(async_session_factory, "agent", uploaded_by=machine.id)

    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, "Boom.")

    rows = await _digests(async_session_factory)
    # Nobody signs in as the machine, so its sponsor reads it instead - and the
    # machine, an owner of the workspace its first upload created, reads nothing.
    assert {row.user_id for row in rows} == {sponsor, owner}
    assert all(row.kind == "processing_failed" for row in rows)
    assert all(row.severity == "error" for row in rows)


@pytest.mark.asyncio
async def test_a_persons_upload_is_kept_for_them_and_the_owners(
    async_session_factory, test_users
):
    uploader, owner = test_users["guest"].id, test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "browser", uploaded_by=uploader)

    await service.notify_processing_outcome(
        record, ProcessingStatus.NEEDS_CHEMISTRY, "No token."
    )

    rows = await _digests(async_session_factory)
    assert {row.user_id for row in rows} == {uploader, owner}
    assert all(row.severity == "warning" for row in rows)


@pytest.mark.asyncio
async def test_workspace_members_who_are_not_owners_are_not_told(
    async_session_factory, test_users
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    async with async_session_factory() as session:
        workspace_id = await session.scalar(
            select(Workspace.workspace_id).where(Workspace.workspace_name == WORKSPACE)
        )
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=workspace_id,
                user_id=test_users["editor"].id,
                workspace_role="editor",
            )
        )
        await session.commit()
    record = await _file(async_session_factory, "unowned")

    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    assert {row.user_id for row in await _digests(async_session_factory)} == {owner}


@pytest.mark.asyncio
async def test_inactive_people_are_not_told(async_session_factory, test_users):
    owner = test_users["owner"].id
    gone = await _person(async_session_factory, "gone", active=False)
    await _workspace(async_session_factory, [owner, gone])
    record = await _file(async_session_factory, "inactive", uploaded_by=gone)

    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    assert {row.user_id for row in await _digests(async_session_factory)} == {owner}


@pytest.mark.asyncio
async def test_an_instrument_nobody_answers_for_keeps_nothing(async_session_factory):
    record = await _file(async_session_factory, "orphan")

    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    assert await _digests(async_session_factory) == []


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_later_files_join_the_open_digest(
    async_session_factory, test_users, emitted
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    first = await _file(async_session_factory, "first")
    second = await _file(async_session_factory, "second")

    await service.notify_processing_outcome(
        first, ProcessingStatus.NEEDS_CHEMISTRY, "No token in first."
    )
    await service.notify_processing_outcome(
        second, ProcessingStatus.NEEDS_CHEMISTRY, "No token in second."
    )

    (row,) = await _digests(async_session_factory, owner)
    assert row.count == 2
    assert row.message == (
        f"2 files from {INSTRUMENT} need a chemistry: no ionization mode could be "
        "bound, so they have no samples."
    )
    assert row.payload["status"] == "needs_chemistry"
    assert [f["filename"] for f in row.payload["files"]] == [
        second["filename"],
        first["filename"],
    ]
    assert row.payload["files"][0]["detail"] == "No token in second."
    assert row.payload["files"][0]["datetime_utc"].startswith("2026-09-01T12:00:00")
    # Opened once, then grown; each change goes to the owner's own room.
    assert emitted["created"].await_count == 1
    assert emitted["updated"].await_count == 1
    assert emitted["updated"].await_args.kwargs["room"] == f"user-{owner}"
    assert emitted["updated"].await_args.kwargs["record_type"] == "notification"


@pytest.mark.asyncio
async def test_the_same_file_again_is_not_counted_twice(
    async_session_factory, test_users
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "again")

    for _ in range(2):
        await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    (row,) = await _digests(async_session_factory, owner)
    assert row.count == 1
    assert len(row.payload["files"]) == 1
    assert row.message == f"Processing failed for 1 file from {INSTRUMENT}."


@pytest.mark.asyncio
async def test_kinds_keep_digests_of_their_own(async_session_factory, test_users):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "kinds")

    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)
    await service.notify_processing_outcome(
        record, ProcessingStatus.CALIBRATION_FAILED, None
    )

    kinds = {row.kind for row in await _digests(async_session_factory, owner)}
    assert kinds == {"processing_failed", "calibration_failed"}


@pytest.mark.asyncio
async def test_a_digest_names_its_latest_files_only(async_session_factory, test_users):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    for index in range(service.DIGEST_FILES + 2):
        record = await _file(async_session_factory, f"many{index:02d}")
        await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    (row,) = await _digests(async_session_factory, owner)
    assert row.count == service.DIGEST_FILES + 2
    assert len(row.payload["files"]) == service.DIGEST_FILES
    assert row.payload["files"][0]["filename"] == record["filename"]


@pytest.mark.asyncio
async def test_a_read_digest_takes_no_more_files(async_session_factory, test_users):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    first = await _file(async_session_factory, "before")
    await service.notify_processing_outcome(first, ProcessingStatus.FAILED, None)
    await service.mark_notifications_read(owner, None)

    second = await _file(async_session_factory, "after")
    await service.notify_processing_outcome(second, ProcessingStatus.FAILED, None)

    rows = await _digests(async_session_factory, owner)
    assert [row.count for row in rows] == [1, 1]
    assert rows[0].read_utc is not None and rows[1].read_utc is None
    assert rows[1].payload["files"][0]["filename"] == second["filename"]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_digest_resolves_once_no_file_is_left_in_its_state(
    async_session_factory, test_users, emitted
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "fixed", processing_status="failed")
    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    await service.resolve_processing_notifications(INSTRUMENT)
    (row,) = await _digests(async_session_factory, owner)
    assert row.resolved_utc is None

    await _set_status(async_session_factory, record, "done")
    emitted["updated"].reset_mock()
    await service.resolve_processing_notifications(INSTRUMENT)

    (row,) = await _digests(async_session_factory, owner)
    assert row.resolved_utc is not None
    assert row.read_utc is None
    emitted["updated"].assert_awaited_once()


@pytest.mark.asyncio
async def test_a_digest_stays_open_while_one_of_its_files_is_left(
    async_session_factory, test_users
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    first = await _file(async_session_factory, "a", processing_status="needs_chemistry")
    second = await _file(
        async_session_factory, "b", processing_status="needs_chemistry"
    )
    for record in (first, second):
        await service.notify_processing_outcome(
            record, ProcessingStatus.NEEDS_CHEMISTRY, None
        )

    await _set_status(async_session_factory, first, "done")
    await service.resolve_processing_notifications(INSTRUMENT)

    (row,) = await _digests(async_session_factory, owner)
    assert row.resolved_utc is None


@pytest.mark.asyncio
async def test_a_new_file_reopens_a_resolved_digest(async_session_factory, test_users):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    first = await _file(async_session_factory, "x", processing_status="done")
    await service.notify_processing_outcome(first, ProcessingStatus.FAILED, None)
    await service.resolve_processing_notifications(INSTRUMENT)

    second = await _file(async_session_factory, "y", processing_status="failed")
    await service.notify_processing_outcome(second, ProcessingStatus.FAILED, None)

    (row,) = await _digests(async_session_factory, owner)
    assert row.resolved_utc is None
    assert row.count == 2


# ---------------------------------------------------------------------------
# The status writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_status_is_kept_and_a_done_one_resolves_it(
    async_session_factory, test_users
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "hook")

    await status.record_processing_status(
        record["sample_file_id"], ProcessingStatus.FAILED, "The peak store is corrupt"
    )

    (row,) = await _digests(async_session_factory, owner)
    assert row.kind == "processing_failed"
    assert row.payload["files"][0]["detail"] == "The peak store is corrupt"
    assert row.resolved_utc is None

    await status.record_processing_status(
        record["sample_file_id"], ProcessingStatus.DONE, "Matched 1 sample."
    )

    (row,) = await _digests(async_session_factory, owner)
    assert row.resolved_utc is not None


@pytest.mark.asyncio
async def test_statuses_on_the_way_keep_nothing(async_session_factory, test_users):
    await _workspace(async_session_factory, [test_users["owner"].id])
    record = await _file(async_session_factory, "busy")

    for stage in (ProcessingStatus.BOUND, ProcessingStatus.CALIBRATED):
        await status.record_processing_status(record["sample_file_id"], stage, None)

    assert await _digests(async_session_factory) == []


@pytest.mark.asyncio
async def test_a_restart_keeps_what_it_interrupted(
    async_session_factory, test_users, emitted
):
    """What startup does with the files the reset marks failed."""
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    await _file(async_session_factory, "cut-short", processing_status="bound")

    await reset_interrupted_processing()

    (row,) = await _digests(async_session_factory, owner)
    assert row.kind == "processing_failed"
    assert row.payload["files"][0]["detail"] == INTERRUPTED_DETAIL
    # No browser is connected to the process that starts the server.
    emitted["created"].assert_not_called()


@pytest.mark.asyncio
async def test_a_restart_resolves_what_its_files_left(
    async_session_factory, test_users
):
    """A file re-processed out of a digest when the restart hit has moved on."""
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(
        async_session_factory, "redo", processing_status="needs_chemistry"
    )
    await service.notify_processing_outcome(
        record, ProcessingStatus.NEEDS_CHEMISTRY, None
    )
    await _set_status(async_session_factory, record, "queued")

    await reset_interrupted_processing()

    rows = {row.kind: row for row in await _digests(async_session_factory, owner)}
    assert rows["needs_chemistry"].resolved_utc is not None
    assert rows["processing_failed"].resolved_utc is None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_person_lists_only_their_own_notifications(
    async_session_factory, test_users, owner_client
):
    owner, editor = test_users["owner"].id, test_users["editor"].id
    await _workspace(async_session_factory, [owner, editor])
    record = await _file(async_session_factory, "listed")
    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, "Boom.")

    resp = await owner_client.get("/api/notifications")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    mine = [row for row in body["data"] if row["instrument"] == INSTRUMENT]
    assert [row["user_id"] for row in mine] == [owner]
    assert body["unread"] >= 1
    assert mine[0]["payload"]["files"][0]["detail"] == "Boom."


@pytest.mark.asyncio
async def test_read_notifications_are_listed_only_when_asked_for(
    async_session_factory, test_users, owner_client
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "read")
    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)
    (row,) = await _digests(async_session_factory, owner)

    resp = await owner_client.post(
        "/api/notifications/read", json={"notifications": [_seen(row)]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {
        "read": 1,
        "notification_ids": [row.notification_id],
    }

    unread = (await owner_client.get("/api/notifications")).json()["data"]
    everything = (
        await owner_client.get("/api/notifications", params={"include_read": "true"})
    ).json()["data"]
    ids = row.notification_id
    assert ids not in {r["notification_id"] for r in unread}
    assert ids in {r["notification_id"] for r in everything}


@pytest.mark.asyncio
async def test_nobody_marks_another_persons_notifications_read(
    async_session_factory, test_users, editor_client
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "theirs")
    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)
    (row,) = await _digests(async_session_factory, owner)

    resp = await editor_client.post(
        "/api/notifications/read", json={"notifications": [_seen(row)]}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"read": 0, "notification_ids": []}
    (row,) = await _digests(async_session_factory, owner)
    assert row.read_utc is None


@pytest.mark.asyncio
async def test_all_of_a_persons_notifications_are_marked_read_at_once(
    async_session_factory, test_users, owner_client
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "all")
    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)
    await service.notify_processing_outcome(
        record, ProcessingStatus.CALIBRATION_FAILED, None
    )

    resp = await owner_client.post("/api/notifications/read", json={"all": True})

    assert resp.status_code == 200, resp.text
    async with async_session_factory() as session:
        unread = await session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == owner, Notification.read_utc.is_(None))
        )
    assert unread == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({}, "Give the notifications to mark read, or all"),
        ({"all": False}, "Give the notifications to mark read, or all"),
        (
            {
                "all": True,
                "notifications": [
                    {"notification_id": "x", "updated_utc": "2026-09-21T10:00:00Z"}
                ],
            },
            "Give notifications or all, not both",
        ),
    ],
)
async def test_marking_read_needs_exactly_one_selection(owner_client, body, message):
    resp = await owner_client.post("/api/notifications/read", json=body)

    assert resp.status_code == 422, resp.text
    assert message in resp.text


# ---------------------------------------------------------------------------
# Review round: identity, resolution, ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_digest_resolves_on_its_own_files_not_the_instruments(
    async_session_factory, test_users
):
    """An old file nobody re-processes does not hold a later digest open."""
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    old = await _file(async_session_factory, "old", processing_status="failed")
    await service.notify_processing_outcome(old, ProcessingStatus.FAILED, None)
    await service.mark_notifications_read(owner, None)
    new = await _file(async_session_factory, "new", processing_status="failed")
    await service.notify_processing_outcome(new, ProcessingStatus.FAILED, None)

    await _set_status(async_session_factory, new, "done")
    await service.resolve_processing_notifications(INSTRUMENT)

    earlier, later = await _digests(async_session_factory, owner)
    assert later.resolved_utc is not None
    assert earlier.resolved_utc is None


@pytest.mark.asyncio
async def test_a_file_again_is_not_counted_twice_once_it_is_off_the_list(
    async_session_factory, test_users
):
    """The digest names its latest files only, but knows every one it took."""
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    first = await _file(async_session_factory, "first")
    await service.notify_processing_outcome(first, ProcessingStatus.FAILED, None)
    for index in range(service.DIGEST_FILES):
        record = await _file(async_session_factory, f"later{index:02d}")
        await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    await service.notify_processing_outcome(first, ProcessingStatus.FAILED, "Again.")

    (row,) = await _digests(async_session_factory, owner)
    assert row.count == service.DIGEST_FILES + 1
    assert row.payload["files"][0]["detail"] == "Again."


@pytest.mark.asyncio
async def test_deleting_a_digests_files_resolves_it(async_session_factory, test_users):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "gone", processing_status="failed")
    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    await sample_files_controller.delete_sample_file_db_record(record["sample_file_id"])

    (row,) = await _digests(async_session_factory, owner)
    assert row.resolved_utc is not None


@pytest.mark.asyncio
async def test_case_variants_of_an_instrument_share_a_digest(
    async_session_factory, test_users
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    lower = await _file(async_session_factory, "lower")
    upper = await _file(async_session_factory, "upper", instrument=INSTRUMENT.upper())

    for record in (lower, upper):
        await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)

    (row,) = await _digests(async_session_factory, owner)
    assert row.count == 2
    assert row.instrument == INSTRUMENT


@pytest.mark.asyncio
async def test_a_digest_that_took_a_file_since_it_was_seen_stays_unread(
    async_session_factory, test_users, owner_client
):
    """Marking read acknowledges what the reader saw, not what came after."""
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    first = await _file(async_session_factory, "seen")
    await service.notify_processing_outcome(first, ProcessingStatus.FAILED, None)
    (seen,) = await _digests(async_session_factory, owner)
    second = await _file(async_session_factory, "unseen")
    await service.notify_processing_outcome(second, ProcessingStatus.FAILED, None)

    stale = await owner_client.post(
        "/api/notifications/read", json={"notifications": [_seen(seen)]}
    )
    (current,) = await _digests(async_session_factory, owner)
    fresh = await owner_client.post(
        "/api/notifications/read", json={"notifications": [_seen(current)]}
    )

    assert stale.json()["data"] == {"read": 0, "notification_ids": []}
    assert fresh.json()["data"]["notification_ids"] == [current.notification_id]


@pytest.mark.asyncio
async def test_every_change_to_a_digest_raises_its_version(
    async_session_factory, test_users
):
    """So a browser can tell which of two copies of a row is newer."""
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    record = await _file(async_session_factory, "versioned", processing_status="failed")

    versions = []
    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)
    versions.append((await _digests(async_session_factory, owner))[0].version)
    await service.notify_processing_outcome(record, ProcessingStatus.FAILED, "Again.")
    versions.append((await _digests(async_session_factory, owner))[0].version)
    await _set_status(async_session_factory, record, "done")
    await service.resolve_processing_notifications(INSTRUMENT)
    versions.append((await _digests(async_session_factory, owner))[0].version)
    await service.mark_notifications_read(owner, None)
    versions.append((await _digests(async_session_factory, owner))[0].version)

    assert versions == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_the_status_stands_when_its_digest_cannot_be_kept(
    async_session_factory, test_users, monkeypatch
):
    """The digest is written under a savepoint of the status's transaction.

    A database error aborts the transaction it happens in; without the
    savepoint, a digest addressed to nobody real would cost the status too.
    """
    await _workspace(async_session_factory, [test_users["owner"].id])
    record = await _file(async_session_factory, "savepoint")
    monkeypatch.setattr(
        service, "processing_audience", AsyncMock(return_value={2_000_000_000})
    )

    await status.record_processing_status(
        record["sample_file_id"], ProcessingStatus.FAILED, "Boom."
    )

    async with async_session_factory() as session:
        stored = await session.get(SampleFile, record["sample_file_id"])
    assert stored.processing_status == "failed"
    assert await _digests(async_session_factory) == []


@pytest.mark.asyncio
async def test_concurrent_outcomes_of_an_instrument_share_one_digest(
    async_session_factory, test_users
):
    """Two workers opening the same digest at once wait for each other."""
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    records = [await _file(async_session_factory, f"race{i}") for i in range(4)]

    await asyncio.gather(
        *(
            service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)
            for record in records
        )
    )

    (row,) = await _digests(async_session_factory, owner)
    assert row.count == len(records)


@pytest.mark.asyncio
async def test_notifications_read_long_ago_are_purged(
    async_session_factory, test_users
):
    owner = test_users["owner"].id
    await _workspace(async_session_factory, [owner])
    for name, read_days_ago in (("old", 100), ("recent", 1)):
        record = await _file(async_session_factory, name)
        await service.notify_processing_outcome(record, ProcessingStatus.FAILED, None)
        async with async_session_factory() as session:
            await session.execute(
                update(Notification)
                .where(
                    Notification.user_id == owner,
                    Notification.instrument_key == INSTRUMENT,
                    Notification.read_utc.is_(None),
                )
                .values(
                    read_utc=datetime.now(timezone.utc) - timedelta(days=read_days_ago)
                )
            )
            await session.commit()

    await service.purge_read_notifications()

    (row,) = await _digests(async_session_factory, owner)
    assert row.payload["files"][0]["filename"].endswith("_recent.raw")
