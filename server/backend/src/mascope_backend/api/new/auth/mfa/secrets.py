"""
The deployment's MFA encryption key.

Read lazily rather than at import, and treated as optional: a deployment that
predates MFA has no such file, and the application must still start. Enrollment
is refused with a clear message until an operator creates it (see
``docs/hosting.md``); nothing else is affected.

Deliberately NOT derived from the JWT secret, unlike the reset and verification
secrets in ``api/new/auth/config.py``. Those protect tokens that live an hour;
a TOTP seed lives for years, and the JWT secret is rotatable - deriving from it
would turn a routine rotation into an unrecoverable lockout of every enrolled
account at once.
"""

from mascope_backend.runtime import runtime


#: Memoized key material. Only a successful read is cached, so an operator who
#: creates the file on a running deployment does not have to restart it.
_cached_key: str | None = None


def mfa_encryption_key() -> str | None:
    """
    The raw MFA key material for this deployment.

    :return: The secret's contents, or ``None`` when the deployment has no MFA
        key file.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    try:
        _cached_key = runtime.secret(
            "MFA_ENCRYPTION_KEY_FILE", "mfa_encryption_key.txt"
        )
    except FileNotFoundError:
        return None
    return _cached_key


def mfa_configured() -> bool:
    """Whether this deployment can store TOTP seeds."""
    return mfa_encryption_key() is not None
