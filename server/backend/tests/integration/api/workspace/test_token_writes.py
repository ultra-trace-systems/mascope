"""
Tests: the writes opened to API tokens for the SDK answer a real token with the
ACL outcome a session gets.

The flag itself is pinned in ``unit/api/routes/test_token_access_coverage.py``.
Here each opened write is sent twice, once with a real SDK token and once with
the same user's session cookie, and the two must end alike: an editor of the
workspace is let through to the controller, a guest of it is refused, and so
is an editor of another workspace. A token holds its user's rights and nothing
more.

What a write does is not the subject, so each route's controller is replaced
by a stub that records the call: the request still goes through the auth
selector, the role dependency and the handler's own checks (the lock, the
destination's ACL), and nothing a session-scoped fixture holds is changed. One
dataset round trip runs on the real controllers.

Fixtures used:
- ``ws_alpha`` and everything in it (every test user is a member)
- ``ws_beta`` and everything in it (only the owner is a member)
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, Dataset, SampleBatch, hash_access_token
from mascope_backend.db.id import gen_id


_DATASETS = "mascope_backend.api.routes.dataset.dataset_routes"
_BATCHES = "mascope_backend.api.routes.sample.batches.sample_batches_routes"
_SAMPLES = "mascope_backend.api.routes.sample.items.sample_items_routes"
_COLLECTIONS = "mascope_backend.api.routes.target.collections.target_collections_routes"


@dataclass(frozen=True)
class Write:
    """One write opened to tokens, and how to send it for a set of ids."""

    method: str
    #: Formatted with the ids of one workspace's objects (``_ids``).
    path: str
    body: Callable[[dict], Any] | None
    #: The handler's module and the controller it calls, stubbed here.
    module: str
    controller: str
    #: What the handler answers when it gets through.
    status: int
    #: What the stub hands back to the handler.
    result: Any = None
    #: Guarded by the user's global role alone, with no workspace to refuse.
    global_role: bool = False
    #: Tells two cases of one route apart.
    variant: str = ""

    def __str__(self) -> str:
        name = f"{self.method} {self.path}"
        return f"{name} ({self.variant})" if self.variant else name


WRITES = [
    Write(
        "POST",
        "/api/workspaces/{ws}/datasets",
        lambda ids: {"dataset_name": "Token write"},
        _DATASETS,
        "create_dataset",
        201,
    ),
    Write(
        "PATCH",
        "/api/workspaces/{ws}/datasets/{dataset}",
        lambda ids: {"dataset_description": "Written with a token"},
        _DATASETS,
        "update_dataset",
        200,
    ),
    Write(
        "POST",
        "/api/workspaces/{ws}/datasets/{dataset}/move",
        lambda ids: {"target_workspace_id": ids["ws"]},
        _DATASETS,
        "move_dataset",
        200,
    ),
    Write(
        "PATCH",
        "/api/sample/batches/{batch}",
        lambda ids: {
            "sample_batch_description": "Written with a token",
            "target_collection_ids": [],
        },
        _BATCHES,
        "update_sample_batch",
        200,
    ),
    Write(
        "POST",
        "/api/sample/batches/{batch}/copy",
        lambda ids: {"dataset_id": ids["dataset"], "sample_batch_name": "Token copy"},
        _BATCHES,
        "copy_sample_batch",
        202,
    ),
    Write(
        "PATCH",
        "/api/sample/items/{item}",
        lambda ids: {
            "sample_item": {
                "sample_batch_id": ids["batch"],
                "sample_file_id": "file",
                "sample_item_name": "Written with a token",
                "sample_item_type": "SAMPLE",
                "sample_item_attributes": {},
                "tic": 1000.0,
                "polarity": "+",
                "ionization_mode_id": "mode",
                "t0": 0.0,
                "t1": 60.0,
            }
        },
        _SAMPLES,
        "update_sample_item",
        200,
    ),
    Write(
        "POST",
        "/api/sample/items/move",
        lambda ids: {"sample_batch_id": ids["batch"], "sample_item_ids": [ids["item"]]},
        _SAMPLES,
        "move_sample_items",
        202,
    ),
    Write(
        "POST",
        "/api/sample/items/copy",
        lambda ids: {"sample_batch_id": ids["batch"], "sample_item_ids": [ids["item"]]},
        _SAMPLES,
        "copy_sample_items",
        202,
    ),
    Write(
        "POST",
        "/api/target/collections",
        lambda ids: {
            "target_collection_name": "Token collection",
            "workspace_id": ids["ws"],
            "sample_batch_ids": [ids["batch"]],
        },
        _COLLECTIONS,
        "create_target_collection",
        201,
    ),
    Write(
        "PATCH",
        "/api/target/collections/{collection}",
        lambda ids: {"target_collection_description": "Written with a token"},
        _COLLECTIONS,
        "update_target_collection",
        200,
    ),
    # A batches-only update needs only guest on the collection; the editor
    # role it needs is on the batches whose association changes.
    Write(
        "PATCH",
        "/api/target/collections/{collection}",
        lambda ids: {"sample_batch_ids": [ids["unlinked_batch"]]},
        _COLLECTIONS,
        "update_target_collection",
        200,
        variant="batches only",
    ),
    Write(
        "DELETE",
        "/api/workspaces/{ws}/datasets/{dataset}",
        None,
        _DATASETS,
        "delete_dataset",
        200,
    ),
    Write(
        "DELETE",
        "/api/sample/batches/{batch}",
        None,
        _BATCHES,
        "delete_sample_batch",
        202,
    ),
    Write(
        "DELETE",
        "/api/target/collections/{collection}",
        None,
        _COLLECTIONS,
        "delete_target_collection",
        200,
    ),
]

#: The writes whose ACL is a workspace's.
WORKSPACE_WRITES = [write for write in WRITES if not write.global_role]


def _ids(workspace, dataset, batch, item, collection, compound) -> dict[str, str]:
    return {
        "ws": workspace["workspace_id"],
        "dataset": dataset,
        "batch": batch,
        "item": item,
        "collection": collection,
        "compound": compound,
    }


@pytest_asyncio.fixture
async def unlinked_batches(async_session_factory, alpha_dataset, beta_dataset):
    """A batch in each workspace that no collection is associated with.

    Associating it is always a change, which needs the editor role on it. The
    session's ``alpha_batch`` may already be associated with the collection
    by an earlier test, and a batches-only update that changes nothing needs
    no more than guest on the collection.
    """
    batch_ids = {"alpha": gen_id(), "beta": gen_id()}
    datasets = {"alpha": alpha_dataset, "beta": beta_dataset}
    async with async_session_factory() as session:
        for key, batch_id in batch_ids.items():
            session.add(
                SampleBatch(
                    sample_batch_id=batch_id,
                    dataset_id=datasets[key],
                    sample_batch_name="Unlinked Batch",
                    sample_batch_utc_created=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            )
        await session.commit()
    yield batch_ids
    async with async_session_factory() as session:
        await session.execute(
            delete(SampleBatch).where(
                SampleBatch.sample_batch_id.in_(list(batch_ids.values()))
            )
        )
        await session.commit()


@pytest.fixture
def alpha_ids(
    ws_alpha,
    alpha_dataset,
    alpha_batch,
    alpha_item,
    alpha_target_collection,
    test_compound,
    unlinked_batches,
):
    ids = _ids(
        ws_alpha,
        alpha_dataset,
        alpha_batch,
        alpha_item,
        alpha_target_collection,
        test_compound,
    )
    return {**ids, "unlinked_batch": unlinked_batches["alpha"]}


@pytest.fixture
def beta_ids(
    ws_beta,
    beta_dataset,
    beta_batch,
    beta_item,
    beta_target_collection,
    test_compound,
    unlinked_batches,
):
    ids = _ids(
        ws_beta,
        beta_dataset,
        beta_batch,
        beta_item,
        beta_target_collection,
        test_compound,
    )
    return {**ids, "unlinked_batch": unlinked_batches["beta"]}


@pytest_asyncio.fixture
async def token_client(async_session_factory):
    """Make a client sending a real API token of a user, as the SDK does."""
    tokens: list[str] = []
    clients: list[AsyncClient] = []

    async def make(user) -> AsyncClient:
        token = f"writes-{gen_id()}".ljust(43, "x")
        async with async_session_factory() as session:
            session.add(
                AccessToken(
                    token_hash=hash_access_token(token),
                    user_id=user.id,
                    service_name="mascope_sdk",
                )
            )
            await session.commit()
        tokens.append(token)
        client = AsyncClient(
            transport=ASGITransport(app=fast),
            base_url="http://test",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Service-Name": "mascope_sdk",
            },
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.token_hash.in_([hash_access_token(t) for t in tokens])
            )
        )
        await session.commit()


@pytest_asyncio.fixture
async def session_client(create_jwt_auth_token):
    """Make a client signed in as a user with the session cookie."""
    clients: list[AsyncClient] = []

    def make(user) -> AsyncClient:
        client = AsyncClient(
            transport=ASGITransport(app=fast),
            base_url="http://test",
            cookies={auth_settings.COOKIE_NAME: create_jwt_auth_token(user)},
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
def stubbed(monkeypatch):
    """Replace a write's controller with a recorder; return the calls list."""

    def stub(write: Write) -> list[dict]:
        calls: list[dict] = []

        async def controller(*args, **kwargs):
            calls.append(kwargs)
            return write.result if write.result is not None else {"data": {}}

        monkeypatch.setattr(f"{write.module}.{write.controller}", controller)
        return calls

    return stub


async def _send(client: AsyncClient, write: Write, ids: dict):
    body = write.body(ids) if write.body else None
    return await client.request(write.method, write.path.format(**ids), json=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("write", WRITES, ids=str)
async def test_an_editors_token_writes_as_their_session_does(
    write, alpha_ids, test_users, token_client, session_client, stubbed
):
    calls = stubbed(write)
    editor = test_users["editor"]

    by_token = await _send(await token_client(editor), write, alpha_ids)
    by_session = await _send(session_client(editor), write, alpha_ids)

    assert by_token.status_code == write.status, by_token.text
    assert by_session.status_code == write.status, by_session.text
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("write", WRITES, ids=str)
async def test_a_guests_token_is_refused_as_their_session_is(
    write, alpha_ids, test_users, token_client, session_client, stubbed
):
    calls = stubbed(write)
    guest = test_users["guest"]

    by_token = await _send(await token_client(guest), write, alpha_ids)
    by_session = await _send(session_client(guest), write, alpha_ids)

    assert by_token.status_code == 403, by_token.text
    assert by_session.status_code == 403, by_session.text
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("write", WORKSPACE_WRITES, ids=str)
async def test_a_token_writes_nothing_outside_its_users_workspaces(
    write, beta_ids, test_users, token_client, session_client, stubbed
):
    """The editor is no member of ws_beta: the token is accepted, the write is not."""
    calls = stubbed(write)
    editor = test_users["editor"]

    by_token = await _send(await token_client(editor), write, beta_ids)
    by_session = await _send(session_client(editor), write, beta_ids)

    assert by_token.status_code == 403, by_token.text
    assert by_session.status_code == 403, by_session.text
    assert not calls


@pytest_asyncio.fixture
async def locked_dataset(async_session_factory, ws_alpha):
    """A locked dataset in ws_alpha: only a global admin may change it."""
    dataset_id = gen_id()
    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=dataset_id,
                workspace_id=ws_alpha["workspace_id"],
                dataset_name="Locked Dataset",
                dataset_type="ANALYSIS",
                dataset_utc_created=datetime(2026, 1, 1, tzinfo=timezone.utc),
                locked=1,
            )
        )
        await session.commit()
    yield dataset_id
    async with async_session_factory() as session:
        await session.execute(delete(Dataset).where(Dataset.dataset_id == dataset_id))
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "write",
    [w for w in WRITES if w.path == "/api/workspaces/{ws}/datasets/{dataset}"],
    ids=str,
)
async def test_a_lock_refuses_a_token_as_it_refuses_a_session(
    write, alpha_ids, locked_dataset, test_users, token_client, session_client, stubbed
):
    """The lock is checked in the handler, after the token has been let in."""
    calls = stubbed(write)
    editor = test_users["editor"]
    ids = {**alpha_ids, "dataset": locked_dataset}

    by_token = await _send(await token_client(editor), write, ids)
    by_session = await _send(session_client(editor), write, ids)

    assert by_token.status_code == 403, by_token.text
    assert by_session.status_code == 403, by_session.text
    assert not calls


@pytest.mark.asyncio
async def test_a_dataset_round_trip_with_a_token(
    ws_alpha, test_users, token_client, async_session_factory
):
    """Create, rename and delete a dataset with a token, on the real controllers."""
    client = await token_client(test_users["editor"])
    workspace_id = ws_alpha["workspace_id"]

    created = await client.post(
        f"/api/workspaces/{workspace_id}/datasets",
        json={"dataset_name": "Token round trip"},
    )
    assert created.status_code == 201, created.text
    dataset_id = created.json()["data"]["dataset_id"]
    try:
        updated = await client.patch(
            f"/api/workspaces/{workspace_id}/datasets/{dataset_id}",
            json={"dataset_name": "Token round trip, renamed"},
        )
        assert updated.status_code == 200, updated.text

        read = await client.get(f"/api/workspaces/{workspace_id}/datasets/{dataset_id}")
        assert read.json()["data"]["dataset_name"] == "Token round trip, renamed"

        deleted = await client.delete(
            f"/api/workspaces/{workspace_id}/datasets/{dataset_id}"
        )
        assert deleted.status_code == 200, deleted.text
        gone = await client.get(f"/api/workspaces/{workspace_id}/datasets/{dataset_id}")
        assert gone.status_code == 404, gone.text
    finally:
        # A no-op once the route has deleted it; here for a failed assertion.
        async with async_session_factory() as session:
            await session.execute(
                delete(Dataset).where(Dataset.dataset_id == dataset_id)
            )
            await session.commit()


@pytest.mark.asyncio
async def test_a_write_not_opened_still_refuses_a_token(
    alpha_dataset, test_users, token_client
):
    client = await token_client(test_users["editor"])

    resp = await client.post(
        "/api/sample/batches",
        json={
            "dataset_id": alpha_dataset,
            "sample_batch_name": "Not opened",
            "target_collection_ids": [],
        },
    )

    assert resp.status_code == 401, resp.text
