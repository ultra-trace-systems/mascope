"""
Seeding an account's membership in the system (acquisition) workspaces.

Every account created outside the invitation flow needs the same starting
point: a membership row in each system workspace carrying the account's role.
Registration does it for a person; pairing approval does it for the machine
account behind an instrument agent, which would otherwise be refused on every
upload to an instrument whose workspace already exists.

Kept here so both callers share one definition rather than each growing its
own loop. ``mirror_system_workspaces`` still imports nothing above the ORM,
which the pairing path relies on; ``add_to_system_workspaces`` goes through
the workspace-member controller and imports it inside the function so that
stays true.
"""

from sqlalchemy import select

from mascope_backend.db import Workspace, WorkspaceMember, async_session
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


async def add_to_system_workspaces(user_id: int, workspace_role: str) -> int:
    """
    Give an account membership in every system workspace, if its role earns one.

    Only admins and owners are enrolled. That is the rule creating a system
    workspace already follows (`_ROLE_MAP` in the acquisition dataset service)
    and the one `docs/authorization.md` states: "Global guests and editors are
    not automatically added - they must be invited." Registration used to
    enrol every new account at its matching role instead, so a guest created
    today reached every instrument on the deployment while a guest created
    before those workspaces existed reached none. The two paths now agree.

    Goes through the workspace-member controller rather than inserting the
    rows here, so a membership created at registration is built exactly like
    one an administrator adds through the members endpoint - same validation,
    same duplicate rule, same record-reload event.

    There is no acting member to bound the grant, so the role ceiling is passed
    as the role being granted. Self-referential on purpose: a no-op that can
    never let this path assign more than it was asked for, which is the honest
    encoding of a system-initiated grant without opening a bypass door in a
    security helper four route paths depend on.

    Each membership is its own transaction, because the controller owns one. A
    failure part-way leaves the memberships already granted in place - the
    better of the two half-states, since the account can then reach the
    instruments it was enrolled in rather than none of them. Registration was
    never atomic with this anyway: the user row is committed before this runs.

    :param user_id: The account to enrol.
    :type user_id: int
    :param workspace_role: Workspace role granted in each system workspace.
    :type workspace_role: str
    :return: How many memberships were added.
    :rtype: int
    """
    if ROLE_ACCESS_LEVELS[workspace_role] < ROLE_ACCESS_LEVELS["admin"]:
        # A guest or an editor is invited to the instruments they work on,
        # never enrolled in all of them by existing.
        return 0

    # Imported here, not at module scope: mirror_system_workspaces above is
    # imported by the pairing path and stays deliberately ORM-only.
    from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
    from mascope_backend.api.new.workspaces.service import add_workspace_member

    async with async_session() as session:
        workspace_ids = (
            (
                await session.execute(
                    select(Workspace.workspace_id).where(Workspace.is_system.is_(True))
                )
            )
            .scalars()
            .all()
        )

    added = 0
    for workspace_id in workspace_ids:
        try:
            await add_workspace_member(
                workspace_id=workspace_id,
                user_id=user_id,
                workspace_role=workspace_role,
                caller_role=workspace_role,
                granted_by=None,
            )
        except ApiException as e:
            # Already a member. An instrument workspace being created
            # concurrently enrols every existing account, so it can win that
            # race; the membership being asked for exists, nothing to do.
            # Matched on the status, never on the original exception class:
            # add_workspace_member is wrapped in @api_controller, which turns
            # WorkspaceMemberAlreadyExistsException (an HTTPException, not an
            # ApiException) into ApiException(status_code=409).
            if e.status_code != 409:
                raise
            continue
        added += 1
    return added
