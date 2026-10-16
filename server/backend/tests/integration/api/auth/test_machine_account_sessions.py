"""Tests: a machine account can never hold an interactive session.

The two-factor policy exempts machine accounts (``policy.exempt``) on one
condition: they never sign in at a browser, so there is no session for a second
factor to protect and no screen to enrol at. That condition is load-bearing, so
it is pinned here rather than assumed.

A session is the ``auth_backend_jwt`` cookie, and it is minted in exactly two
places: the password step of ``POST /api/auth/login`` and the second-factor step
of ``POST /api/auth/mfa/verify``. The first test fails if a third appears, so a
new path cannot open without a test of its own here. The others drive each path
with a machine account that would succeed if it were a person - a password that
verifies, a factor and a pending token that verify - and check that the same
account, flipped to a person, does get a session. Without that control a refusal
proves nothing: the existing sign-in test uses a password the account does not
have, and stays green with the machine-account check removed.
"""

import re
from http.cookies import SimpleCookie
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

import mascope_backend
from mascope_backend.accounts import ACCOUNT_TYPE_PERSON
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.devices.machine_account import create_machine_account
from mascope_backend.api.new.auth.mfa import crypto, service
from mascope_backend.api.new.auth.mfa.pending import create_pending_token
from mascope_backend.app.fast import fast
from mascope_backend.db import (
    AccessToken,
    AgentDevice,
    User,
    UserRecoveryCode,
    async_session,
)
from mascope_backend.socket.storage import redis_storage_client


SESSION_COOKIE = auth_settings.COOKIE_NAME
PENDING_COOKIE = auth_settings.MFA_PENDING_COOKIE_NAME
PASSWORD = "a-password-that-verifies-123"

#: The modules allowed to mint a session, relative to the package root. Each is
#: driven with a machine account below.
SESSION_MINTING_MODULES = {
    "api/new/auth/login.py",
    "api/new/auth/mfa/routes.py",
}

#: Modules that call a ``write_token`` without minting a session: the API token
#: route writes through the database strategy, whose token is a bearer token
#: for scripts and agents, never the session cookie.
NON_SESSION_TOKEN_MODULES = {
    "api/new/auth/access_token/service.py",
}


class _InMemoryRedis:
    """The commands the pending token and the limiters use, over a dict.

    The app lifespan never runs under ``ASGITransport``, so the shared client is
    unconnected and the pending token's burn would silently not exist; same stub
    as ``test_login_flow.py``.
    """

    def __init__(self):
        self.values: dict[str, str | int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    async def decr(self, key: str) -> int:
        self.values[key] = int(self.values.get(key, 0)) - 1
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def ttl(self, key: str) -> int:
        return 60

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.values[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.values else 0

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.values.pop(key, None)
        return len(keys)


@pytest.fixture
def stub_redis(monkeypatch) -> _InMemoryRedis:
    stub = _InMemoryRedis()
    monkeypatch.setattr(redis_storage_client, "_client", stub)
    return stub


@pytest.fixture
def mfa_encryption_key(monkeypatch):
    """A seed key patched onto ``crypto``, as ``test_login_flow.py`` does."""
    monkeypatch.setattr(
        crypto, "mfa_encryption_key", lambda: "integration-test-mfa-key"
    )


@pytest.fixture
def frozen_step(monkeypatch) -> list[int]:
    """The TOTP counter, advanced by hand so no code is refused as a replay."""
    step = [service.current_timestep()]
    monkeypatch.setattr(service, "current_timestep", lambda: step[0])
    return step


@pytest_asyncio.fixture
async def machine_account(test_users, async_session_factory) -> User:
    """A real machine account, given a password that verifies.

    ``create_machine_account`` sets a random password nobody holds, so
    ``authenticate()`` alone would already refuse it. A known one takes that
    luck out: the refusal under test has to come from the account kind.
    """
    async with async_session() as session:
        device = AgentDevice(
            name="AGENT-PC",
            service_name="file-agent",
            sponsor_user_id=test_users["editor"].id,
        )
        session.add(device)
        await session.flush()
        machine = await create_machine_account(
            session,
            machine_name="AGENT-PC",
            device_id=device.device_id,
            sponsor_user_id=test_users["editor"].id,
        )
        machine.hashed_password = PasswordHelper().hash(PASSWORD)
        device.machine_user_id = machine.id
        await session.commit()
        await session.refresh(machine)

    yield machine

    async with async_session_factory() as session:
        await session.execute(
            delete(UserRecoveryCode).where(UserRecoveryCode.user_id == machine.id)
        )
        await session.execute(
            delete(AccessToken).where(AccessToken.user_id == machine.id)
        )
        await session.execute(
            delete(AgentDevice).where(AgentDevice.machine_user_id == machine.id)
        )
        await session.execute(delete(User).where(User.id == machine.id))
        await session.commit()


async def _set_kind(user_id: int, account_type: str) -> None:
    async with async_session() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(account_type=account_type)
        )
        await session.commit()


def _set_cookies(response) -> SimpleCookie:
    """Every cookie a response sets, read off the raw headers."""
    jar = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        jar.load(header)
    return jar


def _client(**cookies: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies=cookies or None,
    )


async def _login(email: str):
    async with _client() as client:
        return await client.post(
            "/api/auth/login", data={"username": email, "password": PASSWORD}
        )


async def _verify(pending_token: str, code: str):
    async with _client(**{PENDING_COOKIE: pending_token}) as client:
        return await client.post("/api/auth/mfa/verify", json={"code": code})


def _code_at(secret: str, step: int) -> str:
    return service._totp(secret).generate_otp(step)


def test_sessions_are_minted_only_where_this_file_drives_them():
    """A new place that mints the session cookie fails here until it is covered.

    Matches the cookie backend's ``login``, any call of a strategy's
    ``write_token`` (however the strategy was obtained, an injected
    ``Depends(get_jwt_strategy)`` included), and a response setting the session
    cookie by hand. A ``write_token`` that is not a session has to be listed in
    ``NON_SESSION_TOKEN_MODULES`` with the reason.
    """
    root = Path(mascope_backend.__file__).parent
    pattern = re.compile(
        r"auth_backend_jwt\.login\("
        r"|\.write_token\("
        r"|set_cookie\(\s*(?:key\s*=\s*)?auth_settings\.COOKIE_NAME\b"
    )
    minting = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    }
    assert minting == SESSION_MINTING_MODULES | NON_SESSION_TOKEN_MODULES


@pytest.mark.asyncio
async def test_the_password_step_opens_no_session_for_a_machine_account(
    machine_account, stub_redis
):
    resp = await _login(machine_account.email)
    assert resp.status_code == 400, resp.text
    assert SESSION_COOKIE not in _set_cookies(resp)
    assert PENDING_COOKIE not in _set_cookies(resp)

    # The control: the same account and password as a person signs in, so the
    # refusal above is the account kind and nothing else.
    await _set_kind(machine_account.id, ACCOUNT_TYPE_PERSON)
    resp = await _login(machine_account.email)
    assert resp.status_code in (200, 204), resp.text
    assert SESSION_COOKIE in _set_cookies(resp)


@pytest.mark.asyncio
async def test_the_second_factor_step_opens_no_session_for_a_machine_account(
    machine_account, stub_redis, mfa_encryption_key, frozen_step
):
    # A machine account holding a confirmed factor and a pending token - neither
    # reachable through the login route, which is why it is built by hand: this
    # route has to refuse on its own, not on the login route's behalf.
    secret, _uri = await service.begin_enrollment(machine_account)
    enrolled = await service.load_user(machine_account.id)
    assert await service.confirm_enrollment(enrolled, _code_at(secret, frozen_step[0]))

    frozen_step[0] += 1
    resp = await _verify(
        create_pending_token(machine_account.id), _code_at(secret, frozen_step[0])
    )
    assert resp.status_code >= 400, resp.text
    assert SESSION_COOKIE not in _set_cookies(resp)

    # The control: the same factor and a fresh pending token complete a sign-in
    # once the account is a person.
    await _set_kind(machine_account.id, ACCOUNT_TYPE_PERSON)
    frozen_step[0] += 1
    resp = await _verify(
        create_pending_token(machine_account.id), _code_at(secret, frozen_step[0])
    )
    assert resp.status_code in (200, 204), resp.text
    assert SESSION_COOKIE in _set_cookies(resp)
