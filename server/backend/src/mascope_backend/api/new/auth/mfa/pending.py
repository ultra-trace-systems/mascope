"""
The short-lived token that carries a half-finished login between the password
step and the code step.

It is not a session and no route accepts it as one: it is signed with its own
secret, stamped with its own audience, and only ``/api/auth/mfa/verify`` reads
it. The session cookie does not exist until that route mints one, which is what
keeps every other surface - REST dependencies, Socket.IO, the role checks -
unchanged and unreachable by a caller who has only passed the first step.
"""

import secrets as _secrets
from typing import Any

import jwt
from fastapi_users.jwt import decode_jwt, generate_jwt

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.mfa.exceptions import InvalidPendingTokenException
from mascope_backend.runtime import runtime
from mascope_backend.socket.storage import redis_storage_client


mfa_settings = auth_settings.mfa


def _burn_key(jti: str) -> str:
    return f"mascope:mfa:pending:burned:{jti}"


def _attempt_key(jti: str) -> str:
    return f"mascope:mfa:pending:attempts:{jti}"


def create_pending_token(user_id: int) -> str:
    """
    Mint a token attesting that the password step passed for ``user_id``.

    :param user_id: The account that authenticated.
    :return: Encoded JWT.
    """
    return generate_jwt(
        data={
            "sub": str(user_id),
            "jti": _secrets.token_urlsafe(16),
            "aud": mfa_settings.PENDING_TOKEN_AUDIENCE,
        },
        secret=auth_settings.MFA_PENDING_TOKEN_SECRET,
        lifetime_seconds=mfa_settings.PENDING_TOKEN_LIFETIME_SECONDS,
    )


def _decode(token: str) -> dict[str, Any]:
    """
    Decode and validate a pending token's signature, audience, and expiry.

    :param token: The encoded token.
    :raises InvalidPendingTokenException: On any decoding or validation failure.
    :return: The token's claims.
    """
    try:
        return decode_jwt(
            token,
            auth_settings.MFA_PENDING_TOKEN_SECRET,
            [mfa_settings.PENDING_TOKEN_AUDIENCE],
        )
    except jwt.PyJWTError as e:
        raise InvalidPendingTokenException() from e


async def _is_burned(jti: str) -> bool:
    """
    Whether this token has already been spent or locked out.

    Fails open on a Redis error, like the rate limiter: single use is defence in
    depth here rather than the primary control, because spending the token a
    second time still requires a TOTP code, and ``User.mfa_last_timestep``
    refuses a code that has already been accepted. An attacker who could produce
    a second valid code would not need the token.

    :param jti: The token's unique id.
    :return: ``True`` if the token must be refused.
    """
    try:
        return await redis_storage_client.client.exists(_burn_key(jti)) > 0
    except Exception:
        runtime.logger.exception("MFA pending token burn check failed")
        return False


async def burn_pending_token(jti: str) -> None:
    """
    Spend a pending token so it cannot be presented again.

    :param jti: The token's unique id.
    """
    try:
        await redis_storage_client.client.set(
            _burn_key(jti),
            "1",
            ex=mfa_settings.PENDING_TOKEN_LIFETIME_SECONDS,
        )
        await redis_storage_client.client.delete(_attempt_key(jti))
    except Exception:
        runtime.logger.exception("Failed to burn MFA pending token")


async def register_failed_attempt(jti: str) -> None:
    """
    Count a wrong code against this token, burning it once too many accumulate.

    Bounds guessing against one password-verified attempt at roughly the odds of
    a lucky first guess, and forces an attacker back through the password step -
    where the per-IP and per-account login limits apply - rather than letting one
    stolen password fund an unbounded code search.

    :param jti: The token's unique id.
    """
    try:
        attempts = await redis_storage_client.client.incr(_attempt_key(jti))
        if attempts == 1:
            await redis_storage_client.client.expire(
                _attempt_key(jti), mfa_settings.PENDING_TOKEN_LIFETIME_SECONDS
            )
        if attempts >= mfa_settings.PENDING_TOKEN_MAX_ATTEMPTS:
            await burn_pending_token(jti)
    except Exception:
        runtime.logger.exception("Failed to record MFA pending token attempt")


async def resolve_pending_token(token: str | None) -> tuple[int, str]:
    """
    Validate a pending token and report whose half-finished login it carries.

    :param token: The encoded token, or ``None`` when the cookie was absent.
    :raises InvalidPendingTokenException: If it is missing, malformed, expired,
        for another audience, or already spent.
    :return: The account id and the token's unique id.
    """
    if not token:
        raise InvalidPendingTokenException()

    claims = _decode(token)
    jti = claims.get("jti")
    subject = claims.get("sub")
    if not jti or not subject:
        raise InvalidPendingTokenException()

    if await _is_burned(jti):
        raise InvalidPendingTokenException()

    try:
        user_id = int(subject)
    except (TypeError, ValueError) as e:
        raise InvalidPendingTokenException() from e

    return user_id, jti
