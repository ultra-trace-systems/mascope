"""Tests: what an account change does to the user's file-converter tokens.

Each upload mints its own converter token (``get_access_token``), so nothing
else mints one: a promotion to editor and a password change used to, when the
server handed every upload the one token the user held, and a token minted
there now could never reach an upload. What still happens is revocation: a
demotion below editor and a password change remove the converter tokens the
user holds, the ones queued work carries included. The demo seeder's fixed
tokens are stored as digests like every other token.
"""

import pytest
import pytest_asyncio
from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, select

from mascope_backend.api.new.auth.access_token.service import get_access_token
from mascope_backend.api.new.users.schemas import UserUpdate
from mascope_backend.api.new.users.user_manager.util import get_user_manager_context
from mascope_backend.db import AccessToken, User, hash_access_token
from mascope_backend.db.scripts import seed_demo


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


editor = _user_fixture("editor", "changes-editor@test.com", "changes-editor")
guest = _user_fixture("guest", "changes-guest@test.com", "changes-guest")


async def _converter_tokens(async_session_factory, user_id) -> set[str]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(AccessToken.token_hash)
            .where(AccessToken.user_id == user_id)
            .where(AccessToken.service_name == SERVICE)
        )
        return set(result.scalars())


async def _update(async_session_factory, user: User, **changes) -> None:
    async with async_session_factory() as session:
        db_user = await session.get(User, user.id)
        async with get_user_manager_context(session) as user_manager:
            await user_manager.update(UserUpdate(**changes), db_user)


@pytest.mark.asyncio
async def test_a_promotion_to_editor_mints_no_converter_token(
    guest, roles, async_session_factory
):
    await _update(async_session_factory, guest, role_id=roles["editor"].role_id)
    assert await _converter_tokens(async_session_factory, guest.id) == set()


@pytest.mark.asyncio
async def test_a_demotion_below_editor_revokes_the_converter_tokens(
    editor, roles, async_session_factory
):
    await get_access_token(user=editor, service_name=SERVICE)
    await _update(async_session_factory, editor, role_id=roles["guest"].role_id)
    assert await _converter_tokens(async_session_factory, editor.id) == set()


@pytest.mark.asyncio
async def test_a_password_change_revokes_the_converter_tokens_and_mints_none(
    editor, async_session_factory
):
    await get_access_token(user=editor, service_name=SERVICE)
    await _update(
        async_session_factory, editor, password="a-New-and-Long-passphrase-42"
    )
    assert await _converter_tokens(async_session_factory, editor.id) == set()


@pytest.mark.asyncio
async def test_the_demo_seeder_stores_its_tokens_as_digests(
    editor, async_session_factory
):
    async with async_session_factory() as session:
        db_user = await session.get(User, editor.id)
        await seed_demo._refresh_demo_tokens(session, db_user)
        await session.commit()
        stored = dict(
            (
                await session.execute(
                    select(AccessToken.service_name, AccessToken.token_hash).where(
                        AccessToken.user_id == editor.id
                    )
                )
            ).all()
        )
    assert stored == {
        service: hash_access_token(token)
        for service, token in seed_demo.DEMO_TOKENS.items()
    }
    # Each upload mints the converter's token; a fixed one would be a
    # credential nothing uses.
    assert SERVICE not in stored
