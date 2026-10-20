"""
Dataset management routes.

This module provides workspace-scoped endpoints for dataset operations including
CRUD operations and dataset management functionality.

All dataset routes are nested under ``/api/workspaces/{workspace_id}/datasets``
and use workspace-level access control via ``require_workspace_role``.
"""

from fastapi import APIRouter, Depends, Path, Query

from mascope_backend.api.controllers.dataset.dataset_controller import (
    create_dataset,
    delete_dataset,
    get_dataset,
    get_datasets,
    move_dataset,
    update_dataset,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.dataset.dataset_pydantic_model import (
    DatasetCreate,
    DatasetMoveBody,
    DatasetUpdate,
    GetDatasetsQueryParams,
)
from mascope_backend.api.new.auth.access_rules import locked_access
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.workspaces.dependencies import (
    check_workspace_access,
    require_workspace_role,
)
from mascope_backend.db import Dataset


dataset_router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/datasets", tags=["Dataset"]
)


@dataset_router.get("")
@api_route(token_access=True)
async def get_datasets_route(
    workspace_id: str = Path(...),
    query_params: GetDatasetsQueryParams = Query(),
    user=Depends(current_active_user),
    membership=Depends(require_workspace_role("guest")),
):
    """Retrieve a list of datasets within a workspace.

    :param workspace_id: The workspace to list datasets for.
    :type workspace_id: str
    :param query_params: Query parameters for sorting and pagination.
    :type query_params: GetDatasetsQueryParams
    :param membership: Workspace membership (injected by require_workspace_role).
    :return: A dictionary containing total count and list of datasets.
    :rtype: dict
    """
    return await get_datasets(workspace_id=workspace_id, **query_params.model_dump())


@dataset_router.get("/{dataset_id}")
@api_route(token_access=True)
async def get_dataset_route(
    dataset_id: str,
    workspace_id: str = Path(...),
    user=Depends(current_active_user),
    membership=Depends(require_workspace_role("guest")),
):
    """Retrieve details of a specific dataset by ID.

    :param dataset_id: The unique identifier of the dataset.
    :type dataset_id: str
    :param workspace_id: The workspace the dataset belongs to.
    :type workspace_id: str
    :param membership: Workspace membership (injected by require_workspace_role).
    :return: A dictionary containing the dataset details.
    :rtype: dict
    """
    return await get_dataset(dataset_id, workspace_id=workspace_id)


@dataset_router.patch("/{dataset_id}")
@api_route(token_access=True)
async def update_dataset_route(
    dataset_id: str,
    dataset_update: DatasetUpdate,
    workspace_id: str = Path(...),
    user=Depends(current_active_user),
    membership=Depends(require_workspace_role("editor")),
):
    """Update an existing dataset's details.

    Locked datasets can only be updated by admins.

    :param dataset_id: The unique identifier of the dataset.
    :type dataset_id: str
    :param dataset_update: The dataset update data.
    :type dataset_update: DatasetUpdate
    :param workspace_id: The workspace the dataset belongs to.
    :type workspace_id: str
    :param membership: Workspace membership (injected by require_workspace_role).
    :return: A dictionary containing the updated dataset details.
    :rtype: dict
    """
    # Verify dataset belongs to this workspace before checking lock status
    await get_dataset(dataset_id, workspace_id=workspace_id)
    # Check if locked dataset - only admins can update
    await locked_access(
        user,
        Dataset,
        dataset_id,
        min_role="admin",
    )
    return await update_dataset(
        dataset_id=dataset_id,
        dataset_update=dataset_update,
        workspace_id=workspace_id,
        independent_transaction=True,
    )


@dataset_router.post("")
@api_route(status_code=201, token_access=True)
async def create_dataset_route(
    dataset: DatasetCreate,
    workspace_id: str = Path(...),
    user=Depends(current_active_user),
    membership=Depends(require_workspace_role("editor")),
):
    """Create a new dataset in a workspace.

    :param dataset: The dataset creation data.
    :type dataset: DatasetCreate
    :param workspace_id: The workspace to create the dataset in.
    :type workspace_id: str
    :param membership: Workspace membership (injected by require_workspace_role).
    :return: A dictionary containing the newly created dataset's details.
    :rtype: dict
    """
    return await create_dataset(
        workspace_id=workspace_id, dataset=dataset, independent_transaction=True
    )


@dataset_router.delete("/{dataset_id}")
@api_route(token_access=True)
async def delete_dataset_route(
    dataset_id: str,
    workspace_id: str = Path(...),
    user=Depends(current_active_user),
    membership=Depends(require_workspace_role("editor")),
):
    """Delete a specific dataset by ID.

    Locked datasets can only be deleted by admins.

    :param dataset_id: The unique identifier of the dataset.
    :type dataset_id: str
    :param workspace_id: The workspace the dataset belongs to.
    :type workspace_id: str
    :param membership: Workspace membership (injected by require_workspace_role).
    :return: A dictionary confirming deletion (if applicable).
    :rtype: dict or None
    """
    # Verify dataset belongs to this workspace before checking lock status
    await get_dataset(dataset_id, workspace_id=workspace_id)
    # Check if locked dataset - only admins can delete
    await locked_access(
        user,
        Dataset,
        dataset_id,
        min_role="admin",
    )
    return await delete_dataset(
        dataset_id=dataset_id,
        workspace_id=workspace_id,
        independent_transaction=True,
    )


@dataset_router.post("/{dataset_id}/move")
@api_route(token_access=True)
async def move_dataset_route(
    body: DatasetMoveBody,
    dataset_id: str,
    workspace_id: str = Path(...),
    user=Depends(current_active_user),
    membership=Depends(require_workspace_role("editor")),
):
    """Move a dataset from this workspace into another workspace.

    Source membership (editor) is enforced by require_workspace_role on the
    path workspace_id, and the controller re-verifies the dataset's
    ownership in the source workspace inside the move transaction (closing
    the TOCTOU window between authorization and mutation). Locked datasets
    require global admin, matching update/delete. Target membership (editor)
    is checked explicitly since the target comes from the body.

    :param body: The move request carrying the target workspace ID.
    :type body: DatasetMoveBody
    :param dataset_id: The unique identifier of the dataset to move.
    :type dataset_id: str
    :param workspace_id: The source workspace the dataset currently belongs to.
    :type workspace_id: str
    :param membership: Source workspace membership (injected by
                       require_workspace_role).
    :return: A dictionary containing the moved dataset's details.
    :rtype: dict
    """
    # Locked datasets: only global admins can move (consistent with update/delete)
    await locked_access(user, Dataset, dataset_id, min_role="admin")
    # Target-side RBAC - body param, so checked explicitly not as a dependency
    await check_workspace_access(body.target_workspace_id, user, "editor")
    return await move_dataset(
        dataset_id=dataset_id,
        source_workspace_id=workspace_id,
        target_workspace_id=body.target_workspace_id,
        independent_transaction=True,
    )
