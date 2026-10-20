from fastapi import APIRouter, Depends, Query

from mascope_backend.api.controllers.target.collections.target_collections_controller import (
    create_target_collection,
    delete_target_collection,
    get_target_collection,
    get_target_collections,
    update_target_collection,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    GetTargetCollectionsQueryParams,
    TargetCollectionCreate,
    TargetCollectionUpdate,
)
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
from mascope_backend.api.new.workspaces.dependencies import (
    accessible_workspace_ids_for_user,
    check_batch_access_bulk,
    check_collection_batch_change_access,
    check_target_collection_access,
    check_workspace_access,
)


target_collections_router = APIRouter(
    prefix="/api/target/collections", tags=["Target Collections"]
)


@target_collections_router.get("")
@api_route(token_access=True)
async def get_target_collections_route(
    query_params: GetTargetCollectionsQueryParams = Query(),
    user=Depends(current_active_user),
):
    """Retrieve a list of target collections.

    :param query_params: Query parameters for filtering, sorting, and pagination.
    :param user: The current authenticated user.
    :return: A dictionary containing the total count and list of target collections.
    """
    ws_ids = (
        None if user.is_superuser else await accessible_workspace_ids_for_user(user)
    )
    return await get_target_collections(
        **query_params.model_dump(),
        accessible_workspace_ids=ws_ids,
    )


@target_collections_router.get("/{target_collection_id}")
@api_route(token_access=True)
async def get_target_collection_route(
    target_collection_id: str,
    user=Depends(current_active_user),
):
    """Retrieve details of a specific target collection by ID.

    :param target_collection_id: The unique identifier of the target collection.
    :param user: The current authenticated user.
    :return: A dictionary containing the target collection details.
    """
    await check_target_collection_access(target_collection_id, user, "guest")
    return await get_target_collection(target_collection_id)


@target_collections_router.post("")
@api_route(status_code=201, token_access=True)
async def create_target_collection_route(
    body: TargetCollectionCreate,
    user=Depends(current_active_user),
) -> dict:
    """Create a new target collection.

    :param body: The data required to create a new target collection.
    :type body: TargetCollectionCreate
    :param user: The current authenticated user.
    :type user: User
    :return: A dictionary containing the created target collection and related message,
             status.
    :rtype: dict
    """
    if body.workspace_id is not None:
        await check_workspace_access(body.workspace_id, user, "editor")
    else:
        # Global collections require admin+ global role
        admin_level = auth_settings.ROLE_ACCESS_LEVELS["admin"]
        if not user.is_superuser and (
            user.role_id is None or user.role_id < admin_level
        ):
            raise ForbiddenAccessException()
    # Assigning batches requires editor access to their workspaces
    if body.sample_batch_ids:
        await check_batch_access_bulk(body.sample_batch_ids, user, "editor")
    return await create_target_collection(
        target_collection_create=body,
        independent_transaction=True,
    )


@target_collections_router.patch("/{target_collection_id}")
@api_route(token_access=True)
async def update_target_collection_route(
    target_collection_id: str,
    body: TargetCollectionUpdate,
    user=Depends(current_active_user),
) -> dict:
    """Update an existing target collection.

    :param target_collection_id: The unique ID of the target collection to update.
    :type target_collection_id: str
    :param body: The data required to update the target collection.
    :type body: TargetCollectionUpdate
    :param user: The current authenticated user.
    :type user: User
    :return: Update results details
    :rtype: dict
    """
    # A batches-only payload changes associations, not the collection itself:
    # read access to the collection suffices here, and the per-batch editor
    # ACL below carries the mutation rights. This lets workspace editors bulk
    # assign global collections to their own workspaces' batches.
    batches_only = body.sample_batch_ids is not None and body.model_fields_set <= {
        "sample_batch_ids"
    }

    await check_target_collection_access(
        target_collection_id, user, "guest" if batches_only else "editor"
    )

    # If scope is being changed, validate access to the new scope
    if "workspace_id" in body.model_fields_set:
        if body.workspace_id is not None:
            # Moving to a specific workspace — require editor in that workspace
            await check_workspace_access(body.workspace_id, user, "editor")
        else:
            # Moving to global — require admin+ global role
            admin_level = auth_settings.ROLE_ACCESS_LEVELS["admin"]
            if not user.is_superuser and (
                user.role_id is None or user.role_id < admin_level
            ):
                raise ForbiddenAccessException()

    # Changing batch associations requires editor access to the workspaces of
    # the batches being added or removed (preserved associations are exempt)
    if body.sample_batch_ids is not None:
        await check_collection_batch_change_access(
            target_collection_id, body.sample_batch_ids, user, "editor"
        )

    return await update_target_collection(
        target_collection_id=target_collection_id,
        target_collection_update=body,
        independent_transaction=True,
    )


@target_collections_router.delete("/{target_collection_id}")
@api_route()
async def delete_target_collection_route(
    target_collection_id: str,
    delete_orphan_compounds: bool = Query(
        False,
        description="Delete orphan compounds associated with the target collection",
    ),
    user=Depends(current_active_user),
) -> dict:
    """Delete a specific target collection by ID.

    :param target_collection_id: The unique ID of the target collection to delete.
    :type target_collection_id: str
    :param delete_orphan_compounds: Boolean flag to delete orphan compounds associated
                                    with the collection.
    :type delete_orphan_compounds: bool
    :param user: The current authenticated user.
    :type user: User
    :return: A dictionary confirming deletion and providing related logs.
    :rtype: dict
    """
    await check_target_collection_access(target_collection_id, user, "editor")
    return await delete_target_collection(
        target_collection_id=target_collection_id,
        delete_orphan_compounds=delete_orphan_compounds,
        independent_transaction=True,
    )
