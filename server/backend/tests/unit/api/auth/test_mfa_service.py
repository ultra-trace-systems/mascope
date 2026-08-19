"""Tests: TOTP verification, recovery codes, and seed encryption.

These cover the parts of the second factor that decide whether a code is
accepted, and are deliberately free of database and Redis: the properties under
test (drift window, replay refusal, single-use normalization, audience
separation) are the ones a reviewer cannot check by inspection.
"""

import pyotp
import pytest
from fastapi_users.jwt import decode_jwt, generate_jwt

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.mfa import crypto, service


PERIOD = auth_settings.mfa.PERIOD_SECONDS
WINDOW = auth_settings.mfa.VALID_WINDOW


@pytest.fixture
def secret() -> str:
    return pyotp.random_base32()


def _code_at(secret: str, timestep: int) -> str:
    """The code a correctly-set authenticator would show at ``timestep``."""
    return service._totp(secret).generate_otp(timestep)


# --- Parameters, pinned ---
#
# The tests below derive their offsets from the configured window, so they
# follow it wherever it goes and cannot notice it being widened. These pin the
# values themselves, which is the only place a change to them has to be argued
# for. Found by mutating VALID_WINDOW to 10 and watching the whole module still
# pass.


def test_totp_parameters_match_what_authenticator_apps_assume():
    # An app scanning the provisioning URI assumes the RFC 6238 defaults.
    # Changing either value silently breaks every already-enrolled account.
    assert auth_settings.mfa.DIGITS == 6
    assert auth_settings.mfa.PERIOD_SECONDS == 30


def test_drift_window_stays_one_step():
    # Each extra step keeps a code alive for another 30 seconds, widening the
    # span an observed code can be replayed in before mfa_last_timestep closes
    # it. One step each way is the intended compromise; raising it is a decision
    # to take deliberately, here.
    assert auth_settings.mfa.VALID_WINDOW == 1


def test_code_two_steps_away_is_refused(secret):
    # The literal counterpart to the parametrized window tests: a fixed offset,
    # so widening the window makes this fail rather than expand.
    now = service.current_timestep()
    assert (
        service.verify_code_at_timestep(secret, _code_at(secret, now + 2), None) is None
    )
    assert (
        service.verify_code_at_timestep(secret, _code_at(secret, now - 2), None) is None
    )


# --- TOTP verification ---


def test_current_code_verifies(secret):
    now = service.current_timestep()
    assert service.verify_code_at_timestep(secret, _code_at(secret, now), None) == now


@pytest.mark.parametrize("offset", range(-WINDOW, WINDOW + 1))
def test_codes_within_drift_window_verify(secret, offset):
    # A phone whose clock is off by less than the window still signs in.
    now = service.current_timestep()
    step = now + offset
    assert service.verify_code_at_timestep(secret, _code_at(secret, step), None) == step


@pytest.mark.parametrize("offset", [-(WINDOW + 1), WINDOW + 1, 100, -100])
def test_codes_outside_drift_window_are_refused(secret, offset):
    now = service.current_timestep()
    code = _code_at(secret, now + offset)
    assert service.verify_code_at_timestep(secret, code, None) is None


def test_wrong_code_is_refused(secret):
    now = service.current_timestep()
    correct = _code_at(secret, now)
    wrong = "000000" if correct != "000000" else "111111"
    assert service.verify_code_at_timestep(secret, wrong, None) is None


def test_blank_code_is_refused(secret):
    assert service.verify_code_at_timestep(secret, "   ", None) is None


@pytest.mark.parametrize(
    "code",
    [
        "12345–67",  # an en-dash, as a mail client or PDF renders a hyphen
        "１２３４５６",  # full-width digits from a mobile IME
        "123 456",  # a non-breaking space, left in by replace(" ", "")
    ],
)
def test_non_ascii_code_is_a_miss_not_an_error(secret, code):
    # hmac.compare_digest raises TypeError on a non-ASCII str, and the code is
    # arbitrary user input; a non-matching one must be refused, not turned into
    # an unhandled 500 that also skips the failed-attempt counter.
    assert service.verify_code_at_timestep(secret, code, None) is None


def test_code_is_refused_once_its_timestep_was_spent(secret):
    # The drift window keeps a code usable for longer than its own 30 seconds,
    # so an observed code could otherwise be replayed inside that span.
    now = service.current_timestep()
    code = _code_at(secret, now)
    assert service.verify_code_at_timestep(secret, code, None) == now
    assert service.verify_code_at_timestep(secret, code, now) is None


def test_older_code_is_refused_after_a_newer_one(secret):
    now = service.current_timestep()
    previous = _code_at(secret, now - 1)
    assert service.verify_code_at_timestep(secret, previous, now) is None


def test_next_code_still_verifies_after_one_is_spent(secret):
    # Refusing replays must not lock a user out of their next sign-in.
    now = service.current_timestep()
    assert service.verify_code_at_timestep(secret, _code_at(secret, now + 1), now) == (
        now + 1
    )


# --- Recovery codes ---


def test_recovery_codes_are_distinct_and_the_configured_count():
    codes = service.generate_recovery_codes()
    assert len(codes) == auth_settings.mfa.RECOVERY_CODE_COUNT
    assert len(set(codes)) == len(codes)


def test_recovery_codes_carry_the_configured_entropy():
    # 16 characters over the 31-symbol alphabet is about 79 bits, so the SHA-256
    # digests they are stored as resist the same offline search of a leaked
    # database dump that the seed encryption addresses.
    for code in service.generate_recovery_codes():
        assert (
            len(service.normalize_recovery_code(code))
            == auth_settings.mfa.RECOVERY_CODE_LENGTH
        )


def test_recovery_codes_avoid_ambiguous_characters():
    # They are read off paper, where 0/O and 1/I/L are guesswork.
    for code in service.generate_recovery_codes():
        assert not set(code) & set("01OIL")


def test_recovery_code_hash_ignores_case_and_dashes():
    code = service.generate_recovery_codes()[0]
    variants = [code, code.lower(), code.replace("-", ""), f"  {code}  "]
    assert len({service.hash_recovery_code(v) for v in variants}) == 1


def test_distinct_recovery_codes_hash_differently():
    a, b = service.generate_recovery_codes()[:2]
    assert service.hash_recovery_code(a) != service.hash_recovery_code(b)


# --- Seed encryption ---


@pytest.fixture
def mfa_key(monkeypatch):
    """A deployment key, without touching the real secrets directory."""
    monkeypatch.setattr(crypto, "mfa_encryption_key", lambda: "unit-test-mfa-key")


def test_secret_round_trips(mfa_key, secret):
    assert crypto.decrypt_secret(crypto.encrypt_secret(secret)) == secret


def test_stored_secret_is_not_the_plaintext(mfa_key, secret):
    # The point of the column being encrypted is that a database dump does not
    # hand over working seeds.
    assert secret not in crypto.encrypt_secret(secret)


def test_decrypt_returns_none_for_a_foreign_ciphertext(mfa_key, secret, monkeypatch):
    stored = crypto.encrypt_secret(secret)
    # What a rotated or restored-from-elsewhere key looks like.
    monkeypatch.setattr(crypto, "mfa_encryption_key", lambda: "a-different-key")
    assert crypto.decrypt_secret(stored) is None


def test_decrypt_returns_none_for_garbage(mfa_key):
    assert crypto.decrypt_secret("not-a-ciphertext") is None


def test_encrypt_refuses_when_the_deployment_has_no_key(monkeypatch, secret):
    from mascope_backend.api.new.auth.mfa.exceptions import MfaNotConfiguredException

    monkeypatch.setattr(crypto, "mfa_encryption_key", lambda: None)
    with pytest.raises(MfaNotConfiguredException):
        crypto.encrypt_secret(secret)


def test_decrypt_returns_none_when_the_key_is_gone(mfa_key, secret, monkeypatch):
    # A key lost entirely (no file) must fail the TOTP check softly so the verify
    # route falls through to the recovery-code path, not 5xx every enrolled
    # account out. Enrolment still refuses, through encrypt_secret.
    stored = crypto.encrypt_secret(secret)
    monkeypatch.setattr(crypto, "mfa_encryption_key", lambda: None)
    assert crypto.decrypt_secret(stored) is None


# --- Pending token separation ---


def _pending_token(user_id: int = 1) -> str:
    return generate_jwt(
        data={"sub": str(user_id), "aud": auth_settings.mfa.PENDING_TOKEN_AUDIENCE},
        secret=auth_settings.MFA_PENDING_TOKEN_SECRET,
        lifetime_seconds=auth_settings.mfa.PENDING_TOKEN_LIFETIME_SECONDS,
    )


def test_pending_token_is_not_accepted_as_a_session():
    # The whole design rests on this: a caller who has passed only the password
    # step holds nothing any session-reading surface will take.
    import jwt

    with pytest.raises(jwt.PyJWTError):
        decode_jwt(
            _pending_token(),
            auth_settings.MFA_PENDING_TOKEN_SECRET,
            auth_settings.JWT_AUDIENCE,
        )


def test_pending_token_is_not_signed_with_the_session_secret():
    import jwt

    with pytest.raises(jwt.PyJWTError):
        decode_jwt(
            _pending_token(),
            auth_settings.JWT_SECRET_KEY,
            [auth_settings.mfa.PENDING_TOKEN_AUDIENCE],
        )


def test_session_token_is_not_accepted_as_a_pending_token():
    # The reverse direction: a stolen session must not be presentable as a
    # half-finished sign-in either.
    import jwt

    session_token = generate_jwt(
        data={"sub": "1", "aud": auth_settings.JWT_AUDIENCE[0]},
        secret=auth_settings.JWT_SECRET_KEY,
        lifetime_seconds=60,
    )
    with pytest.raises(jwt.PyJWTError):
        decode_jwt(
            session_token,
            auth_settings.MFA_PENDING_TOKEN_SECRET,
            [auth_settings.mfa.PENDING_TOKEN_AUDIENCE],
        )


def test_pending_token_carries_its_subject():
    claims = decode_jwt(
        _pending_token(42),
        auth_settings.MFA_PENDING_TOKEN_SECRET,
        [auth_settings.mfa.PENDING_TOKEN_AUDIENCE],
    )
    assert claims["sub"] == "42"
