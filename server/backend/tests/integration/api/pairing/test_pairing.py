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


async def _start(public_client, machine_name="ORBI-PC", **reported):
    """Start a pairing; ``reported`` carries what the agent says about itself."""
    resp = await public_client.post(
        "/api/auth/pairing/start",
        json={"service_name": "file-agent", "machine_name": machine_name, **reported},
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
    # What this server does with what the agent reports, so the agent's setup
    # can skip the upload-prefix question an older server needed answered.
    assert started["capabilities"] == {"files_uploads_under_reported_instrument": True}

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
    # An agent that reports no instrument leaves the field empty, not absent.
    assert approved["instrument"] is None

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
    # Nothing reported, nothing recorded: older agents send neither field.
    assert device.instrument is None
    assert device.last_seen_version is None
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
async def test_pairing_keeps_the_reported_instrument_and_version(
    fake_redis, public_client, editor_client, async_session_factory
):
    # What the agent said about itself when it asked to pair lands on the
    # device row at approval, and the instrument is echoed to the approver.
    started = await _start(
        public_client,
        machine_name="LAB-PC",
        instrument="Orbi-Lab2",
        agent_version="v2.0.0",
    )
    resp = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["instrument"] == "Orbi-Lab2"

    async with async_session_factory() as session:
        device = (
            await session.execute(
                select(AgentDevice).where(AgentDevice.name == "LAB-PC")
            )
        ).scalar_one()
    assert device.instrument == "Orbi-Lab2"
    assert device.last_seen_version == "v2.0.0"


@pytest.mark.asyncio
async def test_pairing_refuses_an_instrument_the_server_cannot_file(
    fake_redis, public_client
):
    # The same rule as the instrument segment of a file name: letters, digits
    # and hyphens. Anything else is refused at the door rather than stored and
    # rejected the day routing starts reading it.
    resp = await public_client.post(
        "/api/auth/pairing/start",
        json={"service_name": "file-agent", "instrument": "orbi lab"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_blank_reported_fields_are_stored_as_nothing(
    fake_redis, public_client, editor_client, async_session_factory
):
    started = await _start(
        public_client, machine_name="BLANK-PC", instrument="  ", agent_version=" "
    )
    resp = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["instrument"] is None
    async with async_session_factory() as session:
        device = (
            await session.execute(
                select(AgentDevice).where(AgentDevice.name == "BLANK-PC")
            )
        ).scalar_one()
    assert device.instrument is None
    assert device.last_seen_version is None


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


@pytest.mark.asyncio
async def test_a_long_agent_version_is_cut_rather_than_refused(
    fake_redis, public_client, editor_client
):
    # `git describe` off a date-style release tag runs past the column's
    # width. Pairing is the only route a machine has to a credential, so a
    # label it reports about itself must never be able to fail it - unlike
    # the instrument beside it, which is what uploads get filed under and is
    # worth refusing when it is wrong.
    long_version = "v2026.09.01-9b9e54d-394-g7e674438e"
    assert len(long_version) > 32

    started = (
        await public_client.post(
            "/api/auth/pairing/start",
            json={
                "service_name": "file-agent",
                "machine_name": "LONG-PC",
                "agent_version": long_version,
            },
        )
    ).json()
    approve = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert approve.status_code == 200, approve.text

    devices = (await editor_client.get("/api/auth/devices")).json()["data"]
    row = next(d for d in devices if d["name"] == "LONG-PC")
    assert row["last_seen_version"] == long_version[:32]


@pytest.mark.asyncio
async def test_pairing_refuses_an_over_long_instrument(fake_redis, public_client):
    # Length is part of the name rule, so an over-long one is refused the
    # same way a badly spelled one is - not silently truncated into a name
    # that would file uploads somewhere nobody asked for.
    resp = await public_client.post(
        "/api/auth/pairing/start",
        json={"service_name": "file-agent", "instrument": "A" * 65},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_a_reported_value_cannot_forge_a_field_in_the_list(
    fake_redis, public_client, editor_client
):
    # Paired machines joins these fields with a middle dot, so a value that
    # carries one would render as several - a machine could describe itself
    # as watching an instrument it does not, in the list a sponsor reads to
    # decide what to revoke.
    started = (
        await public_client.post(
            "/api/auth/pairing/start",
            json={
                "service_name": "file-agent",
                "machine_name": "FORGE-PC",
                "agent_version": "1.0 \u00b7 watching Orbi-Lab2",
            },
        )
    ).json()
    approve = await editor_client.post(
        "/api/auth/pairing/approve", json={"user_code": started["user_code"]}
    )
    assert approve.status_code == 200, approve.text

    devices = (await editor_client.get("/api/auth/devices")).json()["data"]
    row = next(d for d in devices if d["name"] == "FORGE-PC")
    assert "\u00b7" not in row["last_seen_version"]
