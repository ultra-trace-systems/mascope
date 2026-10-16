"""
Integration tests: ending web sessions before they expire.

A web session token carries its issue time, and the user row a cutoff - the
later of the last password change and ``sessions_revoked_at``. A cookie issued
before the cutoff answers 401, as an absent or forged one does, on every route
including the gate-exempt profile read; one issued after works. Bearer tokens
are database rows with their own revocation and are untouched.

Cookies are minted with a backdated issue time rather than by waiting: the
cutoff has a one-minute tolerance for clock skew between workers, so a session
issued in the minute before a revocation survives it on purpose.

Every test uses a throwaway account, so the cutoffs moved here cannot end the
sessions of the shared ``test_users`` other suites authenticate as.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.new.auth.backend import auth_backend_jwt
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, User
from mascope_backend.db.id import gen_id
from mascope_backend.socket.auth.service import authenticate_socket_connection
from mascope_backend.socket.events.handlers.default.subscription import subscribe
from mascope_backend.socket.storage import get_session_user, redis_storage_client


ORIGINAL_PASSWORD = "nine bushels of slate"
COMPLIANT_PASSWORD = "sixteen tonnes of quartz"

#: Comfortably beyond the cutoff's one-minute tolerance.
LONG_AGO = timedelta(minutes=10)


def _token(user: User, issued: datetime | None) -> str:
    """A session token for ``user`` issued at ``issued`` (``None``: no iat)."""
    payload = {
        "sub": str(user.id),
        "aud": auth_settings.JWT_AUDIENCE,
        "exp": datetime.now(timezone.utc) + timedelta(days=1),
    }
    if issued is not None:
        payload["iat"] = issued
    return jwt.encode(
        payload, auth_settings.JWT_SECRET_KEY, algorithm=auth_settings.JWT_ALGORITHM
    )


def _cookie_client(token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: token},
    )


def _bearer_client(token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Service-Name": "mascope_sdk"},
    )


@pytest_asyncio.fixture
async def person(async_session_factory, roles):
    """A throwaway editor account with a known password and no cutoff yet."""
    async with async_session_factory() as session:
        user = User(
            email=f"revoke-{gen_id()[:8]}@test.com",
            username=f"revoke_{gen_id()[:8]}",
            hashed_password=PasswordHelper().hash(ORIGINAL_PASSWORD),
            is_active=True,
            is_verified=False,
            role_id=roles["editor"].role_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    yield user

    async with async_session_factory() as session:
        await session.execute(delete(AccessToken).where(AccessToken.user_id == user.id))
        stored = await session.get(User, user.id)
        if stored is not None:
            await session.delete(stored)
        await session.commit()


@pytest_asyncio.fixture
async def sdk_token(async_session_factory, person):
    """A real API token of the throwaway account."""
    token = f"revoke-{gen_id()}".ljust(43, "x")
    async with async_session_factory() as session:
        session.add(
            AccessToken(token=token, user_id=person.id, service_name="mascope_sdk")
        )
        await session.commit()
    return token


async def _stored(async_session_factory, user_id: int) -> User:
    async with async_session_factory() as session:
        return await session.get(User, user_id)


def _without_error_id(body: dict) -> dict:
    detail = body.get("detail")
    if isinstance(detail, dict):
        detail = {k: v for k, v in detail.items() if k != "error_id"}
    return {**body, "detail": detail}


async def _status(token: str, path: str = "/api/users/me") -> int:
    async with _cookie_client(token) as client:
        return (await client.get(path)).status_code


# --- Sign out everywhere, on one's own account ---


@pytest.mark.asyncio
async def test_signing_out_everywhere_ends_older_sessions(
    person, sdk_token, async_session_factory
):
    old = _token(person, datetime.now(timezone.utc) - LONG_AGO)
    assert await _status(old) == 200

    async with _cookie_client(old) as client:
        resp = await client.post("/api/users/me/sign-out-everywhere")
    assert resp.status_code == 200, resp.text
    assert (await _stored(async_session_factory, person.id)).sessions_revoked_at

    # The gate-exempt profile read refuses it too, and with the 401 an expired
    # or forged cookie gets - nothing tells the caller more than "sign in".
    async with _cookie_client(old) as client:
        refused = await client.get("/api/users/me")
    async with _cookie_client("not.a.session") as client:
        forged = await client.get("/api/users/me")
    assert refused.status_code == 401
    # Equal but for the per-request error id.
    assert _without_error_id(refused.json()) == _without_error_id(forged.json())
    assert await _status(old, "/api/workspaces") == 401

    # A session issued after it works, and the API token never noticed.
    assert await _status(_token(person, datetime.now(timezone.utc))) == 200
    async with _bearer_client(sdk_token) as client:
        assert (await client.get("/api/workspaces")).status_code == 200


@pytest.mark.asyncio
async def test_signing_out_everywhere_tells_open_tabs(person):
    """Open tabs re-fetch the profile on ``user_me`` and find the session over."""
    emit = AsyncMock()
    with patch("mascope_backend.api.new.users.service.emit_record_updated", emit):
        async with _cookie_client(_token(person, datetime.now(timezone.utc))) as c:
            resp = await c.post("/api/users/me/sign-out-everywhere")
    assert resp.status_code == 200, resp.text
    emit.assert_awaited_once()
    assert emit.await_args.kwargs["record_type"] == "user_me"
    assert emit.await_args.kwargs["room"] == f"user-{person.id}"


@pytest.mark.asyncio
async def test_signing_out_everywhere_is_not_reachable_by_navigation(person):
    async with _cookie_client(_token(person, datetime.now(timezone.utc))) as client:
        assert (await client.get("/api/users/me/sign-out-everywhere")).status_code in (
            404,
            405,
        )


# --- Sign out everywhere, by an owner ---


@pytest.mark.asyncio
async def test_an_owner_signs_another_account_out_everywhere(
    owner_client, person, sdk_token
):
    old = _token(person, datetime.now(timezone.utc) - LONG_AGO)

    resp = await owner_client.post(f"/api/users/owner/{person.id}/sign-out-everywhere")
    assert resp.status_code == 200, resp.text

    assert await _status(old) == 401
    # Only that account: the owner's own session carries on.
    assert (await owner_client.get("/api/users/me")).status_code == 200
    async with _bearer_client(sdk_token) as client:
        assert (await client.get("/api/workspaces")).status_code == 200


@pytest.mark.asyncio
async def test_only_an_owner_may_sign_another_account_out(admin_client, person):
    resp = await admin_client.post(f"/api/users/owner/{person.id}/sign-out-everywhere")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_machine_account_is_not_signed_out_like_a_person(
    owner_client, person, async_session_factory
):
    """Refused as every other user-management mutation refuses one: 403."""
    async with async_session_factory() as session:
        stored = await session.get(User, person.id)
        stored.account_type = ACCOUNT_TYPE_MACHINE
        await session.commit()

    resp = await owner_client.post(f"/api/users/owner/{person.id}/sign-out-everywhere")
    assert resp.status_code == 403, resp.text
    assert (await _stored(async_session_factory, person.id)).sessions_revoked_at is None


@pytest.mark.asyncio
async def test_signing_out_a_missing_account_is_a_404(owner_client):
    resp = await owner_client.post("/api/users/owner/987654321/sign-out-everywhere")
    assert resp.status_code == 404, resp.text


# --- A password change ---


@pytest.mark.asyncio
async def test_a_password_change_ends_sessions_issued_before_it(person):
    old = _token(person, datetime.now(timezone.utc) - LONG_AGO)
    async with _cookie_client(old) as client:
        resp = await client.patch(
            "/api/users/me/creds",
            json={
                "current_password": ORIGINAL_PASSWORD,
                "new_password": COMPLIANT_PASSWORD,
                "verify_new_password": COMPLIANT_PASSWORD,
            },
        )
    assert resp.status_code == 200, resp.text

    assert await _status(old) == 401
    assert await _status(_token(person, datetime.now(timezone.utc))) == 200


@pytest.mark.asyncio
async def test_a_session_from_before_tokens_carried_an_issue_time(
    person, async_session_factory
):
    legacy = _token(person, None)
    # No cutoff yet: nothing to compare with, so it is still honoured.
    assert await _status(legacy) == 200

    async with async_session_factory() as session:
        stored = await session.get(User, person.id)
        stored.password_changed_at = datetime.now(timezone.utc) - LONG_AGO
        await session.commit()

    # Any cutoff at all, however old, and it cannot show it came after.
    assert await _status(legacy) == 401


# --- An account disable ---


@pytest.mark.asyncio
async def test_disabling_an_account_ends_its_sessions_for_good(
    owner_client, person, async_session_factory
):
    old = _token(person, datetime.now(timezone.utc) - LONG_AGO)

    resp = await owner_client.patch(
        f"/api/users/owner/{person.id}", json={"is_active": False}
    )
    assert resp.status_code == 200, resp.text
    assert (await _stored(async_session_factory, person.id)).sessions_revoked_at
    assert await _status(old) == 401  # as before: the account is inactive

    # Re-enabling the account does not bring the old session back.
    resp = await owner_client.patch(
        f"/api/users/owner/{person.id}", json={"is_active": True}
    )
    assert resp.status_code == 200, resp.text
    assert await _status(old) == 401
    assert await _status(_token(person, datetime.now(timezone.utc))) == 200


@pytest.mark.asyncio
async def test_other_updates_leave_the_cutoff_alone(
    owner_client, person, async_session_factory
):
    resp = await owner_client.patch(
        f"/api/users/owner/{person.id}", json={"username": f"renamed_{gen_id()[:6]}"}
    )
    assert resp.status_code == 200, resp.text
    assert (await _stored(async_session_factory, person.id)).sessions_revoked_at is None


# --- A socket's session ---


class _SessionRedis:
    """The Redis commands the socket session store uses, over a plain dict."""

    def __init__(self):
        self.values: dict[str, str] = {}

    async def setex(self, key: str, seconds: int, value: str) -> bool:
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self.values

    async def delete(self, *keys: str) -> int:
        return sum(self.values.pop(key, None) is not None for key in keys)


@pytest.mark.asyncio
async def test_a_socket_keeps_its_tokens_issue_time_and_its_rooms(
    person, async_session_factory, monkeypatch
):
    """
    The socket session records the ``iat`` of the token it authenticated with.

    Subscribe compares the account's cutoff with what the session stored, so a
    session stored without it looks issued before any cutoff: every socket of an
    account with one - any account whose password was written since the forced
    change landed - would be held to its own room, and stop hearing anything
    else. Hence a real token through the real store, for an account that has a
    cutoff, rather than a hand-built session dict.
    """
    async with async_session_factory() as session:
        stored = await session.get(User, person.id)
        stored.password_changed_at = datetime.now(timezone.utc) - LONG_AGO
        await session.commit()
    monkeypatch.setattr(redis_storage_client, "_client", _SessionRedis())

    token = await auth_backend_jwt.get_strategy().write_token(person)
    issued_at = jwt.decode(token, options={"verify_signature": False})["iat"]

    await authenticate_socket_connection("socket-sid", token, minimum_role="guest")
    assert (await get_session_user("socket-sid"))["issued_at"] == issued_at

    sio = MagicMock()
    sio.enter_room = AsyncMock()
    room_tracker = MagicMock()
    room_tracker.join = AsyncMock()
    handler = "mascope_backend.socket.events.handlers.default.subscription"
    with (
        patch(f"{handler}.sio", sio),
        patch(f"{handler}.room_tracker", room_tracker),
        patch(
            "mascope_backend.api.new.workspaces.dependencies.user_can_subscribe_to_room",
            AsyncMock(return_value=True),
        ),
    ):
        await subscribe("socket-sid", "some-sample-batch-id")
    sio.enter_room.assert_awaited_once_with("socket-sid", "some-sample-batch-id")
