"""
Maintenance script to withdraw a pending forced password change.

The way back from a `require_password_change` run that should not have happened.
There is deliberately no equivalent in the web interface: reversing a security
action belongs to whoever has shell access to the server, which also keeps the
requirement unclearable by any request to the API.

Accounts whose password an administrator reset keep that administrator-issued
password, so clearing the requirement leaves it in place. Reset those accounts
again instead of clearing them here.

Set MASCOPE_CLEAR_PASSWORD_CHANGE_EMAILS to a comma-separated list of addresses
to release only those accounts; every account is released when it is unset.

Usage:
    mascope dev db script run clear_password_change_requirement
    mascope prod db script run clear_password_change_requirement

Date: 2026-08-14
"""

import asyncio
import os

from sqlalchemy import select

from mascope_backend.db import User, async_session, configure_database_engine
from mascope_backend.db.admin.user.require_password_change import (
    clear_password_change_requirement,
)
from mascope_backend.runtime import runtime


def _target_emails() -> list[str] | None:
    """Addresses to release, or None to release every account."""
    raw = os.environ.get("MASCOPE_CLEAR_PASSWORD_CHANGE_EMAILS", "").strip()
    if not raw:
        return None
    return [email.strip() for email in raw.split(",") if email.strip()]


async def run_clear() -> None:
    """Report what is pending, then withdraw the requirement."""
    await configure_database_engine()

    emails = _target_emails()

    async with async_session() as session:
        query = select(User).where(User.must_change_password.is_(True))
        if emails is not None:
            query = query.where(User.email.in_(emails))
        affected_users = list((await session.execute(query)).scalars().all())

    if not affected_users:
        runtime.logger.info(
            "No user accounts are required to change their password. Nothing to do."
        )
        return

    scope = "all accounts" if emails is None else f"{len(emails)} named account(s)"
    runtime.logger.info(
        f"Withdrawing the password change requirement from {len(affected_users)} "
        f"user account(s) ({scope})..."
    )

    result = await clear_password_change_requirement(emails)

    runtime.logger.info("=" * 80)
    runtime.logger.info("PASSWORD CHANGE REQUIREMENT WITHDRAWN")
    runtime.logger.info(f"Accounts released: {result['data']['cleared_count']}")
    runtime.logger.info("=" * 80)


def main() -> None:
    """Entry point for the script."""
    try:
        asyncio.run(run_clear())
    except KeyboardInterrupt:
        runtime.logger.info("\nCancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Clear password change requirement script failed")
        raise


if __name__ == "__main__":
    main()
