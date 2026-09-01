"""How many database connections one bearer-token validation holds.

Every request from an agent, the file converter or the SDK runs
``validate_service_access_token`` before anything else. It takes no
admission-control permit - ``db_semaphore`` in :mod:`mascope_backend.db` guards
only the dependency-injected session path - so nothing bounds how many of these
run at once. Its per-call connection cost is therefore the thing that decides
whether a bulk upload saturates the pool.

It did. Under a converter upload run this path held three connections per
request, every waiter then blocked for ``pool_timeout`` (120 s), and the worker
stopped serving anything at all for a minute - including unrelated requests,
which failed with ``QueuePool limit ... reached``. The 401s that reached error
monitoring were this: the token lookup could not get a connection, so a valid
token was reported as a validation failure.

Backported from #1928, minus its assertions about a semaphore-sizing refactor
that is not on this line. These tests are about connection accounting, not auth
behaviour.
"""

import pytest

from mascope_backend.api.new.auth.access_token import util as token_util
from mascope_backend.api.new.auth.access_token import validation as validation_mod
from mascope_backend.api.new.auth.strategies import database as strategy_mod
from mascope_backend.api.new.users.user_manager import util as user_mgr_mod


SERVICE = "file-converter"
TOKEN = "tok-abc"


class _Row:
    """A token row, readable the way both the old and new queries read it."""

    def __init__(self, token, service_name):
        self.token = token
        self.service_name = service_name


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

    async def execute(self, statement):
        return _FakeResult(self._row, statement)

    async def commit(self):
        return None


class _FakeResult:
    """Answers a select the way the real one would, per selected column.

    The pre-fix path made two different single-column selects - the whole
    entity, to check the row exists, and the service name - and read both with
    ``scalar_one_or_none``. Returning one value for both would make a token
    with a NULL service name look like a token that does not exist, and this
    suite would report a behaviour change that is purely an artefact of the
    double. So the selected columns decide the answer.
    """

    def __init__(self, row, statement=None):
        self._row = row
        self._statement = statement

    def _selected(self):
        try:
            return [d.get("name") for d in self._statement.column_descriptions]
        except Exception:
            return []

    def one_or_none(self):
        return self._row

    def scalar_one_or_none(self):
        if self._row is None:
            return None
        # select(AccessToken) - the existence check - yields the row itself.
        if self._selected() == ["AccessToken"]:
            return self._row
        # select(AccessToken.service_name) yields just that column.
        return self._row.service_name


@pytest.fixture
def ledger(monkeypatch):
    """Count every ``async_session()`` the validation path opens."""
    led = SessionLedger(row=_Row(TOKEN, SERVICE))

    # Every module on this path resolves async_session from its own namespace.
    #
    # raising=False on purpose: the pre-fix code did not open a session in
    # validation itself, so insisting the name is already there would make
    # these tests error out against it instead of failing an assertion - and a
    # negative control that cannot run proves nothing.
    for module in (validation_mod, token_util, strategy_mod, user_mgr_mod):
        monkeypatch.setattr(module, "async_session", led, raising=False)

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
    async def test_one_validation_holds_one_connection(self, ledger):
        # The strategy, the user manager and the service-name lookup all run on
        # one session. It was three held at once: a session each, plus the
        # lookup nested inside both - a caller holding a connection and then
        # needing another can block on one only it could release.
        await validation_mod.validate_service_access_token(TOKEN, SERVICE)

        assert ledger.peak == 1

    @pytest.mark.asyncio
    async def test_it_opens_no_more_sessions_than_it_holds(self, ledger):
        # Four were opened before: two held, plus an existence check and a
        # service-name query - two round trips for one row that a single query
        # already returns.
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
        with pytest.raises(Exception):
            await validation_mod.validate_service_access_token(
                TOKEN, "some-other-service"
            )

        assert ledger.open == 0


class TestOutcomesAreUnchanged:
    """The collapse must not change which tokens are accepted or why."""

    @pytest.mark.asyncio
    async def test_a_matching_service_token_is_accepted(self, ledger):
        assert await validation_mod.validate_service_access_token(TOKEN, SERVICE)

    @pytest.mark.asyncio
    async def test_an_unknown_token_is_refused(self, monkeypatch, ledger):
        ledger.row = None

        with pytest.raises(Exception, match="Invalid access token"):
            await validation_mod.validate_service_access_token(TOKEN, SERVICE)

    @pytest.mark.asyncio
    async def test_a_token_with_no_service_name_is_refused(self, ledger):
        ledger.row = _Row(TOKEN, None)

        with pytest.raises(Exception, match="No service name for the token"):
            await validation_mod.validate_service_access_token(TOKEN, SERVICE)

    @pytest.mark.asyncio
    async def test_a_wrong_service_token_is_refused(self, ledger):
        with pytest.raises(Exception, match="not authorized for"):
            await validation_mod.validate_service_access_token(TOKEN, "other-service")
