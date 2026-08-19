"""
Integration tests for machine (instrument agent) accounts.

A machine account is the subject of an agent's credential. It must never behave
like a person: no interactive session, no place in the human user list, exempt
from the deployment-wide password sweep, and unmanageable through the user
routes. It also carries the sponsor into the acquisition workspace its uploads
create, so the human who paired the agent still sees the data.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.controllers.dataset.acquisition.service import (
    _ensure_instrument_workspace,
)
from mascope_backend.api.new.auth.devices.machine_account import create_machine_account
from mascope_backend.api.new.workspaces.dependencies import (
    check_instrument_workspace_access,
)
from mascope_backend.app.fast import fast
from mascope_backend.db import (
    AgentDevice,
    User,
    Workspace,
    WorkspaceMember,
    async_session,
)
from mascope_backend.db.admin.user.require_password_change import (
    require_password_change_for_all_users,
)


_TEST_INSTRUMENT = "PentestOrbion"
_OTHER_INSTRUMENT = "PentestOrbionTwo"


@pytest_asyncio.fixture
async def public_client():
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture(autouse=True)
async def clean_machine_state(async_session_factory):
    """Remove machine accounts and devices, the test workspace, and undo the
    deployment-wide password-change flag the sweep test sets on the shared,
    session-scoped person accounts (otherwise later tests' cookie logins gate)."""
    yield
    async with async_session_factory() as session:
        await session.execute(delete(AgentDevice))
        await session.execute(
            delete(User).where(User.account_type == ACCOUNT_TYPE_MACHINE)
        )
        # The workspace name's prefix is config-driven, so match on the
        # instrument substring rather than the exact name.
        test_ws = select(Workspace.workspace_id).where(
            Workspace.workspace_name.contains(_TEST_INSTRUMENT)
        )  # _OTHER_INSTRUMENT shares the prefix, so this matches both
        await session.execute(
            delete(WorkspaceMember).where(WorkspaceMember.workspace_id.in_(test_ws))
        )
        await session.execute(
            delete(Workspace).where(Workspace.workspace_name.contains(_TEST_INSTRUMENT))
        )
        await session.execute(
            update(User)
            .where(User.must_change_password.is_(True))
            .values(must_change_password=False, password_change_reason=None)
        )
        await session.commit()


async def _provision_machine(
    sponsor_id: int, name: str = "AGENT-PC"
) -> tuple[int, int]:
    """Create a device + its machine account, sponsored by ``sponsor_id``.

    :return: ``(device_id, machine_user_id)``.
    """
    async with async_session() as session:
        device = AgentDevice(
            name=name, service_name="file-agent", sponsor_user_id=sponsor_id
        )
        session.add(device)
        await session.flush()
        machine = await create_machine_account(
            session,
            machine_name=name,
            device_id=device.device_id,
            sponsor_user_id=sponsor_id,
        )
        device.machine_user_id = machine.id
        await session.commit()
        return device.device_id, machine.id


@pytest.mark.asyncio
async def test_machine_account_cannot_sign_in(public_client, test_users):
    _, machine_id = await _provision_machine(test_users["editor"].id)
    async with async_session() as session:
        machine = await session.get(User, machine_id)

    resp = await public_client.post(
        "/api/auth/login",
        data={"username": machine.email, "password": "anything-at-all-123"},
    )
    # Refused like any bad sign-in - a machine account has no interactive path.
    assert resp.status_code == 400
    assert "access_token" not in resp.text


@pytest.mark.asyncio
async def test_machine_account_absent_from_user_list(admin_client, test_users):
    _, machine_id = await _provision_machine(test_users["editor"].id)

    resp = await admin_client.get("/api/users")
    assert resp.status_code == 200
    ids = [u["id"] for u in resp.json()["data"]]
    assert machine_id not in ids
    # A person account is still listed, so the filter is not hiding everyone.
    assert test_users["editor"].id in ids


@pytest.mark.asyncio
async def test_password_sweep_skips_machine_accounts(test_users):
    _, machine_id = await _provision_machine(test_users["editor"].id)

    await require_password_change_for_all_users()

    async with async_session() as session:
        machine = await session.get(User, machine_id)
        person = await session.get(User, test_users["editor"].id)
    assert machine.must_change_password is False  # exempt
    assert person.must_change_password is True  # swept


@pytest.mark.asyncio
async def test_machine_account_cannot_be_updated_or_deleted(admin_client, test_users):
    _, machine_id = await _provision_machine(test_users["editor"].id)

    patched = await admin_client.patch(
        f"/api/users/admin/{machine_id}", json={"username": "hijacked"}
    )
    assert patched.status_code == 403

    deleted = await admin_client.delete(f"/api/users/admin/{machine_id}")
    assert deleted.status_code == 403

    # Credentials too: a machine account sits at editor regardless of who
    # sponsors its device, so the "not an admin or above" ceiling on these
    # routes would otherwise let an admin strip an owner's agent - the very
    # thing revoke_device refuses. Revocation is the only way to stop a device.
    tokens_deleted = await admin_client.delete(
        f"/api/users/admin/{machine_id}/access-tokens"
    )
    assert tokens_deleted.status_code == 403

    mfa_reset = await admin_client.post(f"/api/users/admin/{machine_id}/mfa/reset")
    assert mfa_reset.status_code == 403


@pytest.mark.asyncio
async def test_machine_account_joins_a_pre_existing_acquisition_workspace(test_users):
    """The migration case: an agent paired for an instrument already in use.

    The workspace-creation path never runs for such an agent, so without an
    enrolment at provisioning time every one of its uploads is refused by the
    workspace ACL - which is what re-pairing an existing deployment does.
    """
    sponsor_id = test_users["editor"].id

    # The instrument has been acquiring for a while: its workspace exists.
    ws_id = await _ensure_instrument_workspace(
        _TEST_INSTRUMENT, owner_user_id=sponsor_id
    )

    # Only now is the machine paired.
    _, machine_id = await _provision_machine(sponsor_id)

    async with async_session() as session:
        machine = await session.get(User, machine_id)
        member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == ws_id,
                    WorkspaceMember.user_id == machine_id,
                )
            )
        ).scalar_one_or_none()

    assert member is not None, "machine account was not enrolled in the workspace"
    assert member.workspace_role == "editor"

    # And the check the upload routes actually apply agrees.
    await check_instrument_workspace_access(
        _TEST_INSTRUMENT, machine, "editor", allow_new=True
    )


@pytest.mark.asyncio
async def test_machine_account_reaches_no_further_than_its_sponsor(test_users):
    """The device's token must not open instruments its sponsor cannot.

    It lives in plaintext on a shared instrument PC, so enrolling it in every
    acquisition workspace would hand whoever reads it more than the person who
    vouched for it ever had.
    """
    sponsor_id = test_users["editor"].id

    # One workspace the sponsor works in, one they have no membership of.
    sponsored_ws = await _ensure_instrument_workspace(
        _TEST_INSTRUMENT, owner_user_id=sponsor_id
    )
    other_ws = await _ensure_instrument_workspace(
        _OTHER_INSTRUMENT, owner_user_id=test_users["owner"].id
    )
    async with async_session() as session:
        await session.execute(
            delete(WorkspaceMember).where(
                WorkspaceMember.workspace_id == other_ws,
                WorkspaceMember.user_id == sponsor_id,
            )
        )
        await session.commit()

    _, machine_id = await _provision_machine(sponsor_id)

    async with async_session() as session:
        joined = set(
            (
                await session.execute(
                    select(WorkspaceMember.workspace_id).where(
                        WorkspaceMember.user_id == machine_id
                    )
                )
            )
            .scalars()
            .all()
        )

    assert sponsored_ws in joined
    assert other_ws not in joined, "machine reached a workspace its sponsor cannot"


@pytest.mark.asyncio
async def test_auto_created_workspace_makes_the_sponsor_an_owner(test_users):
    sponsor_id = test_users["editor"].id
    _, machine_id = await _provision_machine(sponsor_id)

    # The machine account's upload would create this workspace with itself as
    # owner; drive that path directly.
    ws_id = await _ensure_instrument_workspace(
        _TEST_INSTRUMENT, owner_user_id=machine_id
    )

    async with async_session() as session:
        members = {
            user_id: role
            for user_id, role in (
                await session.execute(
                    select(
                        WorkspaceMember.user_id, WorkspaceMember.workspace_role
                    ).where(WorkspaceMember.workspace_id == ws_id)
                )
            ).all()
        }

    # The machine owns the workspace, and its plain-editor sponsor is elevated
    # to owner so they can see what the agent ingests.
    assert members.get(machine_id) == "owner"
    assert members.get(sponsor_id) == "owner"
