"""Tests: an upload gets the file-converter token the way a sign-in does.

The upload routes ask ``get_access_token`` for the token the converter
writes the upload's results back with, and the request asking is
authenticated as the user already. For the file-converter service the answer
is ``ensure_access_token``'s, the sign-in's: the token the user holds while
it is valid, a new one beside it near its end, one minted where they hold
none. That is what keeps a script, or an agent on a pre-registry token,
uploading on a person's token whose owner has not signed in since the token
neared its end - the sign-in's renewal reaches only an owner who signs in
during the token's last 30 days - and what renews a machine account's token,
which was minted on demand but never early, so it lapsed once a year (its
test is with the pairing tests, which make machine accounts). Any other
service's token is still handed back only while it validates. A guest is
refused with nothing minted: the want of a token is what kept a guest's
upload out of the converter, and authenticated is not allowed to upload.
"""

from datetime import datetime as dt
from datetime import timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, select, update
from sqlalchemy.exc import TimeoutError as PoolTimeout

from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token import service as token_service
from mascope_backend.api.new.auth.access_token.service import (
    create_access_token,
    get_access_token,
)
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.db import AccessToken, User


SERVICE = "file-converter"


def _user_fixture(role_name: str, email: str, username: str):
    """A user of its own, so the tokens minted here are nobody else's."""

    @pytest_asyncio.fixture
    async def _fixture(async_session_factory, roles):
        async with async_session_factory() as session:
            user = User(
                email=email,
                username=username,
                hashed_password=PasswordHelper().hash("never-signed-in-with"),
                is_active=True,
                is_verified=True,
                role_id=roles[role_name].role_id,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        yield user
        async with async_session_factory() as session:
            await session.execute(
                delete(AccessToken).where(AccessToken.user_id == user.id)
            )
            db_user = await session.get(User, user.id)
            if db_user is not None:
                await session.delete(db_user)
            await session.commit()

    return _fixture


uploader = _user_fixture("editor", "uploader-token@test.com", "uploader-token")
guest = _user_fixture("guest", "guest-token@test.com", "guest-token")


async def _tokens(async_session_factory, user_id, service: str = SERVICE) -> list[str]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(AccessToken.token)
            .where(AccessToken.user_id == user_id)
            .where(AccessToken.service_name == service)
        )
        return list(result.scalars())


async def _age_token(async_session_factory, token: str, seconds: float) -> None:
    """Move a token's minting back by ``seconds`` and forget the validation
    cache, which would otherwise answer for the row."""
    async with async_session_factory() as session:
        await session.execute(
            update(AccessToken)
            .where(AccessToken.token == token)
            .values(created_at=dt.now(timezone.utc) - timedelta(seconds=seconds))
        )
        await session.commit()
    token_cache.clear()


@pytest.mark.asyncio
async def test_an_upload_is_handed_the_token_the_user_holds(
    uploader, async_session_factory
):
    """A valid token is kept: the user's queued uploads carry it."""
    held = await create_access_token(user=uploader, service_name=SERVICE)
    assert await get_access_token(user=uploader, service_name=SERVICE) == held
    assert await _tokens(async_session_factory, uploader.id) == [held]


@pytest.mark.asyncio
async def test_an_upload_mints_a_token_where_the_user_holds_none(
    uploader, async_session_factory
):
    """No sign-in needed first: the request is authenticated as the user."""
    minted = await get_access_token(user=uploader, service_name=SERVICE)
    assert minted
    assert await _tokens(async_session_factory, uploader.id) == [minted]


@pytest.mark.asyncio
async def test_an_upload_replaces_a_token_past_its_lifetime(
    uploader, async_session_factory
):
    lifetime = auth_settings.access_token.ACCESS_TOKEN_EXPIRATION_SECONDS
    old = await create_access_token(user=uploader, service_name=SERVICE)
    await _age_token(async_session_factory, old, lifetime + 60)
    new = await get_access_token(user=uploader, service_name=SERVICE)
    assert new != old
    assert await _tokens(async_session_factory, uploader.id) == [new]


@pytest.mark.asyncio
async def test_an_upload_near_the_tokens_end_renews_it_beside_the_old_one(
    uploader, async_session_factory
):
    """The owner's last sign-in was before the window opened; the upload
    renews. Both validate - the uploads queued under the old one keep
    validating, new ones take the newest - and the next upload mints nothing
    more."""
    settings = auth_settings.access_token
    lifetime = settings.ACCESS_TOKEN_EXPIRATION_SECONDS
    window = settings.FILE_CONVERTER_TOKEN_RENEWAL_SECONDS
    old = await create_access_token(user=uploader, service_name=SERVICE)
    await _age_token(async_session_factory, old, lifetime - window + 60)
    new = await get_access_token(user=uploader, service_name=SERVICE)
    assert new != old
    assert set(await _tokens(async_session_factory, uploader.id)) == {old, new}
    assert await get_access_token(user=uploader, service_name=SERVICE) == new
    assert len(await _tokens(async_session_factory, uploader.id)) == 2


@pytest.mark.asyncio
async def test_an_upload_before_the_window_opens_mints_nothing(
    uploader, async_session_factory
):
    """A day before the window the held token is still the answer."""
    settings = auth_settings.access_token
    lifetime = settings.ACCESS_TOKEN_EXPIRATION_SECONDS
    window = settings.FILE_CONVERTER_TOKEN_RENEWAL_SECONDS
    held = await create_access_token(user=uploader, service_name=SERVICE)
    await _age_token(async_session_factory, held, lifetime - window - 24 * 3600)
    assert await get_access_token(user=uploader, service_name=SERVICE) == held
    assert await _tokens(async_session_factory, uploader.id) == [held]


@pytest.mark.asyncio
async def test_an_upload_that_cannot_read_the_token_is_refused_not_handed_one(
    uploader, async_session_factory
):
    """A read that fails is not "no token": the upload is refused with the
    failure (a pool timeout is reported as the retryable 503 it is), and
    nothing is minted or removed."""
    held = await create_access_token(user=uploader, service_name=SERVICE)
    with (
        patch.object(
            token_service, "_newest_access_token", side_effect=PoolTimeout("pool")
        ),
        pytest.raises(ApiException) as refused,
    ):
        await get_access_token(user=uploader, service_name=SERVICE)
    assert refused.value.status_code == 503
    assert await _tokens(async_session_factory, uploader.id) == [held]


@pytest.mark.asyncio
async def test_another_services_token_is_still_refused_once_it_no_longer_validates(
    uploader, async_session_factory
):
    """Only the converter token is kept alive by the request asking. Another
    service's token is handed back while it validates and refused with a 401
    once it is past its lifetime or gone - nothing minted either way."""
    lifetime = auth_settings.access_token.ACCESS_TOKEN_EXPIRATION_SECONDS
    held = await create_access_token(user=uploader, service_name="mascope_sdk")
    assert await get_access_token(user=uploader, service_name="mascope_sdk") == held

    await _age_token(async_session_factory, held, lifetime + 60)
    with pytest.raises(ApiException) as expired:
        await get_access_token(user=uploader, service_name="mascope_sdk")
    assert expired.value.status_code == 401
    assert await _tokens(async_session_factory, uploader.id, "mascope_sdk") == [held]

    async with async_session_factory() as session:
        await session.execute(
            delete(AccessToken).where(AccessToken.user_id == uploader.id)
        )
        await session.commit()
    token_cache.clear()
    with pytest.raises(ApiException) as missing:
        await get_access_token(user=uploader, service_name="mascope_sdk")
    assert missing.value.status_code == 401
    assert await _tokens(async_session_factory, uploader.id, "mascope_sdk") == []


@pytest.mark.asyncio
async def test_a_guests_upload_is_refused_and_nothing_minted(
    guest, async_session_factory
):
    """Authenticated is not allowed to upload: a guest is refused with the
    401 the want of a token gave them before, and holds no token after."""
    with pytest.raises(ApiException) as refused:
        await get_access_token(user=guest, service_name=SERVICE)
    assert refused.value.status_code == 401
    assert await _tokens(async_session_factory, guest.id) == []


@pytest.mark.asyncio
async def test_a_mint_does_not_take_a_token_another_upload_was_just_handed(
    uploader, async_session_factory
):
    """Two uploads on an account holding no valid token both read "none" and
    mint in turn; the second's removal takes only rows past their lifetime,
    so the first's token - on its way to the converter with a file - stays.
    A lapsed token is still removed by it."""
    lifetime = auth_settings.access_token.ACCESS_TOKEN_EXPIRATION_SECONDS
    lapsed = await create_access_token(user=uploader, service_name=SERVICE)
    await _age_token(async_session_factory, lapsed, lifetime + 60)
    first = await get_access_token(user=uploader, service_name=SERVICE)
    assert first != lapsed

    # The second request read the table before the first minted
    read = token_service._newest_access_token

    async def _stale_read(user, service_name):
        token_service._newest_access_token = read
        return None

    with patch.object(token_service, "_newest_access_token", _stale_read):
        second = await get_access_token(user=uploader, service_name=SERVICE)

    assert second != first
    assert set(await _tokens(async_session_factory, uploader.id)) == {first, second}
