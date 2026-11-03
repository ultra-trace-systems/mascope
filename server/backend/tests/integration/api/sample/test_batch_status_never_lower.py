"""
Tests: a ``rematch`` flag never lowers a pending ``recalibrate``.

``update_sample_batch_status(..., never_lower=True)`` leaves a batch with
samples alone when it already waits on a stronger pending status: a
recalibration ends in a rematch, so it covers one. The edit flaggers that ask
for a rematch pass it - the ionization-mode edit
(``tests/integration/api/ionization/test_ionization_mode_update.py``) and the
target collection create, update and delete covered here through their
routes. A batch without samples still goes to ``ready``, and the socket event
carries the status each batch holds, not the one asked for.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from mascope_backend.api.controllers.sample.batches.status import service
from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    Workspace,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NOW_NAIVE = datetime(2026, 1, 1)


@pytest_asyncio.fixture(scope="session")
async def status_dataset(async_session_factory):
    """A workspace, dataset and sample file to host the batches."""
    workspace_id = gen_id()
    dataset_id = gen_id()
    file_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name="Batch Status Workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name="Batch Status Dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleFile(
                sample_file_id=file_id,
                filename=f"test-orbion_{file_id}.raw",
                instrument="test-orbion",
                datetime=_NOW_NAIVE,
                datetime_utc=_NOW,
                length=60.0,
                range={"min": 0, "max": 500},
                polarity="+",
            )
        )
        await session.commit()
    return {"dataset_id": dataset_id, "file_id": file_id}


async def _batch(async_session_factory, ctx, status, with_sample=True):
    """A batch at ``status``, with one sample item unless ``with_sample`` is off."""
    batch_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            SampleBatch(
                sample_batch_id=batch_id,
                dataset_id=ctx["dataset_id"],
                sample_batch_name=f"Status Batch {batch_id}",
                sample_batch_utc_created=_NOW,
                status=status,
            )
        )
        if with_sample:
            session.add(
                SampleItem(
                    sample_item_id=gen_id(),
                    sample_batch_id=batch_id,
                    sample_file_id=ctx["file_id"],
                    sample_item_name="Status Item",
                    sample_item_type="ANALYSIS",
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


async def _collection(async_session_factory, batch_id=None):
    """A global TARGETS collection with one compound, optionally on a batch."""
    collection_id = gen_id()
    compound_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            TargetCollection(
                target_collection_id=collection_id,
                target_collection_name=f"Status Targets {collection_id}",
                target_collection_type="TARGETS",
                workspace_id=None,
            )
        )
        session.add(
            TargetCompound(
                target_compound_id=compound_id,
                target_compound_name=f"Status Compound {compound_id}",
                target_compound_formula="C6H12O6",
            )
        )
        await session.flush()
        session.add(
            TargetCompoundInTargetCollection(
                target_compound_id=compound_id,
                target_collection_id=collection_id,
            )
        )
        if batch_id is not None:
            session.add(
                TargetCollectionInSampleBatch(
                    target_collection_id=collection_id,
                    sample_batch_id=batch_id,
                )
            )
        await session.commit()
    return collection_id, compound_id


async def _status(async_session_factory, batch_id):
    async with async_session_factory() as session:
        return (await session.get(SampleBatch, batch_id)).status


# ============= The service =============


@pytest.mark.asyncio
async def test_never_lower_keeps_recalibrate_and_emits_it(
    async_session_factory, status_dataset, monkeypatch
):
    """A guarded rematch keeps 'recalibrate', raises 'ready', and emits each."""
    emit = AsyncMock()
    monkeypatch.setattr(service, "emit_record_updated", emit)
    pending = await _batch(async_session_factory, status_dataset, "recalibrate")
    idle = await _batch(async_session_factory, status_dataset, "ready")

    result = await service.update_sample_batch_status(
        sample_batch_ids=[pending, idle],
        status="rematch",
        independent_transaction=True,
        never_lower=True,
    )

    assert await _status(async_session_factory, pending) == "recalibrate"
    assert await _status(async_session_factory, idle) == "rematch"
    assert result["data"]["updated_batches"] == 1
    emitted = {
        call.kwargs["record_id"]: call.kwargs["record"]["status"]
        for call in emit.await_args_list
    }
    assert emitted == {pending: "recalibrate", idle: "rematch"}


@pytest.mark.asyncio
async def test_unguarded_rematch_still_lowers(async_session_factory, status_dataset):
    """Without the guard the requested status is written, as other callers expect."""
    batch_id = await _batch(async_session_factory, status_dataset, "recalibrate")

    await service.update_sample_batch_status(
        sample_batch_ids=[batch_id], status="rematch"
    )

    assert await _status(async_session_factory, batch_id) == "rematch"


@pytest.mark.asyncio
async def test_never_lower_still_readies_a_batch_without_samples(
    async_session_factory, status_dataset
):
    """A batch with no samples has nothing to recalibrate: it goes to 'ready'."""
    batch_id = await _batch(
        async_session_factory, status_dataset, "recalibrate", with_sample=False
    )

    await service.update_sample_batch_status(
        sample_batch_ids=[batch_id], status="rematch", never_lower=True
    )

    assert await _status(async_session_factory, batch_id) == "ready"


# ============= The target collection flaggers =============


@pytest.mark.asyncio
async def test_creating_a_collection_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset
):
    """Creating a collection on a batch pending a recalibration keeps the flag."""
    batch_id = await _batch(async_session_factory, status_dataset, "recalibrate")
    _, compound_id = await _collection(async_session_factory)

    resp = await owner_client.post(
        "/api/target/collections",
        json={
            "target_collection_name": f"Status Created {batch_id}",
            "target_collection_type": "TARGETS",
            "target_compound_ids": [compound_id],
            "sample_batch_ids": [batch_id],
        },
    )

    assert resp.status_code == 201, resp.text
    assert await _status(async_session_factory, batch_id) == "recalibrate"


@pytest.mark.asyncio
async def test_updating_a_collection_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset
):
    """Changing a batch's collection compounds keeps a pending recalibrate."""
    batch_id = await _batch(async_session_factory, status_dataset, "recalibrate")
    collection_id, compound_id = await _collection(async_session_factory, batch_id)
    _, other_compound_id = await _collection(async_session_factory)

    resp = await owner_client.patch(
        f"/api/target/collections/{collection_id}",
        json={"target_compound_ids": [compound_id, other_compound_id]},
    )

    assert resp.status_code == 200, resp.text
    assert await _status(async_session_factory, batch_id) == "recalibrate"


@pytest.mark.asyncio
async def test_deleting_a_collection_keeps_recalibrate(
    owner_client, async_session_factory, status_dataset
):
    """Deleting a batch's collection keeps a pending recalibrate."""
    batch_id = await _batch(async_session_factory, status_dataset, "recalibrate")
    collection_id, _ = await _collection(async_session_factory, batch_id)

    resp = await owner_client.delete(f"/api/target/collections/{collection_id}")

    assert resp.status_code == 200, resp.text
    assert await _status(async_session_factory, batch_id) == "recalibrate"


@pytest.mark.asyncio
async def test_updating_a_collection_still_flags_a_ready_batch(
    owner_client, async_session_factory, status_dataset
):
    """The guard leaves the ordinary flag alone: a ready batch goes to rematch."""
    batch_id = await _batch(async_session_factory, status_dataset, "ready")
    collection_id, compound_id = await _collection(async_session_factory, batch_id)
    _, other_compound_id = await _collection(async_session_factory)

    resp = await owner_client.patch(
        f"/api/target/collections/{collection_id}",
        json={"target_compound_ids": [compound_id, other_compound_id]},
    )

    assert resp.status_code == 200, resp.text
    assert await _status(async_session_factory, batch_id) == "rematch"
