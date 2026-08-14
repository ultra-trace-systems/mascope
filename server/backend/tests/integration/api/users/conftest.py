"""
Fixtures for the user-management integration tests.

Everything in this package writes passwords, and a password written by anyone
other than the account holder now requires that account to replace it. The
shared ``test_users`` fixture is session-scoped, so a test that resets one of
those passwords would otherwise leave the account behind the password gate for
the rest of the run - and every later test authenticating as it would be
refused, in a suite that looks unrelated.
"""

import pytest_asyncio
from sqlalchemy import update

from mascope_backend.db import User


@pytest_asyncio.fixture(autouse=True)
async def clear_password_change_requirements(async_session_factory):
    """Release every account from a pending password change after each test."""
    yield
    async with async_session_factory() as session:
        await session.execute(
            update(User)
            .where(User.must_change_password.is_(True))
            .values(must_change_password=False, password_change_reason=None)
        )
        await session.commit()
