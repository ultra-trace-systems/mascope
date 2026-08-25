"""How many database connections one service-token validation holds.

``validate_service_access_token`` authenticates Socket.IO events - the
converter emits them throughout an upload - and re-checks a token in
``access_token.service``. It is *not* the HTTP bearer path: an
``Authorization`` + ``X-Service-Name`` request is authenticated by
``get_enabled_backends`` in ``api/new/auth/backend.py``, which does its own
lookup. Both are ungated: the semaphore in ``mascope_backend.db`` guards only
the dependency-injected session path, so nothing bounds how many run at once,
and the per-call connection cost is what decides whether load saturates the
pool.

It did. Under a converter upload run this path held three connections per
call; every waiter then blocked for ``pool_timeout`` (120 s) and the worker
stopped serving anything at all for a minute. Thirty-five of the thirty-nine
``QueuePool limit ... reached`` failures in that window were this function
failing to get a connection for itself.

The property these tests hold it to is not "few connections" but **never hold
one while needing another**: a caller that does can block on a connection only
it could release, which is the deadlock ``mascope_backend.db`` documents and
sizes its semaphore to prevent. Peak 2 was not good enough; peak 1 is the
invariant.

These tests are about connection accounting, not auth behaviour; the
behaviour is pinned in :mod:`test_service_token_validation_behaviour`.
"""

from datetime import datetime, timedelta, timezone

import pytest

from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token import util as token_util
from mascope_backend.api.new.auth.access_token import validation as validation_mod
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.api.new.auth.strategies import database as strategy_mod
from mascope_backend.api.new.users.user_manager import util as user_mgr_mod


SERVICE = "file-converter"
TOKEN = "tok-abc"


class SessionLedger:
    """Counts sessions opened, and how many were open at once."""

    def __init__(self, row):
        self.row = row
        self.open = 0
        self.peak = 0
        self.total = 0

    def __call__(self):
        return _FakeSessionContext(self)


class _FakeSessionContext:
    def __init__(self, ledger):
        self._ledger = ledger

    async def __aenter__(self):
        self._ledger.open += 1
        self._ledger.total += 1
        self._ledger.peak = max(self._ledger.peak, self._ledger.open)
        return _FakeSession(self._ledger.row)

    async def __aexit__(self, *exc):
        self._ledger.open -= 1
        return False


class _FakeSession:
    def __init__(self, row):
        self._row = row

    async def execute(self, _statement):
        return _FakeResult(self._row)

    async def commit(self):
        return None


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row

    def scalar_one_or_none(self):
        # The single-column selects this path used to make returned just the
        # service name. Modelling both shapes keeps these tests meaningful
        # against the pre-collapse code as well, so they can show that the
        # outcomes did not change.
        return None if self._row is None else self._row[0]


@pytest.fixture(autouse=True)
def _no_cached_validations():
    """Measure the cold path: a cached hit does no database work at all."""
    token_cache.clear()
    yield
    token_cache.clear()


@pytest.fixture
def ledger(monkeypatch):
    """Count every ``async_session()`` the validation path opens."""
    created = datetime.now(timezone.utc) - timedelta(minutes=1)
    led = SessionLedger(row=(SERVICE, None, created))

    # Every module on this path resolves async_session from its own namespace.
    for module in (validation_mod, token_util, strategy_mod, user_mgr_mod):
        monkeypatch.setattr(module, "async_session", led, raising=True)

    # fastapi-users internals are not what these tests are about: the user
    # lookup is stubbed so only our own session handling is measured.
    async def _read_token(self, token, user_manager):  # noqa: ARG001
        return object()

    monkeypatch.setattr(
        strategy_mod.DatabaseStrategy, "read_token", _read_token, raising=True
    )
    return led


class TestConnectionCost:
    @pytest.mark.asyncio
    async def test_it_never_holds_one_connection_while_needing_another(self, ledger):
        # The invariant, not merely a smaller number. A call that holds a
        # connection and then checks out a second can, with enough concurrent
        # callers, block forever on a connection only it could release - the
        # deadlock mascope_backend.db sizes db_semaphore to prevent, on a path
        # that takes no permit. Peak 1 is the only value that removes it.
        await validation_mod.validate_service_access_token(TOKEN, SERVICE)

        assert ledger.peak == 1

    @pytest.mark.asyncio
    async def test_it_opens_a_single_session(self, ledger):
        # Five were opened before: two held, plus an existence check, a service
        # name query and the token-context lookup - three round trips for one
        # row that a single query already returns.
        await validation_mod.validate_service_access_token(TOKEN, SERVICE)

        assert ledger.total == 1

    @pytest.mark.asyncio
    async def test_every_session_is_released(self, ledger):
        await validation_mod.validate_service_access_token(TOKEN, SERVICE)

        assert ledger.open == 0

    @pytest.mark.asyncio
    async def test_sessions_are_released_when_validation_fails(self, ledger):
        # A leaked connection on the rejection path would starve the pool
        # faster than the success path ever could: a wrong-service token is
        # retried, and each retry would cost a connection permanently.
        with pytest.raises(InvalidTokenException):
            await validation_mod.validate_service_access_token(
                TOKEN, "some-other-service"
            )

        assert ledger.open == 0


class TestAdmissionControlAssumption:
    """Why the cost above matters at all.

    ``db_semaphore`` bounds concurrency on ``get_async_session`` only. The
    auth path reaches the database through ``async_session()``, which takes no
    permit - so its per-call cost is multiplied by however many bearer
    requests happen to be in flight, with nothing to cap it.
    """

    def test_only_the_injected_session_path_takes_a_permit(self):
        import inspect

        from mascope_backend import db

        assert "db_semaphore" in inspect.getsource(db.get_async_session)
        assert "db_semaphore" not in inspect.getsource(db.async_session)

    def test_the_permit_count_cannot_exceed_the_overflow(self):
        # Sizing admissions above max_overflow deadlocks a worker: every
        # holder takes its own connection, then blocks on the nested checkout
        # that the overflow was meant to serve. See the note in mascope_backend.db.
        from mascope_backend import db

        assert db._DB_SEMAPHORE_PERMITS <= db.db_cfg.max_overflow
        assert db._DB_SEMAPHORE_PERMITS <= db.db_cfg.pool_size


class TestCachedValidationCostsNothing:
    @pytest.mark.asyncio
    async def test_a_repeat_validation_opens_no_session(self, ledger):
        # The reason the cache exists: an upload revalidates the same token
        # once per chunk, and every repeat used to pay the full cost.
        await validation_mod.validate_service_access_token(TOKEN, SERVICE)
        first = ledger.total

        await validation_mod.validate_service_access_token(TOKEN, SERVICE)

        assert ledger.total == first

    @pytest.mark.asyncio
    async def test_a_burst_of_repeats_costs_one_validation(self, ledger):
        for _ in range(20):
            await validation_mod.validate_service_access_token(TOKEN, SERVICE)

        assert ledger.total == 1
