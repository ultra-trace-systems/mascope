"""Tests: the real login route end-to-end (``POST /api/auth/login``).

The other integration tests mint JWTs directly, so nothing else exercises the
fastapi-users login flow - including the ``on_after_login`` hook, which
re-reads the cached login form to clear the per-account rate-limit counter on
a successful sign-in and refreshes the file-converter access token for
editor+ accounts. These tests pin that a real password login works with the
limiter dependencies and the clearing hook wired in (both fail open when
Redis is not connected, as in this ASGI test setup), and that the converter
token is minted for every editor+ sign-in - API logins without a socket sid
included, since uploads fail without it.
"""

import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, User


LOGIN_EMAIL = "login-flow@test.com"
LOGIN_PASSWORD = "correct-horse-battery-staple"


def _login_user_fixture(role_name: str, email: str, username: str):
    """Build a login-user fixture for the given role.

    The user gets a real (verifiable) password hash and is removed - along
    with any access tokens the login hook minted - afterwards.
    """

    @pytest_asyncio.fixture
    async def _fixture(async_session_factory, roles):
        async with async_session_factory() as session:
            user = User(
                email=email,
                username=username,
                hashed_password=PasswordHelper().hash(LOGIN_PASSWORD),
                is_active=True,
                is_verified=True,
                role_id=roles[role_name].role_id,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        yield user
        async with async_session_factory() as session:
            await session.execute(
                delete(AccessToken).where(AccessToken.user_id == user.id)
            )
            db_user = await session.get(User, user.id)
            if db_user is not None:
                await session.delete(db_user)
            await session.commit()

    return _fixture


login_user = _login_user_fixture("guest", LOGIN_EMAIL, "login_flow_user")
editor_login_user = _login_user_fixture(
    "editor", "login-flow-editor@test.com", "login_flow_editor"
)


@pytest.mark.asyncio
async def test_wrong_password_is_rejected(login_user):
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": LOGIN_EMAIL, "password": "wrong-password"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_successful_login_sets_auth_cookie(login_user):
    """A correct password logs in; on_after_login must not break the flow."""
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        )
    assert resp.status_code in (200, 204)
    assert "set-cookie" in resp.headers


async def _converter_tokens(async_session_factory, user_id) -> list[AccessToken]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(AccessToken)
            .where(AccessToken.user_id == user_id)
            .where(AccessToken.service_name == "file-converter")
        )
        return list(result.scalars())


@pytest.mark.asyncio
async def test_api_login_mints_file_converter_token(
    editor_login_user, async_session_factory
):
    """An editor login without a socket sid still gets the converter token.

    Uploads resolve the uploading user's file-converter token server-side
    and 401 without one, so the login hook must mint it for every editor+
    sign-in - including API logins (SDK, scripts, agent pairing), which
    carry no ``x-sid`` header and skip the socket-authentication step.
    """
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": editor_login_user.email, "password": LOGIN_PASSWORD},
        )
    assert resp.status_code in (200, 204)
    tokens = await _converter_tokens(async_session_factory, editor_login_user.id)
    assert len(tokens) == 1


@pytest.mark.asyncio
async def test_guest_login_mints_no_converter_token(login_user, async_session_factory):
    """Guests cannot upload, so their login must not mint a converter token."""
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        )
    assert resp.status_code in (200, 204)
    assert await _converter_tokens(async_session_factory, login_user.id) == []
