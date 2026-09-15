"""
Enrollment, verification, and recovery for the second authentication factor.
"""

import hashlib
import hmac
import secrets as _secrets
import time
from datetime import datetime as dt
from datetime import timezone

import pyotp
from sqlalchemy import delete, func, select, update

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.mfa.crypto import decrypt_secret, encrypt_secret
from mascope_backend.api.new.auth.mfa.exceptions import (
    MfaAlreadyEnabledException,
    MfaNotEnrolledException,
)
from mascope_backend.api.new.auth.mfa.secrets import mfa_configured
from mascope_backend.db import User, UserRecoveryCode, async_session
from mascope_backend.runtime import runtime


mfa_settings = auth_settings.mfa

#: Recovery codes are read off paper by a human, so the alphabet drops the
#: characters that get misread in that setting (0/O, 1/I/L). Base32's alphabet
#: would reintroduce them.
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _now() -> dt:
    return dt.now(timezone.utc)


def _totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(
        secret,
        digits=mfa_settings.DIGITS,
        interval=mfa_settings.PERIOD_SECONDS,
    )


def current_timestep() -> int:
    """
    The TOTP counter for the present moment.

    :return: Unix time divided by the configured period.
    """
    return int(time.time()) // mfa_settings.PERIOD_SECONDS


def provisioning_uri(secret: str, account_name: str) -> str:
    """
    The ``otpauth://`` URI an authenticator app consumes, usually as a QR code.

    Rendering the QR is left to the frontend, so no image dependency is needed
    here.

    :param secret: The base32 TOTP seed.
    :param account_name: Identifier shown in the app's account list.
    :return: The provisioning URI.
    """
    return _totp(secret).provisioning_uri(
        name=account_name, issuer_name=mfa_settings.ISSUER
    )


def verify_code_at_timestep(
    secret: str, code: str, last_timestep: int | None
) -> int | None:
    """
    Check a TOTP code against a seed, refusing anything already accepted.

    Codes are derived from the counter directly rather than from a timestamp:
    ``generate_otp`` takes the RFC 6238 counter, which sidesteps the local-time
    conversion that the timestamp-shaped entry points go through.

    :param secret: The base32 TOTP seed.
    :param code: The submitted code.
    :param last_timestep: The highest counter this account has already spent.
    :return: The counter the code belongs to, or ``None`` if it does not verify
        or was already spent.
    """
    submitted = code.strip().replace(" ", "")
    if not submitted:
        return None

    totp = _totp(secret)
    now = current_timestep()
    # Compare as bytes: hmac.compare_digest raises TypeError on a str carrying
    # any non-ASCII character (a pasted typographic dash, full-width digits),
    # and the submitted code is arbitrary user input. Encoding both sides turns
    # a non-matching code into a plain miss instead of an unhandled 500.
    submitted_bytes = submitted.encode("utf-8")
    for offset in range(-mfa_settings.VALID_WINDOW, mfa_settings.VALID_WINDOW + 1):
        candidate = now + offset
        if hmac.compare_digest(
            totp.generate_otp(candidate).encode("utf-8"), submitted_bytes
        ):
            if last_timestep is not None and candidate <= last_timestep:
                # Correct code, but already spent. Accepting it would reopen the
                # window that the drift tolerance creates.
                return None
            return candidate
    return None


def generate_recovery_codes() -> list[str]:
    """
    Fresh recovery codes in the form ``XXXX-XXXX-XXXX-XXXX``.

    Each code is ``RECOVERY_CODE_LENGTH`` characters from the 31-symbol alphabet
    - about 79 bits - so the SHA-256 digests they are stored as resist the same
    offline search of a leaked database dump that the seed encryption defends
    against.

    :return: Plaintext codes, which the caller must show exactly once.
    """
    codes = []
    for _ in range(mfa_settings.RECOVERY_CODE_COUNT):
        body = "".join(
            _secrets.choice(_RECOVERY_ALPHABET)
            for _ in range(mfa_settings.RECOVERY_CODE_LENGTH)
        )
        # Grouped for legibility only; the dashes are not significant (see
        # normalize_recovery_code, which strips them).
        groups = [body[i : i + 4] for i in range(0, len(body), 4)]
        codes.append("-".join(groups))
    return codes


def normalize_recovery_code(code: str) -> str:
    """
    Fold a typed recovery code to the form that was hashed.

    These are retyped from paper, so case and the separating dash are not
    treated as significant.

    :param code: As submitted.
    :return: Canonical form.
    """
    return code.strip().upper().replace("-", "").replace(" ", "")


def hash_recovery_code(code: str) -> str:
    """
    Digest of a recovery code, as stored.

    A plain SHA-256 rather than a password hash: these are high-entropy values
    the server generated, so there is no brute-force margin a slow KDF would
    buy, and a digest lets redemption be an indexed lookup.

    :param code: As submitted or as generated.
    :return: Hex digest of the canonical form.
    """
    return hashlib.sha256(normalize_recovery_code(code).encode("utf-8")).hexdigest()


async def load_user(user_id: int) -> User | None:
    """
    Fetch the ORM row for an account mid-sign-in.

    The verify route resolves an account from a pending token rather than from a
    session, so the usual auth dependencies cannot supply it, and the read
    schemas in the users package deliberately omit the factor columns this
    needs.

    :param user_id: The account id from the pending token.
    :return: The user, or ``None`` if the account has since been removed.
    """
    async with async_session() as session:
        return await session.get(User, user_id)


async def begin_enrollment(user: User) -> tuple[str, str]:
    """
    Start enrollment by storing a fresh, unconfirmed seed for the account.

    The seed is written immediately so the confirmation step has something to
    check against, but ``mfa_enabled`` stays False - an unconfirmed seed must
    never gate a login, or closing the tab mid-enrollment would lock the account
    out.

    :param user: The account enrolling.
    :raises MfaAlreadyEnabledException: If a confirmed factor already exists.
    :raises MfaNotConfiguredException: If the deployment has no MFA key.
    :return: The base32 seed and its provisioning URI.
    """
    if user.mfa_enabled:
        raise MfaAlreadyEnabledException()

    secret = pyotp.random_base32()
    # Encrypt before opening the transaction: a missing key must fail before
    # anything is written.
    stored = encrypt_secret(secret)

    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.id == user.id)
            .values(mfa_secret=stored, mfa_enabled=False, mfa_confirmed_at=None)
        )
        await session.commit()

    return secret, provisioning_uri(secret, user.email)


async def confirm_enrollment(user: User, code: str) -> list[str] | None:
    """
    Verify the first code from the authenticator and arm the factor.

    :param user: The account enrolling.
    :param code: The code from the authenticator app.
    :raises MfaAlreadyEnabledException: If a confirmed factor already exists.
    :raises MfaNotEnrolledException: If no enrollment is in progress.
    :return: The recovery codes to show once, or ``None`` if the code was wrong.
    """
    if user.mfa_enabled:
        raise MfaAlreadyEnabledException()
    if not user.mfa_secret:
        raise MfaNotEnrolledException()

    secret = decrypt_secret(user.mfa_secret)
    if secret is None:
        # The stored seed predates the current key, so nothing can verify
        # against it. Treat the enrollment as absent rather than failing forever.
        raise MfaNotEnrolledException()

    timestep = verify_code_at_timestep(secret, code, user.mfa_last_timestep)
    if timestep is None:
        return None

    codes = generate_recovery_codes()
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.id == user.id)
            .values(
                mfa_enabled=True,
                mfa_confirmed_at=_now(),
                mfa_last_timestep=timestep,
            )
        )
        # Replace any codes left from an earlier enrollment, so the set the user
        # just wrote down is the only set that works.
        await session.execute(
            delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id)
        )
        session.add_all(
            [
                UserRecoveryCode(user_id=user.id, code_hash=hash_recovery_code(c))
                for c in codes
            ]
        )
        await session.commit()

    runtime.logger.info(f"MFA enabled for user {user.username}")
    return codes


async def verify_totp_for_user(user: User, code: str) -> bool:
    """
    Check a TOTP code during login and record the counter it consumed.

    :param user: The account signing in.
    :param code: The submitted code.
    :return: Whether the code verified.
    """
    if not user.mfa_secret:
        return False
    secret = decrypt_secret(user.mfa_secret)
    if secret is None:
        return False

    timestep = verify_code_at_timestep(secret, code, user.mfa_last_timestep)
    if timestep is None:
        return False

    async with async_session() as session:
        # The UPDATE is the claim, as in redeem_recovery_code below: guarding on
        # the counter still being behind means two requests racing in with the
        # same code cannot both consume it. Only the one whose UPDATE actually
        # advanced the marker has spent the code; the loser matches zero rows and
        # must be refused, or one observed code would mint two sessions.
        result = await session.execute(
            update(User)
            .where(User.id == user.id)
            .where(
                (User.mfa_last_timestep.is_(None)) | (User.mfa_last_timestep < timestep)
            )
            .values(mfa_last_timestep=timestep)
        )
        await session.commit()
    return result.rowcount > 0


async def redeem_recovery_code(user: User, code: str) -> bool:
    """
    Spend a recovery code, if it matches an unused one for this account.

    :param user: The account signing in.
    :param code: The submitted recovery code.
    :return: Whether a code was spent.
    """
    digest = hash_recovery_code(code)
    async with async_session() as session:
        # The UPDATE is the claim: filtering on used_at IS NULL and reading the
        # affected row count means two concurrent submissions of one code cannot
        # both succeed.
        result = await session.execute(
            update(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user.id)
            .where(UserRecoveryCode.code_hash == digest)
            .where(UserRecoveryCode.used_at.is_(None))
            .values(used_at=_now())
        )
        await session.commit()
        spent = result.rowcount > 0

    if spent:
        runtime.logger.info(f"Recovery code redeemed for user {user.username}")
    return spent


async def unused_recovery_code_count(user: User) -> int:
    """
    How many recovery codes the account has left.

    :param user: The account.
    :return: Count of unused codes.
    """
    async with async_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user.id)
            .where(UserRecoveryCode.used_at.is_(None))
        )
        return result.scalar() or 0


async def disable_mfa(user_id: int) -> None:
    """
    Clear a factor and its recovery codes.

    Takes an id rather than a ``User`` because the administrative reset path
    uses it too, where the caller is not the account being changed.

    :param user_id: The account to disarm.
    """
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                mfa_secret=None,
                mfa_enabled=False,
                mfa_confirmed_at=None,
                mfa_last_timestep=None,
            )
        )
        await session.execute(
            delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id)
        )
        await session.commit()


def mfa_available() -> bool:
    """Whether this deployment can enroll accounts (see mfa/secrets.py)."""
    return mfa_configured()
