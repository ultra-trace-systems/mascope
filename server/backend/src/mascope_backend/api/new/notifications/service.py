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
read, and is marked resolved once none of its files is left in the state it
reports. Every write to an instrument's digests - adding a file, resolving -
holds a lock on the instrument for the rest of its transaction, so the two
cannot interleave: a digest is never stamped resolved while a file is being
added to it, and two workers never open the same digest at once.

Writing them is best effort, as recording the processing status that triggers
them is: a notification that cannot be kept is logged, and the processing it
reports on carries on.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE, ACCOUNT_TYPE_PERSON
from mascope_backend.api.models.dataset.config import acquisition_workspace_name
from mascope_backend.api.models.sample.files.config import (
    IN_PROGRESS,
    ProcessingStatus,
)
from mascope_backend.api.new.notifications.config import (
    DIGEST_FILES,
    PROCESSING_NOTIFICATIONS,
    READ_RETENTION_DAYS,
    NotificationKind,
)
from mascope_backend.db import (
    Notification,
    NotificationFile,
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


#: A digest change: whether the row was created, and the row as sent to
#: browsers.
Change = tuple[bool, dict]


def instrument_key(instrument: str | None) -> str | None:
    """What an instrument's digests are matched on: its trimmed lower case.

    ``SampleFile.instrument`` is recorded with inconsistent case, and one
    workspace serves every variant; its digests do too.
    """
    return instrument.strip().lower() if instrument else None


async def _lock_instrument(session, key: str | None) -> None:
    """Hold the instrument's digests for the rest of the transaction."""
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"notification:{key or ''}")))
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

    # The same case-insensitive match the workspace is found by.
    audience.update(
        await session.scalars(
            select(WorkspaceMember.user_id)
            .join(Workspace, Workspace.workspace_id == WorkspaceMember.workspace_id)
            .where(
                func.lower(Workspace.workspace_name)
                == acquisition_workspace_name(instrument).lower(),
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


def _entry(record: dict, detail: str | None) -> dict:
    """A file as a digest names it."""
    acquired = record.get("datetime_utc")
    return {
        "sample_file_id": record["sample_file_id"],
        "filename": record["filename"],
        "datetime_utc": acquired.isoformat()
        if isinstance(acquired, datetime)
        else acquired,
        "detail": detail,
    }


async def add_to_digests(
    session, status: ProcessingStatus, files: list[tuple[dict, str | None]]
) -> list[Change]:
    """Add files that reached ``status`` to the open digest of everyone told.

    In the caller's transaction, which it leaves holding each instrument's
    lock. Each addressee's open digest for the kind and instrument takes the
    files; the first file after a read opens a new one. A file the digest
    already took is not counted again.

    :param session: An open database session.
    :param status: The outcome, one of ``PROCESSING_NOTIFICATIONS``.
    :param files: Each file's row, as recorded with the status, and its
        status detail.
    :return: The digests written.
    """
    kind, severity = PROCESSING_NOTIFICATIONS[status]
    by_instrument: dict[str | None, list[tuple[dict, str | None]]] = {}
    for record, detail in files:
        by_instrument.setdefault(instrument_key(record["instrument"]), []).append(
            (record, detail)
        )

    now = datetime.now(timezone.utc)
    written: list[tuple[bool, Notification]] = []
    # In one order, so two transactions taking several never deadlock.
    for key in sorted(by_instrument, key=lambda key: key or ""):
        await _lock_instrument(session, key)
        group = by_instrument[key]
        audiences: dict[int | None, set[int]] = {}
        readers: dict[int, dict[str, tuple[dict, str | None]]] = {}
        for record, detail in group:
            uploader = record.get("uploaded_by_user_id")
            if uploader not in audiences:
                audiences[uploader] = await processing_audience(
                    session, record["instrument"], uploader
                )
            for user_id in audiences[uploader]:
                readers.setdefault(user_id, {})[record["sample_file_id"]] = (
                    record,
                    detail,
                )

        for user_id in sorted(readers):
            taken = list(readers[user_id].values())
            # FOR UPDATE: a digest read meanwhile no longer matches, and the
            # file opens a new one instead of joining one nobody will see.
            row = await session.scalar(
                select(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.kind == kind.value,
                    Notification.instrument_key == key,
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
                    instrument=taken[0][0]["instrument"],
                    instrument_key=key,
                    severity=severity,
                    message="",
                    count=0,
                    created_utc=now,
                    updated_utc=now,
                    version=0,
                )
                session.add(row)
                # Written now, so the files can be linked to it below; the
                # message follows from the count they make.
                await session.flush()
            new = set(
                await session.scalars(
                    insert(NotificationFile)
                    .values(
                        [
                            {
                                "notification_id": row.notification_id,
                                "sample_file_id": record["sample_file_id"],
                            }
                            for record, _ in taken
                        ]
                    )
                    .on_conflict_do_nothing()
                    .returning(NotificationFile.sample_file_id)
                )
            )
            # Newest first; a file processed again moves to the front.
            entries = [_entry(record, detail) for record, detail in reversed(taken)]
            fresh = {entry["sample_file_id"] for entry in entries}
            listed = entries + [
                entry
                for entry in (row.payload or {}).get("files") or []
                if entry.get("sample_file_id") not in fresh
            ]
            row.count = (row.count or 0) + len(new)
            row.payload = {"status": status.value, "files": listed[:DIGEST_FILES]}
            row.severity = severity
            row.message = digest_message(kind, row.count, row.instrument)
            row.updated_utc = now
            row.resolved_utc = None
            row.version = (row.version or 0) + 1
            written.append((created, row))
    await session.flush()
    return [(created, row.to_dict()) for created, row in written]


async def resolve_digests(session, keys: set[str | None]) -> list[Change]:
    """Mark resolved the digests none of whose files is left in their state.

    In the caller's transaction. The common case - an instrument with no open
    digest - costs one indexed read and takes no lock.

    :param session: An open database session.
    :param keys: The instruments whose digests to check, as ``instrument_key``.
    :return: The digests resolved.
    """
    now = datetime.now(timezone.utc)
    resolved: list[Change] = []
    for key in sorted(keys, key=lambda key: key or ""):
        if not await session.scalar(
            select(
                exists().where(
                    Notification.instrument_key == key,
                    Notification.resolved_utc.is_(None),
                )
            )
        ):
            continue
        await _lock_instrument(session, key)
        for status, (kind, _) in PROCESSING_NOTIFICATIONS.items():
            rows = await session.scalars(
                update(Notification)
                .where(
                    Notification.instrument_key == key,
                    Notification.kind == kind.value,
                    Notification.resolved_utc.is_(None),
                    ~exists().where(
                        NotificationFile.notification_id
                        == Notification.notification_id,
                        SampleFile.sample_file_id == NotificationFile.sample_file_id,
                        SampleFile.processing_status == status.value,
                    ),
                )
                .values(resolved_utc=now, version=Notification.version + 1)
                .returning(Notification)
            )
            resolved += [(False, row.to_dict()) for row in rows]
    return resolved


async def keep_processing_outcome(
    session, record: dict, status: ProcessingStatus, detail: str | None
) -> list[Change]:
    """Keep what a file's new status means for its instrument's digests.

    In the transaction that writes the status, so the two stand or fall
    together; each part runs under a savepoint of its own, so a digest that
    cannot be written costs the status nothing. An outcome that needs
    someone joins the digests; any settled status may resolve them.

    :param session: The session the status was written in.
    :param record: The file's row, as recorded with the status.
    :param status: The status written.
    :param detail: The status detail.
    :return: The digests written, for :func:`emit_notification_changes` once
        the transaction commits.
    """
    changes: list[Change] = []
    parts = []
    if status in PROCESSING_NOTIFICATIONS:
        parts.append(
            ("keep", lambda: add_to_digests(session, status, [(record, detail)]))
        )
    if status not in IN_PROGRESS:
        key = instrument_key(record["instrument"])
        parts.append(("resolve", lambda: resolve_digests(session, {key})))
    for action, part in parts:
        try:
            async with session.begin_nested():
                changes += await part()
        except Exception:  # noqa: BLE001 - the status matters more than its digest
            # The WARNING names no file: error monitoring groups issues by
            # the message, and an outage fails this for every file in flight.
            runtime.logger.info(
                f"Could not {action} the '{status.value}' notifications for "
                f"sample file {record.get('sample_file_id')}"
            )
            runtime.logger.opt(exception=True).warning(
                f"Could not {action} a sample file's '{status.value}' notifications"
            )
    return changes


async def emit_notification_changes(changes: list[Change]) -> None:
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
    """Keep a processing outcome that needs someone, in a transaction of its own.

    What :func:`keep_processing_outcome` does inside the status write, for a
    caller that has no transaction to share.

    :param record: The file's row, as recorded with the status.
    :param status: The outcome, one of ``PROCESSING_NOTIFICATIONS``.
    :param detail: The status detail, kept with the file in the digest.
    :param emit: Whether to tell open browsers. Off where there are none.
    """
    try:
        async with async_session() as session:
            changes = await add_to_digests(session, status, [(record, detail)])
            await session.commit()
    except Exception:  # noqa: BLE001 - the processing matters more than its report
        runtime.logger.info(
            f"Could not keep the '{status.value}' notification for sample file "
            f"{record.get('sample_file_id')}"
        )
        runtime.logger.opt(exception=True).warning(
            f"Could not keep a sample file's '{status.value}' notification"
        )
        return
    if emit:
        await emit_notification_changes(changes)


async def resolve_processing_notifications(instrument: str | None) -> None:
    """Resolve an instrument's digests, in a transaction of its own.

    For a change that can leave a digest with nothing in its state without
    writing a status, such as deleting its files.

    :param instrument: The instrument whose digests to check.
    """
    try:
        async with async_session() as session:
            changes = await resolve_digests(session, {instrument_key(instrument)})
            await session.commit()
    except Exception:  # noqa: BLE001 - the processing matters more than its report
        runtime.logger.opt(exception=True).warning(
            "Could not resolve an instrument's processing notifications"
        )
        return
    await emit_notification_changes(changes)


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
    user_id: int, seen: dict[str, datetime] | None
) -> dict:
    """Mark a person's notifications read.

    Only the person's own unread rows are touched, so an id that is not
    theirs, or already read, is passed over. A digest that took another file
    after the person saw it (``updated_utc`` later than theirs) is left
    unread too: nobody has seen that file yet. A digest that is read no
    longer takes files: the next one opens a new digest.

    :param user_id: The person.
    :param seen: Each row to mark, with its ``updated_utc`` as the person saw
        it; None for every unread row, whatever it holds.
    :return: How many were marked, and which.
    """
    stmt = update(Notification).where(
        Notification.user_id == user_id, Notification.read_utc.is_(None)
    )
    if seen is not None:
        if not seen:
            return {
                "message": "Marked 0 notifications read.",
                "data": {"read": 0, "notification_ids": []},
            }
        stmt = stmt.where(
            or_(
                *(
                    and_(
                        Notification.notification_id == notification_id,
                        Notification.updated_utc <= updated_utc,
                    )
                    for notification_id, updated_utc in seen.items()
                )
            )
        )
    async with async_session() as session:
        rows = (
            await session.scalars(
                stmt.values(
                    read_utc=datetime.now(timezone.utc),
                    version=Notification.version + 1,
                ).returning(Notification)
            )
        ).all()
        changes = [(False, row.to_dict()) for row in rows]
        await session.commit()
    # Other tabs the person has open drop them from their unread count too.
    await emit_notification_changes(changes)
    read = len(changes)
    return {
        "message": f"Marked {read} notification{'' if read == 1 else 's'} read.",
        "data": {
            "read": read,
            "notification_ids": [record["notification_id"] for _, record in changes],
        },
    }


async def purge_read_notifications(days: int = READ_RETENTION_DAYS) -> int:
    """Delete notifications read more than ``days`` ago.

    A read digest has done its job; the files it named keep their status in
    Raw files. Best effort, at startup.

    :param days: How long a read notification is kept.
    :return: How many were deleted.
    """
    try:
        async with async_session() as session:
            deleted = (
                await session.execute(
                    delete(Notification).where(
                        Notification.read_utc
                        < datetime.now(timezone.utc) - timedelta(days=days)
                    )
                )
            ).rowcount
            await session.commit()
    except Exception:  # noqa: BLE001 - housekeeping must never stop a startup
        runtime.logger.opt(exception=True).warning(
            "Could not purge notifications read long ago"
        )
        return 0
    return deleted
