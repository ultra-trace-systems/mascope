"""
Tests: a collection PATCH may not change compounds and batches together.

``update_target_collection`` reads an empty ``target_compound_ids`` as "the
collection holds no compounds" and deletes every association. Beside
``sample_batch_ids`` that is the mixed update the guard refuses, so the route
answers 422 and the collection keeps its compounds.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.db import TargetCollection
from mascope_backend.db.id import gen_id
from mascope_backend.db.models import TargetCompoundInTargetCollection


@pytest_asyncio.fixture
async def collection_with_compound(async_session_factory, ws_alpha, test_compound):
    """A TARGETS collection in workspace Alpha holding `test_compound`."""
    tc_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCollection(
                target_collection_id=tc_id,
                target_collection_name="Targets (update guard)",
                target_collection_type="TARGETS",
                workspace_id=ws_alpha["workspace_id"],
            )
        )
        await session.flush()
        session.add(
            TargetCompoundInTargetCollection(
                target_collection_id=tc_id, target_compound_id=test_compound
            )
        )
        await session.commit()
    return tc_id


async def _compound_ids(async_session_factory, tc_id):
    async with async_session_factory() as session:
        return set(
            (
                await session.execute(
                    select(TargetCompoundInTargetCollection.target_compound_id).where(
                        TargetCompoundInTargetCollection.target_collection_id == tc_id
                    )
                )
            ).scalars()
        )


@pytest.mark.asyncio
async def test_empty_compound_ids_beside_batches_is_refused(
    editor_client,
    async_session_factory,
    collection_with_compound,
    alpha_batch,
    test_compound,
):
    resp = await editor_client.patch(
        f"/api/target/collections/{collection_with_compound}",
        json={"sample_batch_ids": [alpha_batch], "target_compound_ids": []},
    )
    assert resp.status_code == 422
    assert "simultaneously" in resp.text
    assert await _compound_ids(async_session_factory, collection_with_compound) == {
        test_compound
    }


@pytest.mark.asyncio
async def test_empty_compounds_create_beside_batches_is_a_batch_update(
    editor_client,
    async_session_factory,
    collection_with_compound,
    alpha_batch,
    test_compound,
):
    """An empty `target_compounds_create` creates nothing and is let through."""
    resp = await editor_client.patch(
        f"/api/target/collections/{collection_with_compound}",
        json={"sample_batch_ids": [alpha_batch], "target_compounds_create": []},
    )
    assert resp.status_code == 200
    assert await _compound_ids(async_session_factory, collection_with_compound) == {
        test_compound
    }
