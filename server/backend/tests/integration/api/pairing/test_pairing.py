"""
Integration tests for the agent device-pairing flow.

Exercises the full HTTP cycle (start -> approve -> poll) against the real
app with role-based clients. Redis is replaced with an in-memory fake via
the service's `_redis` hook — pairing state semantics (one-time pickup,
pending/approved/expired) are what matter here, not Redis itself.
"""

import json

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
    """Unauthenticated client, as the polling agent would connect."""
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        yield client


@pytest_asyncio.fixture(autouse=True)
async def clean_file_agent_tokens(async_session_factory):
    """Remove file-agent tokens and devices between tests so asserts are stable."""
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(AccessToken.service_name == "file-agent")
        )
        # Tokens cascade from a device delete, but a pairing that added a token
        # to a pre-existing device leaves the device behind; clear both.
        await session.execute(
            delete(AgentDevice).where(AgentDevice.service_name == "file-agent")
        )
        await session.commit()


async def _start(public_client, machine_name="ORBI-PC"):
    resp = await public_client.post(
        "/api/auth/pairing/start",
        json={"service_name": "file-agent", "machine_name": machine_name},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_full_pairing_flow(
    fake_redis, public_client, editor_client, async_session_factory
):
    started = await _start(public_client)
    assert "-" in started["user_code"]
    assert started["expires_in"] > 0

    # Agent polls before approval: pending
    resp = await public_client.post(
        "/api/auth/pairing/poll", json={"device_code": started["device_code"]}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Editor approves, entering the code lowercase and with the dash
    resp = await editor_client.post(
        "/api/auth/pairing/approve",
        json={"user_code": started["user_code"].lower()},
    )
    assert resp.status_code == 200, resp.text
    approved = resp.json()
    assert approved["service_name"] == "file-agent"
    assert approved["machine_name"] == "ORBI-PC"

    # Agent polls: receives the token exactly once
    resp = await public_client.post(
        "/api/auth/pairing/poll", json={"device_code": started["device_code"]}
    )
    body = resp.json()
    assert body["status"] == "approved"
    token = body["access_token"]
    assert token

    # The token is a real DB row, stamped with service and machine, and bound
    # to a device the pairing created.
    async with async_session_factory() as session:
        row = (
            await session.execute(select(AccessToken).where(AccessToken.token == token))
        ).scalar_one()
        assert row.service_name == "file-agent"
        assert row.description == "Paired: ORBI-PC"
        assert row.device_id is not None
        device = await session.get(AgentDevice, row.device_id)
    assert device.name == "ORBI-PC"
    assert device.service_name == "file-agent"
    assert device.sponsor_user_id is not None  # the approving editor
    assert device.revoked_at is None

    # Second poll: the pairing is gone
    resp = await public_client.post(
        "/api/auth/pairing/poll", json={"device_code": started["device_code"]}
    )
    assert resp.json()["status"] == "expired"


@pytest.mark.asyncio
async def test_pairing_does_not_revoke_existing_tokens(
    fake_redis, public_client, editor_client, async_session_factory, test_users
):
    # An existing token for the same user+service (e.g. another machine)
    resp = await editor_client.post(
        "/api/auth/access_token/regenerate", json={"service_name": "file-agent"}
    )
    assert resp.status_code == 200, resp.text

    started = await _start(public_client, machine_name="TOF-PC")
    resp = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert resp.status_code == 200, resp.text

    async with async_session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AccessToken)
                    .where(AccessToken.user_id == test_users["editor"].id)
                    .where(AccessToken.service_name == "file-agent")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2  # pairing added a token, did not replace


@pytest.mark.asyncio
async def test_approve_requires_editor_role(fake_redis, public_client, guest_client):
    started = await _start(public_client)
    resp = await guest_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_approve_unknown_code(fake_redis, editor_client):
    resp = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": "XXX-XXX"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_double_approve_conflicts(fake_redis, public_client, editor_client):
    started = await _start(public_client)
    resp = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert resp.status_code == 200
    resp = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_start_rejects_non_agent_services(fake_redis, public_client):
    for service_name in ("mascope_sdk", "file-converter", "nonsense"):
        resp = await public_client.post(
            "/api/auth/pairing/start", json={"service_name": service_name}
        )
        assert resp.status_code == 422, service_name


@pytest.mark.asyncio
async def test_poll_unknown_device_code_is_expired(fake_redis, public_client):
    resp = await public_client.post(
        "/api/auth/pairing/poll", json={"device_code": "x" * 32}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "expired"


@pytest.mark.asyncio
async def test_machine_name_is_sanitized(fake_redis, public_client, editor_client):
    started = await _start(public_client, machine_name="EVIL\r\nNAME\x00 ")
    resp = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert resp.status_code == 200
    assert resp.json()["machine_name"] == "EVILNAME"
    # Stored record agrees (control characters never reach Redis either)
    stored = [json.loads(v) for k, v in fake_redis.store.items() if "code:" in k]
    assert all("\r" not in (rec.get("machine_name") or "") for rec in stored)
