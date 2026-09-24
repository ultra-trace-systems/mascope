"""Integration tests: who sponsors a machine account.

``mascope_backend.db.devices.device_sponsor_id`` answers it for two callers:
``error_recipient``, which sends an error from a task a paired agent started to
the person who sponsors the agent's device, since nobody signs in as the
machine account; and the acquisition workspace, whose owners include the
sponsor of an uploading agent. The routing itself is unit-tested with this
lookup scripted (``tests/unit/socket/test_error_notification_audience.py``);
here it runs against rows created exactly as pairing approval creates them.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete, update

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.db import AccessToken, AgentDevice, User
from mascope_backend.db.devices import device_sponsor_id
from mascope_backend.socket.notifications.service import error_recipient


@pytest_asyncio.fixture(autouse=True)
async def clean_devices(async_session_factory):
    """Remove what ``provision_device`` created, as ``test_devices`` does.

    A device left behind shows up in the next suite's device listings.
    """
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(AccessToken.service_name == "file-agent")
        )
        await session.execute(
            delete(User).where(User.account_type == ACCOUNT_TYPE_MACHINE)
        )
        await session.execute(
            delete(AgentDevice).where(AgentDevice.service_name == "file-agent")
        )
        await session.commit()


async def _sponsor(async_session_factory, user_id):
    async with async_session_factory() as session:
        return await device_sponsor_id(session, user_id)


@pytest.mark.asyncio
async def test_a_machine_account_resolves_to_its_devices_sponsor(
    async_session_factory, test_users, provision_device
):
    sponsor_id = test_users["editor"].id
    _device_id, machine, _token = await provision_device(sponsor_id)

    assert await _sponsor(async_session_factory, machine.id) == sponsor_id
    assert await error_recipient(machine.id) == sponsor_id


@pytest.mark.asyncio
async def test_a_person_has_no_device_sponsor(async_session_factory, test_users):
    person_id = test_users["editor"].id

    assert await _sponsor(async_session_factory, person_id) is None
    assert await error_recipient(person_id) == person_id


@pytest.mark.asyncio
async def test_no_account_has_no_sponsor(async_session_factory):
    assert await _sponsor(async_session_factory, None) is None
    assert await error_recipient(None) is None


@pytest.mark.asyncio
async def test_a_machine_whose_sponsor_is_gone_keeps_its_own_errors(
    async_session_factory, test_users, provision_device
):
    """``sponsor_user_id`` is SET NULL when the sponsor's account is removed."""
    device_id, machine, _token = await provision_device(test_users["admin"].id)
    async with async_session_factory() as session:
        await session.execute(
            update(AgentDevice)
            .where(AgentDevice.device_id == device_id)
            .values(sponsor_user_id=None)
        )
        await session.commit()

    assert await _sponsor(async_session_factory, machine.id) is None
    assert await error_recipient(machine.id) == machine.id
