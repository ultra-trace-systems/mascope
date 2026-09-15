"""Tests: the gate holding an un-enrolled account out of the application.

Structural, like the password-gate coverage tests beside them: they assert the
ordering between the two gates and that the gate is scoped to the interactive
session, rather than re-testing the policy arithmetic covered next door.
"""

import pytest

from mascope_backend.api.new.auth import dependencies as deps
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import PasswordChangeRequiredException
from mascope_backend.api.new.auth.mfa import policy
from mascope_backend.api.new.auth.mfa.exceptions import MfaEnrollmentRequiredException
from mascope_backend.roles import ROLE_ACCESS_LEVELS


ADMIN = ROLE_ACCESS_LEVELS["admin"]
EDITOR = ROLE_ACCESS_LEVELS["editor"]


class _Request:
    """A request carrying, or not carrying, the interactive session cookie."""

    def __init__(self, with_cookie: bool = True):
        self.cookies = {auth_settings.COOKIE_NAME: "x"} if with_cookie else {}


class _User:
    def __init__(self, role_id=ADMIN, mfa_enabled=False, must_change_password=False):
        self.id = 7
        self.username = "someone"
        self.role_id = role_id
        self.mfa_enabled = mfa_enabled
        self.must_change_password = must_change_password


@pytest.fixture
def require_admin_mfa(monkeypatch):
    monkeypatch.setattr(policy, "REQUIRED_LEVEL", ADMIN)


@pytest.fixture
def policy_off(monkeypatch):
    monkeypatch.setattr(policy, "REQUIRED_LEVEL", None)


def test_covered_account_without_a_factor_is_refused(require_admin_mfa):
    with pytest.raises(MfaEnrollmentRequiredException):
        deps._enforce_gates(_Request(), _User())


def test_covered_account_with_a_factor_passes(require_admin_mfa):
    user = _User(mfa_enabled=True)
    assert deps._enforce_gates(_Request(), user) is user


def test_uncovered_account_passes(require_admin_mfa):
    user = _User(role_id=EDITOR)
    assert deps._enforce_gates(_Request(), user) is user


def test_nobody_is_refused_while_the_policy_is_off(policy_off):
    user = _User()
    assert deps._enforce_gates(_Request(), user) is user


def test_bearer_token_callers_are_not_gated(require_admin_mfa):
    # A token holder - the SDK, a notebook, an instrument agent - has no way to
    # render an enrolment screen, and its credential was minted under whatever
    # rules applied then. Keyed on the same cookie as the backend selection, so
    # the two cannot drift apart.
    user = _User()
    assert deps._enforce_gates(_Request(with_cookie=False), user) is user


def test_the_password_gate_is_applied_first(require_admin_mfa):
    # An account can owe both. Showing the enrolment screen to someone still
    # holding a password an administrator chose would bind an authenticator to
    # an account whose credentials are known to someone else.
    user = _User(must_change_password=True, mfa_enabled=False)
    with pytest.raises(PasswordChangeRequiredException):
        deps._enforce_gates(_Request(), user)


def test_enrolment_is_owed_once_the_password_is_replaced(require_admin_mfa):
    user = _User(must_change_password=False, mfa_enabled=False)
    with pytest.raises(MfaEnrollmentRequiredException):
        deps._enforce_gates(_Request(), user)


def test_the_gate_refuses_with_a_code_the_client_can_branch_on():
    # The frontend swaps in the enrolment screen on this code, not on the
    # status, which it shares with ordinary permission failures.
    from mascope_backend.api.new.auth.mfa.exceptions import (
        MFA_ENROLLMENT_REQUIRED_CODE,
    )

    exc = MfaEnrollmentRequiredException()
    assert exc.error_code == MFA_ENROLLMENT_REQUIRED_CODE
    assert exc.status_code == 403
