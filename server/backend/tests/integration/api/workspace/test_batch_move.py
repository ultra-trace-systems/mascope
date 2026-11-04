"""
Tests: POST /api/sample/batches/{id}/move moves a batch into another dataset.

The batch keeps its ID, its samples and its collections; only its dataset
changes. The rules follow the dataset move: editor on the batch and on the
destination dataset, a locked batch or the dataset it leaves only for a
global admin, a locked destination for nobody, ACQUISITION batches and
datasets refused, a no-op move refused, and the batch's collections checked
against the destination's workspace.

Fixtures used:
- ``ws_alpha`` (every test user is a member) and ``ws_beta`` (only the owner)
- ``sample_file``, ``alpha_target_collection``, ``global_target_collection``

Each test makes datasets and batches of its own (``make_dataset``,
``make_batch``), so the session's shared fixtures are never moved.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from mascope_backend.api.controllers.sample.batches import (
    sample_batches_controller,
)
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleItem,
    TargetCollectionInSampleBatch,
)
from mascope_backend.db.id import gen_id


_LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def make_dataset(async_session_factory):
    """Make a dataset in a workspace; every one is deleted afterwards."""
    made: list[str] = []

    async def make(workspace, *, dataset_type="ANALYSIS", locked=0, name=None):
        dataset_id = gen_id()
        async with async_session_factory() as session:
            session.add(
                Dataset(
                    dataset_id=dataset_id,
                    workspace_id=workspace["workspace_id"],
                    dataset_name=name or f"Move {dataset_id}",
                    dataset_type=dataset_type,
                    dataset_utc_created=_LONG_AGO,
                    dataset_utc_modified=_LONG_AGO,
                    locked=locked,
                )
            )
            await session.commit()
        made.append(dataset_id)
        return dataset_id

    yield make
    async with async_session_factory() as session:
        # Batches cascade with their dataset, and samples with their batch
        await session.execute(delete(Dataset).where(Dataset.dataset_id.in_(made)))
        await session.commit()


@pytest_asyncio.fixture
async def make_batch(async_session_factory, sample_file):
    """Make a batch, with one sample and the given collections, in a dataset."""

    async def make(
        dataset_id,
        *,
        batch_type="ANALYSIS",
        locked=0,
        collections=(),
        name="Moving Batch",
    ):
        batch_id = gen_id()
        item_id = gen_id()
        async with async_session_factory() as session:
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=dataset_id,
                    sample_batch_name=name,
                    sample_batch_type=batch_type,
                    locked=locked,
                    sample_batch_utc_created=_LONG_AGO,
                )
            )
            await session.flush()
            session.add(
                SampleItem(
                    sample_item_id=item_id,
                    sample_batch_id=batch_id,
                    sample_file_id=sample_file,
                    sample_item_name="Moving Item",
                    sample_item_type="ANALYSIS",
                    sample_item_attributes={},
                    polarity="+",
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_LONG_AGO,
                )
            )
            for collection_id in collections:
                session.add(
                    TargetCollectionInSampleBatch(
                        target_collection_id=collection_id,
                        sample_batch_id=batch_id,
                    )
                )
            await session.commit()
        return {"batch": batch_id, "item": item_id}

    return make


async def _dataset_of(async_session_factory, batch_id):
    async with async_session_factory() as session:
        return await session.scalar(
            select(SampleBatch.dataset_id).where(
                SampleBatch.sample_batch_id == batch_id
            )
        )


def _move(client, batch_id, dataset_id):
    return client.post(
        f"/api/sample/batches/{batch_id}/move", json={"dataset_id": dataset_id}
    )


@pytest.mark.asyncio
async def test_a_batch_moves_with_its_samples_and_collections(
    editor_client,
    ws_alpha,
    make_dataset,
    make_batch,
    alpha_target_collection,
    async_session_factory,
):
    source = await make_dataset(ws_alpha)
    dest = await make_dataset(ws_alpha)
    made = await make_batch(source, collections=[alpha_target_collection])
    # Making the batch bumped its dataset: set both back to see the move's bump
    async with async_session_factory() as session:
        await session.execute(
            update(Dataset)
            .where(Dataset.dataset_id.in_([source, dest]))
            .values(dataset_utc_modified=_LONG_AGO)
        )
        await session.commit()

    resp = await _move(editor_client, made["batch"], dest)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["sample_batch_id"] == made["batch"]
    assert data["dataset_id"] == dest
    async with async_session_factory() as session:
        batch = await session.get(SampleBatch, made["batch"])
        assert batch.dataset_id == dest
        item = await session.get(SampleItem, made["item"])
        assert item.sample_batch_id == made["batch"]
        linked = (
            await session.scalars(
                select(TargetCollectionInSampleBatch.target_collection_id).where(
                    TargetCollectionInSampleBatch.sample_batch_id == made["batch"]
                )
            )
        ).all()
        assert linked == [alpha_target_collection]
        # Both datasets changed: the one it left and the one it went into
        for dataset_id in (source, dest):
            dataset = await session.get(Dataset, dataset_id)
            assert dataset.dataset_utc_modified > _LONG_AGO, dataset_id


@pytest.mark.asyncio
async def test_the_move_tells_both_datasets_rooms(
    editor_client, ws_alpha, make_dataset, make_batch, monkeypatch
):
    source = await make_dataset(ws_alpha)
    dest = await make_dataset(ws_alpha)
    made = await make_batch(source)
    events = []

    async def deleted(**kwargs):
        events.append(("deleted", kwargs["record_id"], kwargs["room"]))

    async def created(**kwargs):
        events.append(("created", kwargs["record_id"], kwargs["room"]))
        assert kwargs["record"]["dataset_id"] == dest

    monkeypatch.setattr(sample_batches_controller, "emit_record_deleted", deleted)
    monkeypatch.setattr(sample_batches_controller, "emit_record_created", created)

    resp = await _move(editor_client, made["batch"], dest)

    assert resp.status_code == 200, resp.text
    assert events == [
        ("deleted", made["batch"], source),
        ("created", made["batch"], dest),
    ]


@pytest.mark.asyncio
async def test_a_batch_moves_into_a_dataset_of_another_workspace(
    owner_client,
    ws_alpha,
    ws_beta,
    make_dataset,
    make_batch,
    global_target_collection,
    async_session_factory,
):
    """A global collection goes anywhere; the owner is editor in both."""
    source = await make_dataset(ws_alpha)
    dest = await make_dataset(ws_beta)
    made = await make_batch(source, collections=[global_target_collection])

    resp = await _move(owner_client, made["batch"], dest)

    assert resp.status_code == 200, resp.text
    assert await _dataset_of(async_session_factory, made["batch"]) == dest


@pytest.mark.asyncio
async def test_a_collection_of_the_old_workspace_refuses_the_move(
    owner_client,
    ws_alpha,
    ws_beta,
    make_dataset,
    make_batch,
    alpha_target_collection,
    async_session_factory,
):
    source = await make_dataset(ws_alpha)
    dest = await make_dataset(ws_beta)
    made = await make_batch(source, collections=[alpha_target_collection])

    resp = await _move(owner_client, made["batch"], dest)

    assert resp.status_code == 409, resp.text
    assert await _dataset_of(async_session_factory, made["batch"]) == source


@pytest.mark.asyncio
async def test_a_name_the_destination_has_does_not_refuse_the_move(
    editor_client, ws_alpha, make_dataset, make_batch
):
    """Batch names may repeat in a dataset, as batch create allows."""
    source = await make_dataset(ws_alpha)
    dest = await make_dataset(ws_alpha)
    await make_batch(dest, name="Same Name")
    made = await make_batch(source, name="Same Name")

    resp = await _move(editor_client, made["batch"], dest)

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("batch_type", "dest_type", "same", "detail"),
    [
        ("ACQUISITION", "ANALYSIS", False, "Acquisition batches cannot be moved"),
        ("ANALYSIS", "ACQUISITION", False, "into an acquisition dataset"),
        ("ANALYSIS", "ANALYSIS", True, "already in the target dataset"),
    ],
    ids=["acquisition batch", "acquisition dataset", "same dataset"],
)
async def test_a_move_the_rules_forbid_is_a_client_error(
    admin_client,
    ws_alpha,
    make_dataset,
    make_batch,
    async_session_factory,
    batch_type,
    dest_type,
    same,
    detail,
):
    source = await make_dataset(ws_alpha)
    dest = source if same else await make_dataset(ws_alpha, dataset_type=dest_type)
    made = await make_batch(source, batch_type=batch_type)

    resp = await _move(admin_client, made["batch"], dest)

    assert resp.status_code == 400, resp.text
    assert detail in resp.text
    assert await _dataset_of(async_session_factory, made["batch"]) == source


@pytest.mark.asyncio
async def test_a_guest_cannot_move_a_batch(
    guest_client, ws_alpha, make_dataset, make_batch, async_session_factory
):
    source = await make_dataset(ws_alpha)
    dest = await make_dataset(ws_alpha)
    made = await make_batch(source)

    resp = await _move(guest_client, made["batch"], dest)

    assert resp.status_code == 403, resp.text
    assert await _dataset_of(async_session_factory, made["batch"]) == source


@pytest.mark.asyncio
async def test_an_editor_cannot_move_a_batch_into_a_workspace_of_another(
    editor_client, ws_alpha, ws_beta, make_dataset, make_batch, async_session_factory
):
    source = await make_dataset(ws_alpha)
    dest = await make_dataset(ws_beta)
    made = await make_batch(source)

    resp = await _move(editor_client, made["batch"], dest)

    assert resp.status_code == 403, resp.text
    assert await _dataset_of(async_session_factory, made["batch"]) == source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("locked", "editor_status", "admin_status"),
    [
        ("batch", 403, 200),
        ("source", 403, 200),
        ("destination", 403, 403),
    ],
)
async def test_a_lock_refuses_the_move(
    editor_client,
    admin_client,
    ws_alpha,
    make_dataset,
    make_batch,
    async_session_factory,
    locked,
    editor_status,
    admin_status,
):
    """A locked batch, or the dataset it leaves, needs the global admin role;
    a locked destination takes no batch, as for a copy."""
    source = await make_dataset(ws_alpha, locked=int(locked == "source"))
    dest = await make_dataset(ws_alpha, locked=int(locked == "destination"))
    made = await make_batch(source, locked=int(locked == "batch"))

    by_editor = await _move(editor_client, made["batch"], dest)
    assert by_editor.status_code == editor_status, by_editor.text
    assert await _dataset_of(async_session_factory, made["batch"]) == source

    by_admin = await _move(admin_client, made["batch"], dest)
    assert by_admin.status_code == admin_status, by_admin.text
    expected = dest if admin_status == 200 else source
    assert await _dataset_of(async_session_factory, made["batch"]) == expected


@pytest.mark.asyncio
async def test_the_controller_refuses_a_destination_that_does_not_exist(
    ws_alpha, make_dataset, make_batch, async_session_factory
):
    source = await make_dataset(ws_alpha)
    made = await make_batch(source)

    with pytest.raises(ApiException) as raised:
        await sample_batches_controller.move_sample_batch(
            sample_batch_id=made["batch"], source_dataset_id=source, dataset_id=gen_id()
        )

    assert raised.value.status_code == 404
    assert await _dataset_of(async_session_factory, made["batch"]) == source


@pytest.mark.asyncio
async def test_a_batch_moved_since_the_route_checked_it_is_not_moved(
    ws_alpha, make_dataset, make_batch, async_session_factory
):
    """The controller moves the batch only out of the dataset the route
    authorised; one moved in between is not found there."""
    checked = await make_dataset(ws_alpha)
    now_in = await make_dataset(ws_alpha)
    dest = await make_dataset(ws_alpha)
    made = await make_batch(now_in)

    with pytest.raises(ApiException) as raised:
        await sample_batches_controller.move_sample_batch(
            sample_batch_id=made["batch"], source_dataset_id=checked, dataset_id=dest
        )

    assert raised.value.status_code == 404
    assert await _dataset_of(async_session_factory, made["batch"]) == now_in
