"""Tests: scoping the auth cookie's name to the runtime env.

Cookies are not port-scoped, so two dev instances on one hostname used to share
one ``mascope_auth`` and sign each other out - a failure that looks like a
successful login followed by a 401. The env suffix is what keeps them apart, so
the risks are two envs collapsing onto one name (the bug returns, silently) and
the suffix leaking into prod (every user signed out on upgrade). Both are
asserted against the resolver directly: the names are computed once at import,
so going through ``auth_settings`` could only observe the env the suite happens
to run in.

The second half of the file guards the readers. Three of them named the cookie
literally and had to be changed for the scoping to work at all, and a literal
put back in any of them is invisible to the rest of the suite:
``get_enabled_backends`` falls through to the cookie backend when nothing
matches, so the integration login flow stays green either way. Each reader is
therefore driven with ``auth_settings.COOKIE_NAME`` pointed at a sentinel, which
passes only if the name is read at call time.
"""

import re

import pytest
from starlette.responses import Response

from mascope_backend.api.new.auth import backend as auth_backend
from mascope_backend.api.new.auth.config import (
    _resolve_cookie_name,
    _resolve_cookie_scoped,
    auth_settings,
)
from mascope_backend.api.new.auth.transports.cookie import session_token_from_response
from mascope_backend.socket.auth.token import get_jwt_from_cookies


AUTH = "mascope_auth"
PENDING = "mascope_mfa_pending"

# RFC 6265: a cookie name is an RFC 2616 token - no CTLs and no separators.
SEPARATORS = set('()<>@,;:\\"/[]?={} \t')

#: A name no reader could produce from a literal, so a test that finds it can
#: only have read the setting.
SENTINEL = "mascope_auth_sentinel_env"


@pytest.fixture(autouse=True)
def _no_scope_override(monkeypatch):
    """
    Keep the escape hatch out of the tests that are about the mode gate itself.
    Inherited from the shell it would decide the answer before the test does.
    """
    monkeypatch.delenv("MASCOPE_COOKIE_SCOPED", raising=False)


def dev(base, env):
    return _resolve_cookie_name(base, "dev", env)


def prod(base, env):
    return _resolve_cookie_name(base, "prod", env)


# --- Per-instance scoping ---


def test_two_dev_envs_get_different_cookie_names():
    assert dev(AUTH, "wt-feature-a") != dev(AUTH, "wt-feature-b")


def test_the_env_name_is_what_scopes_the_cookie():
    assert dev(AUTH, "wt-my-feature") == "mascope_auth_wt-my-feature"


def test_the_same_env_always_resolves_to_the_same_name():
    # The browser has to find the cookie again after a restart.
    assert dev(AUTH, "wt-my-feature") == dev(AUTH, "wt-my-feature")


def test_the_default_env_is_scoped_too():
    # Otherwise a plain `mascope dev run` collides with a local demo stack,
    # which runs in prod mode and keeps the bare name.
    assert dev(AUTH, "default") == "mascope_auth_default"


def test_both_cookies_are_scoped_and_stay_distinct():
    # get_enabled_backends selects the session backend on the presence of the
    # auth cookie, so the pending cookie must never resolve to the same name.
    assert dev(PENDING, "wt-x") == "mascope_mfa_pending_wt-x"
    assert dev(PENDING, "wt-x") != dev(AUTH, "wt-x")


# --- Prod is left alone ---


@pytest.mark.parametrize("env", ["prod", "demo", "anything"])
def test_prod_keeps_the_bare_names(env):
    # Renaming these in prod would sign every user out on upgrade.
    assert prod(AUTH, env) == AUTH
    assert prod(PENDING, env) == PENDING


# --- The mode gate can be overridden ---


def test_scoping_follows_the_mode_by_default(monkeypatch):
    monkeypatch.delenv("MASCOPE_COOKIE_SCOPED", raising=False)
    assert _resolve_cookie_scoped("dev") is True
    assert _resolve_cookie_scoped("prod") is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_the_override_can_force_scoping_on_in_prod_mode(monkeypatch, value):
    # `mascope prod ...` writes mode.override into the shared state.json and
    # never clears it, so a dev host can read "prod" and drop back to the
    # shared cookie name. This is the way out of that.
    monkeypatch.setenv("MASCOPE_COOKIE_SCOPED", value)
    assert _resolve_cookie_scoped("prod") is True
    assert _resolve_cookie_name(AUTH, "prod", "wt-x") == "mascope_auth_wt-x"


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_the_override_can_force_scoping_off_in_dev_mode(monkeypatch, value):
    monkeypatch.setenv("MASCOPE_COOKIE_SCOPED", value)
    assert _resolve_cookie_scoped("dev") is False
    assert _resolve_cookie_name(AUTH, "dev", "wt-x") == AUTH


# --- Token safety ---


@pytest.mark.parametrize(
    "env",
    ["wt-my feature", "feature/branch", "wt=x", "wt;x", "wt(x)", "ä-env", "", None],
)
def test_a_hostile_env_name_still_yields_a_token(env):
    name = dev(AUTH, env)
    assert not SEPARATORS & set(name)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", name)


@pytest.mark.parametrize("env", ["", None])
def test_a_missing_env_never_falls_back_to_the_prod_name(env):
    # The bare name is the one every unscoped stack answers to, including a
    # local demo stack - so falling back to it recreates the collision instead
    # of failing loudly. Any distinct name beats it.
    assert dev(AUTH, env) != AUTH
    assert dev(AUTH, env) == "mascope_auth_unknown"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("wt a", "wt_a"),  # a separator folded onto the underscore
        ("a/b", "a b"),  # two different separators, same fold
        ("a.b", "a_b"),  # a legal env name shadowed by an illegal one
        ("x!", "x?"),  # trailing punctuation of any kind
    ],
)
def test_envs_that_sanitize_alike_still_get_different_names(left, right):
    # Folding alone is not injective, and two envs sharing a cookie name IS the
    # bug this scoping exists to remove - so a fold has to be disambiguated
    # rather than merely made safe.
    assert dev(AUTH, left) != dev(AUTH, right)


def test_a_sanitized_name_is_stable_across_processes():
    # The browser has to find the cookie again after a restart, so the
    # disambiguator cannot be hash()-based (salted per process).
    assert dev(AUTH, "wt my feature") == dev(AUTH, "wt my feature")


# --- What the running app actually uses ---


def test_the_settings_expose_resolved_names():
    assert auth_settings.COOKIE_NAME.startswith(AUTH)
    assert auth_settings.MFA_PENDING_COOKIE_NAME.startswith(PENDING)
    assert auth_settings.COOKIE_NAME != auth_settings.MFA_PENDING_COOKIE_NAME


# --- The readers honour the resolved name ---


class _BearerPathTaken(Exception):
    """Raised by the stubbed token lookup: proves the cookie branch was missed."""


class _Request:
    """The parts of a Request that get_enabled_backends reads."""

    def __init__(self, cookies):
        self.cookies = cookies
        # An Authorization header so a missed cookie falls to the bearer
        # branch, where the stub below is waiting. Without it the function
        # reaches its "neither matched" default, which returns the cookie
        # backend anyway - and the test would pass on a hard-coded name.
        self.headers = {
            "authorization": "Bearer tok",
            "x-service-name": "file-converter",
        }
        self.scope = {"endpoint": None}


@pytest.fixture
def scoped_cookie_name(monkeypatch):
    """Point the setting at a name no literal in the codebase could match."""
    monkeypatch.setattr(auth_settings, "COOKIE_NAME", SENTINEL)

    async def _boom(token, config):  # noqa: ARG001
        raise _BearerPathTaken()

    monkeypatch.setattr(auth_backend, "resolve_token_context", _boom)
    return SENTINEL


@pytest.mark.asyncio
async def test_get_enabled_backends_selects_the_cookie_backend_by_the_setting(
    scoped_cookie_name,
):
    request = _Request({scoped_cookie_name: "tok"})

    assert await auth_backend.get_enabled_backends(request) == [
        auth_backend.auth_backend_jwt
    ]


@pytest.mark.asyncio
async def test_get_enabled_backends_ignores_the_unscoped_name(scoped_cookie_name):
    # The literal that used to be written here. Finding it must NOT count as a
    # session, or a dev instance would authenticate off a neighbouring stack's
    # cookie again.
    request = _Request({AUTH: "tok"})

    with pytest.raises(_BearerPathTaken):
        await auth_backend.get_enabled_backends(request)


@pytest.mark.asyncio
async def test_the_socket_handshake_reads_the_setting(monkeypatch):
    monkeypatch.setattr(auth_settings, "COOKIE_NAME", SENTINEL)

    assert await get_jwt_from_cookies(f"other=1; {SENTINEL}=tok; {AUTH}=stale") == "tok"


def test_the_post_login_hook_reads_the_setting(monkeypatch):
    monkeypatch.setattr(auth_settings, "COOKIE_NAME", SENTINEL)
    response = Response()
    response.set_cookie(SENTINEL, "tok", path="/")

    assert session_token_from_response(response) == "tok"


def test_the_post_login_hook_finds_the_session_behind_another_cookie():
    # The second-factor route clears the pending cookie beside the session it
    # issues, and headers["set-cookie"] returns whichever was appended first.
    # Reading only that one makes socket auth depend on the order two unrelated
    # lines happen to run in.
    response = Response()
    response.delete_cookie(auth_settings.MFA_PENDING_COOKIE_NAME, path="/")
    response.set_cookie(auth_settings.COOKIE_NAME, "tok", path="/")

    assert session_token_from_response(response) == "tok"


def test_the_post_login_hook_reports_a_missing_session_cookie():
    # None rather than an IndexError for the caller's broad `except` to bury.
    response = Response()
    response.delete_cookie(auth_settings.MFA_PENDING_COOKIE_NAME, path="/")

    assert session_token_from_response(response) is None
