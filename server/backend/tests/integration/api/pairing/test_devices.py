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

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.new.auth.access_token import validation
from mascope_backend.api.new.auth.devices import service as devices_service
from mascope_backend.api.new.auth.devices.service import record_reported_instrument
from mascope_backend.api.new.auth.pairing import service as pairing_service
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, AgentDevice, User


@pytest.fixture(autouse=True)
def forget_reported_instruments():
    """Start each test with the per-worker instrument memo empty.

    The memo skips a write when a device already reports the name the row
    holds. Left populated it would make one test's writes disappear from the
    next, so what a test observes would depend on the order they ran in.
    """
    devices_service._reported_instrument.clear()
    yield
    devices_service._reported_instrument.clear()


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
    """Remove file-agent tokens, machine accounts and devices between tests."""
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


async def _pair(public_client, approver_client, machine_name, **reported):
    """Run a full pairing and return (token, device_id).

    ``reported`` is what the agent says about itself in the start request.
    """
    started = (
        await public_client.post(
            "/api/auth/pairing/start",
            json={
                "service_name": "file-agent",
                "machine_name": machine_name,
                **reported,
            },
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
async def test_list_shows_what_the_machine_reported(
    fake_redis, public_client, editor_client
):
    await _pair(
        public_client,
        editor_client,
        "LAB-PC",
        instrument="Orbi-Lab2",
        agent_version="v2.0.0",
    )

    devices = (await editor_client.get("/api/auth/devices")).json()["data"]
    row = next(d for d in devices if d["name"] == "LAB-PC")
    assert row["instrument"] == "Orbi-Lab2"
    assert row["last_seen_version"] == "v2.0.0"


@pytest.mark.asyncio
async def test_a_request_records_the_agent_version(
    fake_redis, public_client, editor_client
):
    # The version rides on the same write as last_seen_at, so the first
    # authenticated request of a fresh device records both.
    token, device_id = await _pair(public_client, editor_client, "VER-PC")

    resp = await public_client.get(
        "/api/sample/files",
        params={"page": 1, "limit": 1},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Service-Name": "file-agent",
            "X-Agent-Version": "v2.0.0",
        },
    )
    assert resp.status_code == 200, resp.text

    devices = (await editor_client.get("/api/auth/devices")).json()["data"]
    row = next(d for d in devices if d["device_id"] == device_id)
    assert row["last_seen_version"] == "v2.0.0"
    assert row["last_seen_at"] is not None


@pytest.mark.asyncio
async def test_an_upload_keeps_the_instrument_the_agent_reports(
    fake_redis, public_client, editor_client, async_session_factory
):
    _, device_id = await _pair(public_client, editor_client, "FILL-PC")

    # A name the server could not file under is dropped, not stored.
    await record_reported_instrument(device_id, "orbi lab")
    async with async_session_factory() as session:
        assert (await session.get(AgentDevice, device_id)).instrument is None

    # The first usable name fills the empty row...
    await record_reported_instrument(device_id, "Orbi-Lab2")
    async with async_session_factory() as session:
        assert (await session.get(AgentDevice, device_id)).instrument == "Orbi-Lab2"

    # ...and a later, different one moves it. The agent is the authority for
    # what it watches, so a machine repointed at another instrument - its
    # config.toml edited and the agent restarted - says so on its next upload
    # instead of showing the name it first reported forever.
    await record_reported_instrument(device_id, "Orbi-Other")
    async with async_session_factory() as session:
        assert (await session.get(AgentDevice, device_id)).instrument == "Orbi-Other"

    # The same for a device that reported one at pairing.
    _, paired_id = await _pair(
        public_client, editor_client, "SET-PC", instrument="Orbi-A"
    )
    await record_reported_instrument(paired_id, "Orbi-B")
    async with async_session_factory() as session:
        assert (await session.get(AgentDevice, paired_id)).instrument == "Orbi-B"

    # No device behind the upload: nothing to record, nothing raised.
    await record_reported_instrument(None, "Orbi-B")


@pytest.mark.asyncio
async def test_an_unchanged_instrument_costs_no_write(
    fake_redis, public_client, editor_client, monkeypatch
):
    # An upload reports the same name every time. Without a guard the steady
    # state would be a session, a connection and a commit per uploaded file to
    # change nothing - on the upload path, which takes no admission-control
    # permit, and which the last-seen write next door is throttled to protect.
    _, device_id = await _pair(public_client, editor_client, "SAME-PC")
    await record_reported_instrument(device_id, "Orbi-Lab2")

    sessions = []
    real_session = devices_service.async_session

    def counting_session(*args, **kwargs):
        sessions.append(1)
        return real_session(*args, **kwargs)

    monkeypatch.setattr(devices_service, "async_session", counting_session)

    await record_reported_instrument(device_id, "Orbi-Lab2")
    await record_reported_instrument(device_id, "Orbi-Lab2")
    assert sessions == []

    # A change still writes.
    await record_reported_instrument(device_id, "Orbi-Other")
    assert len(sessions) == 1


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

    # The revoke response describes the device the same way the list routes
    # do. It was built by hand and reported no sponsor at all, so a client
    # updating its row from this payload blanked the sponsor out.
    revoked_payload = resp.json()["data"]
    listed = await editor_client.get("/api/auth/devices")
    listed_a = next(d for d in listed.json()["data"] if d["device_id"] == device_a)
    assert revoked_payload["sponsor_username"] == listed_a["sponsor_username"]
    assert revoked_payload["sponsor_username"] is not None
    assert revoked_payload["token_count"] == 0
    assert revoked_payload["revoked_at"] is not None

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


@pytest.mark.asyncio
async def test_a_request_without_a_version_clears_the_stored_one(
    fake_redis, public_client, editor_client
):
    # A site rolled back to an agent that reports no version must stop
    # showing the newer release: the column says what the machine last
    # reported, not the highest it ever reported, because following an
    # upgrade across instrument PCs is what it is read for.
    token, device_id = await _pair(
        public_client, editor_client, "ROLLBACK-PC", agent_version="v2.0.0"
    )
    headers = {"Authorization": f"Bearer {token}", "X-Service-Name": "file-agent"}

    validation._last_seen_written_at.clear()
    resp = await public_client.get(
        "/api/sample/files", params={"page": 1, "limit": 1}, headers=headers
    )
    assert resp.status_code == 200, resp.text

    devices = (await editor_client.get("/api/auth/devices")).json()["data"]
    row = next(d for d in devices if d["device_id"] == device_id)
    assert row["last_seen_version"] is None
    assert row["last_seen_at"] is not None
