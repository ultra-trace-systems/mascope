"""
Dataset tombstones: what a deleted dataset was, after its data is gone (FAIR A2).

``write_tombstones`` is called by both paths that delete datasets - the dataset
controller and workspace deletion, which removes its datasets by cascade - in
the session that deletes them, before the delete, so the snapshot and the
delete commit together. ``get_tombstones`` is the read behind
``GET /api/datasets/tombstones``. The table itself is ``DatasetTombstone``.
"""

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.db import (
    Dataset,
    DatasetTombstone,
    SampleBatch,
    SampleItem,
    User,
    WorkspaceMember,
    async_session,
)
from mascope_backend.provenance import build_provenance


#: The descriptive fields of ``Dataset`` a tombstone keeps as one object.
DESCRIPTIVE_FIELDS = (
    "license",
    "authors",
    "keywords",
    "related_identifiers",
    "funding",
)


async def _counts(
    session: AsyncSession, dataset_ids: list[str]
) -> dict[str, tuple[int, int, int]]:
    """(batches, items, distinct files) per dataset, as the rows stand now."""
    batches = await session.execute(
        select(SampleBatch.dataset_id, func.count())
        .where(SampleBatch.dataset_id.in_(dataset_ids))
        .group_by(SampleBatch.dataset_id)
    )
    items = await session.execute(
        select(
            SampleBatch.dataset_id,
            func.count(SampleItem.sample_item_id),
            func.count(func.distinct(SampleItem.sample_file_id)),
        )
        .join(SampleItem, SampleItem.sample_batch_id == SampleBatch.sample_batch_id)
        .where(SampleBatch.dataset_id.in_(dataset_ids))
        .group_by(SampleBatch.dataset_id)
    )
    n_batches = dict(batches.all())
    n_items = {row[0]: (row[1], row[2]) for row in items.all()}
    return {
        dataset_id: (n_batches.get(dataset_id, 0), *n_items.get(dataset_id, (0, 0)))
        for dataset_id in dataset_ids
    }


async def write_tombstones(
    session: AsyncSession,
    datasets: Sequence[Dataset],
    workspace_name: str | None,
    deleted_via: Literal["dataset", "workspace"],
    deleted_by: str | None,
) -> None:
    """Snapshot *datasets* into ``dataset_tombstone``, in *session*.

    Call it before the delete, in the session that deletes, and commit the two
    together: the counts read the rows the delete is about to remove. An
    existing tombstone of the same id - a dataset restored from a dump and
    deleted again - is overwritten.

    :param session: The session that deletes the datasets.
    :param datasets: The datasets about to be deleted, all of one workspace.
    :param workspace_name: The name of their workspace, as it stands.
    :param deleted_via: ``"dataset"`` or ``"workspace"``: which delete path.
    :param deleted_by: The acting user's email; None for a system delete.
    """
    if not datasets:
        return
    counts = await _counts(session, [d.dataset_id for d in datasets])
    deleted_utc = datetime.now(timezone.utc)
    rows = [
        {
            "dataset_id": dataset.dataset_id,
            "workspace_id": dataset.workspace_id,
            "workspace_name": workspace_name,
            "dataset_name": dataset.dataset_name,
            "dataset_description": dataset.dataset_description,
            "dataset_type": dataset.dataset_type,
            "instrument": dataset.instrument,
            "descriptive_metadata": {
                field: getattr(dataset, field) for field in DESCRIPTIVE_FIELDS
            },
            "n_sample_batches": counts[dataset.dataset_id][0],
            "n_sample_items": counts[dataset.dataset_id][1],
            "n_sample_files": counts[dataset.dataset_id][2],
            "dataset_utc_created": dataset.dataset_utc_created,
            "dataset_utc_modified": dataset.dataset_utc_modified,
            "deleted_utc": deleted_utc,
            "deleted_by": deleted_by,
            "deleted_via": deleted_via,
            "provenance": build_provenance({"dataset_id": dataset.dataset_id}),
        }
        for dataset in datasets
    ]
    stmt = insert(DatasetTombstone).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[DatasetTombstone.dataset_id],
            set_={
                column: stmt.excluded[column]
                for column in rows[0]
                if column != "dataset_id"
            },
        )
    )


@api_controller()
async def get_tombstones(
    user: User, page: int | None = None, limit: int | None = None
) -> dict:
    """The tombstones *user* may read, newest delete first.

    A member of a workspace reads the tombstones of its datasets. A global
    admin or owner reads every row, including those whose workspace is gone -
    nobody is a member of a deleted workspace, so nobody else can.

    :param user: The caller.
    :param page: Zero-based page, with *limit*; both or neither.
    :param limit: Rows per page.
    :return: ``{"data": [...], "total": N}``.
    """
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    admin_level = auth_settings.ROLE_ACCESS_LEVELS["admin"]
    sees_all = user.is_superuser or (
        user.role_id is not None and user.role_id >= admin_level
    )
    async with async_session() as session:
        stmt = select(DatasetTombstone)
        if not sees_all:
            stmt = stmt.where(
                DatasetTombstone.workspace_id.in_(
                    select(WorkspaceMember.workspace_id).where(
                        WorkspaceMember.user_id == user.id
                    )
                )
            )
        total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = stmt.order_by(
            desc(DatasetTombstone.deleted_utc), DatasetTombstone.dataset_id
        )
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
    return {"data": [row.to_dict() for row in rows], "total": total}
