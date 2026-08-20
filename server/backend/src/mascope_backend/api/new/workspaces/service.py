"""
Workspace CRUD service module.

Provides core CRUD operations for workspaces and workspace membership.
"""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import asc, func, select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.workspaces.exceptions import (
    WorkspaceMemberAlreadyExistsException,
    WorkspaceMemberNotFoundException,
    WorkspaceNotFoundException,
)
from mascope_backend.db import User, Workspace, WorkspaceMember, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.socket.records.service import (
    emit_record_reload,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _check_last_owner(session, workspace_id: str) -> None:
    """Raise 403 if the workspace has only one owner."""
    result = await session.execute(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.workspace_role == "owner",
        )
    )
    if result.scalar() <= 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Each workspace must have at least one owner.",
        )


def _enforce_role_ceiling(caller_role: str, target_role: str) -> None:
    """Raise 403 if *target_role* exceeds the caller's own level.

    Owners can assign any role (including owner).
    Admins can assign up to admin.
    """
    role_levels = auth_settings.ROLE_ACCESS_LEVELS

    if caller_role not in role_levels:
        raise ValueError(f"Unknown caller role: {caller_role}")
    if target_role not in role_levels:
        raise ValueError(f"Unknown target role: {target_role}")
    if role_levels[target_role] > role_levels[caller_role]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cannot assign role '{target_role}',"
                " as it exceeds your own workspace role."
            ),
        )


# ---------------------------------------------------------------------------
# Workspace CRUD
# ---------------------------------------------------------------------------


@api_controller()
async def get_workspaces(
    user: User,
    workspace_status: str | None = None,
) -> dict:
    """List workspaces visible to *user*.

    Regular users see only workspaces they are a member of.
    Superusers see all workspaces, annotated with ``is_member``.

    Every record carries ``my_role``: the caller's own role in that workspace,
    so a client can gate an action on what the backend will enforce instead of
    offering it and letting the request come back 403. Superusers report
    ``owner`` everywhere, which is what ``_enforce`` grants them regardless of
    membership. It is ``None`` only for a workspace the caller is not a member
    of, which only a superuser ever sees listed.

    ``my_role`` describes workspace membership and nothing else. A global admin
    additionally bypasses the instrument-workspace checks on raw files without
    holding a membership, so a client gating a file-level action wants
    ``my_role`` *or* the global role - see ``docs/authorization.md``.
    """
    async with async_session() as session:
        # The caller's memberships, read once and used to annotate every record
        # below. Kept separate from the listing query so the superuser branch
        # (which does not join membership at all) can be annotated the same way.
        member_rows = await session.execute(
            select(
                WorkspaceMember.workspace_id,
                WorkspaceMember.workspace_role,
            ).where(WorkspaceMember.user_id == user.id)
        )
        my_roles = dict(member_rows.all())

        query = select(Workspace)

        if workspace_status:
            query = query.where(Workspace.workspace_status == workspace_status)

        if user.is_superuser:
            # Superusers see everything; annotate membership
            member_ids = set(my_roles)
        else:
            # Regular users see only their workspaces
            query = query.join(WorkspaceMember).where(
                WorkspaceMember.user_id == user.id
            )
            member_ids = None

        query = query.order_by(asc(Workspace.workspace_name))
        result = await session.execute(query)
        workspaces = result.scalars().all()

        data = []
        for ws in workspaces:
            record = ws.to_dict()
            if member_ids is not None:
                record["is_member"] = ws.workspace_id in member_ids
            record["my_role"] = (
                "owner" if user.is_superuser else my_roles.get(ws.workspace_id)
            )
            data.append(record)

        return {
            "data": data,
            "total": len(data),
        }


@api_controller()
async def get_workspace(workspace_id: str) -> dict:
    """Get a single workspace by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise WorkspaceNotFoundException(workspace_id)
        return {"data": workspace.to_dict()}


@api_controller()
async def create_workspace(
    workspace_name: str,
    creator_user_id: int,
    workspace_description: str | None = None,
) -> dict:
    """Create a new workspace and add the creator as owner."""
    now = datetime.now(timezone.utc)
    workspace_id = gen_id()

    async with async_session() as session:
        # Reject duplicate workspace names early with a clear message
        existing = await session.execute(
            select(Workspace).where(
                func.lower(Workspace.workspace_name) == workspace_name.strip().lower()
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A workspace named '{workspace_name.strip()}' already exists.",
            )

        workspace = Workspace(
            workspace_id=workspace_id,
            workspace_name=workspace_name.strip(),
            workspace_description=workspace_description,
            workspace_status="active",
            workspace_utc_created=now,
            workspace_utc_modified=now,
        )
        session.add(workspace)

        # Auto-add the creating user as workspace owner
        member = WorkspaceMember(
            workspace_member_id=gen_id(),
            workspace_id=workspace_id,
            user_id=creator_user_id,
            workspace_role="owner",
            granted_at=now,
            granted_by=creator_user_id,
        )
        session.add(member)

        await session.commit()

    await emit_record_reload(record_type="workspace")
    return {"data": workspace.to_dict()}


@api_controller()
async def update_workspace(
    workspace_id: str,
    workspace_name: str | None = None,
    workspace_description: str | None = None,
    workspace_status: str | None = None,
) -> dict:
    """Update a workspace's metadata."""
    async with async_session() as session:
        result = await session.execute(
            select(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise WorkspaceNotFoundException(workspace_id)

        if workspace.is_system:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="System workspaces cannot be modified.",
            )

        if workspace_name is not None:
            # Reject duplicate names (case-insensitive), excluding self
            dup = await session.execute(
                select(Workspace).where(
                    func.lower(Workspace.workspace_name)
                    == workspace_name.strip().lower(),
                    Workspace.workspace_id != workspace_id,
                )
            )
            if dup.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A workspace named '{workspace_name.strip()}' already exists.",
                )
            workspace.workspace_name = workspace_name.strip()
        if workspace_description is not None:
            workspace.workspace_description = workspace_description
        if workspace_status is not None:
            workspace.workspace_status = workspace_status

        workspace.workspace_utc_modified = datetime.now(timezone.utc)
        await session.commit()

    await emit_record_reload(record_type="workspace", room=workspace_id)
    return {"data": workspace.to_dict()}


@api_controller()
async def delete_workspace(workspace_id: str) -> dict:
    """Delete a workspace and all its datasets (cascading)."""
    async with async_session() as session:
        result = await session.execute(
            select(Workspace).where(Workspace.workspace_id == workspace_id)
        )
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise WorkspaceNotFoundException(workspace_id)

        await session.delete(workspace)
        await session.commit()

    await emit_record_reload(record_type="workspace")
    return {"data": {"workspace_id": workspace_id, "deleted": True}}


# ---------------------------------------------------------------------------
# Workspace membership
# ---------------------------------------------------------------------------


@api_controller()
async def get_workspace_members(workspace_id: str) -> dict:
    """List all members of a workspace with usernames."""
    async with async_session() as session:
        result = await session.execute(
            select(WorkspaceMember, User.username)
            .join(User, WorkspaceMember.user_id == User.id)
            .where(WorkspaceMember.workspace_id == workspace_id)
        )
        rows = result.all()
        return {
            "data": [{**m.to_dict(), "username": username} for m, username in rows],
            "total": len(rows),
        }


@api_controller()
async def add_workspace_member(
    workspace_id: str,
    user_id: int,
    workspace_role: str,
    caller_role: str,
    granted_by: int | None = None,
) -> dict:
    """Add a user to a workspace with a given role.

    :param workspace_id: The workspace to add the member to.
    :param user_id: The ID of the user to add.
    :param workspace_role: The role to assign (e.g. "guest", "editor", "admin", "owner")
    :param caller_role: The workspace role of the authenticated user performing
        the action. Used to enforce role ceiling — the assigned role must not
        exceed the caller's own level.
    :param granted_by: The ID of the user granting the membership.
    :raises HTTPException: 403 if the assigned role exceeds the caller's level.
    :raises WorkspaceNotFoundException: If the workspace does not exist.
    :raises NotFoundException: If the user does not exist.
    :raises WorkspaceMemberAlreadyExistsException: If the user is already a member.
    """
    _enforce_role_ceiling(caller_role, workspace_role)
    async with async_session() as session:
        # Validate workspace exists
        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundException(workspace_id)

        # Validate user exists
        user = await session.get(User, user_id)
        if user is None:
            raise NotFoundException(f"User with ID '{user_id}' not found.")

        # Check if already a member — reject duplicate
        result = await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        if result.scalar_one_or_none() is not None:
            raise WorkspaceMemberAlreadyExistsException(workspace_id, user_id)

        member = WorkspaceMember(
            workspace_member_id=gen_id(),
            workspace_id=workspace_id,
            user_id=user_id,
            workspace_role=workspace_role,
            granted_at=datetime.now(timezone.utc),
            granted_by=granted_by,
        )
        session.add(member)
        await session.commit()

    await emit_record_reload(
        record_type="workspace",
        room=[workspace_id, f"user-{user_id}"],
    )
    return {"data": member.to_dict()}


@api_controller()
async def update_workspace_member(
    workspace_id: str,
    user_id: int,
    workspace_role: str,
    caller_role: str,
) -> dict:
    """Update a member's role in a workspace.

    :param workspace_id: The workspace containing the member.
    :param user_id: The ID of the member to update.
    :param workspace_role: The new role to assign.
    :param caller_role: The workspace role of the authenticated user. Used to
        enforce role ceiling — the new role must not exceed the caller's own level.
    :raises HTTPException: 403 if the new role exceeds the caller's level, or if
        demoting the last owner.
    :raises WorkspaceMemberNotFoundException: If the user is not a member.
    """
    _enforce_role_ceiling(caller_role, workspace_role)
    async with async_session() as session:
        # Validate workspace exists
        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundException(workspace_id)

        result = await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise WorkspaceMemberNotFoundException(
                f"User {user_id} is not a member of workspace {workspace_id}."
            )

        # Prevent demoting the last owner
        if member.workspace_role == "owner" and workspace_role != "owner":
            await _check_last_owner(session, workspace_id)

        member.workspace_role = workspace_role
        await session.commit()

    await emit_record_reload(record_type="workspace", room=workspace_id)
    return {"data": member.to_dict()}


@api_controller()
async def remove_workspace_member(
    workspace_id: str, user_id: int, caller_role: str
) -> dict:
    """Remove a user from a workspace.

    :param workspace_id: The workspace containing the member.
    :param user_id: The ID of the member to remove.
    :param caller_role: The workspace role of the caller. Enforces a role
        ceiling, the caller cannot remove a member whose role exceeds
        their own (e.g. admin cannot remove owner).
    """
    async with async_session() as session:
        # Validate workspace exists
        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundException(workspace_id)

        result = await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise WorkspaceMemberNotFoundException(
                f"User {user_id} is not a member of workspace {workspace_id}."
            )

        # Enforce role ceiling: caller cannot remove a higher-ranked member
        _enforce_role_ceiling(caller_role, member.workspace_role)

        if member.workspace_role == "owner":
            await _check_last_owner(session, workspace_id)

        await session.delete(member)
        await session.commit()

    await emit_record_reload(
        record_type="workspace",
        room=[workspace_id, f"user-{user_id}"],
    )
    return {"data": {"workspace_id": workspace_id, "user_id": user_id, "removed": True}}
