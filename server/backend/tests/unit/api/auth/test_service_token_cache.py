"""The short-lived cache in front of service-token validation.

It exists because a resumable upload revalidates the same token once per
chunk, and each validation costs database connections on a path with no
admission control. What it trades away is immediacy of revocation, so the
window has to be small, bounded, and genuinely switchable off - which is what
these tests hold it to.
"""

import pytest
from pydantic import ValidationError

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


class _FakeClock:
    """A monotonic clock the test drives, so expiry is exact and instant."""

    def __init__(self, start=1000.0):
        self.now = start

    def advance(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


def _fake_clock(monkeypatch, start=1000.0):
    """Give the cache a clock this test controls.

    Swaps the ``time`` name in the cache's own namespace, not
    ``time.monotonic`` itself: ``cache.time`` IS the stdlib module, so patching
    through it would freeze the clock for the whole process - asyncio's event
    loop reads its own time from the same function, and this suite runs on one
    session-scoped loop.

    :param monkeypatch: The pytest monkeypatch fixture.
    :param start: Initial value for the fake clock.
    :return: The clock, to advance.
    :rtype: _FakeClock
    """
    clock = _FakeClock(start)
    monkeypatch.setattr(token_cache, "time", clock)
    return clock


class TestReuse:
    def test_a_stored_validation_is_returned(self):
        cfg = _config(60)
        token_cache.put(TOKEN, SERVICE, USER, cfg)

        assert token_cache.get(TOKEN, SERVICE, cfg) is USER

    def test_an_unknown_token_misses(self):
        assert token_cache.get("never-seen", SERVICE, _config(60)) is None

    def test_the_entry_expires(self, monkeypatch):
        clock = _fake_clock(monkeypatch)
        cfg = _config(5)
        token_cache.put(TOKEN, SERVICE, USER, cfg)
        clock.advance(5)

        assert token_cache.get(TOKEN, SERVICE, cfg) is None

    def test_the_entry_survives_until_the_window_closes(self, monkeypatch):
        # The other half of the bound. Without this nothing here would fail if
        # put() stamped the expiry in the past and every entry were born dead.
        clock = _fake_clock(monkeypatch)
        cfg = _config(5)
        token_cache.put(TOKEN, SERVICE, USER, cfg)
        clock.advance(4.9)

        assert token_cache.get(TOKEN, SERVICE, cfg) is USER


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

    def test_expired_entries_are_reclaimed_before_eviction(self, monkeypatch):
        # Filling up must not throw away a token that is still in use while
        # dead ones sit in the table. The live entry is deliberately the
        # oldest - it is what plain FIFO discards first - and the dead ones sit
        # behind it, where a front-to-back sweep does not reach them.
        # Reclaiming them is what has to happen before anything live goes.
        #
        # A driven clock, not sleeps: against a real one the fill loop outlives
        # any TTL short enough to be worth waiting for, so the table never
        # reaches the cap and the branch under test never runs. That is exactly
        # how the previous version of this test passed while asserting nothing.
        clock = _fake_clock(monkeypatch)
        live, short = _config(60), _config(1)

        token_cache.put("hot", SERVICE, USER, live)
        for i in range(token_cache._MAX_ENTRIES - 1):
            token_cache.put(f"stale-{i}", SERVICE, USER, short)
        assert len(token_cache._entries) == token_cache._MAX_ENTRIES

        clock.advance(2)  # every stale entry is dead now; "hot" is not
        token_cache.put("newcomer", SERVICE, USER, live)

        assert token_cache.get("hot", SERVICE, live) is USER
        assert len(token_cache._entries) == 2


class TestRevocationWindow:
    def test_clearing_forces_the_next_request_to_revalidate(self):
        # Kept as an escape hatch and used by tests; nothing in the application
        # calls it, and nothing usefully could - the cache is per worker, so
        # clearing it where a revocation was served leaves every other worker's
        # copy standing. The TTL is the real bound.
        cfg = _config(60)
        token_cache.put(TOKEN, SERVICE, USER, cfg)
        token_cache.clear()

        assert token_cache.get(TOKEN, SERVICE, cfg) is None

    def test_the_default_window_is_seconds_not_minutes(self):
        # This number is how long a revoked credential keeps working on a
        # worker that has seen it. It is a security limit, not a tuning knob.
        assert 0 < AccessTokenConfig().SERVICE_TOKEN_CACHE_TTL_SECONDS <= 15

    def test_a_window_of_minutes_is_refused_by_the_config(self):
        # And the limit is enforced where it is declared, not left to whoever
        # edits the default next.
        with pytest.raises(ValidationError):
            AccessTokenConfig(SERVICE_TOKEN_CACHE_TTL_SECONDS=300)


class TestEntriesDoNotLinger:
    def test_an_expired_entry_is_swept_without_being_read(self, monkeypatch):
        # A cached User carries its password hash and MFA secret. An entry
        # nobody looks up again must not keep them alive past its window.
        clock = _fake_clock(monkeypatch)
        token_cache.put("abandoned", SERVICE, USER, _config(5))
        clock.advance(6)

        token_cache.put("something-else", SERVICE, USER, _config(60))

        assert len(token_cache._entries) == 1

    def test_switching_the_cache_off_does_not_pin_what_it_already_holds(self):
        # get() short-circuits on a zero TTL, so an entry left behind would
        # never be read again and never swept: a User row, password hash and
        # MFA secret included, held for the life of the worker.
        token_cache.put(TOKEN, SERVICE, USER, _config(60))

        token_cache.put("anything", SERVICE, USER, _config(0))

        assert token_cache._entries == {}

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
