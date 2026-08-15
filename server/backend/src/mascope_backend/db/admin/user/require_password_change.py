"""
Database operations for the deployment-wide forced password change.

Requiring a change puts every account through the current password policy. An
operator may ask for that for any reason - a tightened rule, a periodic refresh,
a concern about exposed credentials - and nothing here records which, so no
message should claim one. It is a soft requirement: the accounts keep signing in
with their existing password and are held at a mandatory password screen until
they replace it.

Entry Points:
- Async: `require_password_change_for_all_users()`, `clear_password_change_requirement()`
- Sync: `run_require_password_change_for_all_users()`, `run_clear_password_change_requirement()`
"""

import asyncio
from typing import Optional, Sequence

from sqlalchemy import func, select, update

from mascope_backend.db import User, async_session
from mascope_backend.runtime import runtime


#: Recorded on the accounts a deployment-wide sweep touches, so the password
#: screen can explain that the policy changed rather than implying the account
#: was reset by an administrator.
POLICY_REASON = "policy"


async def require_password_change_for_all_users() -> dict:
    """
    Require every account to replace its password before using the application.

    Nobody is excluded, including the owner who triggered it and accounts that
    are currently deactivated: exempting the highest-privilege account would
    defeat the point, and a deactivated account that is later reactivated must
    not walk back in on a password the policy no longer accepts.

    Only accounts that do not already owe a change are touched, which makes the
    operation idempotent and the reported count honest. ``password_changed_at``
    is deliberately left alone - no password was written.

    :return: Operation results with the counts needed to report and to notify.
    :rtype: dict
    """
    async with async_session() as session:
        total_users = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar_one()

        update_result = await session.execute(
            update(User)
            .where(User.must_change_password.is_(False))
            .values(
                must_change_password=True,
                password_change_reason=POLICY_REASON,
            )
            .returning(User.id, User.is_active)
            # The session is committed and discarded immediately below, so there
            # is no identity map worth synchronising.
            .execution_options(synchronize_session=False)
        )
        flagged = update_result.all()
        await session.commit()

    flagged_count = len(flagged)
    flagged_inactive_count = sum(1 for _, is_active in flagged if not is_active)

    if flagged_count == 0:
        message = "All user accounts were already required to change their password"
        runtime.logger.debug(message)
    else:
        message = (
            f"Password change required for {flagged_count} of "
            f"{total_users} user account(s)"
        )
        runtime.logger.info(message)

    return {
        "status": "success",
        "message": message,
        "data": {
            "flagged_count": flagged_count,
            "flagged_inactive_count": flagged_inactive_count,
            "already_flagged": total_users - flagged_count,
            "total_users": total_users,
            "user_ids": [user_id for user_id, _ in flagged],
        },
    }


async def clear_password_change_requirement(
    emails: Optional[Sequence[str]] = None,
) -> dict:
    """
    Withdraw a pending password-change requirement.

    The way back from a sweep that should not have run. Deliberately has no HTTP
    or UI equivalent: reversing a security action belongs to whoever has shell
    access to the server, and it keeps the requirement unclearable by any
    request.

    Accounts an administrator reset keep their administrator-issued password, so
    clearing the requirement leaves that password in place - reset those
    accounts again rather than clearing them here.

    :param emails: Restrict to these addresses; all accounts when omitted.
    :return: Operation results with the count of accounts released.
    :rtype: dict
    """
    statement = (
        update(User)
        .where(User.must_change_password.is_(True))
        .values(must_change_password=False, password_change_reason=None)
        .returning(User.id)
        .execution_options(synchronize_session=False)
    )
    if emails is not None:
        statement = statement.where(User.email.in_(list(emails)))

    async with async_session() as session:
        cleared = (await session.execute(statement)).all()
        await session.commit()

    cleared_count = len(cleared)
    if cleared_count == 0:
        message = "No user accounts were required to change their password"
        runtime.logger.debug(message)
    else:
        message = (
            f"Password change requirement withdrawn for {cleared_count} user account(s)"
        )
        runtime.logger.info(message)

    return {
        "status": "success",
        "message": message,
        "data": {
            "cleared_count": cleared_count,
            "user_ids": [user_id for (user_id,) in cleared],
        },
    }


def run_require_password_change_for_all_users() -> dict:
    """
    Synchronous entry point for requiring a deployment-wide password change.

    :return: Operation results
    :rtype: dict
    """
    return asyncio.run(require_password_change_for_all_users())


def run_clear_password_change_requirement(
    emails: Optional[Sequence[str]] = None,
) -> dict:
    """
    Synchronous entry point for withdrawing a password-change requirement.

    :param emails: Restrict to these addresses; all accounts when omitted.
    :return: Operation results
    :rtype: dict
    """
    return asyncio.run(clear_password_change_requirement(emails))
