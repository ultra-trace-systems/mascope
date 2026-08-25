"""
Short-lived cache for successful service-token validations.

A resumable upload is one request per chunk, and every one of them revalidates
the same token: database sessions on paths that take no admission-control
permit (see :mod:`mascope_backend.db`). A bulk upload run therefore turns into
a stream of identical lookups that between them can exhaust the connection
pool - which is what happened in production, stalling a worker for a minute.

Caching for a few seconds collapses that stream into one lookup per token per
window. Two kinds of entry are held, and they differ in what they cost:

- ``user`` - a validated user from ``validate_service_access_token``, the
  Socket.IO path. **Revocation is delayed by up to the TTL**: deleting a token
  stops being effective immediately and becomes effective within the window
  instead.
- ``auth-context`` - a token's ``(service_name, device_id, created_at)``, used
  by the HTTP bearer path. This one carries no revocation delay of its own,
  because that path still authenticates the token through the auth backend on
  every request; see :func:`get_auth_context`.

The cache is per worker and holds no cross-worker invalidation, so any window
applies to each worker independently. It is deliberately seconds, not minutes,
and setting the TTL to 0 disables caching entirely.

Only successes are cached. A rejection must stay cheap to reverse: tokens are
minted on demand (a machine account's file-converter token is created at its
first upload), so a negative result pinned even briefly would refuse a token
that had just come into existence.
"""

import hashlib
import time
from typing import Any

from mascope_backend.api.new.auth.access_token.config import AccessTokenConfig


#: Cap on distinct cached entries per worker. Reached only by something
#: pathological - a scanner cycling tokens - and bounded eviction is what keeps
#: that from growing the worker's memory without limit.
_MAX_ENTRIES = 1024

#: Namespace for a validated user (the Socket.IO validation path).
USER = "user"

#: Namespace for a token's ``(service_name, device_id, created_at)`` tuple
#: (the HTTP bearer path).
AUTH_CONTEXT = "auth-context"

#: key -> (expires_at_monotonic, value)
_entries: dict[str, tuple[float, Any]] = {}


def _key(token: str, service_name: str, namespace: str) -> str:
    """
    Cache key for a token, scoped to a service and an entry kind.

    The token is hashed rather than stored: the cache is an in-memory dict that
    ends up in tracebacks, debuggers and heap dumps, and a bearer token there is
    a credential in the clear. The service name is part of the key because the
    same token is refused for a service it is not scoped to, and that refusal
    must not be cacheable as an acceptance for another. The namespace keeps the
    two kinds of entry apart, so a hit of one can never be returned where the
    other is expected.

    :param token: The bearer token.
    :param service_name: The service the token is being validated for, or an
        empty string for entries that are not service-scoped.
    :param namespace: The kind of entry.
    :return: An opaque key.
    :rtype: str
    """
    material = "\x00".join((namespace, service_name, token))
    return hashlib.sha256(material.encode()).hexdigest()


def _ttl(config: AccessTokenConfig) -> float:
    return max(0.0, float(config.SERVICE_TOKEN_CACHE_TTL_SECONDS))


def get(
    token: str,
    service_name: str,
    config: AccessTokenConfig,
    namespace: str = USER,
) -> Any | None:
    """
    The cached value for this token, if the entry is still fresh.

    :param token: The bearer token.
    :param service_name: The service the token is being validated for.
    :param config: Access-token settings carrying the TTL.
    :param namespace: The kind of entry.
    :return: The cached value, or None on a miss, an expired entry, or when
        caching is disabled.
    """
    if _ttl(config) <= 0:
        return None
    key = _key(token, service_name, namespace)
    entry = _entries.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() >= expires_at:
        # Drop it here rather than leaving it to eviction, so a token that has
        # stopped being used stops occupying a slot.
        _entries.pop(key, None)
        return None
    return value


def put(
    token: str,
    service_name: str,
    value: Any,
    config: AccessTokenConfig,
    namespace: str = USER,
) -> None:
    """
    Remember a successful lookup for the configured window.

    :param token: The bearer token.
    :param service_name: The service the token was validated for.
    :param value: What to return on a hit.
    :param config: Access-token settings carrying the TTL.
    :param namespace: The kind of entry.
    """
    ttl = _ttl(config)
    if ttl <= 0:
        return
    # Sweep first, on every write. Entries expire lazily on read, so one that
    # is never looked up again would otherwise sit here indefinitely - and a
    # user entry holds a User row, which carries the password hash and the MFA
    # secret. Bounded work: the table is capped, and this runs only on a miss.
    _evict_expired()
    key = _key(token, service_name, namespace)
    # Re-insert rather than overwrite: assigning to an existing key keeps its
    # original position, so a token refreshed on every chunk would keep the
    # place of its first use and be the first thing FIFO evicts.
    _entries.pop(key, None)
    if len(_entries) >= _MAX_ENTRIES:
        # Still full after the sweep: drop the oldest insertion.
        _entries.pop(next(iter(_entries)), None)
    _entries[key] = (time.monotonic() + ttl, value)


def get_auth_context(token: str, config: AccessTokenConfig) -> Any | None:
    """
    A token's cached ``(service_name, device_id, created_at)``, if fresh.

    Unlike a cached user, this carries no revocation delay of its own. The
    caller that uses it - ``get_enabled_backends`` on the HTTP bearer path -
    goes on to authenticate the token through the auth backend, which reads the
    token row and its expiry on every request. A deleted or expired token is
    therefore still refused immediately. What the window defers is only a
    change to the token's own device binding, and unpairing a device deletes
    its tokens anyway.

    :param token: The bearer token.
    :param config: Access-token settings carrying the TTL.
    :return: The cached context tuple, or None.
    """
    return get(token, "", config, namespace=AUTH_CONTEXT)


def put_auth_context(token: str, context: Any, config: AccessTokenConfig) -> None:
    """
    Remember a token's auth context for the configured window.

    :param token: The bearer token.
    :param context: ``(service_name, device_id, created_at)``.
    :param config: Access-token settings carrying the TTL.
    """
    put(token, "", context, config, namespace=AUTH_CONTEXT)


def _evict_expired() -> None:
    now = time.monotonic()
    for key in [k for k, (expires_at, _) in _entries.items() if now >= expires_at]:
        _entries.pop(key, None)


def clear() -> None:
    """Forget every cached entry. Used by tests, and after a revocation."""
    _entries.clear()
