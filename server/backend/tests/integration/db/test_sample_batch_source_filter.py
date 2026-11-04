"""
Tests: a batch's recipe column says "no recipe" as SQL NULL.

``sample_batch.source_filter`` is NULL for every batch built by hand, and
what finds those batches asks ``IS NULL``. A JSON column given None by name
would store the JSON value null instead, which ``IS NULL`` does not find, so
the column stores None as SQL NULL (``SampleBatch.source_filter``).
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.db import Dataset, SampleBatch, Workspace
from mascope_backend.db.id import gen_id


#: A recipe as the batch-from-filter routes will write one.
_RECIPE = {
    "filter": {"v": 1, "polarity": ["-"], "datetime_utc": {"from": None, "to": None}},
    "built_by": 1,
    "refreshed_utc": None,
    "excluded": [],
}


@pytest_asyncio.fixture
async def dataset_id(async_session_factory):
    workspace_id, dataset_id = gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add(Workspace(workspace_id=workspace_id, workspace_name=gen_id()))
        session.add(
            Dataset(
                dataset_id=dataset_id, workspace_id=workspace_id, dataset_name=gen_id()
            )
        )
        await session.commit()
    yield dataset_id
    async with async_session_factory() as session:
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_batch_without_a_recipe_has_none_and_one_with_a_recipe_keeps_it(
    async_session_factory, dataset_id
):
    by_hand, from_filter = gen_id(), gen_id()
    async with async_session_factory() as session:
        session.add_all(
            [
                SampleBatch(
                    sample_batch_id=by_hand,
                    dataset_id=dataset_id,
                    sample_batch_name=gen_id(),
                    source_filter=None,
                ),
                SampleBatch(
                    sample_batch_id=from_filter,
                    dataset_id=dataset_id,
                    sample_batch_name=gen_id(),
                    source_filter=_RECIPE,
                ),
            ]
        )
        await session.commit()

    async with async_session_factory() as session:
        without = (
            (
                await session.execute(
                    select(SampleBatch.sample_batch_id).where(
                        SampleBatch.dataset_id == dataset_id,
                        SampleBatch.source_filter.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        recipe = await session.scalar(
            select(SampleBatch.source_filter).where(
                SampleBatch.sample_batch_id == from_filter
            )
        )

    assert without == [by_hand]
    assert recipe == _RECIPE
