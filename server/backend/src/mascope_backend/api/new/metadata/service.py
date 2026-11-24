"""The metadata field registry: list, create, change, retire and delete fields.

The registry is one per deployment (``docs/dev/sample_filters_and_metadata.md``,
section 4.4). A field is shared reference data: the global editor role or
higher defines it, a guest reads it (decision 14, MAS-104; the routes gate
this). What the service guards is the meaning of values already written
under a field:

- its key never changes, as values are stored under it;
- a field in use is not deleted, and its type does not change: it is retired;
- a field Mascope ships (``system``) keeps its key, type and allowed values,
  and is neither retired nor deleted.

A field is in use when something references it (``field_usage``): today an
attribute template that selects it.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.metadata.schema import (
    CHOICE_TYPES,
    MetadataFieldCreate,
    MetadataFieldUpdate,
)
from mascope_backend.db import AttributeTemplateField, MetadataField, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.socket.records.service import emit_record_reload


#: The record type a change to the registry reloads in the clients.
RECORD_TYPE = "metadata_field"


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
    )


async def field_usage(session: AsyncSession, field_ids=None) -> dict[str, int]:
    """How many rows reference each field, for the fields that have any.

    A field with a count here is in use: it may be retired, not deleted, and
    its type is fixed. The references counted are the attribute templates
    that select the field; acquisition metadata, when it is stored, is
    another.

    :param session: An open session.
    :param field_ids: The fields to count, or ``None`` for every field.
    :return: The count of references per field id, leaving out a field none
        references.
    """
    stmt = select(AttributeTemplateField.metadata_field_id, func.count()).group_by(
        AttributeTemplateField.metadata_field_id
    )
    if field_ids is not None:
        stmt = stmt.where(AttributeTemplateField.metadata_field_id.in_(field_ids))
    return dict((await session.execute(stmt)).all())


def _record(field: MetadataField, uses: int) -> dict:
    record = field.to_dict()
    record["retired"] = field.retired_utc is not None
    record["in_use"] = uses > 0
    return record


def _check_values(field_type: str, allowed_values: list[str] | None) -> None:
    """Refuse allowed values the type cannot have, or an enum without any."""
    if allowed_values is not None and field_type not in CHOICE_TYPES:
        raise _unprocessable(
            f"A {field_type} field has no allowed values: only an enum or a "
            "tags field does."
        )
    if field_type == "enum" and not allowed_values:
        raise _unprocessable("An enum field needs at least one allowed value.")


async def _fetch(session: AsyncSession, metadata_field_id: str) -> MetadataField:
    field = await session.get(MetadataField, metadata_field_id)
    if field is None:
        raise NotFoundException(f"Metadata field '{metadata_field_id}' not found.")
    return field


@api_controller()
async def get_metadata_fields(include_retired: bool = False) -> dict:
    """The registry's fields, ordered by label.

    :param include_retired: Whether to list the retired fields too.
    :return: The fields, each with ``retired`` and ``in_use``.
    """
    async with async_session() as session:
        stmt = select(MetadataField).order_by(
            func.lower(MetadataField.label), MetadataField.key
        )
        if not include_retired:
            stmt = stmt.where(MetadataField.retired_utc.is_(None))
        fields = (await session.execute(stmt)).scalars().all()
        usage = await field_usage(session, [f.metadata_field_id for f in fields])
    return {
        "message": "Metadata fields retrieved successfully.",
        "results": len(fields),
        "data": [_record(f, usage.get(f.metadata_field_id, 0)) for f in fields],
    }


@api_controller()
async def get_metadata_field(metadata_field_id: str) -> dict:
    """One field of the registry.

    :param metadata_field_id: The field's id.
    :raises NotFoundException: If there is no such field.
    """
    async with async_session() as session:
        field = await _fetch(session, metadata_field_id)
        usage = await field_usage(session, [metadata_field_id])
    return {
        "message": f"Metadata field '{field.key}' retrieved successfully.",
        "data": _record(field, usage.get(metadata_field_id, 0)),
    }


@api_controller()
async def create_metadata_field(body: MetadataFieldCreate) -> dict:
    """Add a field to the registry.

    :param body: The field.
    :raises HTTPException: 409 if a field already has the key, 422 if the
        allowed values do not fit the type.
    """
    _check_values(body.type, body.allowed_values)
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        taken = await session.scalar(
            select(MetadataField.metadata_field_id).where(MetadataField.key == body.key)
        )
        if taken is not None:
            raise _conflict(
                f"A metadata field with the key '{body.key}' already exists. A "
                "retired field keeps its key: restore it instead."
            )
        field = MetadataField(
            metadata_field_id=gen_id(16),
            key=body.key,
            label=body.label,
            type=body.type,
            unit=body.unit,
            allowed_values=body.allowed_values,
            description=body.description,
            system=False,
            created_utc=now,
            modified_utc=now,
        )
        session.add(field)
        await session.commit()
    await emit_record_reload(record_type=RECORD_TYPE)
    return {
        "message": f"Metadata field '{field.key}' created successfully.",
        "data": _record(field, 0),
    }


@api_controller()
async def update_metadata_field(
    metadata_field_id: str, body: MetadataFieldUpdate
) -> dict:
    """Change a field, or retire or restore it.

    Only what the body sends changes. A field that is not a choice keeps no
    allowed values, so a change of type away from enum or tags clears them.

    :param metadata_field_id: The field's id.
    :param body: The change.
    :raises NotFoundException: If there is no such field.
    :raises HTTPException: 409 for a change the field's use or a shipped field
        refuses, 422 for allowed values that do not fit the type.
    """
    changes = body.model_dump(exclude_unset=True)
    retired = changes.pop("retired", None)
    # A null type means "unchanged", on a shipped field as on any other.
    if "type" in changes and changes["type"] is None:
        del changes["type"]
    async with async_session() as session:
        field = await _fetch(session, metadata_field_id)
        if field.system:
            fixed = [
                name
                for name in ("type", "allowed_values")
                if name in changes and changes[name] != getattr(field, name)
            ]
            if retired:
                fixed.append("retired")
            if fixed:
                raise _conflict(
                    f"'{field.key}' is a field Mascope ships: its "
                    f"{', '.join(fixed)} cannot be changed."
                )
        new_type = changes.get("type") or field.type
        if new_type != field.type:
            usage = await field_usage(session, [metadata_field_id])
            if usage.get(metadata_field_id):
                raise _conflict(
                    f"'{field.key}' is in use, so its type cannot change: values "
                    "were written as its current type. Retire it and add a "
                    "field of the new type instead."
                )
        if new_type not in CHOICE_TYPES:
            changes["allowed_values"] = None
        allowed_values = changes.get("allowed_values", field.allowed_values)
        _check_values(new_type, allowed_values)

        for name, value in changes.items():
            setattr(field, name, value)
        if retired is not None:
            if retired and field.retired_utc is None:
                field.retired_utc = datetime.now(timezone.utc)
            elif not retired:
                field.retired_utc = None
        field.modified_utc = datetime.now(timezone.utc)
        await session.commit()
        usage = await field_usage(session, [metadata_field_id])
    await emit_record_reload(record_type=RECORD_TYPE)
    return {
        "message": f"Metadata field '{field.key}' updated successfully.",
        "data": _record(field, usage.get(metadata_field_id, 0)),
    }


@api_controller()
async def delete_metadata_field(metadata_field_id: str) -> dict:
    """Remove a field no row references.

    :param metadata_field_id: The field's id.
    :raises NotFoundException: If there is no such field.
    :raises HTTPException: 409 for a field Mascope ships or one in use, which
        is retired instead.
    """
    async with async_session() as session:
        field = await _fetch(session, metadata_field_id)
        if field.system:
            raise _conflict(
                f"'{field.key}' is a field Mascope ships and cannot be deleted."
            )
        uses = (await field_usage(session, [metadata_field_id])).get(
            metadata_field_id, 0
        )
        if uses:
            raise _conflict(
                f"'{field.key}' is in use, selected by {uses} attribute "
                f"template{'s' if uses != 1 else ''}, so it cannot be deleted. "
                "Retire it instead."
            )
        await session.delete(field)
        await session.commit()
    await emit_record_reload(record_type=RECORD_TYPE)
    return {"message": f"Metadata field '{field.key}' deleted successfully."}


async def resolve_template_fields(
    session: AsyncSession, keys: list[str], kept_ids: set[str] = frozenset()
) -> list[MetadataField]:
    """The registry fields an attribute template selects, in the order given.

    :param session: An open session.
    :param keys: The fields' keys.
    :param kept_ids: The fields the template already selects. A retired field
        stays selected where it was, but is not newly added.
    :raises HTTPException: 422 for a key the registry does not have, or for a
        retired field the template did not already select.
    """
    if len(set(keys)) != len(keys):
        raise _unprocessable("A template selects each metadata field only once.")
    if not keys:
        return []
    found = {
        field.key: field
        for field in (
            await session.execute(
                select(MetadataField).where(MetadataField.key.in_(keys))
            )
        ).scalars()
    }
    unknown = [key for key in keys if key not in found]
    if unknown:
        raise _unprocessable(f"No metadata field has the key: {', '.join(unknown)}.")
    retired = [
        key
        for key in keys
        if found[key].retired_utc is not None
        and found[key].metadata_field_id not in kept_ids
    ]
    if retired:
        raise _unprocessable(
            f"A retired metadata field cannot be added to a template: {', '.join(retired)}."
        )
    return [found[key] for key in keys]


async def template_field_keys(
    session: AsyncSession, template_ids: list[str]
) -> dict[str, list[str]]:
    """The keys of the fields each template selects, in its order.

    :param session: An open session.
    :param template_ids: The templates.
    :return: The keys per template id; a template that selects none is left out.
    """
    if not template_ids:
        return {}
    rows = await session.execute(
        select(AttributeTemplateField.attribute_template_id, MetadataField.key)
        .join(
            MetadataField,
            MetadataField.metadata_field_id == AttributeTemplateField.metadata_field_id,
        )
        .where(AttributeTemplateField.attribute_template_id.in_(template_ids))
        .order_by(AttributeTemplateField.position)
    )
    keys: dict[str, list[str]] = {}
    for template_id, key in rows:
        keys.setdefault(template_id, []).append(key)
    return keys
