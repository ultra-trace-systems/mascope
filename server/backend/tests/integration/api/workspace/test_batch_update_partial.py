"""
Tests: PATCH /api/sample/batches/{id} is a partial update.

``target_collection_ids`` is optional: an update that leaves it out, or sends
it as null, keeps the batch's target collections and its status as they are,
so a rename alone flags no rematch. A list keeps its meaning: it is the whole
new set, so an empty list detaches every collection, and either flags the
batch for rematch.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SampleBatchUpdate,
)
from mascope_backend.db import (
    SampleBatch,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _collection(session, workspace_id: str, name: str) -> str:
    tc_id = gen_id()
    session.add(
        TargetCollection(
            target_collection_id=tc_id,
            target_collection_name=name,
            target_collection_type="TARGETS",
            workspace_id=workspace_id,
        )
    )
    return tc_id


@pytest_asyncio.fixture
async def batch_with_collections(
    async_session_factory, ws_alpha, alpha_dataset, sample_file
):
    """Per-test batch in the Alpha dataset: ``ready``, with one sample (so a
    rematch flag sticks) and two associated collections, plus a third
    collection of the workspace that it does not have."""
    batch_id = gen_id()
    workspace_id = ws_alpha["workspace_id"]
    async with async_session_factory() as session:
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=alpha_dataset,
                sample_batch_name="Partial Update Batch",
                sample_batch_description="before",
                status="ready",
                sample_batch_utc_created=_NOW,
            )
        )
        attached = [
            await _collection(session, workspace_id, "Partial Update One"),
            await _collection(session, workspace_id, "Partial Update Two"),
        ]
        spare = await _collection(session, workspace_id, "Partial Update Spare")
        await session.flush()
        session.add(
            SampleItem(
                sample_item_id=gen_id(),
                sample_batch_id=batch_id,
                sample_file_id=sample_file,
                sample_item_name="Partial Update Item",
                sample_item_type="ANALYSIS",
                sample_item_attributes={},
                polarity="+",
                tic=1000.0,
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=_NOW,
            )
        )
        for tc_id in attached:
            session.add(
                TargetCollectionInSampleBatch(
                    target_collection_id=tc_id, sample_batch_id=batch_id
                )
            )
        await session.commit()
    return {"batch_id": batch_id, "attached": attached, "spare": spare}


async def _stored(async_session_factory, batch_id: str) -> tuple[SampleBatch, set]:
    """The batch row as stored, and the IDs of its associated collections."""
    async with async_session_factory() as session:
        batch = await session.get(SampleBatch, batch_id)
        collections = set(
            (
                await session.execute(
                    select(TargetCollectionInSampleBatch.target_collection_id).where(
                        TargetCollectionInSampleBatch.sample_batch_id == batch_id
                    )
                )
            ).scalars()
        )
    return batch, collections


def test_update_schema_does_not_require_collections():
    """The OpenAPI schema a client reads shows the field as optional."""
    schema = SampleBatchUpdate.model_json_schema()
    assert "target_collection_ids" not in schema.get("required", [])
    assert (
        "absent, the associations stay as they are"
        in (schema["properties"]["target_collection_ids"]["description"])
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"sample_batch_name": "Renamed Batch"},
        {"sample_batch_description": "after"},
        {"sample_batch_name": "Renamed Batch", "target_collection_ids": None},
    ],
    ids=["name-only", "description-only", "explicit-null"],
)
async def test_update_without_collections_keeps_them(
    editor_client, async_session_factory, batch_with_collections, body
):
    """An update that does not name the collections succeeds, keeps them, and
    leaves the status alone."""
    ctx = batch_with_collections
    resp = await editor_client.patch(
        f"/api/sample/batches/{ctx['batch_id']}", json=body
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "rematch" not in payload["message"]
    assert payload["data"]["status"] == "ready"

    batch, collections = await _stored(async_session_factory, ctx["batch_id"])
    assert collections == set(ctx["attached"])
    assert batch.status == "ready"
    assert batch.sample_batch_name == body.get(
        "sample_batch_name", "Partial Update Batch"
    )
    assert batch.sample_batch_description == body.get(
        "sample_batch_description", "before"
    )


@pytest.mark.asyncio
async def test_update_with_empty_list_detaches_all(
    editor_client, async_session_factory, batch_with_collections
):
    """An empty list still detaches every collection and flags a rematch."""
    ctx = batch_with_collections
    resp = await editor_client.patch(
        f"/api/sample/batches/{ctx['batch_id']}",
        json={"sample_batch_name": "Renamed Batch", "target_collection_ids": []},
    )
    assert resp.status_code == 200, resp.text
    assert "flagged for rematch" in resp.json()["message"]

    batch, collections = await _stored(async_session_factory, ctx["batch_id"])
    assert collections == set()
    assert batch.status == "rematch"
    assert batch.sample_batch_name == "Renamed Batch"


@pytest.mark.asyncio
async def test_update_with_list_replaces(
    editor_client, async_session_factory, batch_with_collections
):
    """A list is the whole new set: the ones left out go, new ones come, and
    the batch is flagged for rematch."""
    ctx = batch_with_collections
    kept, dropped = ctx["attached"]
    resp = await editor_client.patch(
        f"/api/sample/batches/{ctx['batch_id']}",
        json={"target_collection_ids": [kept, ctx["spare"]]},
    )
    assert resp.status_code == 200, resp.text

    batch, collections = await _stored(async_session_factory, ctx["batch_id"])
    assert collections == {kept, ctx["spare"]}
    assert dropped not in collections
    assert batch.status == "rematch"


@pytest.mark.asyncio
async def test_update_with_same_list_flags_nothing(
    editor_client, async_session_factory, batch_with_collections
):
    """Sending the collections the batch has, as the web app does, changes
    nothing and flags no rematch."""
    ctx = batch_with_collections
    resp = await editor_client.patch(
        f"/api/sample/batches/{ctx['batch_id']}",
        json={
            "sample_batch_name": "Renamed Batch",
            "target_collection_ids": ctx["attached"],
        },
    )
    assert resp.status_code == 200, resp.text

    batch, collections = await _stored(async_session_factory, ctx["batch_id"])
    assert collections == set(ctx["attached"])
    assert batch.status == "ready"
