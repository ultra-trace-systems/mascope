"""
Integration tests for paired-device management.

Devices are created by pairing approval and managed through /api/auth/devices:
listed to their sponsor, renamed, and revoked one at a time. The property that
matters most is revocation isolation - revoking one machine must not disturb
another paired to the same account - because without it operators fall back to
the all-or-nothing regenerate flow.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from mascope_backend.api.new.auth.pairing import service as pairing_service
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, AgentDevice


class FakeRedis:
    """Minimal async stand-in for the shared Redis client (no TTL clock)."""

    def __init__(self):
        self.store = {}

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(pairing_service, "_redis", lambda: fake)
    return fake


@pytest_asyncio.fixture
async def public_client():
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture(autouse=True)
async def clean_devices(async_session_factory):
    """Remove file-agent tokens and devices between tests."""
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(AccessToken.service_name == "file-agent")
        )
        await session.execute(
            delete(AgentDevice).where(AgentDevice.service_name == "file-agent")
        )
        await session.commit()


async def _pair(public_client, approver_client, machine_name):
    """Run a full pairing and return (token, device_id)."""
    started = (
        await public_client.post(
            "/api/auth/pairing/start",
            json={"service_name": "file-agent", "machine_name": machine_name},
        )
    ).json()
    approve = await approver_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert approve.status_code == 200, approve.text
    polled = (
        await public_client.post(
            "/api/auth/pairing/poll", json={"device_code": started["device_code"]}
        )
    ).json()
    token = polled["access_token"]

    listed = (await approver_client.get("/api/auth/devices")).json()["data"]
    device_id = next(d["device_id"] for d in listed if d["name"] == machine_name)
    return token, device_id


@pytest.mark.asyncio
async def test_sponsor_lists_only_their_own_devices(
    fake_redis, public_client, editor_client, admin_client
):
    await _pair(public_client, editor_client, "EDITOR-PC")

    editor_devices = (await editor_client.get("/api/auth/devices")).json()["data"]
    assert [d["name"] for d in editor_devices] == ["EDITOR-PC"]
    assert editor_devices[0]["sponsor_username"] == "editor_user"
    assert editor_devices[0]["token_count"] == 1

    # An admin who sponsored nothing sees an empty self list...
    admin_devices = (await admin_client.get("/api/auth/devices")).json()["data"]
    assert admin_devices == []
    # ...but the deployment-wide view shows every device.
    all_devices = (await admin_client.get("/api/auth/devices/all")).json()["data"]
    assert "EDITOR-PC" in [d["name"] for d in all_devices]


@pytest.mark.asyncio
async def test_rename_device(fake_redis, public_client, editor_client):
    _, device_id = await _pair(public_client, editor_client, "OLD-NAME")

    resp = await editor_client.patch(
        f"/api/auth/devices/{device_id}", json={"name": "NEW-NAME"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["name"] == "NEW-NAME"


@pytest.mark.asyncio
async def test_revoking_one_device_leaves_the_other(
    fake_redis, public_client, editor_client, async_session_factory
):
    token_a, device_a = await _pair(public_client, editor_client, "PC-A")
    token_b, device_b = await _pair(public_client, editor_client, "PC-B")

    resp = await editor_client.delete(f"/api/auth/devices/{device_a}")
    assert resp.status_code == 200, resp.text

    async with async_session_factory() as session:
        # The revoked device's token is gone; the other survives.
        surviving = (
            (
                await session.execute(
                    select(AccessToken.token).where(
                        AccessToken.service_name == "file-agent"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert surviving == [token_b]

        # The revoked device row is kept, stamped, for attribution history.
        revoked = await session.get(AgentDevice, device_a)
        assert revoked is not None
        assert revoked.revoked_at is not None

        survivor = await session.get(AgentDevice, device_b)
        assert survivor.revoked_at is None


@pytest.mark.asyncio
async def test_cannot_revoke_another_users_device(
    fake_redis, public_client, editor_client, guest_client
):
    _, device_id = await _pair(public_client, editor_client, "EDITOR-PC")

    # A guest neither sponsors the device nor has the admin ceiling to revoke it.
    resp = await guest_client.delete(f"/api/auth/devices/{device_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_revoke_an_editors_device(
    fake_redis, public_client, editor_client, admin_client, async_session_factory
):
    _, device_id = await _pair(public_client, editor_client, "EDITOR-PC")

    resp = await admin_client.delete(f"/api/auth/devices/{device_id}")
    assert resp.status_code == 200, resp.text

    async with async_session_factory() as session:
        device = await session.get(AgentDevice, device_id)
        assert device.revoked_at is not None
