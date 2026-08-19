"""
Integration tests for short-lived device tokens and their renewal.

A device token expires far sooner than a personal one and the agent renews it
automatically. Here: the renewal endpoint issues a fresh token and reaps old
ones, a device token past its lifetime is refused, and the machine account's
converter token is minted on demand so a short lifetime never strands uploads.
"""

from datetime import datetime as dt
from datetime import timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.new.auth.access_token.service import (
    create_access_token,
    get_access_token,
)
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.devices.machine_account import create_machine_account
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, AgentDevice, User


@pytest_asyncio.fixture(autouse=True)
async def clean_state(async_session_factory):
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


def _bearer_client(token: str, service: str = "file-agent") -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Service-Name": service},
    )


async def _provision_device(async_session_factory, sponsor_id: int):
    """Create a device + machine account + a device-bound token, as pairing does."""
    async with async_session_factory() as session:
        device = AgentDevice(
            name="ORBI-PC", service_name="file-agent", sponsor_user_id=sponsor_id
        )
        session.add(device)
        await session.commit()
        await session.refresh(device)
        device_id = device.device_id

    machine = await create_machine_account("ORBI-PC", device_id)
    async with async_session_factory() as session:
        d = await session.get(AgentDevice, device_id)
        d.machine_user_id = machine.id
        await session.commit()

    token = await create_access_token(
        user=machine,
        service_name="file-agent",
        description="Paired: ORBI-PC",
        device_id=device_id,
    )
    return device_id, machine, token


@pytest.mark.asyncio
async def test_renewal_issues_a_fresh_token(async_session_factory, test_users):
    device_id, _machine, token = await _provision_device(
        async_session_factory, test_users["editor"].id
    )

    async with _bearer_client(token) as client:
        resp = await client.post("/api/auth/devices/token")
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    new_token = body["access_token"]
    assert new_token and new_token != token
    assert (
        body["expires_in"] == auth_settings.access_token.DEVICE_TOKEN_LIFETIME_SECONDS
    )

    # The new token is device-bound and authenticates.
    async with _bearer_client(new_token) as client:
        check = await client.get("/api/sample/files", params={"page": 0, "limit": 1})
    assert check.status_code != 401


@pytest.mark.asyncio
async def test_renewal_reaps_to_two_tokens(async_session_factory, test_users):
    device_id, _machine, token = await _provision_device(
        async_session_factory, test_users["editor"].id
    )

    # Renew twice; keep-2 means the device never accumulates more than two.
    current = token
    for _ in range(2):
        async with _bearer_client(current) as client:
            resp = await client.post("/api/auth/devices/token")
        assert resp.status_code == 200, resp.text
        current = resp.json()["data"]["access_token"]

    async with async_session_factory() as session:
        count = (
            await session.execute(
                select(func.count(AccessToken.token)).where(
                    AccessToken.device_id == device_id
                )
            )
        ).scalar_one()
    assert count == auth_settings.access_token.DEVICE_TOKENS_KEPT_PER_DEVICE


@pytest.mark.asyncio
async def test_device_token_past_its_lifetime_is_refused(
    async_session_factory, test_users
):
    _device_id, _machine, token = await _provision_device(
        async_session_factory, test_users["editor"].id
    )

    # A fresh device token authenticates...
    async with _bearer_client(token) as client:
        fresh = await client.get("/api/sample/files", params={"page": 0, "limit": 1})
    assert fresh.status_code != 401

    # ...but backdated past the device lifetime, it is refused.
    lifetime = auth_settings.access_token.DEVICE_TOKEN_LIFETIME_SECONDS
    async with async_session_factory() as session:
        await session.execute(
            update(AccessToken)
            .where(AccessToken.token == token)
            .values(created_at=dt.now(timezone.utc) - timedelta(seconds=lifetime + 60))
        )
        await session.commit()

    async with _bearer_client(token) as client:
        stale = await client.get("/api/sample/files", params={"page": 0, "limit": 1})
    assert stale.status_code == 401


@pytest.mark.asyncio
async def test_renewal_refused_for_a_non_device_token(editor_client, test_users):
    # A personal (mascope_sdk) token has no device, so it cannot be renewed.
    regen = await editor_client.post(
        "/api/auth/access_token/regenerate", json={"service_name": "mascope_sdk"}
    )
    assert regen.status_code == 200, regen.text
    personal = regen.json()["data"]["access_token"]

    async with _bearer_client(personal, service="mascope_sdk") as client:
        resp = await client.post("/api/auth/devices/token")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_machine_account_converter_token_minted_on_demand(
    async_session_factory, test_users
):
    _device_id, machine, _token = await _provision_device(
        async_session_factory, test_users["editor"].id
    )
    # Simulate a lapsed/absent converter token by removing any that exist.
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(
                AccessToken.user_id == machine.id,
                AccessToken.service_name == "file-converter",
            )
        )
        await session.commit()

    # get_access_token mints one on demand for a machine account rather than
    # refusing, so the converter can always write back the upload's results.
    minted = await get_access_token(user=machine, service_name="file-converter")
    assert minted

    async with async_session_factory() as session:
        count = (
            await session.execute(
                select(func.count(AccessToken.token)).where(
                    AccessToken.user_id == machine.id,
                    AccessToken.service_name == "file-converter",
                )
            )
        ).scalar_one()
    assert count == 1
