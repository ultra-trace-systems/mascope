"""Tests: an ``ai-agent`` token, minted by a person and used as the SDK uses one.

An AI agent speaking through the SDK presents a service name of its own, so
its requests are told apart from the person's notebook in the backend log.
The token is the person's own, minted in the settings pane for their account:
it opens the routes a notebook's token opens and no other, and only under its
own name.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from test_utils import captured_logs

from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken


SERVICE = "ai-agent"
REGENERATE_URL = "/api/auth/access_token/regenerate"
#: A read route marked token_access, as the SDK's reads are.
TOKEN_ROUTE = "/api/workspaces"
#: A route that answers only to the web app's session.
SESSION_ROUTE = "/api/users/me"


@pytest_asyncio.fixture
async def ai_agent_token(guest_client, async_session_factory, test_users):
    """An ai-agent token, minted by the guest test user through the route."""
    resp = await guest_client.post(REGENERATE_URL, json={"service_name": SERVICE})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    yield token
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.user_id == test_users["guest"].id,
                AccessToken.service_name == SERVICE,
            )
        )
        await session.commit()


def _token_client(token: str, service_name: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Service-Name": service_name},
    )


def _access_log(records: list[dict], path: str) -> list[dict]:
    """The access-log records for ``path``: the INFO line naming the path."""
    return [
        r for r in records if r["message"] == path and r["extra"].get("path") == path
    ]


@pytest.mark.asyncio
async def test_an_ai_agent_token_opens_a_token_route(ai_agent_token):
    async with _token_client(ai_agent_token, SERVICE) as client:
        resp = await client.get(TOKEN_ROUTE)

    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_an_ai_agent_token_is_refused_on_a_session_route(ai_agent_token):
    async with _token_client(ai_agent_token, SERVICE) as client:
        resp = await client.get(SESSION_ROUTE)

    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_an_ai_agent_token_is_refused_under_the_sdk_name(ai_agent_token):
    # Presenting itself as the notebook is what the name of its own replaces.
    async with _token_client(ai_agent_token, "mascope_sdk") as client:
        resp = await client.get(TOKEN_ROUTE)

    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_the_access_log_names_the_ai_agent(ai_agent_token):
    with captured_logs("INFO") as records:
        async with _token_client(ai_agent_token, SERVICE) as client:
            resp = await client.get(TOKEN_ROUTE)

    assert resp.status_code == 200, resp.text
    logged = _access_log(records, TOKEN_ROUTE)
    assert [r["extra"]["service_name"] for r in logged] == [SERVICE]


@pytest.mark.asyncio
async def test_the_access_log_names_the_ai_agent_on_a_refused_route(ai_agent_token):
    # A token the route turns away is attributed too: the name was checked
    # against the token before the route was.
    with captured_logs("INFO") as records:
        async with _token_client(ai_agent_token, SERVICE) as client:
            resp = await client.get(SESSION_ROUTE)

    assert resp.status_code == 401, resp.text
    logged = _access_log(records, SESSION_ROUTE)
    assert [r["extra"]["service_name"] for r in logged] == [SERVICE]


@pytest.mark.asyncio
async def test_the_access_log_names_no_service_for_the_web_app(guest_client):
    with captured_logs("INFO") as records:
        resp = await guest_client.get(TOKEN_ROUTE)

    assert resp.status_code == 200, resp.text
    logged = _access_log(records, TOKEN_ROUTE)
    assert [r["extra"]["service_name"] for r in logged] == [None]
