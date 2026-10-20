"""Tests: the web session's issue time and the per-account session cutoff.

Free of database and Redis: the user is a plain object and the user manager a
stub, because what is under test is the comparison and the token's claims, not
the row they are read from. The integration tests in
``tests/integration/api/users/test_session_revocation.py`` drive the same
cutoff through the routes that move it.
"""

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi_users import exceptions
from fastapi_users.jwt import generate_jwt

from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.sessions import (
    SESSION_CUTOFF_TOLERANCE,
    session_cutoff,
    session_revoked,
)
from mascope_backend.api.new.auth.strategies.jwt import get_jwt_strategy


CUTOFF = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _user(password_changed_at=None, sessions_revoked_at=None):
    return SimpleNamespace(
        id=7,
        password_changed_at=password_changed_at,
        sessions_revoked_at=sessions_revoked_at,
    )


def _iat(moment: datetime) -> int:
    return int(moment.timestamp())


# --- The comparison ---


def test_the_tolerance_is_one_minute():
    # Pinned: the comparisons below derive their offsets from it, so they would
    # follow a widened tolerance without noticing.
    assert SESSION_CUTOFF_TOLERANCE == timedelta(minutes=1)


def test_no_cutoff_revokes_nothing():
    assert session_cutoff(_user()) is None
    assert session_revoked(_user(), _iat(CUTOFF)) is False
    assert session_revoked(_user(), None) is False


def test_the_cutoff_is_the_later_of_the_two_stamps():
    earlier, later = CUTOFF - timedelta(hours=1), CUTOFF
    assert session_cutoff(_user(earlier, later)) == later
    assert session_cutoff(_user(later, earlier)) == later
    assert session_cutoff(_user(password_changed_at=later)) == later
    assert session_cutoff(_user(sessions_revoked_at=later)) == later


@pytest.mark.parametrize("stamp", ["password_changed_at", "sessions_revoked_at"])
def test_a_session_issued_before_the_cutoff_is_revoked(stamp):
    user = _user(**{stamp: CUTOFF})
    issued = CUTOFF - SESSION_CUTOFF_TOLERANCE - timedelta(seconds=1)
    assert session_revoked(user, _iat(issued)) is True


def test_a_session_issued_within_the_tolerance_survives():
    # Another worker's clock may run behind the one that stamped the cutoff.
    user = _user(sessions_revoked_at=CUTOFF)
    issued = CUTOFF - SESSION_CUTOFF_TOLERANCE + timedelta(seconds=1)
    assert session_revoked(user, _iat(issued)) is False


def test_a_session_issued_after_the_cutoff_survives():
    user = _user(sessions_revoked_at=CUTOFF)
    assert session_revoked(user, _iat(CUTOFF + timedelta(minutes=5))) is False


def test_a_session_without_an_issue_time_is_revoked_once_there_is_a_cutoff():
    # Minted before tokens carried one, so it cannot show it postdates the cutoff.
    assert session_revoked(_user(password_changed_at=CUTOFF), None) is True


# --- The strategy ---


class _UserManager:
    """Resolves the one user it was given, as fastapi-users' manager would."""

    def __init__(self, user):
        self._user = user

    def parse_id(self, value):
        return int(value)

    async def get(self, user_id):
        if self._user is None or user_id != self._user.id:
            raise exceptions.UserNotExists()
        return self._user


def _token(issued_at: int | None) -> str:
    data = {"sub": "7", "aud": auth_settings.JWT_AUDIENCE}
    if issued_at is not None:
        data["iat"] = issued_at
    return generate_jwt(
        data,
        auth_settings.JWT_SECRET_KEY,
        auth_settings.JWT_EXPIRATION_SECONDS,
        algorithm=auth_settings.JWT_ALGORITHM,
    )


@pytest.mark.asyncio
async def test_a_written_token_carries_its_issue_time():
    before = int(time.time())
    token = await get_jwt_strategy().write_token(_user())
    after = int(time.time())
    claims = jwt.decode(
        token,
        auth_settings.JWT_SECRET_KEY,
        audience=auth_settings.JWT_AUDIENCE,
        algorithms=[auth_settings.JWT_ALGORITHM],
    )
    assert before <= claims["iat"] <= after
    # The lifetime is unchanged by the new claim.
    assert claims["exp"] - claims["iat"] == pytest.approx(
        auth_settings.JWT_EXPIRATION_SECONDS, abs=2
    )


@pytest.mark.asyncio
async def test_a_written_token_reads_back_with_its_issue_time():
    user = _user(sessions_revoked_at=CUTOFF)
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    read = await strategy.read_session(token, _UserManager(user))
    assert read is not None
    assert read[0] is user
    assert read[1] == jwt.decode(token, options={"verify_signature": False})["iat"]


@pytest.mark.asyncio
async def test_a_token_issued_before_the_cutoff_reads_as_no_user():
    user = _user(sessions_revoked_at=CUTOFF)
    token = _token(_iat(CUTOFF - timedelta(minutes=5)))
    assert await get_jwt_strategy().read_token(token, _UserManager(user)) is None


@pytest.mark.asyncio
async def test_a_token_issued_after_the_cutoff_reads_as_its_user():
    user = _user(sessions_revoked_at=CUTOFF)
    token = _token(_iat(CUTOFF + timedelta(minutes=5)))
    assert await get_jwt_strategy().read_token(token, _UserManager(user)) is user


@pytest.mark.asyncio
async def test_a_token_minted_a_moment_ahead_by_another_worker_is_accepted():
    # PyJWT's own iat check would refuse this with no tolerance at all.
    user = _user()
    token = _token(int(time.time()) + 30)
    assert await get_jwt_strategy().read_token(token, _UserManager(user)) is user


@pytest.mark.asyncio
async def test_a_legacy_token_reads_while_the_account_has_no_cutoff():
    user = _user()
    assert await get_jwt_strategy().read_token(_token(None), _UserManager(user)) is user


@pytest.mark.asyncio
async def test_a_legacy_token_is_refused_once_the_account_has_a_cutoff():
    user = _user(password_changed_at=CUTOFF)
    assert await get_jwt_strategy().read_token(_token(None), _UserManager(user)) is None


@pytest.mark.asyncio
async def test_a_non_integer_issue_time_is_refused():
    user = _user()
    token = generate_jwt(
        {"sub": "7", "aud": auth_settings.JWT_AUDIENCE, "iat": "yesterday"},
        auth_settings.JWT_SECRET_KEY,
        auth_settings.JWT_EXPIRATION_SECONDS,
        algorithm=auth_settings.JWT_ALGORITHM,
    )
    assert await get_jwt_strategy().read_token(token, _UserManager(user)) is None


@pytest.mark.asyncio
async def test_an_expired_token_is_still_refused():
    user = _user()
    token = generate_jwt(
        {"sub": "7", "aud": auth_settings.JWT_AUDIENCE, "iat": int(time.time())},
        auth_settings.JWT_SECRET_KEY,
        -10,
        algorithm=auth_settings.JWT_ALGORITHM,
    )
    assert await get_jwt_strategy().read_token(token, _UserManager(user)) is None
