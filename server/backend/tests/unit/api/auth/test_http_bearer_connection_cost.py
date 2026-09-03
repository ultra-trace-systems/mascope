"""What an HTTP bearer request costs the connection pool before it is served.

``get_enabled_backends`` authenticates every ``Authorization`` +
``X-Service-Name`` request - the converter's uploads and metadata writes, the
SDK, the agents. It is a different entry point from the Socket.IO path in
``validate_service_access_token`` (the two share the token lookup itself,
through ``util.resolve_token_context``, but nothing else), and it was doing two
ungated database round trips per request: the token lookup, and a ``last_seen``
write whose throttle lived in a WHERE clause, so it opened a session and
committed on every request to change nothing 59 times out of 60.

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

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import exc as sa_exc

from mascope_backend.api.new.auth import backend as auth_backend
from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token import util as token_util
from mascope_backend.api.new.auth.access_token import validation as validation_mod
from mascope_backend.api.new.auth.exceptions import AgentCredentialRefusedException


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
    monkeypatch.setattr(token_util, "get_token_auth_context", counter)
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
            token_util, "get_token_auth_context", LookupCounter(service="mascope_sdk")
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
            token_util, "get_token_auth_context", LookupCounter(device_id=DEVICE_ID)
        )
        writes = []
        monkeypatch.setattr(auth_backend, "touch_device_last_seen", _record(writes))

        await auth_backend.get_enabled_backends(_Request())
        await auth_backend.get_enabled_backends(_Request())

        assert writes == [DEVICE_ID, DEVICE_ID]


def _record(sink):
    async def _touch(device_id, **kwargs):
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


class _SessionCtx:
    """An ``async_session()`` stand-in yielding a prepared session double."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class _RecordingSession:
    """Accepts and records statements, and commits without complaint."""

    def __init__(self, sink):
        self.sink = sink

    async def execute(self, statement):
        self.sink.append(statement)

    async def commit(self):
        return None


class TestLastSeenWriteIsThrottled:
    def test_asking_does_not_claim_the_window(self):
        # The point of splitting the predicate from the mark: when one call
        # both decided and recorded, a caller that asked and then failed to
        # write had already bought itself 60 s of silence.
        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is True
        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is True

    def test_marking_claims_the_window(self):
        validation_mod._mark_last_seen_written(DEVICE_ID)

        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is False

    def test_another_device_is_tracked_separately(self):
        validation_mod._mark_last_seen_written(DEVICE_ID)

        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID + 1) is True

    def test_it_becomes_due_again_after_the_window(self, monkeypatch):
        clock = [1000.0]

        class _FakeTime:
            @staticmethod
            def monotonic():
                return clock[0]

        # Patch the name validation.py holds, not time.monotonic itself:
        # validation_mod.time IS the stdlib module, so setattr on it would
        # freeze the clock process-wide - including asyncio's event loop, which
        # reads its own time from the same function, and the token cache.
        monkeypatch.setattr(validation_mod, "time", _FakeTime)
        validation_mod._mark_last_seen_written(DEVICE_ID)
        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is False

        clock[0] += validation_mod.DEVICE_LAST_SEEN_THROTTLE_S

        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is True

    def test_releasing_a_stale_mark_leaves_a_newer_one_alone(self, monkeypatch):
        # A write can outlive its own window: the pool's timeout is twice the
        # throttle. By the time it fails, a later request may legitimately hold
        # the claim, and clearing that one would let the herd back in.
        #
        # A driven clock, not two real calls: time.monotonic() is
        # GetTickCount64 on Windows, resolution 15.6 ms, so back-to-back marks
        # return the same float and the two claims would be indistinguishable -
        # green on a Linux CI runner and red on a developer's machine.
        clock = [1000.0]

        class _FakeTime:
            @staticmethod
            def monotonic():
                return clock[0]

        monkeypatch.setattr(validation_mod, "time", _FakeTime)
        stale = validation_mod._mark_last_seen_written(DEVICE_ID)
        clock[0] += 1.0
        validation_mod._mark_last_seen_written(DEVICE_ID)

        validation_mod._release_last_seen_mark(DEVICE_ID, stale)

        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is False

    @pytest.mark.asyncio
    async def test_a_throttled_call_opens_no_session(self, monkeypatch):
        class _Ctx:
            async def __aenter__(self):
                raise AssertionError("a throttled last_seen write opened a session")

            async def __aexit__(self, *exc):
                return False

        # Mark the device as just written, so the next call must skip the
        # database entirely rather than issuing its no-op UPDATE.
        validation_mod._mark_last_seen_written(DEVICE_ID)
        monkeypatch.setattr(validation_mod, "async_session", lambda: _Ctx())

        await validation_mod.touch_device_last_seen(DEVICE_ID)

    @pytest.mark.asyncio
    async def test_a_due_call_still_writes_and_claims_the_window(self, monkeypatch):
        # The throttle must not become "never write": last_seen is what the
        # device registry reports, and a device that stopped being written
        # would look offline.
        executed = []
        monkeypatch.setattr(
            validation_mod,
            "async_session",
            lambda: _SessionCtx(_RecordingSession(executed)),
        )

        await validation_mod.touch_device_last_seen(DEVICE_ID)

        assert len(executed) == 1
        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is False

    @pytest.mark.asyncio
    async def test_a_failed_write_leaves_the_next_request_due(self, monkeypatch):
        # The reason for the split. A write that never landed must not buy 60 s
        # of silence: pool exhaustion is when this write fails, and it is also
        # when an operator most wants to know what a device is doing.
        class _Session:
            async def execute(self, statement):
                raise sa_exc.TimeoutError("QueuePool limit reached")

            async def commit(self):
                raise AssertionError("commit reached after a failed execute")

        monkeypatch.setattr(
            validation_mod, "async_session", lambda: _SessionCtx(_Session())
        )

        with pytest.raises(sa_exc.TimeoutError):
            await validation_mod.touch_device_last_seen(DEVICE_ID)

        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is True

    @pytest.mark.asyncio
    async def test_a_cancelled_write_leaves_the_next_request_due(self, monkeypatch):
        # A client that hangs up mid-request cancels the task. CancelledError
        # is a BaseException, so `except Exception` would miss it and the
        # device would go unreported for the window.
        started = asyncio.Event()

        class _Session:
            async def execute(self, statement):
                started.set()
                await asyncio.Event().wait()

            async def commit(self):
                return None

        monkeypatch.setattr(
            validation_mod, "async_session", lambda: _SessionCtx(_Session())
        )

        task = asyncio.create_task(validation_mod.touch_device_last_seen(DEVICE_ID))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is True

    @pytest.mark.asyncio
    async def test_a_committed_write_survives_a_cancellation_on_the_way_out(
        self, monkeypatch
    ):
        # AsyncSession.__aexit__ awaits a shielded close(), which is a
        # cancellation point. A mark placed after the `async with` would be
        # skipped here even though the row is written, and every later request
        # in the window would re-issue an UPDATE that already committed.
        class _Session:
            async def execute(self, statement):
                return None

            async def commit(self):
                return None

        class _CancellingCtx(_SessionCtx):
            async def __aexit__(self, *exc):
                raise asyncio.CancelledError()

        monkeypatch.setattr(
            validation_mod, "async_session", lambda: _CancellingCtx(_Session())
        )

        with pytest.raises(asyncio.CancelledError):
            await validation_mod.touch_device_last_seen(DEVICE_ID)

        assert validation_mod._is_due_for_last_seen_write(DEVICE_ID) is False

    @pytest.mark.asyncio
    async def test_a_write_in_flight_blocks_a_concurrent_one(self, monkeypatch):
        # The claim is taken before the await, so an agent's parallel upload
        # workers do not each check out a connection for the same row. This
        # path takes no admission permit, so nothing else bounds them.
        started = asyncio.Event()
        release = asyncio.Event()
        executed = []

        class _Session:
            async def execute(self, statement):
                executed.append(statement)
                started.set()
                await release.wait()

            async def commit(self):
                return None

        monkeypatch.setattr(
            validation_mod, "async_session", lambda: _SessionCtx(_Session())
        )

        first = asyncio.create_task(validation_mod.touch_device_last_seen(DEVICE_ID))
        await started.wait()

        await validation_mod.touch_device_last_seen(DEVICE_ID)

        release.set()
        await first

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

        monkeypatch.setattr(token_util, "get_token_auth_context", _AgeingLookup())
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

        # Named, not "anything with 401 in it": get_enabled_backends raises a
        # bare 401 on four other branches, so a looser assertion would stay
        # green with the freshness check deleted and the request falling
        # through to a different refusal.
        with pytest.raises(
            AgentCredentialRefusedException,
            match="This agent credential has expired",
        ) as excinfo:
            await auth_backend.get_enabled_backends(request)
        assert excinfo.value.status_code == 401
