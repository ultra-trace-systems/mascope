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
from mascope_backend.api.new.auth.access_token import validation
from mascope_backend.api.new.auth.access_token.service import (
    create_access_token,
    get_access_token,
)
from mascope_backend.api.new.auth.config import auth_settings
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


@pytest.mark.asyncio
async def test_renewal_issues_a_fresh_token(
    async_session_factory, test_users, provision_device
):
    device_id, _machine, token = await provision_device(test_users["editor"].id)

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
async def test_unbound_machine_token_cannot_renew(
    async_session_factory, test_users, provision_device
):
    """Renewal follows the presented token's device binding, not its subject.

    A machine account also holds a file-converter token, which is unbound and
    therefore exempt from both the strict-mode gate and the 30-day device
    lifetime. Were the subject enough, that long-lived credential could mint
    device tokens forever and the short lifetime would bound nothing.
    """
    device_id, machine, _token = await provision_device(test_users["editor"].id)

    converter_token = await create_access_token(
        user=machine, service_name="file-converter"
    )

    async with _bearer_client(converter_token, service="file-converter") as client:
        resp = await client.post("/api/auth/devices/token")

    assert resp.status_code == 401, resp.text

    # Refused, and nothing was minted: the device still holds only the token
    # pairing gave it. (The route wrapper replaces the detail with a generic
    # message, so the count is what pins the behaviour, not the text.)
    async with async_session_factory() as session:
        count = (
            await session.execute(
                select(func.count(AccessToken.token)).where(
                    AccessToken.device_id == device_id
                )
            )
        ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_renewal_reaps_to_two_tokens(
    async_session_factory, test_users, provision_device
):
    device_id, _machine, token = await provision_device(test_users["editor"].id)

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
    async_session_factory, test_users, provision_device
):
    _device_id, _machine, token = await provision_device(test_users["editor"].id)

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
    # The regenerate route returns the token transport body directly.
    personal = regen.json()["access_token"]

    async with _bearer_client(personal, service="mascope_sdk") as client:
        resp = await client.post("/api/auth/devices/token")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_machine_account_converter_token_minted_on_demand(
    async_session_factory, test_users, provision_device
):
    _device_id, machine, _token = await provision_device(test_users["editor"].id)
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


# ---------------------------------------------------------------------------
# Strict mode (require_device_tokens) over HTTP
#
# The unit tests call ensure_device_bound directly, which proves the decision
# and nothing about where it is made. The flag is only worth anything if the
# bearer path consults it on every request and the refusal survives all the way
# into the response body - a 401 genericized to "please sign in" is useless to
# an unattended agent, which has no session to sign into. Both are HTTP-level
# properties, so they are pinned here.
# ---------------------------------------------------------------------------


@pytest.fixture
def strict_mode(monkeypatch):
    """Turn require_device_tokens on for one test without touching real config.

    ``runtime.config`` is a property over the one shared ``BackendConfig``
    instance (``runtime._full_config.backend``), and every module reads the
    flag through it at call time - so setting the attribute here is what the
    bearer path in ``api/new/auth/backend.py`` sees on the next request, not
    just what ``validation`` sees.
    """

    def _set(enabled: bool) -> None:
        monkeypatch.setattr(
            validation.runtime.config, "require_device_tokens", enabled, raising=False
        )

    return _set


@pytest.mark.asyncio
async def test_strict_mode_refuses_an_unbound_agent_token_over_http(
    test_users, strict_mode
):
    """An agent token with no device behind it is refused once the flag is on.

    The same token is tried with the flag off first, so a 401 here can only be
    strict mode talking and not a token the suite failed to mint. The body is
    asserted too: the refusal opts out of the 401 genericization by carrying
    ``ClientFacingDetail``, and if that marker is ever dropped the wording
    survives only in the log, where no agent operator will read it.
    """
    # A token as it was issued before the device registry existed: real
    # service, real user, no device binding.
    unbound = await create_access_token(
        user=test_users["editor"], service_name="file-agent"
    )

    strict_mode(False)
    async with _bearer_client(unbound) as client:
        permissive = await client.get(
            "/api/sample/files", params={"page": 0, "limit": 1}
        )
    assert permissive.status_code != 401, permissive.text

    strict_mode(True)
    async with _bearer_client(unbound) as client:
        strict = await client.get("/api/sample/files", params={"page": 0, "limit": 1})
    assert strict.status_code == 401, strict.text
    assert "paired agent credentials" in strict.text


@pytest.mark.asyncio
async def test_strict_mode_lets_paired_and_personal_tokens_through(
    test_users, strict_mode, editor_client, provision_device
):
    """Strict mode must refuse only unbound *agent* tokens.

    The opposite failure is just as silent and worse: a gate that also caught
    device-bound agents would lock out every paired instrument, and one that
    caught personal tokens would lock out the SDK. Neither has a device
    concept the flag was written about.
    """
    _device_id, _machine, bound_token = await provision_device(
        test_users["editor"].id, machine_name="ORBI-STRICT"
    )
    regen = await editor_client.post(
        "/api/auth/access_token/regenerate", json={"service_name": "mascope_sdk"}
    )
    assert regen.status_code == 200, regen.text
    personal = regen.json()["access_token"]

    strict_mode(True)

    async with _bearer_client(bound_token) as client:
        bound = await client.get("/api/sample/files", params={"page": 0, "limit": 1})
    # == 200, not != 401: a machine account with no acquisition-workspace
    # membership still gets a 200 with an empty list, so the sharper assertion
    # is free here and a 403 would not be mistaken for "the gate let it past".
    assert bound.status_code == 200, bound.text

    async with _bearer_client(personal, service="mascope_sdk") as client:
        sdk = await client.get("/api/sample/files", params={"page": 0, "limit": 1})
    assert sdk.status_code == 200, sdk.text
