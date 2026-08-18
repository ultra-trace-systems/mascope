"""Tests: derived token secrets are per-deployment values, not hardcoded.

The former ``SECRET_RESET`` / ``SECRET_VERIFY`` constants were predictable
defaults that would let an attacker forge password-reset / email-verification
tokens if those flows were ever enabled. They - and the file-converter service
token - are derived per deployment from the JWT secret with domain separation
(see ``mascope_backend.service_token``).
"""

from mascope_backend.api.new.auth.config import (
    _derive_token_secret,
    auth_settings,
)
from mascope_backend.service_token import FILE_CONVERTER_SERVICE_TOKEN


def test_secrets_are_not_the_legacy_constants():
    assert auth_settings.RESET_PASSWORD_TOKEN_SECRET != "SECRET_RESET"
    assert auth_settings.VERIFICATION_TOKEN_SECRET != "SECRET_VERIFY"


def test_derived_secrets_are_domain_separated():
    # Every derived purpose must have its own secret.
    derived = {
        auth_settings.RESET_PASSWORD_TOKEN_SECRET,
        auth_settings.VERIFICATION_TOKEN_SECRET,
        FILE_CONVERTER_SERVICE_TOKEN,
    }
    assert len(derived) == 3


def test_derivation_is_deterministic_and_domain_separated():
    assert _derive_token_secret("reset-password") == _derive_token_secret(
        "reset-password"
    )
    assert _derive_token_secret("a") != _derive_token_secret("b")
    # 32-byte HMAC-SHA256, hex-encoded.
    assert len(_derive_token_secret("reset-password")) == 64
