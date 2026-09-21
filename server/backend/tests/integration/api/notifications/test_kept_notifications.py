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

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
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
            delete(Notification).where(Notification.instrument == INSTRUMENT)
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == INSTRUMENT)
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
) -> dict:
    async with async_session_factory() as session:
        row = SampleFile(
            sample_file_id=gen_id(),
            filename=f"{INSTRUMENT}_{name}.raw",
            instrument=INSTRUMENT,
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


async def _digests(async_session_factory, user_id: int | None = None) -> list:
    async with async_session_factory() as session:
        stmt = select(Notification).where(Notification.instrument == INSTRUMENT)
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

    reset = await reset_interrupted_processing()
    # Only this suite's instrument: the reset takes every leftover in the
    # database, and other suites' files are not this test's to notify about.
    for record in reset["data"]["files"]:
        if record["instrument"] == INSTRUMENT:
            await service.notify_processing_outcome(
                record, ProcessingStatus.FAILED, INTERRUPTED_DETAIL, emit=False
            )

    (row,) = await _digests(async_session_factory, owner)
    assert row.payload["files"][0]["detail"] == INTERRUPTED_DETAIL
    emitted["created"].assert_not_called()


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
        "/api/notifications/read", json={"notification_ids": [row.notification_id]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"read": 1}

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
        "/api/notifications/read", json={"notification_ids": [row.notification_id]}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"read": 0}
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
    "body", [{}, {"all": True, "notification_ids": ["x"]}, {"all": False}]
)
async def test_marking_read_needs_exactly_one_selection(owner_client, body):
    resp = await owner_client.post("/api/notifications/read", json=body)

    assert resp.status_code == 422, resp.text
