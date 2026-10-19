"""
Integration tests: a live socket leaves its rooms when its session may no
longer read.

Subscribe refuses a resource room to an account that owes a password change,
is disabled, or whose session cutoff has moved past the socket's token. These
tests open a socket *before* that happens - authenticated with a real token
through the real session store, subscribed through the real handler - then
change the account through the API and check that the socket was made to leave
every room but its own ``user-{id}``, on whichever worker holds it.

The Socket.IO server is a mock: ``leave_room`` is the call the Redis manager
routes to the worker holding the connection, so it is the boundary these tests
can observe in one process. Redis is this package's in-memory stub (see
``conftest.py``), so the session index and the room records are the real code.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import jwt
import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, User
from mascope_backend.db.id import gen_id
from mascope_backend.socket.events.handlers.default.connection import (
    connect,
    disconnect,
)
from mascope_backend.socket.events.handlers.default.subscription import (
    subscribe,
    unsubscribe,
)
from mascope_backend.socket.eviction import evict_ended_sessions
from mascope_backend.socket.storage import (
    get_session_user,
    get_user_socket_sids,
    redis_storage_client,
    room_tracker,
    save_user_session,
    stop_session_renewal,
)
from mascope_backend.socket.storage import sessions as session_store
from mascope_backend.socket.storage.config import storage_config


ORIGINAL_PASSWORD = "nine bushels of slate"
COMPLIANT_PASSWORD = "sixteen tonnes of quartz"

#: Comfortably beyond the session cutoff's one-minute tolerance.
LONG_AGO = timedelta(minutes=10)

RESOURCE_ROOM = "some-sample-batch-id"


def _token(user: User, issued: datetime) -> str:
    """A session token for ``user`` issued at ``issued``."""
    return jwt.encode(
        {
            "sub": str(user.id),
            "aud": auth_settings.JWT_AUDIENCE,
            "exp": datetime.now(timezone.utc) + timedelta(days=1),
            "iat": issued,
        },
        auth_settings.JWT_SECRET_KEY,
        algorithm=auth_settings.JWT_ALGORITHM,
    )


def _cookie_client(token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies={auth_settings.COOKIE_NAME: token},
    )


@pytest_asyncio.fixture
async def person(async_session_factory, roles):
    """A throwaway editor account with a known password and no cutoff yet."""
    async with async_session_factory() as session:
        user = User(
            email=f"evict-{gen_id()[:8]}@test.com",
            username=f"evict_{gen_id()[:8]}",
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


@pytest.fixture
def sio():
    """
    The Socket.IO server, as both the subscribe handler and eviction see it.

    The workspace ACL is opened too: what is under test is the credential
    state, not the room's access check.
    """
    server = MagicMock()
    server.enter_room = AsyncMock()
    server.leave_room = AsyncMock()
    with (
        patch(
            "mascope_backend.socket.events.handlers.default.subscription.sio", server
        ),
        patch("mascope_backend.socket.eviction.sio", server),
        patch(
            "mascope_backend.api.new.workspaces.dependencies.user_can_subscribe_to_room",
            AsyncMock(return_value=True),
        ),
    ):
        yield server


@pytest_asyncio.fixture(autouse=True)
async def stop_renewals():
    """Cancel the session renewals of the sockets a test left connected."""
    yield
    for sid in list(session_store._renewal_tasks):
        await stop_session_renewal(sid)


async def _open_socket(sid: str, user: User, issued: datetime) -> None:
    """Connect a web socket and subscribe it to a resource and its own room."""
    cookie = f"{auth_settings.COOKIE_NAME}={_token(user, issued)}"
    assert await connect(sid, {"HTTP_COOKIE": cookie})
    assert await get_session_user(sid), "the socket did not authenticate"
    await subscribe(sid, RESOURCE_ROOM)
    await subscribe(sid, f"user-{user.id}")


def _left(sio, sid: str) -> set[str]:
    """The rooms ``sid`` was made to leave."""
    return {args[1] for args, _ in sio.leave_room.await_args_list if args[0] == sid}


async def _assert_evicted(sio, sid: str, user: User) -> None:
    own_room = f"user-{user.id}"
    assert _left(sio, sid) == {RESOURCE_ROOM}
    # The record of its rooms follows, so a second eviction has nothing to do.
    assert await room_tracker.socket_rooms(sid) == {own_room}
    # And notification routing no longer counts the user as in the room.
    assert not await room_tracker.is_in_room(user.id, RESOURCE_ROOM)
    assert await room_tracker.is_in_room(user.id, own_room)


# --- Every event that ends the session evicts ---


@pytest.mark.asyncio
async def test_the_sweep_evicts_a_socket_subscribed_before_it(
    owner_client, person, sio
):
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)
    assert sio.enter_room.await_args_list == [
        call("sid-a", RESOURCE_ROOM),
        call("sid-a", f"user-{person.id}"),
    ]

    resp = await owner_client.post(
        "/api/users/owner/require-password-change", json={"confirm": True}
    )
    assert resp.status_code == 200, resp.text

    await _assert_evicted(sio, "sid-a", person)


@pytest.mark.asyncio
async def test_an_administrator_reset_evicts(owner_client, person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)

    resp = await owner_client.post(f"/api/users/owner/{person.id}/reset-password")
    assert resp.status_code == 200, resp.text

    await _assert_evicted(sio, "sid-a", person)


@pytest.mark.asyncio
async def test_disabling_the_account_evicts(owner_client, person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)

    resp = await owner_client.patch(
        f"/api/users/owner/{person.id}", json={"is_active": False}
    )
    assert resp.status_code == 200, resp.text

    await _assert_evicted(sio, "sid-a", person)


@pytest.mark.asyncio
async def test_signing_out_everywhere_evicts(person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)

    async with _cookie_client(_token(person, datetime.now(timezone.utc))) as client:
        resp = await client.post("/api/users/me/sign-out-everywhere")
    assert resp.status_code == 200, resp.text

    await _assert_evicted(sio, "sid-a", person)


@pytest.mark.asyncio
async def test_an_owner_signing_an_account_out_evicts(owner_client, person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)

    resp = await owner_client.post(f"/api/users/owner/{person.id}/sign-out-everywhere")
    assert resp.status_code == 200, resp.text

    await _assert_evicted(sio, "sid-a", person)


# --- Only the sockets subscribe would now refuse ---


@pytest.mark.asyncio
async def test_a_password_change_keeps_the_socket_its_tolerance_keeps(person, sio):
    """
    Changing one's own password evicts the old tab, not the one changing it.

    The cutoff's one-minute tolerance keeps a session issued just before it, so
    subscribe would still admit the fresh socket; evicting it would cut a tab
    the REST API still serves off its rooms. The room both sockets were in stays
    the user's for notification routing, because one socket still holds it.
    """
    now = datetime.now(timezone.utc)
    await _open_socket("sid-old", person, now - LONG_AGO)
    await _open_socket("sid-fresh", person, now)

    async with _cookie_client(_token(person, now)) as client:
        resp = await client.patch(
            "/api/users/me/creds",
            json={
                "current_password": ORIGINAL_PASSWORD,
                "new_password": COMPLIANT_PASSWORD,
                "verify_new_password": COMPLIANT_PASSWORD,
            },
        )
    assert resp.status_code == 200, resp.text

    assert _left(sio, "sid-old") == {RESOURCE_ROOM}
    assert _left(sio, "sid-fresh") == set()
    assert await room_tracker.socket_rooms("sid-fresh") == {
        RESOURCE_ROOM,
        f"user-{person.id}",
    }
    assert await room_tracker.is_in_room(person.id, RESOURCE_ROOM)


@pytest.mark.asyncio
async def test_an_update_that_ends_nothing_evicts_nothing(owner_client, person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)

    resp = await owner_client.patch(
        f"/api/users/owner/{person.id}", json={"username": f"renamed_{gen_id()[:6]}"}
    )
    assert resp.status_code == 200, resp.text

    sio.leave_room.assert_not_awaited()
    assert await room_tracker.is_in_room(person.id, RESOURCE_ROOM)


# --- The index ---


@pytest.mark.asyncio
async def test_the_index_follows_connect_and_disconnect(person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await _open_socket("sid-b", person, datetime.now(timezone.utc))
    assert await get_user_socket_sids(person.id) == {"sid-a", "sid-b"}

    await unsubscribe("sid-a", RESOURCE_ROOM)
    assert await room_tracker.socket_rooms("sid-a") == {f"user-{person.id}"}

    await disconnect("sid-a")
    assert await get_user_socket_sids(person.id) == {"sid-b"}
    assert await room_tracker.socket_rooms("sid-a") == set()


@pytest.mark.asyncio
async def test_a_service_socket_is_not_indexed(person):
    """Service namespaces join no rooms through subscribe; nothing to evict."""
    await save_user_session("sid-svc", person, namespace="/file-converter")
    assert await get_user_socket_sids(person.id) == set()


@pytest.mark.asyncio
async def test_a_socket_gone_without_a_disconnect_is_dropped_from_the_index(
    person, sio, async_session_factory
):
    """A worker that died ran no disconnect; its sockets' sessions went with it."""
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)
    await redis_storage_client.client.sadd(
        storage_config.user_sockets_key(person.id), "sid-dead"
    )
    async with async_session_factory() as session:
        stored = await session.get(User, person.id)
        stored.must_change_password = True
        await session.commit()

    assert await evict_ended_sessions(person.id) == 1
    assert await get_user_socket_sids(person.id) == {"sid-a"}
    assert _left(sio, "sid-dead") == set()


@pytest.mark.asyncio
async def test_a_socket_whose_session_lapsed_is_still_evicted(
    owner_client, person, sio
):
    """
    A missing session does not mean a missing socket.

    A socket whose session key is gone (lapsed, or Redis lost it) may still be
    connected and in its rooms on its worker. Nothing says it may keep them, so
    it leaves every recorded room but its own, and drops out of the index.
    """
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)
    await redis_storage_client.client.delete(storage_config.session_key("sid-a", "/"))

    resp = await owner_client.post(
        "/api/users/owner/require-password-change", json={"confirm": True}
    )
    assert resp.status_code == 200, resp.text

    await _assert_evicted(sio, "sid-a", person)
    assert await get_user_socket_sids(person.id) == set()


@pytest.mark.asyncio
async def test_a_socket_that_never_emits_stays_on_record(
    person, sio, live_rate_limiter, monkeypatch
):
    """
    The worker holding a socket keeps its session and records alive.

    The SPA emits nothing but subscribe/unsubscribe, so a tab that only listens
    sends no event that would refresh them. Without the connection's own
    renewal they would lapse a day after its last subscribe, and eviction would
    then miss a socket still connected and still in its rooms. The renewal
    stops with the connection.
    """
    monkeypatch.setattr(storage_config, "session_renewal_interval", 0.01)
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    refreshed: list[str] = []
    expire = live_rate_limiter.expire

    async def recording_expire(key: str, seconds: int) -> bool:
        refreshed.append(key)
        return await expire(key, seconds)

    live_rate_limiter.expire = recording_expire
    await asyncio.sleep(0.05)

    assert storage_config.session_key("sid-a", "/") in refreshed
    assert storage_config.user_sockets_key(person.id) in refreshed
    assert storage_config.socket_rooms_key("sid-a") in refreshed

    renewal = session_store._renewal_tasks["sid-a"]
    await disconnect("sid-a")
    assert renewal.cancelled()


@pytest.mark.asyncio
async def test_a_socket_authenticated_at_sign_in_stays_on_record(
    person, sio, live_rate_limiter, monkeypatch
):
    """
    A socket that signs in after it connected is renewed too.

    The SPA connects before sign-in, so ``connect`` finds no cookie and leaves
    the socket unauthenticated; the login route then authenticates it from the
    ``X-SID`` header, on whichever worker serves the request. The renewal
    started at connect, on the worker holding the connection, keeps what it
    saved alive from then on.
    """
    monkeypatch.setattr(storage_config, "session_renewal_interval", 0.01)
    assert await connect("sid-a", {})
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": person.email, "password": ORIGINAL_PASSWORD},
            headers={"X-SID": "sid-a"},
        )
    assert resp.status_code in (200, 204), resp.text
    assert (await get_session_user("sid-a"))["user_id"] == person.id
    await subscribe("sid-a", RESOURCE_ROOM)
    refreshed: list[str] = []
    expire = live_rate_limiter.expire

    async def recording_expire(key: str, seconds: int) -> bool:
        refreshed.append(key)
        return await expire(key, seconds)

    live_rate_limiter.expire = recording_expire
    await asyncio.sleep(0.05)

    assert storage_config.session_key("sid-a", "/") in refreshed
    assert storage_config.user_sockets_key(person.id) in refreshed
    assert storage_config.socket_rooms_key("sid-a") in refreshed

    renewal = session_store._renewal_tasks["sid-a"]
    await disconnect("sid-a")
    assert renewal.cancelled()


@pytest.mark.asyncio
async def test_a_long_lived_socket_stays_on_record(person, sio, live_rate_limiter):
    """
    Every access that keeps the session alive keeps the index and rooms alive.

    Both expire like the session itself, as a safety net for a worker that dies
    without running disconnect. The session's TTL is refreshed on each read (a
    client event, or the connection's renewal); if the index and its room
    record were not, they would lapse under it and eviction would no longer
    find that socket.
    """
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    refreshed: list[str] = []
    expire = live_rate_limiter.expire

    async def recording_expire(key: str, seconds: int) -> bool:
        refreshed.append(key)
        return await expire(key, seconds)

    live_rate_limiter.expire = recording_expire
    await get_session_user("sid-a")

    assert storage_config.user_sockets_key(person.id) in refreshed
    assert storage_config.socket_rooms_key("sid-a") in refreshed
