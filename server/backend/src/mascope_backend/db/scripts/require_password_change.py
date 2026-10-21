"""
Maintenance script to require every user account to set a new password.

Puts every account through the current password policy, for whatever reason the
operator has - a tightened rule, a periodic refresh, a concern about exposed
credentials. The requirement is soft: accounts keep signing in with their
existing password and are held at a mandatory password screen until they replace
it. Nobody is excluded, including deactivated accounts and whoever runs this.

Emits no live notification, so sessions that are already open transition when
their next request is refused; the owner-facing action in the web interface does
notify. Their open sockets are evicted from their rooms all the same: this
process serves no sockets, but the Socket.IO server's Redis manager publishes
each ``leave_room`` to the worker that holds the socket.

Set MASCOPE_REQUIRE_PASSWORD_CHANGE_DRY_RUN=1 to report what would change
without changing it.

Withdraw the requirement with `clear_password_change_requirement`.

Usage:
    mascope dev db script run require_password_change
    mascope prod db script run require_password_change

Date: 2026-08-14
"""

import asyncio
import os

from sqlalchemy import func, select

# The role names come from mascope_backend.roles, not the auth config that
# re-exports them: importing the auth package reads the JWT secret at import
# time, and the CLI discovers scripts by importing them on the host - where
# that secret may be absent - silently dropping this script from the list.
from mascope_backend.db import User, async_session, configure_database_engine
from mascope_backend.db.admin.user.require_password_change import (
    require_password_change_for_all_users,
)
from mascope_backend.roles import ROLE_ACCESS_LEVELS
from mascope_backend.runtime import runtime


#: Accounts listed individually before the summary. A deployment can have
#: hundreds of users, and this output ends up wherever the operator captured it.
_MAX_LISTED_USERS = 10


def _dry_run() -> bool:
    """Whether to report the affected accounts without changing anything."""
    return os.environ.get(
        "MASCOPE_REQUIRE_PASSWORD_CHANGE_DRY_RUN", ""
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def find_affected_users() -> list[User]:
    """
    Find accounts that do not already owe a password change.

    :return: List of affected User model instances
    """
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.must_change_password.is_(False))
        )
        return list(result.scalars().all())


async def count_users() -> int:
    """Total number of user accounts."""
    async with async_session() as session:
        return (
            await session.execute(select(func.count()).select_from(User))
        ).scalar_one()


def display_affected_users(users: list[User], total_users: int) -> None:
    """
    Display a bounded summary of the affected accounts.

    Deliberately prints neither email addresses nor a full listing: applied to a
    whole deployment this would otherwise dump the customer's user directory
    into whatever captured the output.

    :param users: List of User model instances
    :param total_users: Total number of user accounts
    """
    role_names = {level: name for name, level in ROLE_ACCESS_LEVELS.items()}
    by_role: dict[str, int] = {}
    for user in users:
        by_role[role_names.get(user.role_id, "unknown")] = (
            by_role.get(role_names.get(user.role_id, "unknown"), 0) + 1
        )

    print("=" * 80)
    print(
        f"{len(users)} of {total_users} user account(s) will be required to "
        "set a new password"
    )
    inactive = sum(1 for user in users if not user.is_active)
    if inactive:
        print(f"  (including {inactive} deactivated account(s))")
    for role_name, count in sorted(by_role.items()):
        print(f"  {role_name}: {count}")

    print("-" * 80)
    for user in users[:_MAX_LISTED_USERS]:
        print(f"  {user.username} ({user.id})")
    if len(users) > _MAX_LISTED_USERS:
        print(f"  ... and {len(users) - _MAX_LISTED_USERS} more")
    print("=" * 80)


async def run_require_password_change() -> None:
    """Find affected accounts, report them, and require the change."""
    await configure_database_engine()

    total_users = await count_users()
    affected_users = await find_affected_users()

    if not affected_users:
        runtime.logger.info(
            "All user accounts are already required to change their password. "
            "Nothing to do."
        )
        return

    display_affected_users(affected_users, total_users)

    if _dry_run():
        runtime.logger.info("Dry run - no accounts were changed.")
        return

    runtime.logger.info("Requiring a password change...")
    result = await require_password_change_for_all_users()
    data = result["data"]
    await evict_sockets(data["user_ids"])

    runtime.logger.info("=" * 80)
    runtime.logger.info("PASSWORD CHANGE REQUIRED")
    runtime.logger.info(f"Accounts affected: {data['flagged_count']}")
    runtime.logger.info(
        "Sessions that are already open transition when their next request is "
        "refused; this script sends no live notification."
    )
    runtime.logger.info(
        "Each user's API access tokens (SDK, notebooks) are revoked once they "
        "change their password, and must be regenerated. Instrument agents "
        "registered as paired machines authenticate as their own machine accounts "
        "and are not affected; an agent still using a token issued to a person - "
        "a TOF agent, or one set up before its machine was registered - is "
        "revoked with that person's tokens."
    )
    runtime.logger.info("=" * 80)


async def evict_sockets(user_ids: list[int]) -> None:
    """
    Make the open sockets of the accounts just armed leave their rooms.

    Without it a socket subscribed before the sweep keeps hearing record-sync
    events until it reconnects. A Redis that cannot be reached is reported, not
    raised: the requirement already stands, and those sockets are no worse off.

    :param user_ids: The accounts the sweep armed.
    """
    if not user_ids:
        return
    # Imported here, not at the top: importing the socket package builds the
    # Socket.IO server from the Redis configuration, and the CLI imports this
    # script on the host to list it, where that configuration may be absent.
    from mascope_backend.socket.eviction import evict_ended_sessions
    from mascope_backend.socket.storage import redis_storage_client

    try:
        await redis_storage_client.connect()
    except Exception:
        runtime.logger.exception(
            "Could not reach Redis: open sockets keep their rooms until they reconnect"
        )
        return
    try:
        evicted = 0
        for user_id in user_ids:
            evicted += await evict_ended_sessions(user_id)
    finally:
        await redis_storage_client.disconnect()
    runtime.logger.info(f"Open sockets evicted from their rooms: {evicted}")


def main() -> None:
    """Entry point for the script."""
    try:
        asyncio.run(run_require_password_change())
    except KeyboardInterrupt:
        runtime.logger.info("\nCancelled by user (Ctrl+C)")
    except Exception:
        runtime.logger.exception("Require password change script failed")
        raise


if __name__ == "__main__":
    main()
