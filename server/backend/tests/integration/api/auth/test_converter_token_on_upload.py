"""Tests: each upload is handed a file-converter token minted for it.

The upload routes ask ``get_access_token`` for the token the converter
writes the upload's results back with, and the request asking is
authenticated as the user already. The server keeps only a digest of each
token, so it holds none it could hand out again: every request that hands the
converter work mints one, which the converter carries with that work. A
converter token lapses after ``FILE_CONVERTER_TOKEN_LIFETIME_SECONDS``,
shorter than any other token's, and the user's tokens past it are reaped at
their next mint. No other service's token is handed out by the server at all.
A guest is refused with nothing minted: the want of a token is what kept a
guest's upload out of the converter, and authenticated is not allowed to
upload.
"""

from datetime import datetime as dt
from datetime import timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import TimeoutError as PoolTimeout

from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token import service as token_service
from mascope_backend.api.new.auth.access_token.service import (
    create_access_token,
    get_access_token,
)
from mascope_backend.api.new.auth.access_token.validation import (
    validate_service_access_token,
)
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import InvalidTokenException
from mascope_backend.app.fast import fast
from mascope_backend.db import AccessToken, User, hash_access_token


SERVICE = "file-converter"
DAY = 24 * 60 * 60


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
other_uploader = _user_fixture(
    "editor", "other-uploader-token@test.com", "other-uploader-token"
)
guest = _user_fixture("guest", "guest-token@test.com", "guest-token")


async def _token_hashes(
    async_session_factory, user_id, service: str = SERVICE
) -> set[str]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(AccessToken.token_hash)
            .where(AccessToken.user_id == user_id)
            .where(AccessToken.service_name == service)
        )
        return set(result.scalars())


async def _age_token(async_session_factory, token: str, seconds: float) -> None:
    """Move a token's minting back by ``seconds`` and forget the validation
    cache, which would otherwise answer for the row."""
    async with async_session_factory() as session:
        await session.execute(
            update(AccessToken)
            .where(AccessToken.token_hash == hash_access_token(token))
            .values(created_at=dt.now(timezone.utc) - timedelta(seconds=seconds))
        )
        await session.commit()
    token_cache.clear()


async def _bearer_status(token: str, service: str) -> int:
    """What a bearer request on the token is answered."""
    async with AsyncClient(
        transport=ASGITransport(app=fast),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}", "X-Service-Name": service},
    ) as client:
        return (await client.get("/api/workspaces")).status_code


@pytest.mark.asyncio
async def test_each_upload_is_handed_a_token_of_its_own(
    uploader, async_session_factory
):
    """Two uploads, two tokens, both stored as digests only, and both
    authenticate: the first upload's work is still queued under its own."""
    first = await get_access_token(user=uploader, service_name=SERVICE)
    second = await get_access_token(user=uploader, service_name=SERVICE)
    assert first != second
    assert await _token_hashes(async_session_factory, uploader.id) == {
        hash_access_token(first),
        hash_access_token(second),
    }
    for token in (first, second):
        assert await validate_service_access_token(token, SERVICE)
        assert await _bearer_status(token, SERVICE) == 200


@pytest.mark.asyncio
async def test_a_mint_reaps_the_users_tokens_past_the_converter_lifetime(
    uploader, async_session_factory
):
    """Only those: a token a day short of it carries queued work still."""
    lifetime = auth_settings.access_token.FILE_CONVERTER_TOKEN_LIFETIME_SECONDS
    lapsed = await get_access_token(user=uploader, service_name=SERVICE)
    queued = await get_access_token(user=uploader, service_name=SERVICE)
    await _age_token(async_session_factory, lapsed, lifetime + 60)
    await _age_token(async_session_factory, queued, lifetime - DAY)

    new = await get_access_token(user=uploader, service_name=SERVICE)
    assert await _token_hashes(async_session_factory, uploader.id) == {
        hash_access_token(queued),
        hash_access_token(new),
    }


@pytest.mark.asyncio
async def test_a_mint_reaps_only_the_users_own_converter_tokens(
    uploader, other_uploader, async_session_factory
):
    """The uploader's SDK token and another user's converter token, both
    past the converter lifetime, are left alone: the SDK token is within its
    own lifetime still, and the other user's tokens are theirs to reap."""
    lifetime = auth_settings.access_token.FILE_CONVERTER_TOKEN_LIFETIME_SECONDS
    sdk = await create_access_token(user=uploader, service_name="mascope_sdk")
    others = await get_access_token(user=other_uploader, service_name=SERVICE)
    for token in (sdk, others):
        await _age_token(async_session_factory, token, lifetime + DAY)

    await get_access_token(user=uploader, service_name=SERVICE)
    assert await _token_hashes(async_session_factory, uploader.id, "mascope_sdk") == {
        hash_access_token(sdk)
    }
    assert await _token_hashes(async_session_factory, other_uploader.id) == {
        hash_access_token(others)
    }


@pytest.mark.asyncio
async def test_a_converter_token_lapses_after_thirty_days(
    uploader, async_session_factory
):
    """Refused past its own lifetime on both paths - the bearer request with
    the 401 any expired token gets, the socket validation as invalid - while
    an SDK token of the same age still authenticates."""
    assert auth_settings.access_token.FILE_CONVERTER_TOKEN_LIFETIME_SECONDS == (
        30 * DAY
    )
    converter = await get_access_token(user=uploader, service_name=SERVICE)
    sdk = await create_access_token(user=uploader, service_name="mascope_sdk")

    await _age_token(async_session_factory, converter, 29 * DAY)
    assert await _bearer_status(converter, SERVICE) == 200

    for token in (converter, sdk):
        await _age_token(async_session_factory, token, 31 * DAY)
    assert await _bearer_status(converter, SERVICE) == 401
    with pytest.raises(InvalidTokenException):
        await validate_service_access_token(converter, SERVICE)
    assert await _bearer_status(sdk, "mascope_sdk") == 200
    assert await validate_service_access_token(sdk, "mascope_sdk")


@pytest.mark.asyncio
async def test_an_sdk_token_lapses_after_ninety_days(uploader, async_session_factory):
    """The default lifetime: a day short of it the token works, a day past it
    the bearer request is refused with the usual 401."""
    assert auth_settings.access_token.ACCESS_TOKEN_EXPIRATION_SECONDS == 90 * DAY
    sdk = await create_access_token(user=uploader, service_name="mascope_sdk")
    await _age_token(async_session_factory, sdk, 89 * DAY)
    assert await _bearer_status(sdk, "mascope_sdk") == 200
    await _age_token(async_session_factory, sdk, 91 * DAY)
    assert await _bearer_status(sdk, "mascope_sdk") == 401


@pytest.mark.asyncio
async def test_an_upload_that_cannot_reap_is_refused_not_handed_a_token(
    uploader, async_session_factory
):
    """A read that fails is reported, not passed over: the upload is refused
    with the failure (a pool timeout is the retryable 503 it is), and
    nothing is minted."""
    with (
        patch.object(token_service, "async_session", side_effect=PoolTimeout("pool")),
        pytest.raises(ApiException) as refused,
    ):
        await get_access_token(user=uploader, service_name=SERVICE)
    assert refused.value.status_code == 503
    assert await _token_hashes(async_session_factory, uploader.id) == set()


@pytest.mark.asyncio
async def test_no_other_services_token_is_handed_out(uploader, async_session_factory):
    """The server holds only digests: an SDK token is shown once, when it is
    minted, and asking for it again is refused with a 401 and mints none."""
    held = await create_access_token(user=uploader, service_name="mascope_sdk")
    with pytest.raises(ApiException) as refused:
        await get_access_token(user=uploader, service_name="mascope_sdk")
    assert refused.value.status_code == 401
    assert await _token_hashes(async_session_factory, uploader.id, "mascope_sdk") == {
        hash_access_token(held)
    }


@pytest.mark.asyncio
async def test_a_guests_upload_is_refused_and_nothing_minted(
    guest, async_session_factory
):
    """Authenticated is not allowed to upload: a guest is refused with the
    401 the want of a token gave them before, and holds no token after."""
    with pytest.raises(ApiException) as refused:
        await get_access_token(user=guest, service_name=SERVICE)
    assert refused.value.status_code == 401
    assert await _token_hashes(async_session_factory, guest.id) == set()
