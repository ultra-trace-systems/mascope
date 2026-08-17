"""
Tests: the forced-password-change gate on the socket subscription path.

The REST dependencies refuse an account that owes a password change, but rooms
carry record-sync events with full record data, so the same account must not be
able to keep reading over the socket what the API has started refusing.

The handler is wrapped in ``socket_auth``, which swallows exceptions by design -
a bug in this harness would therefore look exactly like a refusal. Every refusal
case here is paired with a case that must join, so a harness that cannot produce
a join at all fails instead of reporting a gate it never exercised.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import order matters, and only here. Entering the graph through socket.auth
# leaves that package half-built when user_manager.service imports
# authenticate_socket_connection from it, so the handler import below fails on
# its own. Importing the auth stack first lets socket.auth complete once.
import mascope_backend.api.new.auth  # noqa: F401
from mascope_backend.socket.events.handlers.default.subscription import subscribe


_HANDLER = "mascope_backend.socket.events.handlers.default.subscription"
_DECORATORS = "mascope_backend.socket.auth.decorators"
_ACL = "mascope_backend.api.new.workspaces.dependencies.user_can_subscribe_to_room"

#: Comfortably above any configured role threshold, so the decorator's role
#: check never becomes the reason a subscription is refused.
_ROLE_ID = 1000

USER_ID = 42
RESOURCE_ROOM = "some-sample-batch-id"
OWN_ROOM = f"user-{USER_ID}"


class _FakeSession:
    """Async context manager standing in for ``async_session()``."""

    def __init__(self, user):
        self._user = user

    async def __aenter__(self):
        return SimpleNamespace(get=AsyncMock(return_value=self._user))

    async def __aexit__(self, *_):
        return False


async def _subscribe(room: str, *, must_change_password: bool):
    """
    Drive ``subscribe`` for a user in the given credential state.

    :param room: Room the client asks to join.
    :param must_change_password: Whether the account owes a password change.
    :return: The patched ``sio`` mock, for asserting on ``enter_room``.
    """
    user = SimpleNamespace(
        id=USER_ID, must_change_password=must_change_password, role_id=_ROLE_ID
    )
    session = {"user_id": USER_ID, "role_id": _ROLE_ID}
    sio = MagicMock()
    sio.enter_room = AsyncMock()
    room_tracker = MagicMock()
    room_tracker.join = AsyncMock()

    with (
        patch(f"{_DECORATORS}.get_session_user", AsyncMock(return_value=session)),
        patch(f"{_HANDLER}.get_session_user", AsyncMock(return_value=session)),
        patch(f"{_HANDLER}.sio", sio),
        patch(f"{_HANDLER}.room_tracker", room_tracker),
        patch(_ACL, AsyncMock(return_value=True)),
        patch(
            "mascope_backend.db.async_session",
            MagicMock(return_value=_FakeSession(user)),
        ),
    ):
        await subscribe("test-sid", room)

    return sio


# ============= Positive controls: the harness can produce a join =============


@pytest.mark.asyncio
async def test_unflagged_user_joins_a_resource_room():
    """Without this, every assertion below would hold against a dead handler."""
    sio = await _subscribe(RESOURCE_ROOM, must_change_password=False)
    sio.enter_room.assert_awaited_once_with("test-sid", RESOURCE_ROOM)


@pytest.mark.asyncio
async def test_flagged_user_still_joins_its_own_channel():
    """The single exemption: the gated tab hears it is gated, and released."""
    sio = await _subscribe(OWN_ROOM, must_change_password=True)
    sio.enter_room.assert_awaited_once_with("test-sid", OWN_ROOM)


# ============= The gate =============


@pytest.mark.asyncio
async def test_flagged_user_is_refused_a_resource_room():
    sio = await _subscribe(RESOURCE_ROOM, must_change_password=True)
    sio.enter_room.assert_not_awaited()


@pytest.mark.asyncio
async def test_flagged_user_is_refused_another_users_channel():
    """The exemption is this user's own room, not the shape ``user-*``."""
    sio = await _subscribe(f"user-{USER_ID + 1}", must_change_password=True)
    sio.enter_room.assert_not_awaited()
