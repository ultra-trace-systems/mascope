"""
Step-up verification for actions a session alone should not authorize.

The second factor gates sign-in, but some routes hand out credentials that
outlive the session that requested them - an access token is valid for a year
and is not tied to the browser it was minted in. Without a check here, one
stolen session buys a permanent credential and the second factor is a formality
after that.

So those routes ask that a code was presented recently, not merely that a
session exists. Completing a sign-in counts, which is what keeps the common
path free of a second prompt: a user who just signed in with a code can mint a
token without entering another.

Accounts with no second factor are unaffected. There is nothing to present, and
refusing them would disable token minting for every account on a deployment that
does not use MFA.
"""

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.mfa.exceptions import MfaReauthRequiredException
from mascope_backend.db import User
from mascope_backend.runtime import runtime
from mascope_backend.socket.storage import redis_storage_client


def _key(user_id: int) -> str:
    return f"mascope:mfa:reauth:{user_id}"


async def mark_recently_verified(user_id: int) -> None:
    """
    Record that this account just presented a code.

    Best-effort: on a Redis error the marker is simply absent, and the user is
    asked for a code when they reach a route that needs one. That is the safe
    direction to fail.

    :param user_id: The account that presented the code.
    """
    try:
        await redis_storage_client.client.set(
            _key(user_id),
            "1",
            ex=auth_settings.mfa.REAUTH_WINDOW_SECONDS,
        )
    except Exception:
        runtime.logger.exception("Failed to record MFA step-up verification")


async def require_recent_mfa(user: User) -> None:
    """
    Refuse unless this account presented a code within the step-up window.

    Fails **closed** on a Redis error, unlike the rate limiters in
    ``api/lib/rate_limit.py``. Those blunt abuse and are safe to skip when the
    store is unreachable; this one decides whether to hand out a long-lived
    credential, and answering "allow" when the record cannot be read would make
    a Redis outage into the bypass this exists to close. The cost of failing
    closed is that token minting is unavailable during such an outage, which the
    application does not survive anyway.

    :param user: The authenticated account.
    :raises MfaReauthRequiredException: When a fresh code is owed.
    """
    if not user.mfa_enabled:
        return

    try:
        recent = await redis_storage_client.client.exists(_key(user.id)) > 0
    except Exception:
        runtime.logger.exception(
            "MFA step-up check failed; refusing the action rather than allowing it"
        )
        raise MfaReauthRequiredException() from None

    if not recent:
        raise MfaReauthRequiredException()


async def clear_recent_verification(user_id: int) -> None:
    """
    Drop the marker, so the next sensitive action asks again.

    :param user_id: The account whose marker is being cleared.
    """
    try:
        await redis_storage_client.client.delete(_key(user_id))
    except Exception:
        runtime.logger.exception("Failed to clear MFA step-up verification")
