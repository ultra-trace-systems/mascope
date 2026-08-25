"""What ``validate_service_access_token`` accepts and refuses.

The path had no tests at all, which is how it came to read the same token row
four times without anyone noticing. Collapsing those reads to one had to be
provably behaviour-preserving, so the outcomes are pinned here: the same
exception, with the same message, for each way a token can be wrong.
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
USER = object()


class _Ctx:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return _Session(self._row)

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, row):
        self._row = row

    async def execute(self, _statement):
        return _Result(self._row)

    async def commit(self):
        return None


class _Result:
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
    """Each case validates for real; a hit from a previous one would mask it."""
    token_cache.clear()
    yield
    token_cache.clear()


def _install(monkeypatch, *, row, user=USER):
    """Point the whole path at a fixed token row and user."""
    for module in (validation_mod, token_util, strategy_mod, user_mgr_mod):
        monkeypatch.setattr(module, "async_session", lambda: _Ctx(row), raising=True)

    async def _read_token(self, token, user_manager):  # noqa: ARG001
        return user

    monkeypatch.setattr(
        strategy_mod.DatabaseStrategy, "read_token", _read_token, raising=True
    )


def _fresh():
    return datetime.now(timezone.utc) - timedelta(minutes=1)


class TestAccepts:
    @pytest.mark.asyncio
    async def test_a_valid_service_token_returns_its_user(self, monkeypatch):
        _install(monkeypatch, row=(SERVICE, None, _fresh()))

        assert (
            await validation_mod.validate_service_access_token(TOKEN, SERVICE) is USER
        )


class TestRefuses:
    @pytest.mark.asyncio
    async def test_a_non_string_token(self, monkeypatch):
        _install(monkeypatch, row=(SERVICE, None, _fresh()))

        with pytest.raises(InvalidTokenException) as excinfo:
            await validation_mod.validate_service_access_token(None, SERVICE)
        assert "not a string" in str(excinfo.value.detail)

    @pytest.mark.asyncio
    async def test_a_token_with_no_user(self, monkeypatch):
        _install(monkeypatch, row=(SERVICE, None, _fresh()), user=None)

        with pytest.raises(InvalidTokenException) as excinfo:
            await validation_mod.validate_service_access_token(TOKEN, SERVICE)
        assert "no associated user found" in str(excinfo.value.detail)

    @pytest.mark.asyncio
    async def test_an_unknown_token(self, monkeypatch):
        # No row at all - the message the removed existence check produced.
        _install(monkeypatch, row=None)

        with pytest.raises(InvalidTokenException) as excinfo:
            await validation_mod.validate_service_access_token(TOKEN, SERVICE)
        assert "Invalid access token" in str(excinfo.value.detail)

    @pytest.mark.asyncio
    async def test_a_token_with_no_service_name(self, monkeypatch):
        # The message the removed service-name query produced.
        _install(monkeypatch, row=("", None, _fresh()))

        with pytest.raises(InvalidTokenException) as excinfo:
            await validation_mod.validate_service_access_token(TOKEN, SERVICE)
        assert "No service name for the token" in str(excinfo.value.detail)

    @pytest.mark.asyncio
    async def test_a_token_scoped_to_another_service(self, monkeypatch):
        _install(monkeypatch, row=("mascope_sdk", None, _fresh()))

        with pytest.raises(InvalidTokenException) as excinfo:
            await validation_mod.validate_service_access_token(TOKEN, SERVICE)
        assert f"not authorized for {SERVICE}" in str(excinfo.value.detail)


class TestErrorPrecedence:
    @pytest.mark.asyncio
    async def test_the_user_lookup_still_runs_before_the_service_check(
        self, monkeypatch
    ):
        # Collapsing the reads must not reorder the failures: a token with no
        # user AND the wrong service reported the missing user first, and
        # still does. Reordering would change what an agent is told to do
        # about it.
        _install(monkeypatch, row=("mascope_sdk", None, _fresh()), user=None)

        with pytest.raises(InvalidTokenException) as excinfo:
            await validation_mod.validate_service_access_token(TOKEN, SERVICE)
        assert "no associated user found" in str(excinfo.value.detail)
