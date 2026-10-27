"""Workspace management routes."""

from fastapi import APIRouter, Depends, Path, Query

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.dependencies import current_active_user, editor_user
from mascope_backend.api.new.workspaces.dependencies import (
    require_workspace_role,
)
from mascope_backend.api.new.workspaces.schemas import (
    WorkspaceCreate,
    WorkspaceMemberCreate,
    WorkspaceMemberUpdate,
    WorkspaceUpdate,
)
from mascope_backend.api.new.workspaces.service import (
    add_workspace_member,
    create_workspace,
    delete_workspace,
    get_workspace,
    get_workspace_members,
    get_workspaces,
    remove_workspace_member,
    update_workspace,
    update_workspace_member,
)
from mascope_backend.db import User


workspaces_router = APIRouter(prefix="/api/workspaces", tags=["Workspaces"])


@workspaces_router.get("")
@api_route(token_access=True)
async def get_workspaces_route(
    workspace_status: str | None = Query(None),
    user: User = Depends(current_active_user),
):
    """List workspaces the current user has access to."""
    return await get_workspaces(
        user=user,
        workspace_status=workspace_status,
    )


@workspaces_router.get("/{workspace_id}")
@api_route(token_access=True)
async def get_workspace_route(
    workspace_id: str = Path(...),
    user: User = Depends(current_active_user),
    membership=Depends(require_workspace_role("guest")),
):
    return await get_workspace(workspace_id=workspace_id)


@workspaces_router.post("")
@api_route(status_code=201)
async def create_workspace_route(
    body: WorkspaceCreate,
    user: User = Depends(editor_user),
):
    return await create_workspace(
        workspace_name=body.workspace_name,
        workspace_description=body.workspace_description,
        creator_user_id=user.id,
    )


@workspaces_router.patch("/{workspace_id}")
@api_route()
async def update_workspace_route(
    body: WorkspaceUpdate,
    workspace_id: str = Path(...),
    user: User = Depends(current_active_user),
    membership=Depends(require_workspace_role("admin")),
):
    return await update_workspace(
        workspace_id=workspace_id,
        workspace_name=body.workspace_name,
        workspace_description=body.workspace_description,
        workspace_status=body.workspace_status,
    )


@workspaces_router.delete("/{workspace_id}")
@api_route()
async def delete_workspace_route(
    workspace_id: str = Path(...),
    user: User = Depends(current_active_user),
    membership=Depends(require_workspace_role("owner")),
):
    return await delete_workspace(workspace_id=workspace_id, deleted_by=user.email)


# ---------------------------------------------------------------------------
# Membership sub-routes
# ---------------------------------------------------------------------------


@workspaces_router.get("/{workspace_id}/members")
@api_route()
async def get_workspace_members_route(
    workspace_id: str = Path(...),
    user: User = Depends(current_active_user),
    membership=Depends(require_workspace_role("guest")),
):
    return await get_workspace_members(workspace_id=workspace_id)


@workspaces_router.post("/{workspace_id}/members")
@api_route(status_code=201)
async def add_workspace_member_route(
    body: WorkspaceMemberCreate,
    workspace_id: str = Path(...),
    user: User = Depends(current_active_user),
    membership=Depends(require_workspace_role("admin")),
):
    return await add_workspace_member(
        workspace_id=workspace_id,
        user_id=body.user_id,
        workspace_role=body.workspace_role,
        granted_by=user.id,
        caller_role=membership.workspace_role,
    )


@workspaces_router.patch("/{workspace_id}/members/{user_id}")
@api_route()
async def update_workspace_member_route(
    body: WorkspaceMemberUpdate,
    workspace_id: str = Path(...),
    user_id: int = Path(...),
    user: User = Depends(current_active_user),
    membership=Depends(require_workspace_role("admin")),
):
    return await update_workspace_member(
        workspace_id=workspace_id,
        user_id=user_id,
        workspace_role=body.workspace_role,
        caller_role=membership.workspace_role,
    )


@workspaces_router.delete("/{workspace_id}/members/{user_id}")
@api_route()
async def remove_workspace_member_route(
    workspace_id: str = Path(...),
    user_id: int = Path(...),
    user: User = Depends(current_active_user),
    membership=Depends(require_workspace_role("guest")),
):
    # Any member can remove themselves; removing others requires admin
    if user_id != user.id:
        role_levels = auth_settings.ROLE_ACCESS_LEVELS
        if role_levels.get(membership.workspace_role, -1) < role_levels["admin"]:
            from mascope_backend.api.new.auth.exceptions import (
                ForbiddenAccessException,
            )

            raise ForbiddenAccessException()
    return await remove_workspace_member(
        workspace_id=workspace_id,
        user_id=user_id,
        caller_role=membership.workspace_role,
    )
