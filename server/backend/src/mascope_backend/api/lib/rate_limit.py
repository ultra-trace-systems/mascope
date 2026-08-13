"""
Redis-backed, per-client rate limiting for sensitive endpoints.

Exposed as a FastAPI dependency factory so a limit can be attached to routes
we do not own (e.g. the fastapi-users login route) as well as our own, via the
router/route ``dependencies=[...]`` argument. Counting uses the shared async
Redis connection (``redis_storage_client``), so a limit is enforced
consistently across all uvicorn workers rather than per-process.

The window is a simple fixed window: the first request for a key sets the
counter's expiry, and each request increments it until the window rolls over.
This is intentionally lightweight; it is meant to blunt brute-force and
credential-stuffing bursts, not to be a precise quota system.
"""

import hashlib

from fastapi import HTTPException, Request, status

from mascope_backend.runtime import runtime
from mascope_backend.socket.storage import redis_storage_client


def client_ip(request: Request) -> str:
    """
    Best-effort client IP, used for the rate-limit key and the access log.

    Prefer ``X-Real-IP``, which nginx sets to the real peer address and
    overwrites on every request, so a client cannot spoof it (and the backend
    has no host port, so nginx is the only way in). Fall back to the direct
    peer address for setups without the proxy (dev, or a direct connection).

    :param request: Incoming request.
    :return: Client IP string, or ``"unknown"`` if it cannot be determined.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


async def _over_fixed_window(key: str, times: int, seconds: int) -> int | None:
    """
    Increment the fixed-window counter for ``key`` and report whether the
    caller is over the limit.

    :param key: Redis key namespacing this window.
    :param times: Maximum number of hits allowed within the window.
    :param seconds: Window length in seconds.
    :return: ``Retry-After`` seconds if the limit is exceeded, else ``None``.
    :raises Exception: On any Redis error, so callers can fail open.
    """
    client = redis_storage_client.client
    count = await client.incr(key)
    if count == 1:
        # First hit in this window: start the expiry countdown.
        await client.expire(key, seconds)
    if count > times:
        retry_after = await client.ttl(key)
        # ttl can be -1 (no expiry set due to a race); fall back to the full
        # window so the header is always sensible.
        if retry_after is None or retry_after < 0:
            retry_after = seconds
        return retry_after
    return None


def rate_limit(*, times: int, seconds: int, scope: str):
    """
    Build a dependency that allows at most ``times`` requests per ``seconds``
    per client IP for the given ``scope``.

    On limit exceed it raises ``429 Too Many Requests`` with a ``Retry-After``
    header. If Redis is unavailable the check fails open (the request is
    allowed) and logs an error -- the app already requires Redis to function,
    so a Redis outage is a broader failure than the rate limiter, and we prefer
    not to lock every user out on a transient blip.

    :param times: Maximum number of requests allowed within the window.
    :param seconds: Window length in seconds.
    :param scope: Namespacing label so different endpoints count independently.
    :return: An async FastAPI dependency callable.
    """

    async def dependency(request: Request) -> None:
        ip = client_ip(request)
        key = f"mascope:ratelimit:{scope}:{ip}"
        try:
            retry_after = await _over_fixed_window(key, times, seconds)
        except Exception:
            # Fail open on Redis errors, but make the gap visible.
            runtime.logger.exception(f"Rate limit check failed for scope '{scope}'")
            return
        if retry_after is not None:
            # Expected throttling of a noisy client, not a server fault.
            runtime.logger.info(f"Rate limit exceeded for scope '{scope}' from {ip}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait and try again.",
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


def _account_digest(identifier: str) -> str:
    """
    Normalized, hashed form of a submitted login identifier.

    Hashing (rather than embedding the raw string) keeps Redis keys fixed-size
    no matter what an attacker submits as the username, and lets log lines
    reference the account without recording what was typed into the login form
    (which is occasionally a password). Operators can still correlate a digest
    to a known address by hashing its lowercased form.

    :param identifier: Raw identifier from the login form ``username`` field.
    :return: Hex SHA-256 digest of the normalized identifier.
    """
    account = identifier.strip().lower()
    return hashlib.sha256(account.encode("utf-8")).hexdigest()


def _account_limit_key(identifier: str, scope: str) -> str:
    """Redis key for the per-account login counter."""
    return f"mascope:ratelimit:{scope}:{_account_digest(identifier)}"


def rate_limit_login_by_account(
    *, times: int, seconds: int, scope: str = "auth-login-account"
):
    """
    Build a dependency that limits login attempts **per account**, keyed on the
    submitted login identifier (the form ``username`` field).

    This complements the per-IP :func:`rate_limit`: on its own the per-IP limit
    does not stop a distributed attack that guesses one account's password from
    many rotating IPs. Keying on the account blunts that.

    The counter effectively tracks only *failed* attempts: a successful login
    clears it via :func:`clear_login_rate_limit` (wired into the user manager's
    ``on_after_login``). This keeps a user's own routine logins from consuming
    the budget and stops an attacker from locking a known address out
    indefinitely with a trickle of bogus attempts - after the real user signs
    in, the window resets.

    No-ops when the request carries no form identifier (e.g. logout, which
    shares the auth router), and fails open on Redis errors like
    :func:`rate_limit`.

    :param times: Maximum login attempts allowed per account within the window.
    :param seconds: Window length in seconds.
    :param scope: Namespacing label for the Redis keys.
    :return: An async FastAPI dependency callable.
    """

    async def dependency(request: Request) -> None:
        try:
            form = await request.form()
        except Exception:
            # Not a form-encoded request (nothing to key on); let it through.
            return
        identifier = form.get("username")
        if not identifier:
            return
        key = _account_limit_key(str(identifier), scope)
        try:
            retry_after = await _over_fixed_window(key, times, seconds)
        except Exception:
            runtime.logger.exception("Account login rate limit check failed")
            return
        if retry_after is not None:
            # Expected throttling of a noisy client, not a server fault. Log
            # the digest, never the submitted identifier (see _account_digest).
            runtime.logger.info(
                "Login rate limit exceeded for account "
                f"sha256:{_account_digest(str(identifier))[:12]}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Too many login attempts for this account. "
                    "Please wait and try again."
                ),
                headers={"Retry-After": str(retry_after)},
            )

    return dependency


async def clear_login_rate_limit(
    identifier: str, *, scope: str = "auth-login-account"
) -> None:
    """
    Reset the per-account login counter after a successful login.

    Called from the user manager's ``on_after_login`` so the limiter counts
    only failed attempts. Best-effort: the counter self-expires anyway, so a
    Redis error here is logged and swallowed rather than failing the login.

    :param identifier: The login identifier as submitted (normalized and
        hashed the same way as the limiter keys on it).
    :param scope: Namespacing label; must match the limiter's ``scope``.
    """
    try:
        await redis_storage_client.client.delete(_account_limit_key(identifier, scope))
    except Exception:
        runtime.logger.exception("Failed to clear account login rate limit")
