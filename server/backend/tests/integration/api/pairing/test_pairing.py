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

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.new.auth.pairing import service as pairing_service
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, AgentDevice, User
from mascope_backend.roles import ROLE_ACCESS_LEVELS


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
    """Remove file-agent tokens, machine accounts and devices between tests."""
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(AccessToken.service_name == "file-agent")
        )
        # Deleting a machine account cascades its tokens (the file-converter
        # token pairing mints) and NULLs the device's machine_user_id.
        await session.execute(
            delete(User).where(User.account_type == ACCOUNT_TYPE_MACHINE)
        )
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

    # The token is a real DB row, stamped with service and machine, bound to a
    # device the pairing created, and owned by a machine account - not the
    # approver.
    async with async_session_factory() as session:
        row = (
            await session.execute(select(AccessToken).where(AccessToken.token == token))
        ).scalar_one()
        assert row.service_name == "file-agent"
        assert row.description == "Paired: ORBI-PC"
        assert row.device_id is not None
        device = await session.get(AgentDevice, row.device_id)
        token_user = await session.get(User, row.user_id)
    assert device.name == "ORBI-PC"
    assert device.service_name == "file-agent"
    assert device.sponsor_user_id is not None  # the approving editor
    assert device.revoked_at is None
    # The device authenticates as its own machine account, sponsored by the
    # approver but not owned by them.
    assert device.machine_user_id == token_user.id
    assert token_user.account_type == ACCOUNT_TYPE_MACHINE
    assert token_user.id != device.sponsor_user_id
    assert token_user.role_id == ROLE_ACCESS_LEVELS["editor"]

    # Second poll: the pairing is gone
    resp = await public_client.post(
        "/api/auth/pairing/poll", json={"device_code": started["device_code"]}
    )
    assert resp.json()["status"] == "expired"


@pytest.mark.asyncio
async def test_pairing_token_belongs_to_a_machine_account_not_the_approver(
    fake_redis, public_client, editor_client, async_session_factory, test_users
):
    # The approver holds a file-agent token of their own (e.g. a manual one).
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
        # The approver's own file-agent tokens are untouched: pairing no longer
        # mints against the approver at all.
        approver_rows = (
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
        assert len(approver_rows) == 1

        # The pairing's token belongs to a machine account, device-bound.
        machine_rows = (
            (
                await session.execute(
                    select(AccessToken)
                    .join(User, User.id == AccessToken.user_id)
                    .where(User.account_type == ACCOUNT_TYPE_MACHINE)
                    .where(AccessToken.service_name == "file-agent")
                )
            )
            .scalars()
            .all()
        )
    assert len(machine_rows) == 1
    assert machine_rows[0].device_id is not None


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
