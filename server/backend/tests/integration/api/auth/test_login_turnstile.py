"""Tests: Turnstile on the sign-in form (``api/new/auth/turnstile.py``).

The real login route, with Cloudflare's siteverify replaced by a stub that
records what it was sent and answers as told. No test reaches Cloudflare.

The site turns Turnstile on with ``MASCOPE_TURNSTILE_SITE_KEY`` and keeps the
secret in a file named by ``TURNSTILE_SECRET_KEY_FILE``; both are set per test
with monkeypatch, so the default (off) is what every other test sees.
"""

import httpx
import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from mascope_backend.api.new.auth import turnstile
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, User


EMAIL = "login-turnstile@test.com"
PASSWORD = "correct-horse-battery-staple"
SITE_KEY = "0x4AAAAAAAsitekeyfortests"
SECRET = "0x4AAAAAAAsecretfortests"
TOKEN = "a-solved-widget-token"
SESSION_COOKIE = auth_settings.COOKIE_NAME


@pytest_asyncio.fixture
async def login_user(async_session_factory, roles):
    async with async_session_factory() as session:
        user = User(
            email=EMAIL,
            username="login_turnstile_user",
            hashed_password=PasswordHelper().hash(PASSWORD),
            is_active=True,
            is_verified=True,
            role_id=roles["guest"].role_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    yield user
    async with async_session_factory() as session:
        await session.execute(delete(AccessToken).where(AccessToken.user_id == user.id))
        db_user = await session.get(User, user.id)
        if db_user is not None:
            await session.delete(db_user)
        await session.commit()


class _Siteverify:
    """Cloudflare's siteverify, answering what the test sets in ``outcome``.

    ``fail`` makes the call raise as an unreachable host would. ``calls``
    holds the form each call posted.
    """

    def __init__(self):
        self.outcome: dict = {"success": True, "action": turnstile.TURNSTILE_ACTION}
        self.status = 200
        self.fail = False
        self.calls: list[tuple[str, dict]] = []

    def client(self, *args, **kwargs):
        stub = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, data=None, **_):
                stub.calls.append((url, dict(data or {})))
                request = httpx.Request("POST", url)
                if stub.fail:
                    raise httpx.ConnectError("unreachable", request=request)
                return httpx.Response(stub.status, json=stub.outcome, request=request)

        return _Client()


@pytest.fixture
def siteverify(monkeypatch):
    stub = _Siteverify()
    monkeypatch.setattr(turnstile.httpx, "AsyncClient", stub.client)
    return stub


@pytest.fixture
def turnstile_on(monkeypatch, tmp_path):
    """Turnstile on, with its secret in a file of the test's own."""
    secret_file = tmp_path / "turnstile_secret_key.txt"
    secret_file.write_text(SECRET + "\n", encoding="utf-8")
    monkeypatch.setenv("MASCOPE_TURNSTILE_SITE_KEY", SITE_KEY)
    monkeypatch.setenv("TURNSTILE_SECRET_KEY_FILE", str(secret_file))
    return secret_file


@pytest.fixture
def turnstile_off(monkeypatch):
    monkeypatch.delenv("MASCOPE_TURNSTILE_SITE_KEY", raising=False)


async def _login(form: dict, headers: dict | None = None) -> httpx.Response:
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        return await client.post("/api/auth/login", data=form, headers=headers)


def _form(token: str | None = TOKEN, password: str = PASSWORD) -> dict:
    form = {"username": EMAIL, "password": password}
    if token is not None:
        form[turnstile.TOKEN_FIELD] = token
    return form


def _signed_in(resp: httpx.Response) -> bool:
    return resp.status_code in (200, 204) and SESSION_COOKIE in resp.cookies


def _code(resp: httpx.Response) -> str | None:
    return (resp.json().get("detail") or {}).get("code")


# --- The login options the sign-in form reads -------------------------------


@pytest.mark.asyncio
async def test_login_options_say_off_by_default(turnstile_off):
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.get("/api/auth/login/options")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"turnstile_site_key": None}


@pytest.mark.asyncio
async def test_login_options_give_the_site_key_without_a_session(turnstile_on):
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.get("/api/auth/login/options")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"turnstile_site_key": SITE_KEY}


@pytest.mark.asyncio
async def test_a_blank_site_key_is_off(monkeypatch, login_user, siteverify):
    """Compose passes the variable through empty on a site without Turnstile."""
    monkeypatch.setenv("MASCOPE_TURNSTILE_SITE_KEY", "  ")
    resp = await _login(_form(token=None))
    assert _signed_in(resp)
    assert siteverify.calls == []


# --- Off: the sign-in is as before ------------------------------------------


@pytest.mark.asyncio
async def test_off_signs_in_with_no_token_and_no_call(
    turnstile_off, login_user, siteverify
):
    resp = await _login(_form(token=None))
    assert _signed_in(resp)
    assert siteverify.calls == []


# --- On ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_signs_in_with_an_accepted_token(turnstile_on, login_user, siteverify):
    resp = await _login(_form(), headers={"X-Real-IP": "198.51.100.7"})
    assert _signed_in(resp)
    assert siteverify.calls == [
        (
            turnstile.SITEVERIFY_URL,
            {"secret": SECRET, "response": TOKEN, "remoteip": "198.51.100.7"},
        )
    ]


@pytest.mark.asyncio
async def test_on_refuses_a_sign_in_with_no_token(turnstile_on, login_user, siteverify):
    """The right password and no token: refused, and Cloudflare is not asked."""
    resp = await _login(_form(token=None))
    assert resp.status_code == 400
    assert _code(resp) == turnstile.LOGIN_CHALLENGE_FAILED
    assert "human" in resp.json()["error"]
    assert SESSION_COOKIE not in resp.cookies
    assert siteverify.calls == []


@pytest.mark.asyncio
async def test_on_refuses_an_oversized_token_unsent(
    turnstile_on, login_user, siteverify
):
    resp = await _login(_form(token="x" * (turnstile.MAX_TOKEN_LENGTH + 1)))
    assert resp.status_code == 400
    assert siteverify.calls == []


@pytest.mark.asyncio
async def test_on_refuses_a_token_cloudflare_rejects(
    turnstile_on, login_user, siteverify
):
    siteverify.outcome = {
        "success": False,
        "action": turnstile.TURNSTILE_ACTION,
        "error-codes": ["timeout-or-duplicate"],
    }
    resp = await _login(_form())
    assert resp.status_code == 400
    assert _code(resp) == turnstile.LOGIN_CHALLENGE_FAILED
    assert SESSION_COOKIE not in resp.cookies


@pytest.mark.asyncio
async def test_on_refuses_a_token_solved_for_another_action(
    turnstile_on, login_user, siteverify
):
    siteverify.outcome = {"success": True, "action": "contact-form"}
    resp = await _login(_form())
    assert resp.status_code == 400
    assert SESSION_COOKIE not in resp.cookies


@pytest.mark.asyncio
async def test_on_accepts_a_token_with_no_action(turnstile_on, login_user, siteverify):
    """Cloudflare's test keys answer without an action, as staging uses them."""
    siteverify.outcome = {"success": True}
    resp = await _login(_form())
    assert _signed_in(resp)


@pytest.mark.asyncio
async def test_on_checks_the_token_before_the_password(
    monkeypatch, turnstile_on, login_user, siteverify
):
    """A refused token never reaches the password check, so a wrong password
    and a right one are refused the same way."""
    siteverify.outcome = {"success": False}
    checked = []

    from mascope_backend.api.new.users.user_manager.service import UserManager

    original = UserManager.authenticate

    async def spy(self, credentials):
        checked.append(credentials.username)
        return await original(self, credentials)

    monkeypatch.setattr(UserManager, "authenticate", spy)
    right = await _login(_form())
    wrong = await _login(_form(password="wrong-password"))
    assert right.status_code == wrong.status_code == 400
    assert checked == []


@pytest.mark.asyncio
async def test_on_with_a_wrong_password_and_a_good_token_is_bad_credentials(
    turnstile_on, login_user, siteverify
):
    resp = await _login(_form(password="wrong-password"))
    assert resp.status_code == 400
    assert _code(resp) is None
    assert "Invalid login credentials" in resp.json()["error"]
    assert len(siteverify.calls) == 1


@pytest.mark.asyncio
async def test_on_without_a_secret_refuses_every_sign_in(
    turnstile_on, login_user, siteverify
):
    """The CLI's empty placeholder file: fail closed, never sign in unchecked."""
    turnstile_on.write_text("", encoding="utf-8")
    resp = await _login(_form())
    assert resp.status_code == 503
    assert _code(resp) == turnstile.LOGIN_CHALLENGE_UNAVAILABLE
    assert siteverify.calls == []


@pytest.mark.asyncio
async def test_on_with_a_blank_secret_refuses_every_sign_in(
    turnstile_on, login_user, siteverify
):
    """A file holding only whitespace is no secret either."""
    turnstile_on.write_text("   \n", encoding="utf-8")
    resp = await _login(_form())
    assert resp.status_code == 503
    assert siteverify.calls == []


@pytest.mark.asyncio
async def test_on_refuses_when_siteverify_is_unreachable(
    turnstile_on, login_user, siteverify
):
    siteverify.fail = True
    resp = await _login(_form())
    assert resp.status_code == 503
    assert _code(resp) == turnstile.LOGIN_CHALLENGE_UNAVAILABLE
    assert SESSION_COOKIE not in resp.cookies


@pytest.mark.asyncio
async def test_on_refuses_when_siteverify_answers_an_error(
    turnstile_on, login_user, siteverify
):
    siteverify.status = 500
    resp = await _login(_form())
    assert resp.status_code == 503
    assert SESSION_COOKIE not in resp.cookies
