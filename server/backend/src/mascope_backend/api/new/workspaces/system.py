"""
Seeding an account's membership in the system (acquisition) workspaces.

Every account created outside the invitation flow needs the same starting
point: a membership row in each system workspace carrying the account's role.
Registration does it for a person; pairing approval does it for the machine
account behind an instrument agent, which would otherwise be refused on every
upload to an instrument whose workspace already exists.

Kept here, importing nothing above the ORM, so both callers share one
definition rather than each growing its own loop.
"""

from sqlalchemy import select

from mascope_backend.db import Workspace, WorkspaceMember
from mascope_backend.db.id import gen_id
from mascope_backend.roles import ROLE_ACCESS_LEVELS


async def mirror_system_workspaces(
    session, sponsor_user_id: int | None, user_id: int, workspace_role: str
) -> int:
    """
    Give an account the system workspaces its sponsor can already work in.

    Bounded by the sponsor on purpose. An instrument agent's token sits in
    plaintext on a shared instrument PC, so it must never reach further than
    the person who vouched for it: enrolling in *every* system workspace would
    hand it instruments the sponsor cannot see themselves, because a person is
    enrolled only in the workspaces that existed when they registered.

    Only workspaces where the sponsor already holds at least ``workspace_role``
    are copied, and always at exactly that role - never the sponsor's own,
    which may be higher.

    Adds to the given session without committing; the caller owns the
    transaction.

    :param session: An open async session.
    :param sponsor_user_id: The vouching account, None when there is none.
    :type sponsor_user_id: int | None
    :param user_id: The account to enrol.
    :type user_id: int
    :param workspace_role: Role granted, and the minimum the sponsor must hold.
    :type workspace_role: str
    :return: How many memberships were added.
    :rtype: int
    """
    if sponsor_user_id is None:
        return 0
    minimum = ROLE_ACCESS_LEVELS[workspace_role]
    rows = (
        await session.execute(
            select(WorkspaceMember.workspace_id, WorkspaceMember.workspace_role)
            .join(Workspace, Workspace.workspace_id == WorkspaceMember.workspace_id)
            .where(
                WorkspaceMember.user_id == sponsor_user_id,
                Workspace.is_system.is_(True),
            )
        )
    ).all()

    added = 0
    for workspace_id, sponsor_role in rows:
        if ROLE_ACCESS_LEVELS.get(sponsor_role, 0) < minimum:
            continue
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(),
                workspace_id=workspace_id,
                user_id=user_id,
                workspace_role=workspace_role,
                granted_by=sponsor_user_id,
            )
        )
        added += 1
    return added


async def add_to_system_workspaces(session, user_id: int, workspace_role: str) -> int:
    """
    Give an account membership in every system workspace.

    Adds to the given session without committing - the caller owns the
    transaction, so this can be part of a larger atomic provisioning step.

    :param session: An open async session.
    :param user_id: The account to enrol.
    :type user_id: int
    :param workspace_role: Workspace role granted in each system workspace.
    :type workspace_role: str
    :return: How many memberships were added.
    :rtype: int
    """
    workspace_ids = (
        (
            await session.execute(
                select(Workspace.workspace_id).where(Workspace.is_system.is_(True))
            )
        )
        .scalars()
        .all()
    )
    for workspace_id in workspace_ids:
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(),
                workspace_id=workspace_id,
                user_id=user_id,
                workspace_role=workspace_role,
                granted_by=None,
            )
        )
    return len(workspace_ids)
