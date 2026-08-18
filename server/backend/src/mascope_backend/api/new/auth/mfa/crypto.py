"""
Encryption of TOTP seeds at rest.

The threat this addresses is a database dump reaching someone who does not have
the host: a leaked backup, a copied volume, an injection that reads rows. The
key lives in the deployment's secrets directory, outside the database and
outside its backups, so a dump on its own yields no working seeds. It is not a
defence against a compromised host, where the key is readable anyway.
"""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from mascope_backend.api.new.auth.mfa.exceptions import MfaNotConfiguredException
from mascope_backend.api.new.auth.mfa.secrets import mfa_encryption_key


def _fernet() -> Fernet:
    """
    Build the Fernet cipher for this deployment.

    Fernet wants 32 urlsafe-base64 bytes; the secret file holds arbitrary
    high-entropy text, so it is condensed through HMAC-SHA256 under a fixed
    label. The label domain-separates this use from any later one that reads
    the same file.

    :raises MfaNotConfiguredException: When the deployment has no MFA key.
    :return: A cipher bound to this deployment's key.
    """
    key = mfa_encryption_key()
    if key is None:
        raise MfaNotConfiguredException()
    digest = hmac.new(key.encode("utf-8"), b"totp-secret", hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    """
    Encrypt a base32 TOTP seed for storage.

    :param plaintext: The base32 seed.
    :raises MfaNotConfiguredException: When the deployment has no MFA key.
    :return: Ciphertext safe to store in ``user.mfa_secret``.
    """
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str | None:
    """
    Recover a stored TOTP seed.

    Returns ``None`` rather than raising when the stored value cannot be
    decrypted, which is what a key that was replaced or lost looks like. The
    caller turns that into "this account cannot complete MFA" and points the
    user at recovery, instead of a 500 on every login attempt.

    :param ciphertext: The stored value from ``user.mfa_secret``.
    :raises MfaNotConfiguredException: When the deployment has no MFA key.
    :return: The base32 seed, or ``None`` if it cannot be decrypted.
    """
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
