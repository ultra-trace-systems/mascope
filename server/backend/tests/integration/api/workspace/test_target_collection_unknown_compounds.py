"""
Tests: a collection request naming a compound id the library lacks is refused.

``target_compound_ids`` replaces a collection's compound list. Unknown ids used
to be dropped with an info log, so a PATCH naming only stale ids emptied the
collection and sent its batches to rematch. Create and update now answer 422
naming the unknown ids, and change nothing: no collection, no association, no
compound from ``target_compounds_create``, no batch status.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from mascope_backend.db import (
    SampleBatch,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
)
from mascope_backend.db.id import gen_id
from mascope_backend.db.models import TargetCompoundInTargetCollection


_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def ready_batch(async_session_factory, alpha_dataset, sample_file):
    """A batch of workspace Alpha at "ready", with one sample."""
    batch_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=alpha_dataset,
                sample_batch_name=f"Unknown compounds batch {batch_id}",
                sample_batch_utc_created=_NOW,
                status="ready",
            )
        )
        session.add(
            SampleItem(
                sample_item_id=gen_id(),
                sample_batch_id=batch_id,
                sample_file_id=sample_file,
                sample_item_name="Unknown compounds item",
                sample_item_type="SAMPLE",
                sample_item_attributes={},
                polarity="+",
                tic=1000.0,
                t0=0.0,
                t1=60.0,
                sample_item_utc_created=_NOW,
            )
        )
        await session.commit()
    return batch_id


@pytest_asyncio.fixture
async def collection_on_batch(async_session_factory, ws_alpha, ready_batch):
    """A TARGETS collection of workspace Alpha, on `ready_batch`, with a compound."""
    tc_id = gen_id()
    compound_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCollection(
                target_collection_id=tc_id,
                target_collection_name=f"Targets (unknown ids) {tc_id}",
                target_collection_type="TARGETS",
                workspace_id=ws_alpha["workspace_id"],
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=compound_id,
                target_compound_name=f"Held compound {compound_id}",
                target_compound_formula="C6H12O6",
            )
        )
        await session.flush()
        session.add(
            TargetCompoundInTargetCollection(
                target_collection_id=tc_id, target_compound_id=compound_id
            )
        )
        session.add(
            TargetCollectionInSampleBatch(
                target_collection_id=tc_id, sample_batch_id=ready_batch
            )
        )
        await session.commit()
    return {"collection_id": tc_id, "compound_id": compound_id}


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


async def _batch_status(async_session_factory, batch_id):
    async with async_session_factory() as session:
        return (await session.get(SampleBatch, batch_id)).status


async def _compounds_named(async_session_factory, name):
    async with async_session_factory() as session:
        return await session.scalar(
            select(func.count()).where(TargetCompound.target_compound_name == name)
        )


async def _collections_named(async_session_factory, name):
    async with async_session_factory() as session:
        return await session.scalar(
            select(func.count()).where(TargetCollection.target_collection_name == name)
        )


@pytest.mark.asyncio
async def test_patch_of_only_unknown_ids_is_refused(
    editor_client, async_session_factory, collection_on_batch, ready_batch
):
    unknown = gen_id()
    resp = await editor_client.patch(
        f"/api/target/collections/{collection_on_batch['collection_id']}",
        json={"target_compound_ids": [unknown]},
    )
    assert resp.status_code == 422, resp.text
    assert unknown in resp.json()["error"]
    assert await _compound_ids(
        async_session_factory, collection_on_batch["collection_id"]
    ) == {collection_on_batch["compound_id"]}
    assert await _batch_status(async_session_factory, ready_batch) == "ready"


@pytest.mark.asyncio
async def test_patch_mixing_known_and_unknown_ids_is_refused(
    editor_client, async_session_factory, collection_on_batch, ready_batch
):
    """One unknown id refuses the whole list, though the other id would change it."""
    other = gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCompound(
                target_compound_id=other,
                target_compound_name=f"Other compound {other}",
                target_compound_formula="C2H6O",
            )
        )
        await session.commit()
    unknown = gen_id()
    resp = await editor_client.patch(
        f"/api/target/collections/{collection_on_batch['collection_id']}",
        json={"target_compound_ids": [other, unknown]},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert unknown in body["error"]
    assert other not in body["error"]
    assert await _compound_ids(
        async_session_factory, collection_on_batch["collection_id"]
    ) == {collection_on_batch["compound_id"]}
    assert await _batch_status(async_session_factory, ready_batch) == "ready"


@pytest.mark.asyncio
async def test_patch_with_unknown_id_creates_no_compound(
    editor_client, async_session_factory, collection_on_batch
):
    """The ids are checked before `target_compounds_create` writes anything."""
    name = f"Not to be created {gen_id()}"
    resp = await editor_client.patch(
        f"/api/target/collections/{collection_on_batch['collection_id']}",
        json={
            "target_compound_ids": [collection_on_batch["compound_id"], gen_id()],
            "target_compounds_create": [
                {"target_compound_name": name, "target_compound_formula": "C3H8O"}
            ],
        },
    )
    assert resp.status_code == 422, resp.text
    assert await _compounds_named(async_session_factory, name) == 0
    assert await _compound_ids(
        async_session_factory, collection_on_batch["collection_id"]
    ) == {collection_on_batch["compound_id"]}


@pytest.mark.asyncio
async def test_patch_of_known_ids_still_replaces_the_list(
    editor_client, async_session_factory, collection_on_batch, ready_batch
):
    """The control: a list of known ids, one named twice, replaces as before."""
    other = gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCompound(
                target_compound_id=other,
                target_compound_name=f"Replacing compound {other}",
                target_compound_formula="C2H6O",
            )
        )
        await session.commit()
    resp = await editor_client.patch(
        f"/api/target/collections/{collection_on_batch['collection_id']}",
        json={"target_compound_ids": [other, other]},
    )
    assert resp.status_code == 200, resp.text
    assert await _compound_ids(
        async_session_factory, collection_on_batch["collection_id"]
    ) == {other}
    assert await _batch_status(async_session_factory, ready_batch) == "rematch"


@pytest.mark.asyncio
async def test_create_with_unknown_id_is_refused(
    editor_client, async_session_factory, ws_alpha, test_compound
):
    collection_name = f"Not to be created {gen_id()}"
    compound_name = f"Not to be created {gen_id()}"
    unknown = gen_id()
    resp = await editor_client.post(
        "/api/target/collections",
        json={
            "target_collection_name": collection_name,
            "target_collection_type": "TARGETS",
            "workspace_id": ws_alpha["workspace_id"],
            "target_compound_ids": [test_compound, unknown],
            "target_compounds_create": [
                {
                    "target_compound_name": compound_name,
                    "target_compound_formula": "C3H8O",
                }
            ],
        },
    )
    assert resp.status_code == 422, resp.text
    assert unknown in resp.json()["error"]
    assert await _collections_named(async_session_factory, collection_name) == 0
    assert await _compounds_named(async_session_factory, compound_name) == 0


@pytest.mark.asyncio
async def test_create_with_known_ids_holds_them(
    editor_client, async_session_factory, ws_alpha, test_compound
):
    resp = await editor_client.post(
        "/api/target/collections",
        json={
            "target_collection_name": f"Known ids {gen_id()}",
            "target_collection_type": "TARGETS",
            "workspace_id": ws_alpha["workspace_id"],
            "target_compound_ids": [test_compound, test_compound],
        },
    )
    assert resp.status_code == 201, resp.text
    tc_id = resp.json()["data"]["target_collection_id"]
    assert await _compound_ids(async_session_factory, tc_id) == {test_compound}


@pytest.mark.asyncio
async def test_refusal_names_ten_ids_and_details_all(
    editor_client, collection_on_batch
):
    """The message names the first ten unknown ids; `detail` carries every one."""
    unknown = [gen_id() for _ in range(12)]
    resp = await editor_client.patch(
        f"/api/target/collections/{collection_on_batch['collection_id']}",
        json={"target_compound_ids": unknown},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "12 target compound ID(s) not found" in body["error"]
    assert all(cid in body["error"] for cid in unknown[:10])
    assert not any(cid in body["error"] for cid in unknown[10:])
    assert "and 2 more" in body["error"]
    assert body["detail"]["missing_target_compound_ids"] == unknown
