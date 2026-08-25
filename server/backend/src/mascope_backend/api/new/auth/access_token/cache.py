"""
Short-lived cache for successful service-token validations.

A resumable upload is one request per chunk, and every one of them revalidates
the same token: two database sessions each, on a path that takes no
admission-control permit (see :mod:`mascope_backend.db`). A bulk upload run
therefore turns into a stream of identical validations that between them can
exhaust the connection pool - which is what happened in production, stalling a
worker for a minute.

Caching the outcome for a few seconds collapses that stream into one
validation per token per window, at a cost worth stating plainly:

**Revocation is delayed by up to the TTL.** Deleting a token - unpairing a
device, or clearing a user's credentials - stops being effective immediately
and becomes effective within the window instead. The cache is per worker and
holds no cross-worker invalidation, so the window applies to each worker
independently. It is deliberately seconds, not minutes, and setting the TTL to
0 disables caching entirely.

Only successes are cached. A rejection must stay cheap to reverse: tokens are
minted on demand (a machine account's file-converter token is created at its
first upload), so a negative result pinned even briefly would refuse a token
that had just come into existence.
"""

import hashlib
import time
from typing import Any

from mascope_backend.api.new.auth.access_token.config import AccessTokenConfig


#: Cap on distinct cached tokens per worker. Reached only by something
#: pathological - a scanner cycling tokens - and bounded eviction is what keeps
#: that from growing the worker's memory without limit.
_MAX_ENTRIES = 1024

#: key -> (expires_at_monotonic, user)
_entries: dict[str, tuple[float, Any]] = {}


def _key(token: str, service_name: str) -> str:
    """
    Cache key for a token/service pair.

    The token is hashed rather than stored: the cache is an in-memory dict that
    ends up in tracebacks, debuggers and heap dumps, and a bearer token there is
    a credential in the clear. The service name is part of the key because the
    same token is refused for a service it is not scoped to, and that refusal
    must not be cacheable as an acceptance for another.

    :param token: The bearer token.
    :param service_name: The service the token is being validated for.
    :return: An opaque key.
    :rtype: str
    """
    return hashlib.sha256(f"{service_name}\x00{token}".encode()).hexdigest()


def _ttl(config: AccessTokenConfig) -> float:
    return max(0.0, float(config.SERVICE_TOKEN_CACHE_TTL_SECONDS))


def get(token: str, service_name: str, config: AccessTokenConfig) -> Any | None:
    """
    The cached user for this token/service pair, if the entry is still fresh.

    :param token: The bearer token.
    :param service_name: The service the token is being validated for.
    :param config: Access-token settings carrying the TTL.
    :return: The validated user, or None on a miss, an expired entry, or when
        caching is disabled.
    """
    if _ttl(config) <= 0:
        return None
    entry = _entries.get(_key(token, service_name))
    if entry is None:
        return None
    expires_at, user = entry
    if time.monotonic() >= expires_at:
        # Drop it here rather than leaving it to eviction, so a token that has
        # stopped being used stops occupying a slot.
        _entries.pop(_key(token, service_name), None)
        return None
    return user


def put(token: str, service_name: str, user: Any, config: AccessTokenConfig) -> None:
    """
    Remember a successful validation for the configured window.

    :param token: The bearer token.
    :param service_name: The service the token was validated for.
    :param user: The validated user to return on a hit.
    :param config: Access-token settings carrying the TTL.
    """
    ttl = _ttl(config)
    if ttl <= 0:
        return
    # Sweep first, on every write. Entries expire lazily on read, so one that
    # is never looked up again would otherwise sit here indefinitely - and it
    # holds a User row, which carries the password hash and the MFA secret.
    # Bounded work: the table is capped, and this runs only on a cache miss.
    _evict_expired()
    key = _key(token, service_name)
    # Re-insert rather than overwrite: assigning to an existing key keeps its
    # original position, so a token refreshed on every chunk would keep the
    # place of its first use and be the first thing FIFO evicts.
    _entries.pop(key, None)
    if len(_entries) >= _MAX_ENTRIES:
        # Still full after the sweep: drop the oldest insertion.
        _entries.pop(next(iter(_entries)), None)
    _entries[key] = (time.monotonic() + ttl, user)


def _evict_expired() -> None:
    now = time.monotonic()
    for key in [k for k, (expires_at, _) in _entries.items() if now >= expires_at]:
        _entries.pop(key, None)


def clear() -> None:
    """Forget every cached validation. Used by tests, and after a revocation."""
    _entries.clear()
