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
