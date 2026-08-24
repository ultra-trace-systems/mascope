"""Tests: the real login route end-to-end (``POST /api/auth/login``).

The other integration tests mint JWTs directly, so nothing else exercises the
fastapi-users login flow - including the ``on_after_login`` hook, which
re-reads the cached login form to clear the per-account rate-limit counter on
a successful sign-in and refreshes the file-converter access token for
editor+ accounts. These tests pin that a real password login works with the
limiter dependencies and the clearing hook wired in (both fail open when
Redis is not connected, as in this ASGI test setup), and that the converter
token is minted for every editor+ sign-in - API logins without a socket sid
included, since uploads fail without it.

The second-factor tests below stub the shared Redis client instead: the
pending token's single use and its miss budget are Redis-backed, so under the
fail-open default they would silently not exist.
"""

from dataclasses import dataclass
from http.cookies import SimpleCookie

import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.mfa import crypto, service
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, User, UserRecoveryCode
from mascope_backend.socket.storage import redis_storage_client


LOGIN_EMAIL = "login-flow@test.com"
LOGIN_PASSWORD = "correct-horse-battery-staple"


def _login_user_fixture(role_name: str, email: str, username: str):
    """Build a login-user fixture for the given role.

    The user gets a real (verifiable) password hash and is removed - along
    with any access tokens the login hook minted - afterwards.
    """

    @pytest_asyncio.fixture
    async def _fixture(async_session_factory, roles):
        async with async_session_factory() as session:
            user = User(
                email=email,
                username=username,
                hashed_password=PasswordHelper().hash(LOGIN_PASSWORD),
                is_active=True,
                is_verified=True,
                role_id=roles[role_name].role_id,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        yield user
        async with async_session_factory() as session:
            await session.execute(
                delete(AccessToken).where(AccessToken.user_id == user.id)
            )
            db_user = await session.get(User, user.id)
            if db_user is not None:
                await session.delete(db_user)
            await session.commit()

    return _fixture


login_user = _login_user_fixture("guest", LOGIN_EMAIL, "login_flow_user")
editor_login_user = _login_user_fixture(
    "editor", "login-flow-editor@test.com", "login_flow_editor"
)


@pytest.mark.asyncio
async def test_wrong_password_is_rejected(login_user):
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": LOGIN_EMAIL, "password": "wrong-password"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_successful_login_sets_auth_cookie(login_user):
    """A correct password logs in; on_after_login must not break the flow."""
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        )
    assert resp.status_code in (200, 204)
    assert "set-cookie" in resp.headers


async def _converter_tokens(async_session_factory, user_id) -> list[AccessToken]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(AccessToken)
            .where(AccessToken.user_id == user_id)
            .where(AccessToken.service_name == "file-converter")
        )
        return list(result.scalars())


@pytest.mark.asyncio
async def test_api_login_mints_file_converter_token(
    editor_login_user, async_session_factory
):
    """An editor login without a socket sid still gets the converter token.

    Uploads resolve the uploading user's file-converter token server-side
    and 401 without one, so the login hook must mint it for every editor+
    sign-in - including API logins (SDK, scripts, agent pairing), which
    carry no ``x-sid`` header and skip the socket-authentication step.
    """
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": editor_login_user.email, "password": LOGIN_PASSWORD},
        )
    assert resp.status_code in (200, 204)
    tokens = await _converter_tokens(async_session_factory, editor_login_user.id)
    assert len(tokens) == 1


@pytest.mark.asyncio
async def test_guest_login_mints_no_converter_token(login_user, async_session_factory):
    """Guests cannot upload, so their login must not mint a converter token."""
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/auth/login",
            data={"username": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        )
    assert resp.status_code in (200, 204)
    assert await _converter_tokens(async_session_factory, login_user.id) == []


# --- An account that holds a second factor ---------------------------------
#
# The password step must hand such an account no session at all, and the code
# step must be spendable exactly once. Three seams have to be arranged before
# any of that can be observed, and each of them is a way these tests could pass
# while the behaviour was gone:
#
# - The TOTP counter is frozen and advanced by hand. Enrolment spends one
#   (``confirm_enrollment`` writes ``mfa_last_timestep``), so the sign-in code
#   has to come from a later counter or it is refused as a replay; and a
#   deliberately replayed code has to come from a counter the server would
#   otherwise accept, or its refusal proves only that the drift window closed.
#   Under the wall clock the whole sequence normally lands inside one
#   30-second step, which gives neither.
# - Redis is stubbed. The app lifespan never runs under ``ASGITransport``, so
#   the shared client is unconnected and every call in ``mfa/pending.py``
#   raises, is swallowed, and does nothing - the burn and the miss budget would
#   silently not exist. Same reasoning as the limiter stub in
#   ``integration/api/users/conftest.py``.
# - The seed-encryption key is patched onto ``crypto``, as the unit tests do,
#   rather than read from the deployment's secrets directory.

PENDING_COOKIE = auth_settings.MFA_PENDING_COOKIE_NAME
SESSION_COOKIE = auth_settings.COOKIE_NAME
#: The miss budget, written out rather than read from
#: ``auth_settings.mfa.PENDING_TOKEN_MAX_ATTEMPTS``. Reading it back would move
#: the loop below in lockstep with any edit to that setting, and the test would
#: stay green with the budget at 4 or at 60 - the number is what is being
#: pinned, not the fact that some number exists.
MAX_ATTEMPTS = 5

mfa_login_user = _login_user_fixture(
    "guest", "login-flow-mfa@test.com", "login_flow_mfa"
)


class _Clock:
    """The TOTP counter, under the test's control rather than the machine's."""

    def __init__(self, step: int):
        self.step = step

    def __call__(self) -> int:
        return self.step


class _InMemoryRedis:
    """The Redis commands this flow uses, over a plain dict.

    The stub from ``integration/api/users/conftest.py``, plus ``set`` and
    ``exists`` for the pending token's burn key. Nothing expires: each test
    gets a fresh instance, and what is under test is the counting and the burn,
    not the TTLs.
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


@dataclass
class _Enrollment:
    """A confirmed second factor and the material needed to present it."""

    user: User
    secret: str
    recovery_codes: list[str]


def _code_at(secret: str, timestep: int) -> str:
    """The code a correctly-set authenticator would show at ``timestep``."""
    return service._totp(secret).generate_otp(timestep)


def _wrong_codes(secret: str, count: int, timestep: int) -> list[str]:
    """Codes that cannot verify at ``timestep``, by exclusion rather than luck.

    A six-digit string picked arbitrarily matches a code inside the drift
    window about once in three hundred thousand tries, which across a suite
    that runs on every push is a flake waiting to happen.
    """
    valid = {_code_at(secret, timestep + offset) for offset in range(-2, 3)}
    codes: list[str] = []
    candidate = 0
    while len(codes) < count:
        code = f"{candidate:06d}"
        if code not in valid:
            codes.append(code)
        candidate += 1
    return codes


def _set_cookies(response) -> SimpleCookie:
    """Every cookie a response sets, parsed off the raw headers.

    Read from the headers rather than from httpx's jar so the assertions see
    what the server actually sent, attributes included.
    """
    jar = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        jar.load(header)
    return jar


def _client(**cookies: str) -> AsyncClient:
    """A client carrying exactly the cookies named and nothing it collected."""
    return AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        cookies=cookies or None,
    )


async def _password_step(email: str):
    """Submit the password half of the sign-in."""
    async with _client() as client:
        return await client.post(
            "/api/auth/login",
            data={"username": email, "password": LOGIN_PASSWORD},
        )


async def _pending_token(email: str) -> str:
    """The pending token the password step issued, off the Set-Cookie header."""
    return _set_cookies(await _password_step(email))[PENDING_COOKIE].value


async def _verify(pending_token: str, code: str):
    """Submit a code against one pending token, and nothing else."""
    async with _client(**{PENDING_COOKIE: pending_token}) as client:
        return await client.post("/api/auth/mfa/verify", json={"code": code})


@pytest.fixture
def mfa_encryption_key(monkeypatch):
    """A deployment key, without touching the real secrets directory.

    Patched on ``crypto`` rather than on ``mfa.secrets``, whose module-level
    ``_cached_key`` memoizes the first successful read and would leak a key
    into every later test in the session. Same seam as
    ``tests/unit/api/auth/test_mfa_service.py``.
    """
    monkeypatch.setattr(
        crypto, "mfa_encryption_key", lambda: "integration-test-mfa-key"
    )


@pytest.fixture
def totp_clock(monkeypatch) -> _Clock:
    """The TOTP counter, frozen at the present step and advanced by hand."""
    clock = _Clock(service.current_timestep())
    monkeypatch.setattr(service, "current_timestep", clock)
    return clock


@pytest.fixture
def stub_redis(monkeypatch) -> _InMemoryRedis:
    """A store that actually counts, so the burn and the miss budget exist."""
    stub = _InMemoryRedis()
    monkeypatch.setattr(redis_storage_client, "_client", stub)
    return stub


@pytest_asyncio.fixture
async def enrolled_user(
    mfa_login_user, mfa_encryption_key, totp_clock, stub_redis, async_session_factory
):
    """A throwaway account holding a confirmed second factor."""
    secret, _uri = await service.begin_enrollment(mfa_login_user)
    # begin_enrollment writes through a session of its own, so the fixture's
    # detached user still carries mfa_secret = None; confirm_enrollment reads
    # that column off the object it is handed and would raise
    # MfaNotEnrolledException. Re-read the row, as the real second request does.
    fresh = await service.load_user(mfa_login_user.id)
    codes = await service.confirm_enrollment(fresh, _code_at(secret, totp_clock.step))
    assert codes is not None, "enrolment refused its own code"

    yield _Enrollment(user=mfa_login_user, secret=secret, recovery_codes=codes)

    # Runs before the user fixture's teardown, which this one nests inside.
    async with async_session_factory() as session:
        await session.execute(
            delete(UserRecoveryCode).where(
                UserRecoveryCode.user_id == mfa_login_user.id
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_enrolled_login_mints_no_session(enrolled_user, stub_redis):
    """The password step hands an enrolled account a prompt, not a session.

    This is why login is ours rather than ``fastapi_users.get_auth_router``:
    every surface that trusts ``mascope_auth`` - the role dependencies,
    Socket.IO - would otherwise open to someone who has only guessed a
    password.
    """
    resp = await _password_step(enrolled_user.user.email)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"mfa_required": True}

    cookies = _set_cookies(resp)
    assert SESSION_COOKIE not in cookies, "the password step alone minted a session"

    pending = cookies[PENDING_COOKIE]
    assert pending.value, "no pending token was issued"
    # Out of reach of JavaScript, like the session cookie it stands in for: a
    # readable one is a half-finished sign-in that an XSS can finish.
    assert pending["httponly"]
    # Five minutes, written out for the same reason as MAX_ATTEMPTS: comparing
    # it to the setting the cookie is built from would pass whatever that
    # setting said, including the session's seven days.
    assert int(pending["max-age"]) == 5 * 60

    # And the token buys nothing. The profile read is the most permissive
    # authenticated route there is - exempt from both interactive gates - so a
    # 401 here is the strongest available statement that no session exists.
    async with _client(**{PENDING_COOKIE: pending.value}) as client:
        me = await client.get("/api/users/me")
    assert me.status_code == 401, me.text


@pytest.mark.asyncio
async def test_verify_spends_the_pending_token(enrolled_user, totp_clock, stub_redis):
    """One pending token completes one sign-in and never a second.

    The drift window keeps a code usable for about ninety seconds, so a
    captured pending cookie replayed against the next code would otherwise turn
    one password entry into two sessions.
    """
    pending_token = await _pending_token(enrolled_user.user.email)

    # Enrolment already spent the current counter, so the sign-in code comes
    # from the next one.
    totp_clock.step += 1
    resp = await _verify(pending_token, _code_at(enrolled_user.secret, totp_clock.step))
    assert resp.status_code in (200, 204), resp.text
    issued = _set_cookies(resp)
    assert issued[SESSION_COOKIE].value, "the code step minted no session"
    # The half-finished sign-in is over, so its cookie is taken back.
    assert issued[PENDING_COOKIE].value == ""

    # The same cookie against a code the server would otherwise accept: refused
    # because the token is spent, not because the code is stale. 401 rather
    # than the 400 a wrong code gets - the attempt itself is gone.
    totp_clock.step += 1
    replay = await _verify(
        pending_token, _code_at(enrolled_user.secret, totp_clock.step)
    )
    assert replay.status_code == 401, replay.text
    assert SESSION_COOKIE not in _set_cookies(replay)

    # And against a recovery code, which carries no counter at all - so this
    # one cannot be refused for any reason except that the token was burned.
    recovery = await _verify(pending_token, enrolled_user.recovery_codes[0])
    assert recovery.status_code == 401, recovery.text
    fresh = await service.load_user(enrolled_user.user.id)
    assert await service.unused_recovery_code_count(fresh) == len(
        enrolled_user.recovery_codes
    ), "a refused replay spent a recovery code"


@pytest.mark.asyncio
async def test_wrong_codes_burn_the_pending_token(
    enrolled_user, totp_clock, stub_redis
):
    """A password-verified attempt gets a small, fixed number of guesses.

    Without the budget one stolen password funds an unbounded search of the
    six-digit space against a single token; with it the attacker is pushed back
    through the password step, where the per-IP and per-account login limits
    apply.
    """
    assert auth_settings.mfa.PENDING_TOKEN_MAX_ATTEMPTS == MAX_ATTEMPTS, (
        "the deployed miss budget moved; decide whether the new number is "
        "intended before editing this test to match it"
    )

    pending_token = await _pending_token(enrolled_user.user.email)

    totp_clock.step += 1

    # Five misses, all reported as a wrong code against a still-live attempt:
    # a budget lowered to four turns the fifth into a 401.
    for code in _wrong_codes(enrolled_user.secret, MAX_ATTEMPTS, totp_clock.step):
        miss = await _verify(pending_token, code)
        assert miss.status_code == 400, miss.text

    # The budget is spent, so a code that would otherwise sign the account in
    # is refused - and refused as a dead attempt (401), not a bad code. A budget
    # raised to six would sign in here instead.
    correct = await _verify(
        pending_token, _code_at(enrolled_user.secret, totp_clock.step)
    )
    assert correct.status_code == 401, correct.text
    assert SESSION_COOKIE not in _set_cookies(correct)
