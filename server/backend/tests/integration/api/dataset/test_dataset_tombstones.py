"""
Tests: dataset tombstones (FAIR A2) - what a deleted dataset was, kept after it.

Both delete paths write one: the dataset route (``deleted_via="dataset"``) and
workspace deletion, which removes its datasets by cascade without the dataset
controller (``deleted_via="workspace"``). ``GET /api/datasets/tombstones``
reads them back: a member reads its workspaces' rows, a global admin or owner
every row, those of a deleted workspace included.

Tombstones accumulate in the session's database, so each test reads back the
rows of its own datasets by id.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from mascope_backend.api.new.dataset_tombstones import routes as tombstone_routes
from mascope_backend.db import (
    Dataset,
    DatasetTombstone,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime


DEPLOYMENT = "tombstone-test"

URL = "/api/datasets/tombstones"

METADATA = {
    "license": "CC-BY-4.0",
    "authors": [{"name": "A. Author", "orcid": "0000-0002-1825-0097"}],
    "keywords": ["aerosol", "field campaign"],
    "related_identifiers": [{"identifier": "10.1234/x", "relation": "IsSupplementTo"}],
    "funding": [{"funder": "A funder", "award": "123"}],
}

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def configured_deployment(monkeypatch):
    """A configured id, so the provenance block reads no real filestore."""
    monkeypatch.setattr(runtime.config, "deployment_id", DEPLOYMENT)


async def _workspace(async_session_factory, members: dict) -> tuple[str, str]:
    """A workspace with *members* (user -> workspace role); (id, name)."""
    workspace_id = gen_id()
    name = f"Tombstone workspace {workspace_id}"
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace_id,
                workspace_name=name,
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        for user, role in members.items():
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(),
                    workspace_id=workspace_id,
                    user_id=user.id,
                    workspace_role=role,
                    granted_at=_NOW,
                )
            )
        await session.commit()
    return workspace_id, name


async def _dataset(
    async_session_factory, workspace_id: str, dataset_id: str | None = None
) -> str:
    """A dataset with metadata: two batches, three samples on two files."""
    dataset_id = dataset_id or gen_id()
    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=workspace_id,
                dataset_name=f"Campaign {dataset_id}",
                dataset_description="What was measured",
                dataset_type="ANALYSIS",
                instrument="instrument-x",
                dataset_utc_created=_NOW,
                dataset_utc_modified=_NOW,
                **METADATA,
            )
        )
        files = [gen_id() for _ in range(2)]
        for file_id in files:
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    filename=f"test-orbion_{file_id}.raw",
                    # A name the instrument type resolves from
                    instrument="test-orbion",
                    datetime=_NOW.replace(tzinfo=None),
                    datetime_utc=_NOW,
                    length=60.0,
                    range={"min": 0, "max": 500},
                    polarity="+",
                )
            )
        batches = [gen_id() for _ in range(2)]
        for batch_id in batches:
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=dataset_id,
                    sample_batch_name=f"Batch {batch_id}",
                    sample_batch_utc_created=_NOW,
                )
            )
        await session.flush()
        # Two samples of one file in the first batch, one of the other file in
        # the second: three samples, two distinct files
        for batch_id, file_id in [
            (batches[0], files[0]),
            (batches[0], files[0]),
            (batches[1], files[1]),
        ]:
            session.add(
                SampleItem(
                    sample_item_id=gen_id(),
                    sample_batch_id=batch_id,
                    sample_file_id=file_id,
                    sample_item_name="Sample",
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
    return dataset_id


async def _read(client, dataset_ids) -> dict[str, dict]:
    resp = await client.get(URL)
    assert resp.status_code == 200, resp.text
    return {
        row["dataset_id"]: row
        for row in resp.json()["data"]
        if row["dataset_id"] in set(dataset_ids)
    }


async def _stored(async_session_factory, dataset_id: str) -> list[DatasetTombstone]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatasetTombstone).where(DatasetTombstone.dataset_id == dataset_id)
        )
        return list(result.scalars().all())


@pytest_asyncio.fixture
async def members(test_users):
    return {
        test_users["owner"]: "owner",
        test_users["editor"]: "editor",
        test_users["guest"]: "guest",
    }


@pytest.mark.asyncio
async def test_deleting_a_dataset_leaves_its_tombstone(
    async_session_factory, test_users, members, editor_client, guest_client
):
    workspace_id, workspace_name = await _workspace(async_session_factory, members)
    dataset_id = await _dataset(async_session_factory, workspace_id)
    async with async_session_factory() as session:
        modified = (await session.get(Dataset, dataset_id)).dataset_utc_modified

    resp = await editor_client.delete(
        f"/api/workspaces/{workspace_id}/datasets/{dataset_id}"
    )
    assert resp.status_code == 200, resp.text

    row = (await _read(guest_client, [dataset_id]))[dataset_id]
    assert row["workspace_id"] == workspace_id
    assert row["workspace_name"] == workspace_name
    assert row["dataset_name"] == f"Campaign {dataset_id}"
    assert row["dataset_description"] == "What was measured"
    assert row["dataset_type"] == "ANALYSIS"
    assert row["instrument"] == "instrument-x"
    assert row["descriptive_metadata"] == METADATA
    assert (row["n_sample_batches"], row["n_sample_items"], row["n_sample_files"]) == (
        2,
        3,
        2,
    )
    assert row["dataset_utc_created"].startswith("2026-10-01T12:00:00")
    assert datetime.fromisoformat(row["dataset_utc_modified"]) == modified
    assert row["deleted_utc"] is not None
    assert row["deleted_by"] == test_users["editor"].email
    assert row["deleted_via"] == "dataset"
    assert row["provenance"]["deployment_id"] == DEPLOYMENT
    assert row["provenance"]["inputs"] == {
        "dataset_id": dataset_id,
        "dataset_global_id": f"{DEPLOYMENT}:dataset:{dataset_id}",
    }
    assert "mascope_version" in row["provenance"]["produced_with"]

    # The data itself is gone
    async with async_session_factory() as session:
        assert await session.get(Dataset, dataset_id) is None


@pytest.mark.asyncio
async def test_deleting_a_workspace_leaves_a_tombstone_per_dataset(
    async_session_factory,
    test_users,
    members,
    owner_client,
    admin_client,
    editor_client,
):
    workspace_id, workspace_name = await _workspace(async_session_factory, members)
    dataset_ids = [
        await _dataset(async_session_factory, workspace_id) for _ in range(2)
    ]

    resp = await owner_client.delete(f"/api/workspaces/{workspace_id}")
    assert resp.status_code == 200, resp.text

    async with async_session_factory() as session:
        assert await session.get(Workspace, workspace_id) is None

    # The workspace is gone, and its memberships with it: a global admin or
    # owner still reads its tombstones, its former editor does not
    for client in (owner_client, admin_client):
        rows = await _read(client, dataset_ids)
        assert set(rows) == set(dataset_ids)
        for dataset_id, row in rows.items():
            assert row["deleted_via"] == "workspace"
            assert row["deleted_by"] == test_users["owner"].email
            assert row["workspace_id"] == workspace_id
            assert row["workspace_name"] == workspace_name
            assert row["dataset_name"] == f"Campaign {dataset_id}"
            assert row["descriptive_metadata"] == METADATA
            assert row["n_sample_items"] == 3
    assert await _read(editor_client, dataset_ids) == {}


@pytest.mark.asyncio
async def test_a_non_member_reads_no_tombstone_and_a_global_admin_reads_it(
    async_session_factory, test_users, owner_client, admin_client, guest_client
):
    # Only the owner is a member; the admin is a global admin, not a member
    workspace_id, _ = await _workspace(
        async_session_factory, {test_users["owner"]: "owner"}
    )
    dataset_id = await _dataset(async_session_factory, workspace_id)
    resp = await owner_client.delete(
        f"/api/workspaces/{workspace_id}/datasets/{dataset_id}"
    )
    assert resp.status_code == 200, resp.text

    assert await _read(guest_client, [dataset_id]) == {}
    assert set(await _read(admin_client, [dataset_id])) == {dataset_id}


@pytest.mark.asyncio
async def test_a_dataset_deleted_again_overwrites_its_tombstone(
    async_session_factory, test_users, members, editor_client, owner_client
):
    workspace_id, _ = await _workspace(async_session_factory, members)
    dataset_id = await _dataset(async_session_factory, workspace_id)
    resp = await editor_client.delete(
        f"/api/workspaces/{workspace_id}/datasets/{dataset_id}"
    )
    assert resp.status_code == 200, resp.text
    first = await _stored(async_session_factory, dataset_id)

    # Restored from a dump, under its old id, and deleted again - this time
    # with its workspace
    await _dataset(async_session_factory, workspace_id, dataset_id=dataset_id)
    resp = await owner_client.delete(f"/api/workspaces/{workspace_id}")
    assert resp.status_code == 200, resp.text

    [row] = await _stored(async_session_factory, dataset_id)
    assert row.deleted_via == "workspace"
    assert row.deleted_by == test_users["owner"].email
    assert row.deleted_utc > first[0].deleted_utc


@pytest.mark.asyncio
async def test_the_listing_pages_newest_first(
    async_session_factory, members, editor_client, owner_client
):
    workspace_id, _ = await _workspace(async_session_factory, members)
    older, newer = [
        await _dataset(async_session_factory, workspace_id) for _ in range(2)
    ]
    for dataset_id in (older, newer):
        resp = await editor_client.delete(
            f"/api/workspaces/{workspace_id}/datasets/{dataset_id}"
        )
        assert resp.status_code == 200, resp.text

    everything = (await owner_client.get(URL)).json()
    ids = [row["dataset_id"] for row in everything["data"]]
    assert ids.index(newer) < ids.index(older)
    assert everything["total"] == len(ids)

    page = (await owner_client.get(URL, params={"page": 0, "limit": 1})).json()
    assert len(page["data"]) == 1
    assert page["total"] == everything["total"]


def test_the_route_is_declared_token_access():
    """The SDK reads it with an API token."""
    assert tombstone_routes.get_tombstones_route.token_access is True
