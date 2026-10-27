"""
Uploads the file converter set aside without storing them, kept for the admins.

The converter writes a file's records with its uploader's credential, which
only the upload's ``file_context`` event carries and which it holds in memory
alone. A converter restarted between the upload and the conversion has lost
it, so a file it finds left in the streams folder can never be stored: it is
moved to ``failed_files`` and nothing retries it (#2535). Its uploader cannot
be told either, since every notification the converter sends on someone's
behalf is checked against that same credential.

So the people who can act on the deployment are told instead: every active
global admin and owner (:func:`lost_upload_audience`), with the file's name,
so it can be uploaded again. They hear of a file from two places: the
converter, as it sets the file aside (the ``upload_lost`` event), and the
backend's startup, which looks through ``failed_files`` for files no record
was made of (``report_unstored_failed_files``) and so also finds those the
converter could not report.

Each person has one digest of the kind ``upload_lost``, about no instrument,
which takes every such file until it is read; the next file opens a new one.
The digests keep every name they took (``payload.filenames``), read ones
included. The startup scan names a file to a person once, against all of
them, so a file still sitting in ``failed_files`` at the next start is not
reported again; a read digest is purged after ``READ_RETENTION_DAYS``, after
which a file still there is reported once more. The converter's event is
checked against the open digest alone: a file uploaded again under the same
name and lost again is a new loss, and is named again. Nothing marks these
resolved: no record will ever settle them, so they stay until read.

Not every file the startup scan finds was lost silently: a file the
converter could not read also lands in ``failed_files`` with no record, and
its uploader was told why. So the message claims nothing about the uploader;
each file's ``detail`` says what is known of it.
"""

from datetime import datetime, timezone

from sqlalchemy import func, or_, select

from mascope_backend.accounts import ACCOUNT_TYPE_PERSON
from mascope_backend.api.new.notifications.config import (
    DIGEST_FILES,
    NotificationKind,
)
from mascope_backend.api.new.notifications.service import (
    Change,
    emit_notification_changes,
)
from mascope_backend.db import Notification, User, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.roles import ROLE_ACCESS_LEVELS
from mascope_backend.runtime import runtime


#: A file the converter set aside as it found no upload registered for it.
UNREGISTERED_DETAIL = (
    "The file converter had no record of who uploaded it, most likely because "
    "it was restarted after the upload. Its uploader was not told."
)

#: A file the backend found in ``failed_files`` at startup with no record.
FOUND_DETAIL = (
    "Found in failed_files at a server start, with no record made of it. If it "
    "could not be read, its uploader was told why at the time."
)

#: A lost file: its name in the streams folder, its instrument as the name
#: says, and why it is reported.
LostUpload = tuple[str, str | None, str]


async def lost_upload_audience(session) -> list[int]:
    """The people told of a lost upload: every active global admin and owner.

    The uploader cannot be addressed (see the module), and the file has no
    record to find its instrument's people by, so it goes to those who can
    act on the whole deployment. A machine account never reads anything.

    :param session: An open database session.
    :return: Their ids, in order.
    """
    return list(
        await session.scalars(
            select(User.id)
            .where(
                User.is_active.is_(True),
                User.account_type == ACCOUNT_TYPE_PERSON,
                or_(
                    User.is_superuser.is_(True),
                    User.role_id >= ROLE_ACCESS_LEVELS["admin"],
                ),
            )
            .order_by(User.id)
        )
    )


def lost_upload_message(count: int, latest: str) -> str:
    """The sentence a digest of lost uploads reads."""
    if count == 1:
        return (
            f"{latest} was set aside by the file converter and never stored. "
            "Check why below, and upload it again if it should be stored: the "
            "converter kept it in its failed_files folder."
        )
    return (
        f"{count} uploaded files were set aside by the file converter and never "
        "stored. Check why below, and upload again those that should be stored: "
        "the converter kept them in its failed_files folder."
    )


async def add_lost_uploads(
    session, files: list[LostUpload], once: bool = False
) -> list[Change]:
    """Name lost uploads to every admin and owner not told of them yet.

    In the caller's transaction, which it leaves holding the lock of these
    digests, so two reports cannot open two digests for one person or name
    one file twice.

    :param session: An open database session.
    :param files: The files, each as a :data:`LostUpload`.
    :param once: Skip a file any digest of the person named, read ones
        included (the startup scan). Otherwise only the open digest's names
        are skipped, so a second loss of one name is heard (the converter).
    :return: The digests written.
    """
    if not files:
        return []
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext("notification:upload_lost")))
    )
    kind = NotificationKind.UPLOAD_LOST.value
    unique: dict[str, LostUpload] = {}
    for file in files:
        unique.setdefault(file[0], file)

    now = datetime.now(timezone.utc)
    written: list[tuple[bool, Notification]] = []
    for user_id in await lost_upload_audience(session):
        rows = (
            await session.scalars(
                select(Notification)
                .where(Notification.user_id == user_id, Notification.kind == kind)
                .with_for_update()
            )
        ).all()
        row = next((row for row in rows if row.read_utc is None), None)
        told = {
            name
            for kept in (rows if once else [row] if row else [])
            for name in (kept.payload or {}).get("filenames") or []
        }
        new = [file for name, file in unique.items() if name not in told]
        if not new:
            continue
        created = row is None
        if created:
            row = Notification(
                notification_id=gen_id(16),
                user_id=user_id,
                kind=kind,
                instrument=None,
                instrument_key=None,
                severity="error",
                message="",
                count=0,
                created_utc=now,
                updated_utc=now,
                version=0,
            )
            session.add(row)
        payload = row.payload or {}
        # Newest first, as a processing digest lists its files.
        entries = [
            {
                "sample_file_id": None,
                "filename": name,
                "instrument": instrument,
                "datetime_utc": None,
                "detail": detail,
            }
            for name, instrument, detail in reversed(new)
        ]
        row.payload = {
            "files": (entries + (payload.get("files") or []))[:DIGEST_FILES],
            "filenames": (payload.get("filenames") or []) + [file[0] for file in new],
        }
        row.count = (row.count or 0) + len(new)
        row.message = lost_upload_message(row.count, new[-1][0])
        row.updated_utc = now
        row.version = (row.version or 0) + 1
        written.append((created, row))
    await session.flush()
    return [(created, row.to_dict()) for created, row in written]


async def notify_lost_uploads(
    files: list[LostUpload], emit: bool = True, once: bool = False
) -> int:
    """Name lost uploads to the admins and owners, in a transaction of its own.

    Best effort: a report that cannot be kept is logged, never raised.

    :param files: The files, each as a :data:`LostUpload`.
    :param emit: Whether to tell open browsers. Off where there are none.
    :param once: Name a file to a person once ever, as :func:`add_lost_uploads`
        says.
    :return: How many digests were written.
    """
    try:
        async with async_session() as session:
            changes = await add_lost_uploads(session, files, once=once)
            await session.commit()
    except Exception:  # noqa: BLE001 - reporting must not become the failure
        runtime.logger.opt(exception=True).warning(
            "Could not keep the notification of uploads the file converter "
            "could not store"
        )
        return 0
    if emit:
        await emit_notification_changes(changes)
    return len(changes)
