"""
Core authentication configuration including JWT, cookies, and access tokens settings.
"""

import os

from pydantic import BaseModel

from mascope_backend.api.new.auth.access_token.config import AccessTokenConfig
from mascope_backend.api.new.auth.mfa.config import MfaConfig
from mascope_backend.api.new.auth.secrets import jwt_secret_key
from mascope_backend.roles import ROLE_ACCESS_LEVELS as _ROLE_ACCESS_LEVELS
from mascope_backend.runtime import runtime

# Domain-separated per-deployment secrets (reset, verification) derive from the
# JWT secret so the values are unique and unforgeable without extra secret
# files. The derivation lives in mascope_backend.service_token - a leaf module
# the file-converter process can import without the backend app's import graph.
from mascope_backend.service_token import derive_token_secret as _derive_token_secret


def _resolve_cookie_secure() -> bool:
    """
    Whether the auth cookie is marked ``Secure`` (sent only over HTTPS).

    Defaults to ``True`` in prod mode and ``False`` in dev. Override with the
    ``MASCOPE_COOKIE_SECURE`` env var to support an HTTP-only deployment on
    ``localhost`` (loopback is a browser "secure context", so cookie auth works
    over plain HTTP there). Do NOT disable this for network-reachable
    deployments -- serve those over HTTPS instead.

    :return: ``True`` to set the Secure cookie flag.
    """
    override = os.environ.get("MASCOPE_COOKIE_SECURE")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    return runtime.mode == "prod"


# HS256 signs with the raw secret bytes; RFC 7518 requires a key at least as long
# as the hash output (32 bytes for SHA-256). Warn rather than fail so an existing
# deployment with a short key keeps running, but the operator is told to rotate.
_MIN_JWT_SECRET_BYTES = 32
if len(jwt_secret_key.encode("utf-8")) < _MIN_JWT_SECRET_BYTES:
    runtime.logger.warning(
        f"JWT secret key is shorter than {_MIN_JWT_SECRET_BYTES} bytes, which is "
        "below the RFC 7518 minimum for HS256. Regenerate it with "
        "`head -c 32 /dev/urandom | xxd -p -c 32 > .runtime/secrets/jwt_secret_key.txt` "
        "and restart. Note: rotating the secret invalidates existing sessions."
    )


# TODO_configuration for auth
class AuthConfig(BaseModel):
    """
    Configuration settings related to user authentication and secrets.
    Should be securely stored in the environment variables
    """

    # Main JWT Token settings for user authentication
    JWT_SECRET_KEY: str = (
        jwt_secret_key  # PRIVATE_KEY used for signing and verifying JWT tokens
    )
    # Token lifetime - 7 days in seconds (JWT expiration). The JWT is stateless
    # and cannot be revoked server-side, so this bounds how long a stolen token
    # or cookie stays valid. Users re-authenticate weekly.
    JWT_EXPIRATION_SECONDS: int = 7 * 24 * 60 * 60
    JWT_AUDIENCE: list = ["mascope-users:auth"]  # Audience claim for token validation
    JWT_ALGORITHM: str = (
        "HS256"  # Algorithm used for signing the JWT (HMAC with SHA-256)
    )

    # Cookie settings for web-based JWT storage
    COOKIE_NAME: str = "mascope_auth"  # Name of the authentication cookie
    # Lifetime of the cookie - 7 days in seconds (matches JWT expiration)
    COOKIE_MAX_AGE_SECONDS: int = 7 * 24 * 60 * 60
    COOKIE_SECURE: bool = (
        _resolve_cookie_secure()
    )  # send cookies only over HTTPS; prod default, override via MASCOPE_COOKIE_SECURE
    COOKIE_HTTP_ONLY: bool = (
        True  # Set cookies as HTTPOnly to prevent access from JavaScript
    )
    # SameSite policy for the auth cookie. "lax" is not sent on cross-site POST/
    # PATCH/DELETE, which covers our state-changing routes and mitigates CSRF.
    # Set explicitly rather than relying on the transport library's default.
    COOKIE_SAMESITE: str = "lax"

    # Password reset token settings. Secret derived per-deployment from the JWT
    # secret (see _derive_token_secret) rather than a hardcoded constant.
    RESET_PASSWORD_TOKEN_SECRET: str = _derive_token_secret("reset-password")
    RESET_PASSWORD_TOKEN_LIFETIME_SECONDS: int = (
        3600  # Expiration time for reset tokens
    )
    RESET_PASSWORD_TOKEN_AUDIENCE: str = (
        "mascope-users:reset"  # Audience for password reset tokens
    )

    # Email verification token settings. Secret derived per-deployment (see above).
    VERIFICATION_TOKEN_SECRET: str = _derive_token_secret("email-verification")
    VERIFICATION_TOKEN_LIFETIME_SECONDS: int = (
        3600  # Expiration time for email verification tokens
    )
    VERIFICATION_TOKEN_AUDIENCE: str = (
        "mascope-users:verify"  # Audience for email verification tokens
    )

    # Signing secret for the token that carries a half-finished login between
    # the password step and the code step. Derived per-deployment (see above).
    # Safe to derive from the JWT secret, unlike the seed encryption key in
    # mfa/secrets.py: these tokens live five minutes, so rotating the JWT secret
    # costs at most an interrupted sign-in.
    MFA_PENDING_TOKEN_SECRET: str = _derive_token_secret("mfa-pending")
    # Cookie carrying that token. Separate name from COOKIE_NAME so
    # get_enabled_backends, which selects the session backend on the presence of
    # the auth cookie, can never mistake one for the other.
    MFA_PENDING_COOKIE_NAME: str = "mascope_mfa_pending"

    # Role access levels for RBAC
    # Role names correspond to the role_id values in the database (access_level)
    ROLE_ACCESS_LEVELS: dict = _ROLE_ACCESS_LEVELS  # see mascope_backend.roles

    # Access token settings
    access_token: AccessTokenConfig = AccessTokenConfig()

    # Second-factor settings
    mfa: MfaConfig = MfaConfig()


auth_settings = AuthConfig()
