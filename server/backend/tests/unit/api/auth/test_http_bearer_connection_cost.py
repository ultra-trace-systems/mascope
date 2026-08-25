"""What an HTTP bearer request costs the connection pool before it is served.

``get_enabled_backends`` authenticates every ``Authorization`` +
``X-Service-Name`` request - the converter's uploads and metadata writes, the
SDK, the agents. It is a separate implementation from the Socket.IO path in
``validate_service_access_token``, and it was doing two ungated database round
trips per request: the token lookup, and a ``last_seen`` write whose throttle
lived in a WHERE clause, so it opened a session and committed on every request
to change nothing 59 times out of 60.

Neither is deadlock-shaped - they run sequentially, and the injected session
has not touched the database yet at this point - so this is about volume, not
about the hold-one-need-another pattern the Socket.IO path had. Volume was
enough: this is the hottest authenticated path in the system.

The safety argument for caching the token lookup here differs from, and is
stronger than, the one for caching a validated user: ``get_enabled_backends``
only *selects* a backend, and the strategy it returns reads the token row and
its expiry on every request, so a deleted or expired token is refused
immediately regardless of the cache. That property is asserted below, because
the caching is only defensible while it holds.

These tests drive ``get_enabled_backends`` itself. Re-implementing the
cache lookup in the test body would pass just as well with the caching deleted
from the production path, which is the failure mode that let a previous fix in
this repo ship green and broken.
"""

from datetime import datetime, timedelta, timezone

import pytest

from mascope_backend.api.new.auth import backend as auth_backend
from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token import validation as validation_mod


TOKEN = "tok-http"
SERVICE = "file-converter"
DEVICE_ID = 77


@pytest.fixture(autouse=True)
def clean_state():
    token_cache.clear()
    validation_mod._last_seen_written_at.clear()
    yield
    token_cache.clear()
    validation_mod._last_seen_written_at.clear()


class _Endpoint:
    """A route stamped for token access, as the tus and upload routes are."""

    token_access = True


class _Request:
    """The parts of a Request that get_enabled_backends reads."""

    def __init__(self, token=TOKEN, service=SERVICE):
        self.headers = {"authorization": f"Bearer {token}", "x-service-name": service}
        self.cookies = {}
        self.scope = {"endpoint": _Endpoint()}
        self.state = type("State", (), {})()


class LookupCounter:
    """Stands in for the token row read, counting how often it happens."""

    def __init__(self, device_id=None, service=SERVICE):
        self.calls = 0
        self.device_id = device_id
        self.service = service

    async def __call__(self, token, session=None):  # noqa: ARG002
        self.calls += 1
        return (
            self.service,
            self.device_id,
            datetime.now(timezone.utc) - timedelta(minutes=1),
        )


@pytest.fixture
def counted_lookup(monkeypatch):
    counter = LookupCounter()
    monkeypatch.setattr(auth_backend, "get_token_auth_context", counter)
    return counter


class TestTokenLookupIsReused:
    @pytest.mark.asyncio
    async def test_a_burst_of_requests_reads_the_token_once(self, counted_lookup):
        # Twenty upload chunks on one token used to be twenty ungated reads.
        for _ in range(20):
            await auth_backend.get_enabled_backends(_Request())

        assert counted_lookup.calls == 1

    @pytest.mark.asyncio
    async def test_the_request_is_still_authenticated_from_the_cached_context(
        self, counted_lookup
    ):
        first = await auth_backend.get_enabled_backends(_Request())
        second = await auth_backend.get_enabled_backends(_Request())

        assert second == first
        assert counted_lookup.calls == 1

    @pytest.mark.asyncio
    async def test_a_different_token_is_read_separately(self, counted_lookup):
        await auth_backend.get_enabled_backends(_Request(token="tok-a"))
        await auth_backend.get_enabled_backends(_Request(token="tok-b"))

        assert counted_lookup.calls == 2

    @pytest.mark.asyncio
    async def test_caching_does_not_bypass_the_service_scope_check(self, monkeypatch):
        # A token cached for one service must still be refused for a request
        # claiming another: the comparison happens after the lookup, cached or
        # not. Otherwise the cache would be a privilege escalation.
        monkeypatch.setattr(
            auth_backend, "get_token_auth_context", LookupCounter(service="mascope_sdk")
        )
        await auth_backend.get_enabled_backends(_Request(service="mascope_sdk"))

        with pytest.raises(auth_backend.HTTPException) as excinfo:
            await auth_backend.get_enabled_backends(_Request(service=SERVICE))
        assert excinfo.value.status_code == 401

    @pytest.mark.asyncio
    async def test_the_device_binding_survives_the_cache(self, monkeypatch):
        # device_id is used for the paired-device gate and the last_seen write;
        # a cached context that lost it would silently unbind the request.
        monkeypatch.setattr(
            auth_backend, "get_token_auth_context", LookupCounter(device_id=DEVICE_ID)
        )
        writes = []
        monkeypatch.setattr(auth_backend, "touch_device_last_seen", _record(writes))

        await auth_backend.get_enabled_backends(_Request())
        await auth_backend.get_enabled_backends(_Request())

        assert writes == [DEVICE_ID, DEVICE_ID]


def _record(sink):
    async def _touch(device_id):
        sink.append(device_id)

    return _touch


class TestNamespacesDoNotBleed:
    def test_a_context_entry_cannot_satisfy_a_user_lookup(self):
        # An auth context is a tuple, not an authenticated identity. Returning
        # one where a user is expected would hand a caller something it would
        # treat as a signed-in account.
        from mascope_backend.api.new.auth.config import auth_settings

        cfg = auth_settings.access_token
        token_cache.put_auth_context(TOKEN, (SERVICE, None, None), cfg)

        assert token_cache.get(TOKEN, SERVICE, cfg) is None

    def test_a_user_entry_cannot_satisfy_a_context_lookup(self):
        from mascope_backend.api.new.auth.config import auth_settings

        cfg = auth_settings.access_token
        token_cache.put(TOKEN, SERVICE, object(), cfg)

        assert token_cache.get_auth_context(TOKEN, cfg) is None


class TestRevocationStaysImmediateHere:
    @pytest.mark.asyncio
    async def test_this_function_only_selects_a_backend(self, counted_lookup):
        # The property the context cache rests on: nothing here authenticates
        # the user. The returned backend's strategy reads the token row through
        # the injected session on every request, so a deleted token is refused
        # at once whatever the cache holds. If this ever returns a user instead
        # of a backend, the cache acquires a revocation window.
        from fastapi_users.authentication import AuthenticationBackend

        backends = await auth_backend.get_enabled_backends(_Request())

        assert isinstance(backends, list)
        assert all(isinstance(b, AuthenticationBackend) for b in backends)

    def test_the_selected_backend_reads_tokens_from_the_database(self):
        from mascope_backend.api.new.auth.strategies import get_database_strategy

        assert auth_backend.auth_backend_access_token.get_strategy is (
            get_database_strategy
        )


class TestLastSeenWriteIsThrottled:
    def test_the_first_call_is_due(self):
        assert validation_mod._due_for_last_seen_write(DEVICE_ID) is True

    def test_a_second_call_within_the_window_is_not(self):
        validation_mod._due_for_last_seen_write(DEVICE_ID)

        assert validation_mod._due_for_last_seen_write(DEVICE_ID) is False

    def test_another_device_is_tracked_separately(self):
        validation_mod._due_for_last_seen_write(DEVICE_ID)

        assert validation_mod._due_for_last_seen_write(DEVICE_ID + 1) is True

    def test_it_becomes_due_again_after_the_window(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(validation_mod.time, "monotonic", lambda: clock[0])
        assert validation_mod._due_for_last_seen_write(DEVICE_ID) is True

        clock[0] += validation_mod.DEVICE_LAST_SEEN_THROTTLE_S

        assert validation_mod._due_for_last_seen_write(DEVICE_ID) is True

    @pytest.mark.asyncio
    async def test_a_throttled_call_opens_no_session(self, monkeypatch):
        class _Ctx:
            async def __aenter__(self):
                raise AssertionError("a throttled last_seen write opened a session")

            async def __aexit__(self, *exc):
                return False

        # Mark the device as just written, so the next call must skip the
        # database entirely rather than issuing its no-op UPDATE.
        validation_mod._due_for_last_seen_write(DEVICE_ID)
        monkeypatch.setattr(validation_mod, "async_session", lambda: _Ctx())

        await validation_mod.touch_device_last_seen(DEVICE_ID)

    @pytest.mark.asyncio
    async def test_a_due_call_still_writes(self, monkeypatch):
        # The throttle must not become "never write": last_seen is what the
        # device registry reports, and a device that stopped being written
        # would look offline.
        executed = []

        class _Session:
            async def execute(self, statement):
                executed.append(statement)

            async def commit(self):
                return None

        class _Ctx:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(validation_mod, "async_session", lambda: _Ctx())

        await validation_mod.touch_device_last_seen(DEVICE_ID)

        assert len(executed) == 1


class TestCacheDoesNotOutliveTokenFreshness:
    """A cached context must not keep a device token alive past its lifetime.

    The freshness rule compares the token's creation time against the clock, so
    caching an *immutable* created_at is exact: the token is refused the moment
    the clock crosses the boundary, cache or no cache. What would break this is
    caching the freshness *decision* rather than the timestamp.
    """

    @pytest.mark.asyncio
    async def test_a_token_cached_while_fresh_is_refused_once_it_ages_out(
        self, monkeypatch
    ):
        from mascope_backend.api.new.auth.config import auth_settings

        lifetime = auth_settings.access_token.DEVICE_TOKEN_LIFETIME_SECONDS
        created = datetime.now(timezone.utc) - timedelta(seconds=lifetime - 5)

        class _AgeingLookup:
            calls = 0

            async def __call__(self, token, session=None):  # noqa: ARG002
                type(self).calls += 1
                return ("file-agent", DEVICE_ID, created)

        monkeypatch.setattr(auth_backend, "get_token_auth_context", _AgeingLookup())
        monkeypatch.setattr(auth_backend, "touch_device_last_seen", _record([]))
        request = _Request(service="file-agent")

        # Fresh: accepted, and now cached.
        await auth_backend.get_enabled_backends(request)

        # The clock moves past the boundary. The cached created_at is unchanged
        # - it is immutable - so the check must now refuse.
        real_now = validation_mod.dt.now

        class _Clock:
            @staticmethod
            def now(tz=None):
                return real_now(tz) + timedelta(seconds=10)

        monkeypatch.setattr(validation_mod, "dt", _Clock)

        with pytest.raises(Exception) as excinfo:
            await auth_backend.get_enabled_backends(request)
        assert "401" in str(excinfo.value) or getattr(
            excinfo.value, "status_code", None
        ) in (401, 403)
