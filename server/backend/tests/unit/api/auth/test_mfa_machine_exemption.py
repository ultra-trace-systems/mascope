"""Tests: every reader of the two-factor policy sees the machine-account exemption.

`test_mfa_policy.py` pins the exemption in the policy itself. These pin that
each place deciding something from it passes the account's kind through: the
user schema operators and audits read, the status route the enrolment screen
reads, and the route that refuses to switch a factor off. A caller that dropped
the kind, or hard-coded a person, would report a machine account as owing an
enrolment again - so each is checked with a machine account and a person at the
same role, under a policy that covers that role.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE, ACCOUNT_TYPE_PERSON
from mascope_backend.api.new.auth.mfa import policy, routes
from mascope_backend.api.new.auth.mfa.exceptions import MfaRequiredByPolicyException
from mascope_backend.api.new.auth.mfa.schemas import MfaCodeRequest
from mascope_backend.api.new.users.schemas import UserRead
from mascope_backend.roles import ROLE_ACCESS_LEVELS


EDITOR = ROLE_ACCESS_LEVELS["editor"]


@pytest.fixture(autouse=True)
def require_editor_mfa(monkeypatch):
    """The deployment the exemption exists for: a policy covering editors."""
    monkeypatch.setattr(policy, "REQUIRED_LEVEL", EDITOR)


def _user_read(account_type: str, mfa_enabled: bool = False) -> UserRead:
    return UserRead(
        id=1,
        email="someone@example.org",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        username="someone",
        role_id=EDITOR,
        registered_at=datetime.now(timezone.utc),
        must_change_password=False,
        mfa_enabled=mfa_enabled,
        account_type=account_type,
    )


# --- The user schema ---


def test_a_machine_account_reads_as_exempt_not_pending():
    user = _user_read(ACCOUNT_TYPE_MACHINE)
    assert user.mfa_enrollment_required is False
    assert user.mfa_exempt is True


def test_a_person_at_the_same_role_reads_as_pending_not_exempt():
    user = _user_read(ACCOUNT_TYPE_PERSON)
    assert user.mfa_enrollment_required is True
    assert user.mfa_exempt is False


def test_both_fields_are_in_the_serialized_user():
    # Computed fields only reach the client if they serialize; the Manage users
    # column reads them off the JSON.
    dumped = _user_read(ACCOUNT_TYPE_MACHINE).model_dump()
    assert dumped["mfa_exempt"] is True
    assert dumped["mfa_enrollment_required"] is False


# --- The routes ---


def _account(account_type: str, mfa_enabled: bool = True):
    return SimpleNamespace(
        id=7,
        username="someone",
        role_id=EDITOR,
        mfa_enabled=mfa_enabled,
        mfa_confirmed_at=None,
        account_type=account_type,
    )


@pytest.fixture
def disable_collaborators(monkeypatch):
    """Everything the disable route touches besides the policy, stubbed."""
    disabled: list[int] = []

    async def _no_limit(*args, **kwargs):
        return None

    async def _verifies(user, code):
        return True

    async def _disable(user_id):
        disabled.append(user_id)

    async def _clear(user_id):
        return None

    monkeypatch.setattr(routes, "enforce_user_rate_limit", _no_limit)
    monkeypatch.setattr(routes.service, "verify_totp_for_user", _verifies)
    monkeypatch.setattr(routes.service, "disable_mfa", _disable)
    monkeypatch.setattr(routes, "clear_recent_verification", _clear)
    return disabled


@pytest.mark.asyncio
async def test_disable_is_refused_for_a_person_the_policy_covers(
    disable_collaborators,
):
    with pytest.raises(MfaRequiredByPolicyException):
        await routes.disable_route.__wrapped__(
            body=MfaCodeRequest(code="123456"),
            user=_account(ACCOUNT_TYPE_PERSON),
        )
    assert disable_collaborators == []


@pytest.mark.asyncio
async def test_disable_is_not_refused_by_policy_for_a_machine_account(
    disable_collaborators,
):
    # Same role, same policy. The account kind is what the refusal turns on.
    await routes.disable_route.__wrapped__(
        body=MfaCodeRequest(code="123456"),
        user=_account(ACCOUNT_TYPE_MACHINE),
    )
    assert disable_collaborators == [7]


@pytest.fixture
def status_collaborators(monkeypatch):
    async def _count(user):
        return 0

    monkeypatch.setattr(routes.service, "unused_recovery_code_count", _count)
    monkeypatch.setattr(routes.service, "mfa_available", lambda: True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "account_type,required",
    [(ACCOUNT_TYPE_PERSON, True), (ACCOUNT_TYPE_MACHINE, False)],
)
async def test_status_reports_the_requirement_by_account_kind(
    status_collaborators, account_type, required
):
    result = await routes.status_route.__wrapped__(
        user=_account(account_type, mfa_enabled=False)
    )
    assert result["data"]["required"] is required
