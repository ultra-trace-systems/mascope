"""Tests: the provenance route ``GET /api/provenance``.

The route hands the SDK the provenance block it stamps onto the frames it
returns, so it must answer an API token as well as a signed-in browser, and
it must refuse anonymous callers: the deployment id is not secret, but nothing
an anonymous visitor does needs it.

What the block says is pinned in ``unit/test_provenance_block.py``; here it is
enough that the route serves that block, whole, without ``inputs``.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.api.new.provenance import routes as provenance_routes
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime


DEPLOYMENT_SENTINEL = "provenance-route-test"


@pytest.fixture(autouse=True)
def configured_deployment(monkeypatch):
    """A configured id, so the route reads no real filestore."""
    monkeypatch.setattr(runtime.config, "deployment_id", DEPLOYMENT_SENTINEL)


@pytest_asyncio.fixture
async def sdk_token(async_session_factory, test_users):
    """A real API token of the guest user, issued to the SDK's service name."""
    token = f"provenance-{gen_id()}".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(
                token=token,
                user_id=test_users["guest"].id,
                service_name="mascope_sdk",
            )
        )
        await session.commit()
    yield token
    async with async_session_factory() as session:
        await session.execute(delete(AccessToken).where(AccessToken.token == token))
        await session.commit()


def _client(headers: dict | None = None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test", headers=headers
    )


@pytest.mark.asyncio
async def test_a_signed_in_user_reads_the_block(guest_client, monkeypatch):
    monkeypatch.setattr(runtime, "_version", "v9.9.9-test", raising=False)

    resp = await guest_client.get("/api/provenance")

    assert resp.status_code == 200, resp.text
    block = resp.json()["data"]
    assert block["provenance_version"] == 1
    assert block["deployment_id"] == DEPLOYMENT_SENTINEL
    assert block["produced_with"]["mascope_version"] == "v9.9.9-test"
    assert set(block["produced_with"]) == {
        "mascope_version",
        "match_score_version",
        "peak_assignment_engine_version",
    }
    # The route describes the deployment, not a result built from records
    assert "inputs" not in block


@pytest.mark.asyncio
async def test_the_sdk_reads_it_with_an_api_token(sdk_token):
    """What the SDK sends: a bearer token and its service name."""
    headers = {"Authorization": f"Bearer {sdk_token}", "X-Service-Name": "mascope_sdk"}
    async with _client(headers) as client:
        resp = await client.get("/api/provenance")

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deployment_id"] == DEPLOYMENT_SENTINEL


def test_the_route_is_declared_token_access():
    """Without the flag the auth layer refuses every bearer token before the
    handler runs - invisible from the browser, which signs in with a cookie."""
    assert provenance_routes.get_provenance_route.token_access is True


@pytest.mark.asyncio
async def test_anonymous_callers_are_challenged():
    async with _client() as client:
        resp = await client.get("/api/provenance")

    assert resp.status_code == 401
    assert DEPLOYMENT_SENTINEL not in resp.text
