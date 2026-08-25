"""
Short-lived cache for successful service-token validations.

A resumable upload is one request per chunk, and every one of them revalidates
the same token: database sessions on paths that take no admission-control
permit (see :mod:`mascope_backend.db`). A bulk upload run therefore turns into
a stream of identical lookups that between them can exhaust the connection
pool - which is what happened in production, stalling a worker for a minute.

Caching for a few seconds collapses that stream into one lookup per token per
window. Two kinds of entry are held, and they differ in what they cost:

- ``auth-context`` - a token's ``(service_name, device_id, created_at)``,
  resolved through
  :func:`mascope_backend.api.new.auth.access_token.util.resolve_token_context`
  and used by both authenticated paths. This one defers nothing, because those
  three columns are written when the token is minted and no request path
  updates them afterwards, and because every caller still re-establishes what
  the row's existence buys: the HTTP bearer path authenticates the token
  through the auth backend on every request, and ``validate_service_access_token``
  has already read the row through ``read_token`` before it asks for the
  context. See :func:`get_auth_context`.
- ``user`` - a validated ``User`` row from ``validate_service_access_token``:
  the Socket.IO path, and the liveness probe in
  ``access_token.service.get_access_token`` that decides whether to hand back
  an existing converter token or mint a replacement - so that decision comes
  out of this window too. **This one is stale for up to the TTL, in two
  ways.** The token itself: a deleted token keeps authenticating until the
  entry lapses, and several things delete tokens - unpairing a device, an
  administrator clearing a user's credentials, and any password write (see
  ``UserManager.on_after_update``). And the row's own fields: ``role_id`` is
  read straight off the cached object by ``verify_role_permission`` and
  ``save_user_session``, so a role change lands late even when no token was
  revoked - a demotion deletes only the file-converter token, and only across
  the editor boundary, so agent and SDK tokens survive it. That matters more
  than the TTL alone suggests, because ``save_user_session`` copies ``role_id``
  into the Redis socket session, which lives far longer than this window, and
  ``socket_auth`` authorises from it without reading the database again.

  Deliberately not claimed: that this defers account deactivation. It does not.
  ``is_active`` is never consulted on the Socket.IO service-token path, cached
  or not - it is enforced by the ``current_user(active=True)`` HTTP dependency.
  That gap is real, and it is not this cache's.

  The cached ``User`` is detached: its session has closed by the time the entry
  is stored. ``expire_on_commit`` is off, so the columns loaded stay readable,
  but a lazy relationship (``User.role``) would raise on access - and only on a
  cache hit, which is a failure that hides behind the window.

The cache is per worker and holds no cross-worker invalidation, so any window
applies to each worker independently. It is deliberately seconds, not minutes,
and setting the TTL to 0 disables caching entirely.

Only successes are cached. A rejection must stay cheap to reverse: tokens are
minted on demand (a machine account's file-converter token is created at its
first upload), so a negative result pinned even briefly would refuse a token
that had just come into existence. It is also what keeps the window between a
token's INSERT and the UPDATE that labels it harmless - a row read in between
has no service name, is refused, and the refusal is not remembered.
"""

import hashlib
import time
from typing import Any

from mascope_backend.api.new.auth.access_token.config import AccessTokenConfig


#: Cap on distinct cached entries per worker. Both namespaces share it, and a
#: token in use on both paths occupies two slots, so what reaches the cap is
#: simply a deployment with many tokens live at once rather than anything
#: pathological. Eviction is bounded work and keeps a worker's memory finite;
#: overflowing it costs a database read, not a refusal.
_MAX_ENTRIES = 1024

#: Namespace for a validated user (the Socket.IO validation path).
USER = "user"

#: Namespace for a token's ``(service_name, device_id, created_at)`` tuple
#: (both authenticated paths, through ``util.resolve_token_context``).
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

    The NUL delimiter is what makes the three components unambiguous, and it
    holds because neither a namespace (a constant in this module) nor a service
    name (checked against the token's own row before anything is stored, so
    always one of ``ALLOWED_SERVICES``) can contain one. That matters: on a
    ``user`` hit the caller returns before any service check, so this key is
    the only thing separating one service's acceptance from another's.

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
    # The field itself is bounded (see AccessTokenConfig), but these functions
    # take anything carrying the attribute, so the clamp stays.
    return max(0.0, float(config.SERVICE_TOKEN_CACHE_TTL_SECONDS))


def get(
    token: str,
    service_name: str,
    config: AccessTokenConfig,
    *,
    namespace: str = USER,
) -> Any | None:
    """
    The cached value for this token, if the entry is still fresh.

    ``namespace`` is keyword-only: it selects which of two very different
    guarantees the caller gets, and it must never be something that arrived in
    a positional slot by accident.

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
    *,
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
        # Caching is off, so nothing new is stored - and nothing already stored
        # may linger. get() short-circuits on the TTL as well, so an entry left
        # here would never be read again and never swept: a User row, password
        # hash and MFA secret included, pinned for the life of the worker.
        _entries.clear()
        return
    # Sweep first, on every write. Entries expire lazily on read, so one that
    # is never looked up again would otherwise sit here indefinitely - and a
    # user entry holds a User row, which carries the password hash and the MFA
    # secret. Bounded work: the table is capped, and this runs only on a miss.
    _evict_expired()
    key = _key(token, service_name, namespace)
    # Re-insert rather than overwrite: assigning to an existing key keeps its
    # original position, so anything re-cached inside its window would hold the
    # place of its first use and be the first thing FIFO evicts. Defensive
    # today - both callers write only on a miss, and a miss means the entry was
    # already gone - but the alternative is a rule about caller behaviour that
    # this module cannot see being broken.
    _entries.pop(key, None)
    if len(_entries) >= _MAX_ENTRIES:
        # The cheap sweep stops at the first live entry, so a dead one sitting
        # behind it survives. At the cap the alternative is discarding
        # something live, which is worth one full pass over a bounded table.
        _evict_all_expired()
    if len(_entries) >= _MAX_ENTRIES:
        # Genuinely full: drop the oldest insertion.
        _entries.pop(next(iter(_entries)), None)
    _entries[key] = (time.monotonic() + ttl, value)


def get_auth_context(token: str, config: AccessTokenConfig) -> Any | None:
    """
    A token's cached ``(service_name, device_id, created_at)``, if fresh.

    Unlike a cached user, this carries no revocation delay of its own. Both
    callers go on to establish that the token is still live: the HTTP bearer
    path authenticates it through the auth backend, which reads the token row
    and its expiry on every request, and the Socket.IO path has already read
    the row through ``read_token`` before it asks for the context. A deleted or
    expired token is therefore still refused immediately. What the window
    defers is only a change to the three columns themselves, and nothing
    updates those after a token is minted.

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
    """
    Drop the expired entries at the front of the table.

    One process holds one ``AccessTokenConfig``, so every entry is written with
    the same TTL and insertion order is expiry order: the expired ones are a
    prefix. Sweeping that prefix and stopping at the first live entry costs one
    dict operation per entry actually reclaimed, rather than a scan of the
    whole table on every write.

    Sound but incomplete when TTLs do differ: it never drops a live entry, and
    a short-lived entry sitting behind a long-lived one is simply reached
    later - by :func:`get`, which checks expiry itself, or by
    :func:`_evict_all_expired` at the cap.
    """
    now = time.monotonic()
    while _entries:
        key = next(iter(_entries))
        if now < _entries[key][0]:
            return
        del _entries[key]


def _evict_all_expired() -> None:
    """
    Drop every expired entry, wherever it sits.

    The cap is the one place a full pass earns its cost: it is where the
    alternative is discarding a live entry.
    """
    now = time.monotonic()
    for key in [k for k, (expires_at, _) in _entries.items() if now >= expires_at]:
        del _entries[key]


def clear() -> None:
    """
    Forget every cached entry.

    Called only from tests. No revocation path invokes it, and none usefully
    could: the cache is per worker with no cross-worker invalidation, so
    clearing it in the worker that served the revocation leaves every other
    worker's copy standing. The TTL is the entire bound on how long a revoked
    credential keeps working - not a backstop behind an eviction that happens
    sooner.
    """
    _entries.clear()
