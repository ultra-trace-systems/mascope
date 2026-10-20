"""
Tests: the reads opened to API tokens for the AI toolkit answer a real SDK
token, and keep their workspace ACL.

The flag itself is pinned in ``unit/api/routes/test_token_access_coverage.py``;
here a guest's token, sent the way the SDK sends it, goes through the auth
selector, the role dependency and the handler of each route. A token holds its
user's rights and nothing more, so a batch of a workspace the user is not a
member of is still refused, and a read the toolkit does not need still answers
a token with 401.

Fixtures used:
- ``ws_alpha`` and everything in it (every test user is a member)
- ``beta_batch`` - in ``ws_beta``, where only the owner is a member
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text

from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, Sample, hash_access_token
from mascope_backend.db.id import gen_id


@pytest_asyncio.fixture(scope="session")
async def sample_view(async_engine):
    """The ``sample_view`` VIEW the aggregate preview reads samples through.

    ``Base.metadata.create_all`` does not create it; see
    ``integration/api/match/conftest.py``, which does the same.
    """
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def sdk_client(async_session_factory, test_users):
    """A client sending a real API token of the guest user, as the SDK does."""
    token = f"toolkit-{gen_id()}".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(
                token_hash=hash_access_token(token),
                user_id=test_users["guest"].id,
                service_name="mascope_sdk",
            )
        )
        await session.commit()
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Service-Name": "mascope_sdk",
        },
    ) as client:
        yield client
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.token_hash == hash_access_token(token)
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_the_collections_listing(sdk_client, alpha_target_collection):
    resp = await sdk_client.get("/api/target/collections")

    assert resp.status_code == 200, resp.text
    ids = {each["target_collection_id"] for each in resp.json()["data"]}
    assert alpha_target_collection in ids


@pytest.mark.asyncio
async def test_one_collection(sdk_client, alpha_target_collection):
    resp = await sdk_client.get(f"/api/target/collections/{alpha_target_collection}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["target_collection_id"] == alpha_target_collection


@pytest.mark.asyncio
async def test_the_collections_of_a_batch(sdk_client, alpha_collection_in_batch):
    collection_id, batch_id = alpha_collection_in_batch

    resp = await sdk_client.get(
        "/api/target/associations/target_collections_in_sample_batch",
        params={"sample_batch_id": batch_id},
    )

    assert resp.status_code == 200, resp.text
    ids = {each["target_collection_id"] for each in resp.json()["data"]}
    assert collection_id in ids


@pytest.mark.asyncio
async def test_one_batch(sdk_client, alpha_batch):
    resp = await sdk_client.get(f"/api/sample/batches/{alpha_batch}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["sample_batch_id"] == alpha_batch


@pytest.mark.asyncio
async def test_one_dataset(sdk_client, ws_alpha, alpha_dataset):
    workspace_id = ws_alpha["workspace_id"]

    resp = await sdk_client.get(
        f"/api/workspaces/{workspace_id}/datasets/{alpha_dataset}"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["dataset_id"] == alpha_dataset


@pytest.mark.asyncio
async def test_the_aggregate_preview_of_a_sample(sdk_client, sample_view, alpha_item):
    resp = await sdk_client.post(f"/api/match/aggregate/sample/{alpha_item}", json={})

    # Nothing is matched in the seeded sample: the preview says so, no error.
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == 0


@pytest.mark.asyncio
async def test_the_aggregate_preview_of_a_batch(sdk_client, sample_view, alpha_batch):
    resp = await sdk_client.post(f"/api/match/aggregate/batch/{alpha_batch}", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["results"] == 0


@pytest.mark.asyncio
async def test_a_token_keeps_its_users_workspace_acl(sdk_client, beta_batch):
    """The guest is no member of ws_beta: the token is accepted, the read is not."""
    resp = await sdk_client.post(f"/api/match/aggregate/batch/{beta_batch}", json={})

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_a_read_the_toolkit_does_not_need_still_refuses_a_token(
    sdk_client, test_compound
):
    resp = await sdk_client.get(f"/api/target/compounds/{test_compound}")

    assert resp.status_code == 401, resp.text
