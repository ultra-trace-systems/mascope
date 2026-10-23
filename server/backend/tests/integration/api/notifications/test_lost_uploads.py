"""
Integration tests: uploads the file converter could not store, kept for the admins.

A file the converter finds with no upload registered for it - left in the
streams folder across a converter restart - can never be stored, and its
uploader cannot be told (#2535). It is named instead to every active global
admin and owner, in one digest each, once per file: as the converter sets it
aside, and at a server start that finds it in ``failed_files`` with no
record. Here the audience, the digests and the startup scan meet a real
database; nothing is sent to a browser.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.new.notifications import lost_uploads, service
from mascope_backend.db import Notification, SampleFile, User
from mascope_backend.db.admin.sample_file import report_unstored_failed_files as scan
from mascope_backend.db.id import gen_id


KIND = "upload_lost"
INSTRUMENT = "lost-orbi"


@pytest_asyncio.fixture(autouse=True)
async def clean_state(async_session_factory):
    yield
    async with async_session_factory() as session:
        await session.execute(delete(Notification).where(Notification.kind == KIND))
        await session.execute(
            delete(SampleFile).where(SampleFile.instrument == INSTRUMENT)
        )
        await session.execute(delete(User).where(User.email.like("lost-%@test.com")))
        await session.commit()


@pytest.fixture(autouse=True)
def emitted(monkeypatch) -> dict[str, AsyncMock]:
    """Record what would be sent to browsers instead of sending it."""
    created, updated = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "emit_record_created", created)
    monkeypatch.setattr(service, "emit_record_updated", updated)
    return {"created": created, "updated": updated}


async def _account(async_session_factory, name: str, **fields) -> int:
    async with async_session_factory() as session:
        user = User(
            email=f"lost-{name}@test.com",
            username=f"lost_{name}",
            hashed_password="123456",
            **fields,
        )
        session.add(user)
        await session.commit()
        return user.id


async def _digests(async_session_factory, user_id: int) -> list[Notification]:
    async with async_session_factory() as session:
        return (
            await session.scalars(
                select(Notification)
                .where(Notification.kind == KIND, Notification.user_id == user_id)
                .order_by(Notification.created_utc)
            )
        ).all()


async def _read(async_session_factory, user_id: int) -> None:
    async with async_session_factory() as session:
        await session.execute(
            update(Notification)
            .where(Notification.kind == KIND, Notification.user_id == user_id)
            .values(read_utc=datetime.now(timezone.utc))
        )
        await session.commit()


def _lost(name: str) -> lost_uploads.LostUpload:
    return (name, INSTRUMENT, lost_uploads.UNREGISTERED_DETAIL)


# ---------------------------------------------------------------------------
# Who is told
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_admins_and_owners_are_told(async_session_factory, test_users):
    await lost_uploads.notify_lost_uploads([_lost(f"{INSTRUMENT}_a.raw")])

    for role in ("admin", "owner"):
        (row,) = await _digests(async_session_factory, test_users[role].id)
        assert row.severity == "error"
        assert row.instrument is None
    for role in ("editor", "guest"):
        assert await _digests(async_session_factory, test_users[role].id) == []


@pytest.mark.asyncio
async def test_a_superuser_is_told_whatever_their_role(async_session_factory, roles):
    superuser = await _account(
        async_session_factory,
        "super",
        is_superuser=True,
        role_id=roles["guest"].role_id,
    )

    await lost_uploads.notify_lost_uploads([_lost(f"{INSTRUMENT}_a.raw")])

    assert len(await _digests(async_session_factory, superuser)) == 1


@pytest.mark.asyncio
async def test_inactive_and_machine_accounts_are_not_told(async_session_factory, roles):
    owner = roles["owner"].role_id
    gone = await _account(async_session_factory, "gone", is_active=False, role_id=owner)
    machine = await _account(
        async_session_factory,
        "machine",
        account_type=ACCOUNT_TYPE_MACHINE,
        role_id=owner,
    )

    await lost_uploads.notify_lost_uploads([_lost(f"{INSTRUMENT}_a.raw")])

    assert await _digests(async_session_factory, gone) == []
    assert await _digests(async_session_factory, machine) == []


# ---------------------------------------------------------------------------
# What they read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_file_is_named_in_the_message(async_session_factory, test_users):
    name = f"{INSTRUMENT}_a.raw"

    await lost_uploads.notify_lost_uploads([_lost(name)])

    (row,) = await _digests(async_session_factory, test_users["admin"].id)
    assert row.count == 1
    assert name in row.message
    assert "upload it again" in row.message
    # It may have been told: the message makes no claim about the uploader
    assert "uploader" not in row.message
    assert row.payload["files"] == [
        {
            "sample_file_id": None,
            "filename": name,
            "instrument": INSTRUMENT,
            "datetime_utc": None,
            "detail": lost_uploads.UNREGISTERED_DETAIL,
        }
    ]


@pytest.mark.asyncio
async def test_more_files_join_the_open_digest_newest_first(
    async_session_factory, test_users
):
    first, second = f"{INSTRUMENT}_a.raw", f"{INSTRUMENT}_b.raw"

    await lost_uploads.notify_lost_uploads([_lost(first)])
    await lost_uploads.notify_lost_uploads([_lost(second)])

    (row,) = await _digests(async_session_factory, test_users["admin"].id)
    assert row.count == 2
    assert row.message.startswith("2 uploaded files were set aside")
    assert "kept them in its failed_files folder" in row.message
    assert [entry["filename"] for entry in row.payload["files"]] == [second, first]
    assert row.version == 2

    # The digest keeps every name it took, not only the latest
    await lost_uploads.notify_lost_uploads([_lost(first)])
    (row,) = await _digests(async_session_factory, test_users["admin"].id)
    assert row.payload["filenames"] == [first, second]
    assert row.count == 2


@pytest.mark.asyncio
async def test_a_digest_lists_its_latest_files_and_counts_them_all(
    async_session_factory, test_users
):
    names = [f"{INSTRUMENT}_{n:02d}.raw" for n in range(service.DIGEST_FILES + 1)]

    await lost_uploads.notify_lost_uploads([_lost(name) for name in names])

    (row,) = await _digests(async_session_factory, test_users["admin"].id)
    assert row.count == len(names)
    assert [entry["filename"] for entry in row.payload["files"]] == list(
        reversed(names)
    )[: service.DIGEST_FILES]
    assert row.payload["filenames"] == names


@pytest.mark.asyncio
async def test_a_file_is_named_once(async_session_factory, test_users):
    name = f"{INSTRUMENT}_a.raw"

    await lost_uploads.notify_lost_uploads([_lost(name), _lost(name)])
    await lost_uploads.notify_lost_uploads([_lost(name)])

    (row,) = await _digests(async_session_factory, test_users["admin"].id)
    assert row.count == 1
    assert row.version == 1


@pytest.mark.asyncio
async def test_a_file_named_in_a_read_digest_is_not_named_again_once(
    async_session_factory, test_users
):
    admin = test_users["admin"].id
    await lost_uploads.notify_lost_uploads([_lost(f"{INSTRUMENT}_a.raw")])
    await _read(async_session_factory, admin)

    await lost_uploads.notify_lost_uploads([_lost(f"{INSTRUMENT}_a.raw")], once=True)
    assert len(await _digests(async_session_factory, admin)) == 1

    # A new file after the read opens a new digest
    await lost_uploads.notify_lost_uploads([_lost(f"{INSTRUMENT}_b.raw")])
    rows = await _digests(async_session_factory, admin)
    assert len(rows) == 2
    assert rows[1].read_utc is None
    assert rows[1].count == 1


@pytest.mark.asyncio
async def test_a_second_loss_of_a_name_after_a_read_is_named_again(
    async_session_factory, test_users
):
    """Uploaded again under the same name and lost again: a new loss."""
    admin = test_users["admin"].id
    name = f"{INSTRUMENT}_a.raw"
    await lost_uploads.notify_lost_uploads([_lost(name)])
    await _read(async_session_factory, admin)

    await lost_uploads.notify_lost_uploads([_lost(name)])

    rows = await _digests(async_session_factory, admin)
    assert len(rows) == 2
    assert rows[1].read_utc is None
    assert rows[1].payload["filenames"] == [name]


@pytest.mark.asyncio
async def test_browsers_are_told_unless_asked_not_to(
    async_session_factory, test_users, emitted
):
    await lost_uploads.notify_lost_uploads([_lost(f"{INSTRUMENT}_a.raw")])
    rooms = {call.kwargs["room"] for call in emitted["created"].call_args_list}
    assert f"user-{test_users['admin'].id}" in rooms

    emitted["created"].reset_mock()
    await lost_uploads.notify_lost_uploads([_lost(f"{INSTRUMENT}_b.raw")], emit=False)
    emitted["created"].assert_not_called()
    emitted["updated"].assert_not_called()


# ---------------------------------------------------------------------------
# The startup scan of failed_files
# ---------------------------------------------------------------------------


async def _stored(async_session_factory, filename: str) -> None:
    async with async_session_factory() as session:
        session.add(
            SampleFile(
                sample_file_id=gen_id(),
                filename=filename,
                instrument=INSTRUMENT,
                instrument_type="orbi",
                datetime=datetime(2026, 9, 1, 12, 0, 0),
                datetime_utc=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
                length=60.0,
                range=[50.0, 500.0],
                polarity="-",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_start_names_the_files_with_no_record(
    async_session_factory, test_users, tmp_path, emitted
):
    folder = tmp_path / "failed_files"
    folder.mkdir()
    for name in ("lost-orbi_kept.raw", "lost-orbi_gone.raw", "lost-orbi_gone.h5"):
        (folder / name).write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"x")
    # The Thermo reader stored it with its acquisition time put in
    await _stored(async_session_factory, "lost-orbi_2026.09.01-12h00m00s_kept")

    result = await scan.report_unstored_failed_files(str(folder))

    assert result["data"]["files"] == ["lost-orbi_gone.h5", "lost-orbi_gone.raw"]
    (row,) = await _digests(async_session_factory, test_users["owner"].id)
    assert row.payload["filenames"] == ["lost-orbi_gone.h5", "lost-orbi_gone.raw"]
    assert {entry["detail"] for entry in row.payload["files"]} == {
        lost_uploads.FOUND_DETAIL
    }
    assert {entry["instrument"] for entry in row.payload["files"]} == {INSTRUMENT}
    # No browser is connected to the process that starts the server.
    emitted["created"].assert_not_called()


@pytest.mark.asyncio
async def test_a_file_the_converter_reported_is_not_named_again_at_a_start(
    async_session_factory, test_users, tmp_path
):
    folder = tmp_path / "failed_files"
    folder.mkdir()
    (folder / "lost-orbi_a.raw").write_bytes(b"x")
    await lost_uploads.notify_lost_uploads([_lost("lost-orbi_a.raw")])

    await scan.report_unstored_failed_files(str(folder))
    await scan.report_unstored_failed_files(str(folder))

    (row,) = await _digests(async_session_factory, test_users["owner"].id)
    assert row.count == 1
    assert row.payload["files"][0]["detail"] == lost_uploads.UNREGISTERED_DETAIL

    # Nor once the digest is read: the file is still in the folder
    await _read(async_session_factory, test_users["owner"].id)
    await scan.report_unstored_failed_files(str(folder))
    assert len(await _digests(async_session_factory, test_users["owner"].id)) == 1


@pytest.mark.asyncio
async def test_a_start_looks_up_a_name_with_a_leading_underscore_by_it(
    async_session_factory, test_users, tmp_path
):
    folder = tmp_path / "failed_files"
    folder.mkdir()
    (folder / "_lost-orbi.raw").write_bytes(b"x")
    await _stored(async_session_factory, "lost-orbi_other")

    async with async_session_factory() as session:
        assert await scan._stored_names(session, ["_lost-orbi.raw"]) == set()

    result = await scan.report_unstored_failed_files(str(folder))
    assert result["data"]["files"] == ["_lost-orbi.raw"]


@pytest.mark.asyncio
async def test_a_start_finds_the_record_of_a_name_with_a_leading_underscore(
    async_session_factory, test_users, tmp_path
):
    folder = tmp_path / "failed_files"
    folder.mkdir()
    (folder / "_lost-orbi.raw").write_bytes(b"x")
    # Both readers put the timestamp in after the first underscore
    await _stored(async_session_factory, "_2026.09.01-12h00m00s_lost-orbi")

    result = await scan.report_unstored_failed_files(str(folder))

    assert result["data"]["files"] == []
    assert await _digests(async_session_factory, test_users["owner"].id) == []


@pytest.mark.asyncio
async def test_a_start_with_no_failed_files_folder_names_nothing(
    async_session_factory, test_users, tmp_path
):
    result = await scan.report_unstored_failed_files(str(tmp_path / "missing"))

    assert result["status"] == "success"
    assert result["data"]["files"] == []
    assert await _digests(async_session_factory, test_users["owner"].id) == []


@pytest.mark.asyncio
async def test_a_start_reads_the_converters_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(scan.runtime.config, "filestreams", str(tmp_path))

    assert scan.failed_files_folder() == str(tmp_path / "failed_files")
