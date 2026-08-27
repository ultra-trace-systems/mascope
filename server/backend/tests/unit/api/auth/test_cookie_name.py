"""Tests: scoping the auth cookie's name to the runtime env.

Cookies are not port-scoped, so two dev instances on one hostname used to share
one ``mascope_auth`` and sign each other out - a failure that looks like a
successful login followed by a 401. The env suffix is what keeps them apart, so
the risks are two envs collapsing onto one name (the bug returns, silently) and
the suffix leaking into prod (every user signed out on upgrade). Both are
asserted against the resolver directly: the names are computed once at import,
so going through ``auth_settings`` could only observe the env the suite happens
to run in.
"""

import re

import pytest

from mascope_backend.api.new.auth.config import _resolve_cookie_name, auth_settings


AUTH = "mascope_auth"
PENDING = "mascope_mfa_pending"

# RFC 6265: a cookie name is an RFC 2616 token - no CTLs and no separators.
SEPARATORS = set('()<>@,;:\\"/[]?={} \t')


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


# --- Token safety ---


@pytest.mark.parametrize(
    "env",
    ["wt-my feature", "feature/branch", "wt=x", "wt;x", "wt(x)", "ä-env"],
)
def test_a_hostile_env_name_still_yields_a_token(env):
    name = dev(AUTH, env)
    assert not SEPARATORS & set(name)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", name)


@pytest.mark.parametrize("env", ["", None])
def test_an_empty_env_falls_back_to_the_bare_name(env):
    # Better an unsuffixed name than a trailing "_" nobody can predict.
    assert dev(AUTH, env) == AUTH


# --- What the running app actually uses ---


def test_the_settings_expose_resolved_names():
    assert auth_settings.COOKIE_NAME.startswith(AUTH)
    assert auth_settings.MFA_PENDING_COOKIE_NAME.startswith(PENDING)
    assert auth_settings.COOKIE_NAME != auth_settings.MFA_PENDING_COOKIE_NAME
