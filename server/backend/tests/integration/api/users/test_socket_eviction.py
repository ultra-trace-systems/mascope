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
from mascope_backend.db.scripts import require_password_change as sweep_script
from mascope_backend.socket.auth.exceptions import SocketUnauthenticatedError
from mascope_backend.socket.auth.service import authenticate_socket_connection
from mascope_backend.socket.events.handlers.default.connection import (
    connect,
    disconnect,
)
from mascope_backend.socket.events.handlers.default.subscription import (
    subscribe,
    unsubscribe,
)
from mascope_backend.socket.eviction import evict_ended_sessions
from mascope_backend.socket.signed_in import SIGNED_IN_ROOM
from mascope_backend.socket.storage import (
    SocketSessionError,
    get_session_user,
    get_user_socket_sids,
    redis_storage_client,
    room_tracker,
    save_user_session,
    socket_is_held,
    stop_session_renewal,
    stop_worker_heartbeat,
)
from mascope_backend.socket.storage import sessions as session_store
from mascope_backend.socket.storage import workers as worker_store
from mascope_backend.socket.storage.config import storage_config


ORIGINAL_PASSWORD = "nine bushels of slate"
COMPLIANT_PASSWORD = "sixteen tonnes of quartz"

#: Comfortably beyond the session cutoff's one-minute tolerance.
LONG_AGO = timedelta(minutes=10)

RESOURCE_ROOM = "some-sample-batch-id"
OTHER_ROOM = "another-sample-batch-id"


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


async def _throwaway_account(async_session_factory, roles):
    """An editor account with a known password and no cutoff yet, then gone."""
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


@pytest_asyncio.fixture
async def person(async_session_factory, roles):
    """A throwaway editor account with a known password and no cutoff yet."""
    async for user in _throwaway_account(async_session_factory, roles):
        yield user


@pytest_asyncio.fixture
async def other_person(async_session_factory, roles):
    """A second throwaway account, for what must happen once per account."""
    async for user in _throwaway_account(async_session_factory, roles):
        yield user


@pytest.fixture
def sio():
    """
    The Socket.IO server, as the subscribe handler, eviction and the signed-in
    room see it.

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
        patch("mascope_backend.socket.signed_in.sio", server),
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
    await stop_worker_heartbeat()


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
    # The signed-in room too: the room-less broadcasts stop with the rest.
    assert _left(sio, sid) == {RESOURCE_ROOM, SIGNED_IN_ROOM}
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
        call("sid-a", SIGNED_IN_ROOM),
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


@pytest.mark.asyncio
async def test_deleting_the_account_evicts(owner_client, person, sio):
    """The row is gone before eviction reads it; no account may read nothing."""
    await _open_socket("sid-a", person, datetime.now(timezone.utc))

    resp = await owner_client.delete(f"/api/users/owner/{person.id}")
    assert resp.status_code == 200, resp.text

    await _assert_evicted(sio, "sid-a", person)


@pytest.fixture
def script_redis(monkeypatch):
    """
    The maintenance script's Redis connection, kept on this package's stub.

    The script connects the session store itself, as a process of its own must;
    here the stub is already in place, and the database engine is the test's.
    """
    connect = AsyncMock()
    disconnect = AsyncMock()
    monkeypatch.setattr(redis_storage_client, "connect", connect)
    monkeypatch.setattr(redis_storage_client, "disconnect", disconnect)
    monkeypatch.setattr(sweep_script, "configure_database_engine", AsyncMock())
    monkeypatch.delenv("MASCOPE_REQUIRE_PASSWORD_CHANGE_DRY_RUN", raising=False)
    return connect, disconnect


@pytest.fixture
def record_sio(sio):
    """
    The Socket.IO server as the record-sync emitters see it.

    One log with eviction's ``leave_room`` keeps the order the two were called in.
    """
    calls = []
    sio.leave_room.side_effect = lambda sid, room: calls.append(("leave", sid, room))
    server = MagicMock()
    server.emit = AsyncMock(
        side_effect=lambda event, payload, room=None, namespace=None: calls.append(
            ("emit", event, room, payload)
        )
    )
    with patch("mascope_backend.socket.records.service.sio", server):
        yield server, calls


@pytest.mark.asyncio
async def test_the_maintenance_script_evicts(person, sio, script_redis):
    """
    The script serves no sockets, but its ``leave_room`` reaches the worker.

    Under the Redis manager a ``leave_room`` for a socket this process does not
    hold is published to the workers, so the mocked server's call is the same
    boundary the route's eviction is checked at.
    """
    connect, disconnect = script_redis
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)

    await sweep_script.run_require_password_change()

    await _assert_evicted(sio, "sid-a", person)
    connect.assert_awaited_once()
    disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_maintenance_script_survives_an_unreachable_redis(
    person, sio, script_redis, async_session_factory
):
    """The requirement already stands; the sockets are left as they were."""
    connect, disconnect = script_redis
    connect.side_effect = ConnectionError("Redis connection failed")
    await _open_socket("sid-a", person, datetime.now(timezone.utc) - LONG_AGO)

    await sweep_script.run_require_password_change()

    sio.leave_room.assert_not_awaited()
    disconnect.assert_not_awaited()
    async with async_session_factory() as session:
        assert (await session.get(User, person.id)).must_change_password


@pytest.mark.asyncio
async def test_the_maintenance_script_notifies_open_tabs(
    person, other_person, sio, record_sio, script_redis
):
    """
    As the owner's route does: each armed account hears on its own room, after
    its sockets are evicted, and open user tables are told to reload once.
    """
    server, calls = record_sio
    issued = datetime.now(timezone.utc) - LONG_AGO
    await _open_socket("sid-a", person, issued)
    await _open_socket("sid-b", other_person, issued)

    await sweep_script.run_require_password_change()

    for sid, user in (("sid-a", person), ("sid-b", other_person)):
        notices = [c for c in calls if c[0] == "emit" and c[2] == f"user-{user.id}"]
        assert len(notices) == 1
        _, event, _, payload = notices[0]
        assert event == "user_me_updated"
        assert payload["record_id"] == str(user.id)
        assert payload["record"] == {"id": user.id, "must_change_password": True}
        assert calls.index(("leave", sid, RESOURCE_ROOM)) < calls.index(notices[0])

    reloads = [c for c in calls if c[0] == "emit" and c[1] == "user_reload"]
    assert len(reloads) == 1
    # Room-less: every signed-in socket, none of the evicted ones.
    assert reloads[0][2] == SIGNED_IN_ROOM
    assert calls[-1] == reloads[0]


@pytest.mark.asyncio
async def test_the_maintenance_script_notifies_nothing_without_redis(
    person, sio, record_sio, script_redis
):
    """An unreachable Redis is reported once, not once per account."""
    server, _ = record_sio
    connect, _ = script_redis
    connect.side_effect = ConnectionError("Redis connection failed")

    await sweep_script.run_require_password_change()

    server.emit.assert_not_awaited()


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

    assert _left(sio, "sid-old") == {RESOURCE_ROOM, SIGNED_IN_ROOM}
    assert _left(sio, "sid-fresh") == set()
    # Already in the signed-in room: eviction does not enter it again.
    assert sio.enter_room.await_args_list.count(call("sid-fresh", SIGNED_IN_ROOM)) == 1
    assert await room_tracker.socket_rooms("sid-fresh") == {
        RESOURCE_ROOM,
        f"user-{person.id}",
        SIGNED_IN_ROOM,
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


# --- Signing out of one tab signs out its socket ---


async def _logout(user: User, sid: str | None):
    """Sign out through the logout route, as the SPA does, naming its socket."""
    headers = {"X-SID": sid} if sid else None
    async with _cookie_client(_token(user, datetime.now(timezone.utc))) as client:
        resp = await client.post("/api/auth/logout", headers=headers)
    assert resp.status_code == 204, resp.text
    # The session cookie is cleared whatever happened to the socket.
    assert f'{auth_settings.COOKIE_NAME}=""' in resp.headers["set-cookie"]
    return resp


@pytest.mark.asyncio
async def test_signing_out_signs_the_tab_socket_out(person, sio):
    """
    Clearing the cookie used to be all: the tab's socket kept its rooms, the
    signed-in room among them, and its session still named the account.
    """
    await _open_socket("sid-a", person, datetime.now(timezone.utc))

    await _logout(person, "sid-a")

    # Every room, its own user room and the signed-in room included.
    assert _left(sio, "sid-a") == {RESOURCE_ROOM, SIGNED_IN_ROOM, f"user-{person.id}"}
    assert await room_tracker.socket_rooms("sid-a") == set()
    # The session is gone, so the socket can no longer subscribe as the account.
    with pytest.raises(SocketSessionError):
        await get_session_user("sid-a")
    assert "sid-a" not in await get_user_socket_sids(person.id)
    sio.enter_room.reset_mock()
    await subscribe("sid-a", OTHER_ROOM)
    sio.enter_room.assert_not_awaited()
    # Notification routing no longer counts the user as listening anywhere.
    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)
    assert not await room_tracker.is_in_room(person.id, f"user-{person.id}")


@pytest.mark.asyncio
async def test_signing_out_of_one_tab_keeps_the_other_tab(person, sio):
    """The other tab's session goes on; only the rooms no other tab holds go."""
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await subscribe("sid-a", OTHER_ROOM)
    await _open_socket("sid-b", person, datetime.now(timezone.utc))

    await _logout(person, "sid-a")

    assert _left(sio, "sid-b") == set()
    assert (await get_session_user("sid-b"))["user_id"] == person.id
    assert await get_user_socket_sids(person.id) == {"sid-b"}
    assert await room_tracker.socket_rooms("sid-b") == {
        RESOURCE_ROOM,
        SIGNED_IN_ROOM,
        f"user-{person.id}",
    }
    assert await room_tracker.is_in_room(person.id, RESOURCE_ROOM)
    assert await room_tracker.is_in_room(person.id, f"user-{person.id}")
    assert not await room_tracker.is_in_room(person.id, OTHER_ROOM)


@pytest.mark.asyncio
async def test_signing_out_leaves_another_accounts_socket_alone(
    person, other_person, sio
):
    """X-SID is the client's to send: a sid of another account changes nothing."""
    await _open_socket("sid-b", other_person, datetime.now(timezone.utc))

    await _logout(person, "sid-b")

    sio.leave_room.assert_not_awaited()
    assert (await get_session_user("sid-b"))["user_id"] == other_person.id
    assert await room_tracker.socket_rooms("sid-b") == {
        RESOURCE_ROOM,
        SIGNED_IN_ROOM,
        f"user-{other_person.id}",
    }
    assert await room_tracker.is_in_room(other_person.id, RESOURCE_ROOM)


@pytest.mark.asyncio
async def test_signing_out_without_a_socket_touches_none(person, sio):
    """An API sign-out names no socket; the account's tabs stay signed in."""
    await _open_socket("sid-a", person, datetime.now(timezone.utc))

    await _logout(person, None)

    sio.leave_room.assert_not_awaited()
    assert (await get_session_user("sid-a"))["user_id"] == person.id


@pytest.mark.asyncio
async def test_signing_out_survives_a_failing_socket_store(person, sio):
    """The session has ended either way; the socket's cleanup is best effort."""
    await _open_socket("sid-a", person, datetime.now(timezone.utc))

    with patch.object(
        room_tracker, "release_socket", AsyncMock(side_effect=RuntimeError("down"))
    ):
        await _logout(person, "sid-a")


# --- The signed-in room the room-less broadcasts go to ---


def _entered(sio, sid: str) -> set[str]:
    """The rooms ``sid`` was put in."""
    return {args[1] for args, _ in sio.enter_room.await_args_list if args[0] == sid}


async def _hold(async_session_factory, user: User) -> None:
    """Arm the forced password change on ``user``, as the sweep does."""
    async with async_session_factory() as session:
        stored = await session.get(User, user.id)
        stored.must_change_password = True
        await session.commit()


async def _login(sid: str, user: User) -> None:
    """Sign in through the login route, as the SPA does, naming its socket."""
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": user.email, "password": ORIGINAL_PASSWORD},
            headers={"X-SID": sid},
        )
    assert resp.status_code in (200, 204), resp.text
    assert (await get_session_user(sid))["user_id"] == user.id


@pytest.mark.asyncio
async def test_a_socket_that_has_not_signed_in_hears_no_broadcast(sio):
    """The SPA connects before its login form; that socket is in no room."""
    assert await connect("sid-anon", {})
    assert await connect(
        "sid-bad", {"HTTP_COOKIE": f"{auth_settings.COOKIE_NAME}=not.a.token"}
    )

    sio.enter_room.assert_not_awaited()
    assert await room_tracker.socket_rooms("sid-anon") == set()
    assert await room_tracker.socket_rooms("sid-bad") == set()


@pytest.mark.asyncio
async def test_a_socket_connecting_with_a_session_hears_broadcasts(person, sio):
    cookie = f"{auth_settings.COOKIE_NAME}={_token(person, datetime.now(timezone.utc))}"
    assert await connect("sid-a", {"HTTP_COOKIE": cookie})

    assert _entered(sio, "sid-a") == {SIGNED_IN_ROOM}
    # On record, so eviction on any worker knows to take it away.
    assert await room_tracker.socket_rooms("sid-a") == {SIGNED_IN_ROOM}


@pytest.mark.asyncio
async def test_a_socket_signed_in_at_login_hears_broadcasts(person, sio):
    assert await connect("sid-a", {})
    sio.enter_room.assert_not_awaited()

    await _login("sid-a", person)

    assert _entered(sio, "sid-a") == {SIGNED_IN_ROOM}
    assert await room_tracker.socket_rooms("sid-a") == {SIGNED_IN_ROOM}


@pytest.mark.asyncio
async def test_a_held_account_hears_no_broadcast(person, sio, async_session_factory):
    """Held at the password screen, at connect and at sign-in alike."""
    await _hold(async_session_factory, person)
    cookie = f"{auth_settings.COOKIE_NAME}={_token(person, datetime.now(timezone.utc))}"
    assert await connect("sid-a", {"HTTP_COOKIE": cookie})
    assert await get_session_user("sid-a"), "the socket did not authenticate"
    assert await connect("sid-b", {})
    await _login("sid-b", person)

    sio.enter_room.assert_not_awaited()
    assert await room_tracker.socket_rooms("sid-a") == set()
    assert await room_tracker.socket_rooms("sid-b") == set()


@pytest.mark.asyncio
async def test_a_held_account_hears_broadcasts_once_it_changes_its_password(
    person, sio, async_session_factory
):
    """The tab that changed the password is let back in; an older one is not."""
    await _hold(async_session_factory, person)
    now = datetime.now(timezone.utc)
    for sid, issued in (("sid-fresh", now), ("sid-old", now - LONG_AGO)):
        cookie = f"{auth_settings.COOKIE_NAME}={_token(person, issued)}"
        assert await connect(sid, {"HTTP_COOKIE": cookie})
    sio.enter_room.assert_not_awaited()

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

    assert _entered(sio, "sid-fresh") == {SIGNED_IN_ROOM}
    assert await room_tracker.socket_rooms("sid-fresh") == {SIGNED_IN_ROOM}
    assert _entered(sio, "sid-old") == set()


@pytest.mark.asyncio
async def test_a_socket_failing_to_sign_in_again_stops_hearing_broadcasts(person, sio):
    """A failed attempt clears the earlier session, and the room goes with it."""
    await _open_socket("sid-a", person, datetime.now(timezone.utc))

    with pytest.raises(SocketUnauthenticatedError):
        await authenticate_socket_connection(
            sid="sid-a", token="not.a.token", minimum_role="guest"
        )

    assert _left(sio, "sid-a") == {SIGNED_IN_ROOM}
    assert SIGNED_IN_ROOM not in await room_tracker.socket_rooms("sid-a")


# --- The index ---


@pytest.mark.asyncio
async def test_the_index_follows_connect_and_disconnect(person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await _open_socket("sid-b", person, datetime.now(timezone.utc))
    assert await get_user_socket_sids(person.id) == {"sid-a", "sid-b"}

    await unsubscribe("sid-a", RESOURCE_ROOM)
    assert await room_tracker.socket_rooms("sid-a") == {
        f"user-{person.id}",
        SIGNED_IN_ROOM,
    }

    await disconnect("sid-a")
    assert await get_user_socket_sids(person.id) == {"sid-b"}
    assert await room_tracker.socket_rooms("sid-a") == set()


# --- The user's membership follows the sockets that hold the room ---


@pytest.mark.asyncio
async def test_closing_one_tab_keeps_the_rooms_another_tab_holds(person, sio):
    """
    Notification routing asks whether the user is in a room, not a socket.

    Closing one of two tabs on a workspace must not tell it the user has
    stopped listening there; the room only the closed tab held goes.
    """
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await subscribe("sid-a", OTHER_ROOM)
    await _open_socket("sid-b", person, datetime.now(timezone.utc))

    await disconnect("sid-a")

    assert await room_tracker.is_in_room(person.id, RESOURCE_ROOM)
    assert await room_tracker.is_in_room(person.id, f"user-{person.id}")
    assert not await room_tracker.is_in_room(person.id, OTHER_ROOM)

    await disconnect("sid-b")

    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)
    assert not await room_tracker.is_in_room(person.id, f"user-{person.id}")


@pytest.mark.asyncio
async def test_leaving_a_room_in_one_tab_keeps_it_while_another_holds_it(person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await _open_socket("sid-b", person, datetime.now(timezone.utc))

    await unsubscribe("sid-a", RESOURCE_ROOM)
    assert await room_tracker.is_in_room(person.id, RESOURCE_ROOM)

    await unsubscribe("sid-b", RESOURCE_ROOM)
    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)
    # The tabs' other rooms are theirs still.
    assert await room_tracker.is_in_room(person.id, f"user-{person.id}")


@pytest.mark.asyncio
async def test_leaving_a_room_in_the_only_tab_keeps_its_other_rooms(person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await subscribe("sid-a", OTHER_ROOM)

    await unsubscribe("sid-a", RESOURCE_ROOM)

    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)
    assert await room_tracker.is_in_room(person.id, OTHER_ROOM)


@pytest.mark.asyncio
async def test_a_socket_gone_without_a_disconnect_holds_no_room(person, sio):
    """A worker that died ran no disconnect; its room record may outlive it."""
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await _open_socket("sid-dead", person, datetime.now(timezone.utc))
    await subscribe("sid-dead", OTHER_ROOM)
    await stop_session_renewal("sid-dead")
    await redis_storage_client.client.delete(
        storage_config.session_key("sid-dead", "/")
    )
    await _open_socket("sid-b", person, datetime.now(timezone.utc))

    await unsubscribe("sid-a", RESOURCE_ROOM)
    await unsubscribe("sid-b", RESOURCE_ROOM)
    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)

    await disconnect("sid-a")
    await disconnect("sid-b")
    assert not await room_tracker.is_in_room(person.id, OTHER_ROOM)
    assert not await room_tracker.is_in_room(person.id, f"user-{person.id}")


async def _strand(sid: str, worker: str = "a-worker-that-died") -> None:
    """
    Leave ``sid`` as an unclean worker death does: session and records intact.

    Its worker ran no disconnect, so only the worker's heartbeat is gone.
    """
    await stop_session_renewal(sid)
    await redis_storage_client.client.setex(
        storage_config.socket_worker_key(sid), storage_config.session_ttl, worker
    )


@pytest.mark.asyncio
async def test_a_socket_its_dead_worker_left_holds_no_room(person, sio):
    """
    A dead worker's sockets keep their sessions for up to a day.

    The user's last real tab closing must still clear every room, or
    notification routing keeps emitting to rooms no tab of theirs is in.
    """
    await _open_socket("sid-ghost", person, datetime.now(timezone.utc))
    await subscribe("sid-ghost", OTHER_ROOM)
    await _strand("sid-ghost")
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    assert await get_session_user("sid-ghost"), "the ghost lost its session"

    await disconnect("sid-a")

    assert not await room_tracker.is_in_room(person.id, OTHER_ROOM)
    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)
    assert not await room_tracker.is_in_room(person.id, f"user-{person.id}")


@pytest.mark.asyncio
async def test_leaving_a_room_a_dead_worker_s_socket_was_in_leaves_it(person, sio):
    await _open_socket("sid-ghost", person, datetime.now(timezone.utc))
    await _strand("sid-ghost")
    await _open_socket("sid-a", person, datetime.now(timezone.utc))

    await unsubscribe("sid-a", RESOURCE_ROOM)

    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)


@pytest.mark.asyncio
async def test_a_socket_on_another_running_worker_holds_its_rooms(person, sio):
    """Liveness is the holding worker's heartbeat, not this worker's."""
    await _open_socket("sid-b", person, datetime.now(timezone.utc))
    await subscribe("sid-b", OTHER_ROOM)
    await _strand("sid-b", worker="another-worker")
    await redis_storage_client.client.setex(
        storage_config.worker_key("another-worker"), storage_config.worker_ttl, "1"
    )
    await _open_socket("sid-a", person, datetime.now(timezone.utc))

    await disconnect("sid-a")

    assert await room_tracker.is_in_room(person.id, OTHER_ROOM)
    assert await room_tracker.is_in_room(person.id, RESOURCE_ROOM)


@pytest.mark.asyncio
async def test_a_disconnect_drops_the_socket_s_worker_record(person, sio):
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    assert await socket_is_held("sid-a")

    await disconnect("sid-a")

    assert not await redis_storage_client.client.exists(
        storage_config.socket_worker_key("sid-a")
    )


@pytest.mark.asyncio
async def test_the_worker_heartbeat_is_renewed_and_removed_on_shutdown(
    person, sio, live_rate_limiter, monkeypatch
):
    """
    The heartbeat lives exactly as long as the worker.

    It is refreshed while the worker runs, well inside its TTL, and a clean
    shutdown removes it, so a socket whose disconnect did not run reads as
    gone at once.
    """
    monkeypatch.setattr(storage_config, "worker_renewal_interval", 0.01)
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    beats: list[str] = []
    setex = live_rate_limiter.setex

    async def recording_setex(key: str, seconds: int, value: str) -> bool:
        beats.append(key)
        return await setex(key, seconds, value)

    live_rate_limiter.setex = recording_setex
    await asyncio.sleep(0.05)

    worker_key = storage_config.worker_key(worker_store.worker_id())
    assert beats.count(worker_key) >= 2
    assert await socket_is_held("sid-a")

    await stop_worker_heartbeat()

    assert not await live_rate_limiter.exists(worker_key)
    assert not await socket_is_held("sid-a")


@pytest.mark.asyncio
async def test_the_last_tab_closing_clears_every_room_of_the_user(person, sio):
    """Including a membership no socket's room record names (a lapsed record)."""
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await room_tracker.join(person.id, OTHER_ROOM)

    await disconnect("sid-a")

    assert not await room_tracker.is_in_room(person.id, OTHER_ROOM)
    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)


def _yield_at_every_call(redis) -> None:
    """
    Make each Redis command of the in-memory stub yield to the event loop.

    Two handlers awaited together then interleave at every command, as two
    workers sharing one Redis do.
    """
    for name in ("get", "exists", "setex", "delete", "sadd", "srem", "smembers"):
        command = getattr(redis, name)

        async def yielding(*args, _command=command, **kwargs):
            await asyncio.sleep(0)
            return await _command(*args, **kwargs)

        setattr(redis, name, yielding)


@pytest.mark.asyncio
async def test_two_tabs_closing_at_once_clear_every_room(
    person, sio, live_rate_limiter
):
    """
    Closing the browser window disconnects both tabs at the same moment.

    Neither may count the other as still holding the rooms, or the user stays
    in every room with no tab open.
    """
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await _open_socket("sid-b", person, datetime.now(timezone.utc))
    _yield_at_every_call(live_rate_limiter)

    await asyncio.gather(disconnect("sid-a"), disconnect("sid-b"))

    assert not await room_tracker.is_in_room(person.id, RESOURCE_ROOM)
    assert not await room_tracker.is_in_room(person.id, f"user-{person.id}")


@pytest.mark.asyncio
async def test_a_tab_leaving_a_room_another_is_entering_keeps_the_user_in_it(
    person, sio
):
    """
    Subscribe records the socket's room before the user's membership.

    An unsubscribe in another tab that runs between the two writes then finds
    the entering socket's hold, or its leave comes before the membership.
    """
    await _open_socket("sid-a", person, datetime.now(timezone.utc))
    await _open_socket("sid-b", person, datetime.now(timezone.utc))
    await unsubscribe("sid-b", RESOURCE_ROOM)
    join_socket = room_tracker.join_socket

    async def unsubscribe_meanwhile(sid, room):
        if sid == "sid-b":
            await unsubscribe("sid-a", RESOURCE_ROOM)
        await join_socket(sid, room)

    with patch.object(room_tracker, "join_socket", unsubscribe_meanwhile):
        await subscribe("sid-b", RESOURCE_ROOM)

    assert await room_tracker.socket_rooms("sid-b") >= {RESOURCE_ROOM}
    assert await room_tracker.is_in_room(person.id, RESOURCE_ROOM)


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
async def test_eviction_drops_a_socket_its_dead_worker_left(person, sio):
    """
    A dead worker's socket keeps a session subscribe would still admit.

    It holds no room for the user all the same: the room the evicted tab
    leaves goes from the user's membership. The ghost is evicted with it, and
    stays indexed: a worker that only stalled past its heartbeat still holds
    its socket, which the next eviction must find.
    """
    now = datetime.now(timezone.utc)
    await _open_socket("sid-ghost", person, now)
    await _strand("sid-ghost")
    await _open_socket("sid-old", person, now - LONG_AGO)

    async with _cookie_client(_token(person, now)) as client:
        resp = await client.post("/api/users/me/sign-out-everywhere")
    assert resp.status_code == 200, resp.text

    await _assert_evicted(sio, "sid-old", person)
    assert _left(sio, "sid-ghost") == {RESOURCE_ROOM, SIGNED_IN_ROOM}
    assert await get_user_socket_sids(person.id) == {"sid-old", "sid-ghost"}


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
    assert storage_config.socket_worker_key("sid-a") in refreshed
