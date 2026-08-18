"""Tests: the deployment policy deciding who must hold a second factor.

The policy is read from config and consulted by the gate, so the risks are a
misconfigured value being taken for "nobody required" and the role comparison
covering the wrong set of accounts. Both are silent failures - nobody is ever
prompted and the deployment looks compliant - so they are asserted directly
rather than through the gate.
"""

import importlib
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.auth.mfa import policy
from mascope_backend.roles import ROLE_ACCESS_LEVELS


GUEST = ROLE_ACCESS_LEVELS["guest"]
EDITOR = ROLE_ACCESS_LEVELS["editor"]
ADMIN = ROLE_ACCESS_LEVELS["admin"]
OWNER = ROLE_ACCESS_LEVELS["owner"]


@pytest.fixture
def with_policy(monkeypatch):
    """Set the resolved requirement without going through config or a reload."""

    def _set(level):
        monkeypatch.setattr(policy, "REQUIRED_LEVEL", level)

    return _set


# --- Resolution from config ---


@pytest.fixture
def configured():
    """Resolve a configured value through the mapping under test."""
    return policy.resolve_required_level


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unset_policy_requires_nobody(configured, value):
    assert configured(value) is None


@pytest.mark.parametrize(
    "name,expected",
    [("guest", GUEST), ("editor", EDITOR), ("admin", ADMIN), ("owner", OWNER)],
)
def test_each_role_name_resolves_to_its_level(configured, name, expected):
    assert configured(name) == expected


@pytest.mark.parametrize("name", ["ADMIN", "Admin", " admin "])
def test_role_name_is_read_case_and_space_insensitively(configured, name):
    # Operators type this into a TOML by hand.
    assert configured(name) == ADMIN


@pytest.mark.parametrize("value", ["admins", "superuser", "yes", "true", "1"])
def test_an_unknown_role_name_is_refused_rather_than_ignored(configured, value):
    # The failure that matters: treating a typo as "nobody required" leaves an
    # operator believing the requirement is in force while no account is ever
    # asked to enrol.
    with pytest.raises(policy.InvalidMfaPolicyError):
        configured(value)


def test_the_refusal_names_the_valid_roles(configured):
    with pytest.raises(policy.InvalidMfaPolicyError, match="admin"):
        configured("admins")


def test_a_missing_setting_is_treated_as_unset(monkeypatch):
    # A config layer on disk that predates this setting must not raise: the
    # reader falls back to None rather than failing every deployment on update.
    # Patches the name in the policy module, not runtime.config, which is a
    # read-only property.
    monkeypatch.setattr(policy, "runtime", SimpleNamespace(config=object()))
    assert policy._configured_value() is None


def test_a_present_setting_is_read_from_the_backend_config(monkeypatch):
    monkeypatch.setattr(
        policy,
        "runtime",
        SimpleNamespace(config=SimpleNamespace(mfa_required_min_role="admin")),
    )
    assert policy._configured_value() == "admin"


def test_module_import_resolves_the_policy():
    # REQUIRED_LEVEL is resolved at import so a bad value stops the process at
    # startup rather than on whichever request first consults it.
    reloaded = importlib.reload(policy)
    assert hasattr(reloaded, "REQUIRED_LEVEL")


# --- Who the requirement covers ---


def test_nobody_is_covered_when_the_policy_is_off(with_policy):
    with_policy(None)
    assert not policy.policy_active()
    for role in (GUEST, EDITOR, ADMIN, OWNER):
        assert not policy.required_for_role(role)


def test_the_threshold_covers_that_role_and_above(with_policy):
    with_policy(ADMIN)
    assert policy.policy_active()
    assert not policy.required_for_role(GUEST)
    assert not policy.required_for_role(EDITOR)
    assert policy.required_for_role(ADMIN)
    assert policy.required_for_role(OWNER)


def test_guest_threshold_covers_everyone(with_policy):
    with_policy(GUEST)
    for role in (GUEST, EDITOR, ADMIN, OWNER):
        assert policy.required_for_role(role)


def test_an_account_with_no_role_is_not_covered(with_policy):
    # It cannot use the application either way, and the role checks refuse it
    # before this matters.
    with_policy(GUEST)
    assert not policy.required_for_role(None)


# --- What the gate asks ---


def test_enrolment_is_owed_only_by_a_covered_account_without_a_factor(with_policy):
    with_policy(ADMIN)
    assert policy.enrollment_required(ADMIN, mfa_enabled=False)
    assert not policy.enrollment_required(ADMIN, mfa_enabled=True)
    assert not policy.enrollment_required(EDITOR, mfa_enabled=False)


def test_no_enrolment_is_owed_while_the_policy_is_off(with_policy):
    with_policy(None)
    for role in (GUEST, EDITOR, ADMIN, OWNER):
        assert not policy.enrollment_required(role, mfa_enabled=False)
