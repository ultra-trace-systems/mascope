"""
Core authentication configuration including JWT, cookies, and access tokens settings.
"""

import os
import re

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
from mascope_runtime import RuntimeMode


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


def _resolve_cookie_name(base: str, mode: RuntimeMode, env: str) -> str:
    """
    Scope a cookie name to the runtime env, so dev instances on one hostname
    stop clobbering each other's sessions.

    Cookies are not port-scoped (RFC 6265) and ours are set host-only with
    ``Path=/`` and no ``Domain``, so every stack served from one hostname
    shares a single cookie jar: each worktree's dev instance on its own port,
    plus any demo stack, would all read and write the same ``mascope_auth``.
    Each instance signs its JWTs with its own secret, so signing into one
    silently invalidates the session in the others - the app reports a
    successful login and the next ``GET /api/users/me`` answers 401. Appending
    the env name gives every instance a cookie of its own.

    Prod keeps the bare name: renaming it there would sign every user out on
    upgrade, and a deployment owns its hostname anyway. Mode-dependent like
    ``_resolve_cookie_secure`` above.

    Note that this prevents the collision rather than tolerating it. Should two
    cookies of one name ever reach the server anyway, the last one in the header
    wins - Starlette's cookie parser assigns into a dict per chunk - so a stale
    cookie ordered after a valid one authenticates as neither, and reading past
    it would mean replacing that parser and threading several candidate tokens
    through fastapi-users. We only ever set host-only ``Path=/`` cookies, so a
    browser has nothing to duplicate once the names differ.

    :param base: The unsuffixed cookie name, used as-is in prod.
    :param mode: The runtime mode ("dev" or "prod").
    :param env: The active runtime env, e.g. "default" or "wt-my-feature".
    :return: The cookie name to set and read.
    """
    if mode == "prod":
        return base
    # A cookie name is an RFC 6265 token, which excludes separators such as
    # "/" and " ". Envs created through the CLI are already token-safe
    # (``[A-Za-z0-9_-]+``), but MASCOPE_ENV is taken as given, so fold
    # anything outside that set onto "_" rather than emitting a name a
    # browser would reject.
    suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", env or "")
    return f"{base}_{suffix}" if suffix else base


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
    # Name of the authentication cookie. Suffixed with the runtime env in dev
    # so several instances on one hostname keep separate sessions; the prod
    # name stays exactly "mascope_auth" (see _resolve_cookie_name).
    COOKIE_NAME: str = _resolve_cookie_name(
        "mascope_auth", runtime.mode, runtime.env.name
    )
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
    # Env-suffixed in dev alongside COOKIE_NAME, for the same reason.
    MFA_PENDING_COOKIE_NAME: str = _resolve_cookie_name(
        "mascope_mfa_pending", runtime.mode, runtime.env.name
    )

    # Role access levels for RBAC
    # Role names correspond to the role_id values in the database (access_level)
    ROLE_ACCESS_LEVELS: dict = _ROLE_ACCESS_LEVELS  # see mascope_backend.roles

    # Access token settings
    access_token: AccessTokenConfig = AccessTokenConfig()

    # Second-factor settings
    mfa: MfaConfig = MfaConfig()


auth_settings = AuthConfig()
