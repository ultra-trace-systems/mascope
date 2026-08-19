"""Tests: the step-up gate in front of credential-minting routes.

The second factor is only worth anything on those routes if it cannot be
skipped, so these cover the two ways it could be: an account that never
presented a code getting through, and the check quietly passing when its store
is unreachable.
"""

import pytest

from mascope_backend.api.new.auth.access_token.routes import (
    access_token_regenerate_route,
)
from mascope_backend.api.new.auth.access_token.schemas import AccessTokenRequest
from mascope_backend.api.new.auth.mfa import reauth
from mascope_backend.api.new.auth.mfa.exceptions import MfaReauthRequiredException
from mascope_backend.api.new.auth.pairing.routes import pairing_approve_route
from mascope_backend.api.new.auth.pairing.schemas import PairingApproveRequest


class _FakeRedis:
    """Minimal stand-in for the shared client, with an optional failure mode."""

    def __init__(self, present: bool = False, broken: bool = False):
        self.present = present
        self.broken = broken
        self.deleted = False
        self.set_calls: list[tuple] = []

    async def exists(self, _key) -> int:
        if self.broken:
            raise ConnectionError("redis is down")
        return 1 if self.present else 0

    async def set(self, key, value, ex=None):
        if self.broken:
            raise ConnectionError("redis is down")
        self.set_calls.append((key, value, ex))
        self.present = True

    async def delete(self, _key):
        if self.broken:
            raise ConnectionError("redis is down")
        self.deleted = True
        self.present = False


class _User:
    def __init__(self, mfa_enabled: bool, user_id: int = 7):
        self.id = user_id
        self.mfa_enabled = mfa_enabled


@pytest.fixture
def fake_redis(monkeypatch):
    """Swap the shared Redis client for one this test controls.

    Patches the backing attribute rather than ``client``: that name is a
    property which raises when nothing is connected, so monkeypatch cannot read
    its current value to restore later.
    """

    def _install(**kwargs):
        client = _FakeRedis(**kwargs)
        monkeypatch.setattr(reauth.redis_storage_client, "_client", client)
        return client

    return _install


@pytest.mark.asyncio
async def test_account_without_a_factor_passes(fake_redis):
    # Nothing to present. Refusing here would disable token minting for every
    # account on a deployment that does not use MFA.
    fake_redis(present=False)
    await reauth.require_recent_mfa(_User(mfa_enabled=False))


@pytest.mark.asyncio
async def test_enrolled_account_is_refused_without_a_recent_code(fake_redis):
    fake_redis(present=False)
    with pytest.raises(MfaReauthRequiredException):
        await reauth.require_recent_mfa(_User(mfa_enabled=True))


@pytest.mark.asyncio
async def test_enrolled_account_passes_after_presenting_a_code(fake_redis):
    client = fake_redis(present=False)
    await reauth.mark_recently_verified(7)
    assert client.present
    await reauth.require_recent_mfa(_User(mfa_enabled=True))


@pytest.mark.asyncio
async def test_marker_carries_the_configured_window(fake_redis):
    from mascope_backend.api.new.auth.config import auth_settings

    client = fake_redis(present=False)
    await reauth.mark_recently_verified(7)
    (_key, _value, expiry) = client.set_calls[0]
    assert expiry == auth_settings.mfa.REAUTH_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_check_fails_closed_when_the_store_is_unreachable(fake_redis):
    # Deliberately unlike the rate limiters, which fail open: this one decides
    # whether to hand out a year-long credential, so a Redis outage must not
    # become the bypass it exists to close.
    fake_redis(broken=True)
    with pytest.raises(MfaReauthRequiredException):
        await reauth.require_recent_mfa(_User(mfa_enabled=True))


@pytest.mark.asyncio
async def test_unenrolled_account_passes_even_when_the_store_is_unreachable(fake_redis):
    # Failing closed must not take down deployments that never enrolled anyone:
    # the check returns before it ever reaches the store.
    fake_redis(broken=True)
    await reauth.require_recent_mfa(_User(mfa_enabled=False))


@pytest.mark.asyncio
async def test_marking_is_best_effort(fake_redis):
    # A failure to record leaves the user to enter a code, which is the safe
    # direction; it must not turn the action that recorded it into an error.
    fake_redis(broken=True)
    await reauth.mark_recently_verified(7)


@pytest.mark.asyncio
async def test_clearing_removes_the_marker(fake_redis):
    client = fake_redis(present=True)
    await reauth.clear_recent_verification(7)
    assert client.deleted
    with pytest.raises(MfaReauthRequiredException):
        await reauth.require_recent_mfa(_User(mfa_enabled=True))


@pytest.mark.asyncio
async def test_markers_do_not_leak_between_accounts(fake_redis):
    # The key is per account; a shared one would let any enrolled user ride
    # another's verification.
    client = fake_redis(present=False)
    await reauth.mark_recently_verified(7)
    assert reauth._key(7) == client.set_calls[0][0]


# --- The routes actually wire the gate in ---
#
# The checks above prove require_recent_mfa refuses correctly; these prove the
# two credential-minting routes call it, and call it first - before any DB work
# - so an enrolled account with no recent code is turned away. Pinning the
# routes' behaviour is what would catch a future route (or a refactor) that
# drops the step-up, which the module-level tests alone cannot see.


@pytest.mark.asyncio
async def test_access_token_regenerate_refuses_without_a_recent_code(fake_redis):
    fake_redis(present=False)
    with pytest.raises(MfaReauthRequiredException):
        await access_token_regenerate_route(
            AccessTokenRequest(service_name="mascope_sdk"),
            user=_User(mfa_enabled=True),
        )


@pytest.mark.asyncio
async def test_pairing_approve_refuses_without_a_recent_code(fake_redis):
    fake_redis(present=False)
    with pytest.raises(MfaReauthRequiredException):
        await pairing_approve_route(
            PairingApproveRequest(user_code="ABC-123"),
            user=_User(mfa_enabled=True),
        )
    assert reauth._key(8) != reauth._key(7)
