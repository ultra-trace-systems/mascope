"""Tests: the gate that refuses agent tokens with no paired device behind them.

``ensure_device_bound`` is the whole of the ``require_device_tokens`` control,
consulted on every bearer request. The failure modes are silent in opposite
directions - refusing a valid personal token, or accepting an unbound agent
token while the flag is on - so both are asserted directly.
"""

import pytest

from mascope_backend.api.new.auth.access_token import validation
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.api.new.auth.pairing.config import pairing_settings
from mascope_runtime.config import BackendConfig


@pytest.fixture
def strict_mode(monkeypatch):
    """Set require_device_tokens without touching real config."""

    def _set(enabled: bool):
        monkeypatch.setattr(
            validation.runtime.config, "require_device_tokens", enabled, raising=False
        )

    return _set


AGENT_SERVICE = pairing_settings.ALLOWED_SERVICES[0]


def test_unbound_agent_token_refused_when_strict(strict_mode):
    strict_mode(True)
    with pytest.raises(InvalidTokenException):
        validation.ensure_device_bound(AGENT_SERVICE, device_id=None)


def test_bound_agent_token_allowed_when_strict(strict_mode):
    strict_mode(True)
    # A device-bound token passes regardless of the flag.
    validation.ensure_device_bound(AGENT_SERVICE, device_id=7)


def test_unbound_agent_token_allowed_when_not_strict(strict_mode):
    strict_mode(False)
    # The migration state: unbound agent tokens keep working until the flag flips.
    validation.ensure_device_bound(AGENT_SERVICE, device_id=None)


@pytest.mark.parametrize("service", ["mascope_sdk", "file-converter"])
def test_non_agent_services_are_never_gated(strict_mode, service):
    # Personal and internal tokens have no device concept, so the flag must not
    # touch them even when it is on.
    strict_mode(True)
    validation.ensure_device_bound(service, device_id=None)


def test_refusals_deliver_their_own_remediation(strict_mode):
    """The refusal must reach the caller, not be genericized into "sign in".

    An agent has no session and cannot sign in, so a 401 answering "please
    sign in to the Mascope" tells its operator nothing. Both refusals opt out
    of that genericization by carrying ``ClientFacingDetail``; if that marker
    is dropped, the wording below still exists in the log and nowhere else.
    """
    from datetime import datetime, timedelta, timezone

    from mascope_backend.api.lib.exceptions.api_exceptions import ClientFacingDetail

    strict_mode(True)
    with pytest.raises(InvalidTokenException) as unbound:
        validation.ensure_device_bound(AGENT_SERVICE, device_id=None)
    assert isinstance(unbound.value, ClientFacingDetail)
    assert "paired agent credentials" in unbound.value.detail

    stale = datetime.now(timezone.utc) - timedelta(days=365)
    with pytest.raises(InvalidTokenException) as expired:
        validation.ensure_device_token_fresh(
            AGENT_SERVICE, device_id=7, created_at=stale
        )
    assert isinstance(expired.value, ClientFacingDetail)
    assert "expired" in expired.value.detail


def test_ordinary_token_failures_stay_generic():
    """Only the two agent refusals opt in; the rest keep "please sign in".

    Their details name internals ("Invalid token format", "Token validation
    failed") and must not reach a client.
    """
    from mascope_backend.api.lib.exceptions.api_exceptions import ClientFacingDetail

    assert not isinstance(
        InvalidTokenException("Invalid access token."), ClientFacingDetail
    )


def test_require_device_tokens_ships_off():
    """The flag defaults off, which is what makes the migration state work.

    Tokens issued before the device registry keep authenticating until a
    deployment has re-paired every agent machine and turns this on. Flipping
    the default would refuse them on upgrade, with no pairing done yet. The
    class default is asserted, not ``runtime.config``, so an env overlay in a
    developer's ``mascope.toml`` cannot make this flap.
    """
    assert BackendConfig(name="backend").require_device_tokens is False
