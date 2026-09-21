"""
Notifications kept for people until they have read them.

A live notification reaches only the browsers open when it is sent (see
``socket/notifications``). A processing outcome that needs someone - files of
an instrument that failed, need a chemistry or could not be calibrated - is
also kept as a row for each person answerable for the instrument, so it waits
for them when nobody was signed in at the time (#1910).

Who is answerable (:func:`processing_audience`): the person who uploaded the
file - for a paired agent's upload, the person who sponsors the agent's device
- and the owners of the instrument's acquisition workspace.

Each row is a digest (:class:`~mascope_backend.db.Notification`): one unread
row per person, kind and instrument grows with every such file until it is
read, and is marked resolved once no file of the instrument is left in the
state it reports.

Writing them is best effort, as recording the processing status that triggers
them is: a notification that cannot be kept is logged, and the processing it
reports on carries on.
"""

from datetime import datetime, timezone

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE, ACCOUNT_TYPE_PERSON
from mascope_backend.api.models.dataset.config import dataset_config
from mascope_backend.api.models.sample.files.config import ProcessingStatus
from mascope_backend.api.new.notifications.config import (
    DIGEST_FILES,
    PROCESSING_NOTIFICATIONS,
    NotificationKind,
)
from mascope_backend.db import (
    Notification,
    SampleFile,
    User,
    Workspace,
    WorkspaceMember,
    async_session,
)
from mascope_backend.db.devices import device_sponsor_id
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import (
    emit_record_created,
    emit_record_updated,
)


async def processing_audience(
    session, instrument: str, uploaded_by_user_id: int | None
) -> set[int]:
    """The people told how an instrument's file was processed.

    The uploader, unless it is a paired agent's machine account, which nobody
    signs in as: the person who sponsors the agent's device reads it instead.
    Then every owner of the instrument's acquisition workspace. Only active
    people are kept; a machine account never reads anything.

    :param session: An open database session.
    :param instrument: The file's instrument.
    :param uploaded_by_user_id: The account that uploaded the file, if known.
    :return: The ids of the accounts to address.
    """
    audience: set[int] = set()
    if uploaded_by_user_id is not None:
        uploader = await session.get(User, uploaded_by_user_id)
        if uploader is not None and uploader.account_type == ACCOUNT_TYPE_MACHINE:
            sponsor_id = await device_sponsor_id(session, uploader.id)
            if sponsor_id is not None:
                audience.add(sponsor_id)
        elif uploader is not None:
            audience.add(uploader.id)

    # The same case-insensitive match the workspace was created and is found by.
    workspace_name = (
        f"{dataset_config.ACQUISITION_NAME_PREFIX} {instrument.strip()}".lower()
    )
    audience.update(
        await session.scalars(
            select(WorkspaceMember.user_id)
            .join(Workspace, Workspace.workspace_id == WorkspaceMember.workspace_id)
            .where(
                func.lower(Workspace.workspace_name) == workspace_name,
                Workspace.is_system.is_(True),
                WorkspaceMember.workspace_role == "owner",
            )
        )
    )
    if not audience:
        return set()
    return set(
        await session.scalars(
            select(User.id).where(
                User.id.in_(audience),
                User.is_active.is_(True),
                User.account_type == ACCOUNT_TYPE_PERSON,
            )
        )
    )


def _files(count: int) -> str:
    return f"{count} file{'' if count == 1 else 's'}"


def digest_message(kind: NotificationKind, count: int, instrument: str) -> str:
    """The sentence a digest reads, for the files it stands for so far."""
    one = count == 1
    if kind is NotificationKind.NEEDS_CHEMISTRY:
        return (
            f"{_files(count)} from {instrument} {'needs' if one else 'need'} a "
            "chemistry: no ionization mode could be bound, so "
            f"{'it has' if one else 'they have'} no samples."
        )
    if kind is NotificationKind.CALIBRATION_FAILED:
        return (
            f"The m/z calibration failed for {_files(count)} from {instrument}, "
            f"so {'it was' if one else 'they were'} not matched."
        )
    return f"Processing failed for {_files(count)} from {instrument}."


async def _add_to_digests(
    record: dict, status: ProcessingStatus, detail: str | None
) -> list[tuple[bool, dict]]:
    """Add one file to the open digest of every person told about it.

    :return: ``(created, row)`` for each digest written.
    """
    kind, severity = PROCESSING_NOTIFICATIONS[status]
    instrument = record["instrument"]
    uploaded = record.get("datetime_utc")
    entry = {
        "sample_file_id": record["sample_file_id"],
        "filename": record["filename"],
        "datetime_utc": uploaded.isoformat()
        if isinstance(uploaded, datetime)
        else uploaded,
        "detail": detail,
    }
    now = datetime.now(timezone.utc)
    written: list[tuple[bool, Notification]] = []
    async with async_session() as session:
        audience = await processing_audience(
            session, instrument, record.get("uploaded_by_user_id")
        )
        for user_id in sorted(audience):
            row = await session.scalar(
                select(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.kind == kind.value,
                    Notification.instrument == instrument,
                    Notification.read_utc.is_(None),
                )
                .with_for_update()
            )
            created = row is None
            if created:
                row = Notification(
                    notification_id=gen_id(16),
                    user_id=user_id,
                    kind=kind.value,
                    instrument=instrument,
                    count=0,
                    created_utc=now,
                )
                session.add(row)
            files = list((row.payload or {}).get("files") or [])
            again = any(
                f.get("sample_file_id") == entry["sample_file_id"] for f in files
            )
            files = [entry] + [
                f for f in files if f.get("sample_file_id") != entry["sample_file_id"]
            ]
            # A file processed again into the same outcome is not a new file.
            row.count = (row.count or 0) + (0 if again else 1)
            row.payload = {"status": status.value, "files": files[:DIGEST_FILES]}
            row.severity = severity
            row.message = digest_message(kind, row.count, instrument)
            row.updated_utc = now
            row.resolved_utc = None
            written.append((created, row))
        await session.flush()
        records = [(created, row.to_dict()) for created, row in written]
        await session.commit()
    return records


async def _emit(changes: list[tuple[bool, dict]]) -> None:
    """Tell each addressee's open browsers about their changed rows."""
    for created, record in changes:
        emit = emit_record_created if created else emit_record_updated
        await emit(
            record_type="notification",
            record_id=record["notification_id"],
            record=record,
            room=f"user-{record['user_id']}",
        )


async def notify_processing_outcome(
    record: dict,
    status: ProcessingStatus,
    detail: str | None,
    emit: bool = True,
) -> None:
    """Keep a processing outcome that needs someone for everyone answerable.

    Called for the statuses of
    :data:`~mascope_backend.api.new.notifications.config.PROCESSING_NOTIFICATIONS`
    once the file's status is written. Each addressee's open digest for the
    kind and instrument takes the file; the first file after a read opens a
    new one. Two workers opening the same digest at once collide on its
    unique index, and the loser adds to the winner's.

    :param record: The file's row, as recorded with the status.
    :param status: The outcome.
    :param detail: The status detail, kept with the file in the digest.
    :param emit: Whether to tell open browsers. Off where there are none, such
        as at startup.
    """
    try:
        try:
            changes = await _add_to_digests(record, status, detail)
        except IntegrityError:
            changes = await _add_to_digests(record, status, detail)
    except Exception:  # noqa: BLE001 - the processing matters more than its report
        runtime.logger.opt(exception=True).warning(
            f"Could not keep the '{status.value}' notification for sample file "
            f"{record.get('sample_file_id')}"
        )
        return
    if emit:
        await _emit(changes)


async def resolve_processing_notifications(instrument: str) -> None:
    """Mark resolved the digests whose files have all moved on.

    A digest reports that files of an instrument are in some state. Once none
    is - each was re-processed, given a chemistry, or deleted - the digest is
    stamped resolved for everyone it was addressed to, read or not. Called
    after a file of the instrument reaches an outcome, so one query settles
    the usual case of an instrument with no open digest.

    :param instrument: The instrument whose digests to check.
    """
    status_of = {
        kind.value: status.value
        for status, (kind, _) in PROCESSING_NOTIFICATIONS.items()
    }
    try:
        async with async_session() as session:
            open_kinds = set(
                await session.scalars(
                    select(Notification.kind)
                    .where(
                        Notification.instrument == instrument,
                        Notification.kind.in_(status_of),
                        Notification.resolved_utc.is_(None),
                    )
                    .distinct()
                )
            )
            settled = [
                kind
                for kind in sorted(open_kinds)
                if not await session.scalar(
                    select(
                        exists().where(
                            SampleFile.instrument == instrument,
                            SampleFile.processing_status == status_of[kind],
                        )
                    )
                )
            ]
            if not settled:
                return
            rows = (
                await session.scalars(
                    update(Notification)
                    .where(
                        Notification.instrument == instrument,
                        Notification.kind.in_(settled),
                        Notification.resolved_utc.is_(None),
                    )
                    .values(resolved_utc=datetime.now(timezone.utc))
                    .returning(Notification)
                )
            ).all()
            changes = [(False, row.to_dict()) for row in rows]
            await session.commit()
    except Exception:  # noqa: BLE001 - the processing matters more than its report
        runtime.logger.opt(exception=True).warning(
            f"Could not resolve the processing notifications of {instrument}"
        )
        return
    await _emit(changes)


async def list_notifications(user_id: int, include_read: bool, limit: int) -> dict:
    """A person's notifications, unread first, then newest first.

    :param user_id: The person.
    :param include_read: Whether to list the ones already read.
    :param limit: The most to list.
    :return: The rows, how many were listed and how many are unread.
    """
    async with async_session() as session:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if not include_read:
            stmt = stmt.where(Notification.read_utc.is_(None))
        rows = (
            await session.scalars(
                stmt.order_by(
                    Notification.read_utc.is_(None).desc(),
                    Notification.updated_utc.desc(),
                ).limit(limit)
            )
        ).all()
        unread = await session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_utc.is_(None))
        )
        data = [row.to_dict() for row in rows]
    return {
        "message": f"{unread} unread notification{'' if unread == 1 else 's'}.",
        "results": len(data),
        "unread": unread,
        "data": data,
    }


async def mark_notifications_read(
    user_id: int, notification_ids: list[str] | None
) -> dict:
    """Mark a person's notifications read.

    Only the person's own unread rows are touched, so an id that is not
    theirs, or already read, is passed over. A digest that is read no longer
    takes files: the next one opens a new digest.

    :param user_id: The person.
    :param notification_ids: The rows to mark, or None for every unread one.
    :return: How many were marked.
    """
    stmt = update(Notification).where(
        Notification.user_id == user_id, Notification.read_utc.is_(None)
    )
    if notification_ids is not None:
        stmt = stmt.where(Notification.notification_id.in_(notification_ids))
    async with async_session() as session:
        rows = (
            await session.scalars(
                stmt.values(read_utc=datetime.now(timezone.utc)).returning(Notification)
            )
        ).all()
        changes = [(False, row.to_dict()) for row in rows]
        await session.commit()
    # Other tabs the person has open drop them from their unread count too.
    await _emit(changes)
    read = len(changes)
    return {
        "message": f"Marked {read} notification{'' if read == 1 else 's'} read.",
        "data": {"read": read},
    }
