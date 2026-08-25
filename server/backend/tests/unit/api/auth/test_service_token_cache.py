"""The short-lived cache in front of service-token validation.

It exists because a resumable upload revalidates the same token once per
chunk, and each validation costs database connections on a path with no
admission control. What it trades away is immediacy of revocation, so the
window has to be small, bounded, and genuinely switchable off - which is what
these tests hold it to.
"""

import time

import pytest

from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token.config import AccessTokenConfig


TOKEN = "tok-abc"
SERVICE = "file-converter"
USER = object()


@pytest.fixture(autouse=True)
def clean_cache():
    token_cache.clear()
    yield
    token_cache.clear()


def _config(ttl):
    return AccessTokenConfig(SERVICE_TOKEN_CACHE_TTL_SECONDS=ttl)


class TestReuse:
    def test_a_stored_validation_is_returned(self):
        cfg = _config(60)
        token_cache.put(TOKEN, SERVICE, USER, cfg)

        assert token_cache.get(TOKEN, SERVICE, cfg) is USER

    def test_an_unknown_token_misses(self):
        assert token_cache.get("never-seen", SERVICE, _config(60)) is None

    def test_the_entry_expires(self):
        cfg = _config(0.05)
        token_cache.put(TOKEN, SERVICE, USER, cfg)
        time.sleep(0.08)

        assert token_cache.get(TOKEN, SERVICE, cfg) is None


class TestScoping:
    def test_a_token_cached_for_one_service_is_not_reused_for_another(self):
        # The same token is legitimately refused for a service it is not
        # scoped to. Keying on the token alone would turn one acceptance into
        # an acceptance everywhere - a privilege escalation, not a cache hit.
        cfg = _config(60)
        token_cache.put(TOKEN, SERVICE, USER, cfg)

        assert token_cache.get(TOKEN, "mascope_sdk", cfg) is None

    def test_different_tokens_do_not_collide(self):
        cfg = _config(60)
        token_cache.put(TOKEN, SERVICE, USER, cfg)

        assert token_cache.get("another-token", SERVICE, cfg) is None


class TestDisabling:
    def test_a_zero_ttl_never_stores(self):
        # Asserting on get() alone would pass even if put() stored the entry,
        # because get() also short-circuits on a zero TTL. Check the table.
        cfg = _config(0)
        token_cache.put(TOKEN, SERVICE, USER, cfg)

        assert token_cache._entries == {}
        assert token_cache.get(TOKEN, SERVICE, cfg) is None

    def test_a_zero_ttl_never_reads_an_existing_entry(self):
        # Switching caching off must take effect for entries already held,
        # not just for new ones.
        token_cache.put(TOKEN, SERVICE, USER, _config(60))

        assert token_cache.get(TOKEN, SERVICE, _config(0)) is None

    def test_a_negative_ttl_is_treated_as_off(self):
        cfg = _config(-1)
        token_cache.put(TOKEN, SERVICE, USER, cfg)

        assert token_cache.get(TOKEN, SERVICE, cfg) is None


class TestTokensAreNotHeldInTheClear:
    def test_the_raw_token_is_not_used_as_a_key(self):
        # The cache is a module-level dict: it surfaces in tracebacks, in a
        # debugger and in a heap dump. A bearer token among its keys would be
        # a credential in the clear in all three.
        cfg = _config(60)
        token_cache.put(TOKEN, SERVICE, USER, cfg)

        assert TOKEN not in token_cache._entries
        assert all(TOKEN not in key for key in token_cache._entries)


class TestBounded:
    def test_it_does_not_grow_without_limit(self):
        cfg = _config(60)
        for i in range(token_cache._MAX_ENTRIES + 50):
            token_cache.put(f"token-{i}", SERVICE, USER, cfg)

        assert len(token_cache._entries) <= token_cache._MAX_ENTRIES

    def test_expired_entries_are_reclaimed_before_eviction(self):
        # Filling up with live entries should not throw away a token that is
        # still in use while dead ones sit in the table.
        expired = _config(0.01)
        for i in range(token_cache._MAX_ENTRIES):
            token_cache.put(f"stale-{i}", SERVICE, USER, expired)
        time.sleep(0.05)

        fresh = _config(60)
        token_cache.put("live", SERVICE, USER, fresh)

        assert token_cache.get("live", SERVICE, fresh) is USER
        assert len(token_cache._entries) < token_cache._MAX_ENTRIES


class TestRevocationWindow:
    def test_clearing_forces_the_next_request_to_revalidate(self):
        # The escape hatch a revocation path can reach for, and what the TTL
        # bounds in its absence.
        cfg = _config(60)
        token_cache.put(TOKEN, SERVICE, USER, cfg)
        token_cache.clear()

        assert token_cache.get(TOKEN, SERVICE, cfg) is None

    def test_the_default_window_is_seconds_not_minutes(self):
        # This number is how long a revoked credential keeps working on a
        # worker that has seen it. It is a security limit, not a tuning knob.
        assert 0 < AccessTokenConfig().SERVICE_TOKEN_CACHE_TTL_SECONDS <= 15


class TestEntriesDoNotLinger:
    def test_an_expired_entry_is_swept_without_being_read(self):
        # A cached User carries its password hash and MFA secret. An entry
        # nobody looks up again must not keep them alive past its window.
        expired = _config(0.01)
        token_cache.put("abandoned", SERVICE, USER, expired)
        time.sleep(0.05)

        token_cache.put("something-else", SERVICE, USER, _config(60))

        assert len(token_cache._entries) == 1

    def test_a_refreshed_token_is_not_the_first_evicted(self):
        # Re-caching on every chunk must move a token to the back of the
        # eviction queue, not leave it at the position of its first use.
        cfg = _config(60)
        token_cache.put("hot", SERVICE, USER, cfg)
        for i in range(token_cache._MAX_ENTRIES - 1):
            token_cache.put(f"cold-{i}", SERVICE, USER, cfg)
        token_cache.put("hot", SERVICE, USER, cfg)  # refreshed, as a chunk would

        token_cache.put("newcomer", SERVICE, USER, cfg)  # forces an eviction

        assert token_cache.get("hot", SERVICE, cfg) is USER
