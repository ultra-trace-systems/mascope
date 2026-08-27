"""
Integration tests for ``POST /api/match/rematch/dataset/{dataset_id}``.

The route is the dataset-wide "Refresh matches": it resolves the dataset's
batches and hands them to ``rematch_batches``, which walks them one at a time.
What is pinned here is the route's own contract - who may call it, which
batches it submits and in which order, and what it answers - with the walk
itself stubbed out; the walking is ``rematch_batches``' own behaviour.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

import mascope_backend.api.routes.match.match_routes as match_routes
from mascope_backend.db import Dataset, SampleBatch, Workspace, WorkspaceMember
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def dataset_with_batches(async_session_factory, test_users):
    """A dataset holding two batches, in a workspace every test user is in.

    The newer batch is added to the session *second*, so a route that submits
    the batches in insertion order rather than newest first fails the ordering
    assertion instead of passing by luck.
    """
    ids = {
        "workspace": gen_id(),
        "dataset": gen_id(),
        "older_batch": gen_id(),
        "newer_batch": gen_id(),
    }
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=ids["workspace"],
                workspace_name=f"Rematch WS {ids['workspace']}",
                workspace_description="Dataset rematch test workspace",
                workspace_status="active",
                workspace_utc_created=_NOW,
                workspace_utc_modified=_NOW,
            )
        )
        for role_name, user in test_users.items():
            session.add(
                WorkspaceMember(
                    workspace_member_id=gen_id(),
                    workspace_id=ids["workspace"],
                    user_id=user.id,
                    workspace_role=role_name,
                    granted_at=_NOW,
                    granted_by=user.id,
                )
            )
        session.add(
            Dataset(
                dataset_id=ids["dataset"],
                workspace_id=ids["workspace"],
                dataset_name="Rematch Dataset",
                dataset_description="Dataset rematch test dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["older_batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Older Batch",
                sample_batch_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=ids["newer_batch"],
                dataset_id=ids["dataset"],
                sample_batch_name="Newer Batch",
                sample_batch_utc_created=_NOW + timedelta(hours=1),
            )
        )
        await session.commit()

    return ids


@pytest.fixture
def submitted_rematches(monkeypatch):
    """Record what the route hands to ``rematch_batches`` instead of running it.

    The background task runs inside the request under ASGITransport, so
    without this the whole match machinery would execute for every case here.
    """
    calls = []

    async def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(match_routes, "rematch_batches", _record)
    return calls


@pytest.mark.asyncio
async def test_editor_refreshes_every_batch_newest_first(
    dataset_with_batches, submitted_rematches, editor_client, test_users
):
    """An editor's refresh submits the dataset's batches newest first.

    The recent end of a dataset is the end someone is usually waiting on, so
    a long run over a large dataset reaches it before working backwards.
    """
    response = await editor_client.post(
        f"/api/match/rematch/dataset/{dataset_with_batches['dataset']}"
    )

    assert response.status_code == 202
    message = response.json()["message"]
    assert "2 sample batches" in message
    assert "Rematch Dataset" in message

    assert len(submitted_rematches) == 1
    submitted = submitted_rematches[0]
    assert submitted["sample_batch_ids"] == [
        dataset_with_batches["newer_batch"],
        dataset_with_batches["older_batch"],
    ]
    # The dataset-wide entry is the per-batch "Refresh matches" repeated, not
    # the "Rematch" that rebuilds from scratch: neither flag may default on.
    assert submitted["full_remove"] is False
    assert submitted["force"] is False
    assert submitted["user_id"] == test_users["editor"].id


@pytest.mark.asyncio
async def test_guest_may_not_refresh(
    dataset_with_batches, submitted_rematches, guest_client
):
    """Refreshing writes matches, so a guest is refused before anything runs."""
    response = await guest_client.post(
        f"/api/match/rematch/dataset/{dataset_with_batches['dataset']}"
    )

    assert response.status_code == 403
    assert submitted_rematches == []


@pytest.mark.asyncio
async def test_unknown_dataset_is_not_found(submitted_rematches, owner_client):
    """A superuser passes the workspace check on a dataset that does not exist,
    so the route's own existence check is what answers - 404, not an empty run.
    """
    response = await owner_client.post(f"/api/match/rematch/dataset/{gen_id()}")

    assert response.status_code == 404
    assert submitted_rematches == []


@pytest.mark.asyncio
async def test_empty_dataset_is_accepted(
    async_session_factory, submitted_rematches, editor_client, dataset_with_batches
):
    """A dataset with no batches is still accepted, with nothing to submit.

    The run reports "nothing to refresh" over the notification socket, which
    is the only channel a 202 has - refusing here would leave the click with
    no answer at all.
    """
    empty_dataset_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=empty_dataset_id,
                workspace_id=dataset_with_batches["workspace"],
                dataset_name="Empty Dataset",
                dataset_description="No batches here",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        await session.commit()

    response = await editor_client.post(
        f"/api/match/rematch/dataset/{empty_dataset_id}"
    )

    assert response.status_code == 202
    assert "0 sample batches" in response.json()["message"]
    assert submitted_rematches[0]["sample_batch_ids"] == []
