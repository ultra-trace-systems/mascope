"""
Tests: registration enrols the new account in the system workspaces.

``register_user`` seeds every new account's membership in each system
(acquisition) workspace, so the holder can reach the instruments the
deployment already has. That enrolment goes through the same workspace-member
controller an administrator's ``POST /workspaces/{id}/members`` uses, which is
what these tests pin: the memberships appear at the right role, only in system
workspaces, tolerate already being there, and announce themselves on the
record-reload channel like any other membership.

Lives in the workspace package because the ``acquisitions_workspace``
(``is_system=True``) fixture is defined in its conftest.

Fixtures used:
- ``acquisitions_workspace`` - system workspace "Acquisitions test-orbion"
- ``ws_alpha`` - an ordinary (non-system) workspace
- ``owner_client`` - authenticated owner, the only role that may assign any role
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from mascope_backend.api.new.workspaces import service as workspaces_service
from mascope_backend.api.new.workspaces.system import add_to_system_workspaces
from mascope_backend.db import User, WorkspaceMember, async_session
from mascope_backend.db.id import gen_id


#: Email/username prefix the teardown below matches on. The integration
#: database is one shared, session-scoped database, so accounts created here
#: must not survive into other suites' user listings.
_PREFIX = "regtest-"


@pytest_asyncio.fixture(autouse=True)
async def clean_registered_accounts(async_session_factory):
    """Delete the accounts these tests register.

    ``workspace_member.user_id`` is ``ondelete="CASCADE"``, so the memberships
    go with the account.
    """
    yield
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.email.like(f"{_PREFIX}%")))
        await session.commit()


async def _register(client, role_id: int) -> int:
    """Register a throwaway account and return its user id.

    No password is sent: the server generates the hand-over one, which is how
    an administrator creates an account through the UI.

    :param client: An authenticated AsyncClient.
    :param role_id: Global role access level (see ``ROLE_ACCESS_LEVELS``).
    :return: The new account's user id.
    :rtype: int
    """
    suffix = gen_id()
    resp = await client.post(
        "/api/users/owner/register",
        json={
            "email": f"{_PREFIX}{suffix}@test.com",
            "username": f"{_PREFIX}{suffix}",
            "role_id": role_id,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


async def _membership_role(workspace_id: str, user_id: int) -> str | None:
    """The account's role in a workspace, or None when it is not a member."""
    async with async_session() as session:
        result = await session.execute(
            select(WorkspaceMember.workspace_role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()


async def _membership_count(user_id: int) -> int:
    """How many workspaces the account is a member of."""
    async with async_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.user_id == user_id)
        )
        return result.scalar()


# ============= Enrolment =============


@pytest.mark.asyncio
async def test_registration_enrols_an_admin_in_every_system_workspace(
    owner_client, acquisitions_workspace
):
    """A newly registered admin is a member of the system workspace."""
    user_id = await _register(owner_client, role_id=300)

    assert await _membership_role(acquisitions_workspace, user_id) == "admin"


@pytest.mark.asyncio
@pytest.mark.parametrize("role_id", [100, 200])
async def test_registration_does_not_enrol_a_guest_or_an_editor(
    owner_client, acquisitions_workspace, role_id
):
    """Guests and editors are invited to instruments, never enrolled by default.

    This is the rule creating a system workspace already follows and the one
    docs/authorization.md states. Registration used to enrol every account at
    its matching role, so a guest created today reached every instrument on
    the deployment while a guest created before those workspaces existed
    reached none.
    """
    user_id = await _register(owner_client, role_id=role_id)

    assert await _membership_role(acquisitions_workspace, user_id) is None
    assert await _membership_count(user_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role_id", "expected_role"),
    [(300, "admin"), (400, "owner")],
)
async def test_registration_grants_the_workspace_role_matching_the_global_role(
    owner_client, acquisitions_workspace, role_id, expected_role
):
    """The workspace role mirrors the account's global role.

    The owner case is the one that matters: the enrolment passes the granted
    role as its own ceiling, so nothing is refused. A fixed lower ceiling
    would fail the whole registration with a 403 here.
    """
    user_id = await _register(owner_client, role_id=role_id)

    assert await _membership_role(acquisitions_workspace, user_id) == expected_role


@pytest.mark.asyncio
async def test_registration_does_not_touch_non_system_workspaces(
    owner_client, acquisitions_workspace, ws_alpha
):
    """Ordinary workspaces are left alone - only ``is_system`` ones are seeded."""
    user_id = await _register(owner_client, role_id=300)

    assert await _membership_role(ws_alpha["workspace_id"], user_id) is None


# ============= Re-enrolment =============


@pytest.mark.asyncio
async def test_enrolling_an_already_enrolled_account_is_a_no_op(
    owner_client, acquisitions_workspace
):
    """A second enrolment adds nothing and does not raise.

    An instrument workspace created concurrently enrols every existing account,
    so it can win that race against a registration. The membership asked for
    then already exists, which the controller reports as a 409 - tolerated
    here, rather than failing a registration whose user row is already
    committed.
    """
    user_id = await _register(owner_client, role_id=300)
    before = await _membership_count(user_id)

    added = await add_to_system_workspaces(user_id, "admin")

    assert added == 0
    assert await _membership_count(user_id) == before


# ============= Record reload =============


@pytest.mark.asyncio
async def test_registration_announces_the_new_membership(
    owner_client, acquisitions_workspace, monkeypatch
):
    """The enrolment emits the record-reload event a member addition emits.

    Going through the controller is what puts this on the wire; the hand-built
    insert it replaced was silent. The members dialog reloads its roster on
    this broadcast and filters it by ``record_id``, so both the event and the
    workspace it names are part of that contract.
    """
    emit = AsyncMock()
    monkeypatch.setattr(workspaces_service, "emit_record_reload", emit)

    user_id = await _register(owner_client, role_id=300)

    # The rooms and the record_id are asserted as one pair, on one call: the
    # dialog only reloads when a single broadcast both reaches a room the tab
    # has joined and names the workspace on screen. Checked over the list of
    # calls instead, an implementation splitting the two across separate emits
    # would pass here and refresh nothing.
    assert (acquisitions_workspace, [acquisitions_workspace, f"user-{user_id}"]) in [
        (call.kwargs.get("record_id"), call.kwargs.get("room"))
        for call in emit.await_args_list
        if call.kwargs.get("record_type") == "workspace"
    ]
