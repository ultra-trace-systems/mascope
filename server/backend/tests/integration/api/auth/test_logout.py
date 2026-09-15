"""Tests: which credentials end a session (``POST /api/auth/logout``).

Logout takes the session cookie and nothing else. Its credentials are chosen by
``get_enabled_backends``, as on every other authenticated route, so an API token
is refused with 401 here: logout is not ``token_access``. Without the selector
fastapi-users tries each backend in turn, and a bare token authenticated -
without its ``X-Service-Name`` checked, and on a route that does not accept
tokens. That destroyed nothing, but it contradicted what the OpenAPI document
says about tokens and what it declares for this operation (the cookie alone).
"""

from http.cookies import SimpleCookie

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken


LOGOUT_URL = "/api/auth/logout"
SERVICE = "mascope_sdk"
SESSION_COOKIE = auth_settings.COOKIE_NAME


@pytest_asyncio.fixture
async def sdk_token(async_session_factory, test_users):
    """A real Jupyter Notebooks token row for the guest test user."""
    token = "logout-bearer-integration-test".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(
                token=token, user_id=test_users["guest"].id, service_name=SERVICE
            )
        )
        await session.commit()
    yield token
    async with async_session_factory() as session:
        await session.execute(delete(AccessToken).where(AccessToken.token == token))
        await session.commit()


def _client(headers: dict | None = None, cookies: dict | None = None) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers=headers,
        cookies=cookies,
    )


def _set_cookies(response) -> SimpleCookie:
    """Every cookie a response sets, parsed off the raw headers."""
    jar = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        jar.load(header)
    return jar


@pytest.mark.asyncio
@pytest.mark.parametrize("with_service_name", [True, False])
async def test_bearer_only_logout_is_refused(sdk_token, with_service_name):
    headers = {"Authorization": f"Bearer {sdk_token}"}
    if with_service_name:
        headers["X-Service-Name"] = SERVICE
    async with _client(headers) as client:
        resp = await client.post(LOGOUT_URL)
        # The same headers on a token_access route: the token itself is good,
        # so the 401 is the route refusing a token, not a broken credential.
        # Only with its service name - without one the token is refused
        # everywhere, which is the other half of the rule logout used to skip.
        control = await client.get("/api/workspaces")

    assert resp.status_code == 401, resp.text
    assert SESSION_COOKIE not in _set_cookies(resp)
    assert control.status_code == (200 if with_service_name else 401), control.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stray_bearer", [False, True])
async def test_cookie_logout_clears_the_session_cookie(
    sdk_token, test_users, create_jwt_auth_token, stray_bearer
):
    """A session ends as before - including one whose request also carries an
    Authorization header, which the selector ignores once the cookie is there."""
    headers = {"Authorization": f"Bearer {sdk_token}"} if stray_bearer else None
    cookies = {SESSION_COOKIE: create_jwt_auth_token(test_users["guest"])}
    async with _client(headers, cookies) as client:
        resp = await client.post(LOGOUT_URL)

    assert resp.status_code == 204, resp.text
    cleared = _set_cookies(resp)[SESSION_COOKIE]
    assert cleared.value == ""
    assert int(cleared["max-age"]) == 0
