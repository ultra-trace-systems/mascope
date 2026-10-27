from typing import List

from fastapi import APIRouter, Depends

from mascope_backend.api.controllers.target.compounds.target_compound_structures import (
    find_target_compound_structures,
)
from mascope_backend.api.controllers.target.compounds.target_compounds_controller import (
    create_target_compound,
    delete_target_compound,
    get_target_compound,
    get_target_compounds,
    update_target_compound,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    FindTargetCompoundStructuresQueryParams,
    GetTargetCompoundsQueryParams,
    TargetCompoundBase,
    TargetCompoundUpdate,
)
from mascope_backend.api.new.auth.dependencies import editor_user, guest_user
from mascope_backend.api.new.workspaces.dependencies import (
    accessible_workspace_ids_for_user,
    check_batch_access,
    check_target_collection_access,
)


# TODO_target_compound_management refactor to send the same result as as other routes

target_compounds_router = APIRouter(
    prefix="/api/target/compounds", tags=["Target Compounds"]
)


@target_compounds_router.get("")
@api_route()
async def get_target_compounds_route(
    query_params: GetTargetCompoundsQueryParams = Depends(),
    user=Depends(guest_user),
):
    """Retrieve a list of target compounds with optional filters and pagination.

    A named ``sample_batch_id`` or ``target_collection_id`` needs the guest
    role on its workspace, as the collection and association routes do; a
    global collection is readable by anyone signed in. Without either filter
    the list is the shared compound and ion library. ``show_target_collection``
    lists only the collections the caller can read: the global ones and those
    of the workspaces they are a member of (superusers see all).

    :param query_params: Query parameters for filtering, sorting, and pagination.
    :param user: The authenticated user, defaults to Depends(guest_user).
    :return: Dictionary with total results and data list of target compounds.
    """
    if query_params.sample_batch_id:
        await check_batch_access(query_params.sample_batch_id, user, "guest")
    if query_params.target_collection_id:
        await check_target_collection_access(
            query_params.target_collection_id, user, "guest"
        )
    ws_ids = (
        None if user.is_superuser else await accessible_workspace_ids_for_user(user)
    )
    return await get_target_compounds(
        **query_params.model_dump(), accessible_workspace_ids=ws_ids
    )


# Before /{target_compound_id}, which would otherwise take "structures" for an ID.
@target_compounds_router.get("/structures")
@api_route(token_access=True)
async def find_target_compound_structures_route(
    query_params: FindTargetCompoundStructuresQueryParams = Depends(),
    user=Depends(guest_user),
):
    """List the reference compounds that could give a compound its structure.

    The active reference rows whose Hill formula is the one asked about and
    that carry an InChIKey, an InChI or a SMILES, one per compound per
    source, each with its source and licence. The rows recording the CAS
    number asked about come first, then those of the name, then a stable
    order. Paged; nothing is written.

    :param query_params: The formula, the CAS number and name to rank on, and
        the page.
    :param user: The authenticated user, defaults to Depends(guest_user).
    :return: ``results`` (the rows on every page), ``formula`` (canonical) and
        ``data``, the page's rows.
    """
    return await find_target_compound_structures(**query_params.model_dump())


@target_compounds_router.get("/{target_compound_id}")
@api_route()
async def get_target_compound_route(
    target_compound_id: str,
    user=Depends(guest_user),
):
    """Retrieve details of a specific target compound by ID.

    :param target_compound_id: The unique identifier of the target compound.
    :param user: The authenticated user, defaults to Depends(guest_user).
    :return: Dictionary with detailed information of the target compound.
    """
    return await get_target_compound(target_compound_id)


@target_compounds_router.post("")
@api_route(status_code=201, token_access=True)
async def create_target_compounds_route(
    target_compounds: List[TargetCompoundBase], user=Depends(editor_user)
):
    """Create new target compounds with specified details.

    :param target_compounds: List of target compounds to create.
    :param background_tasks: Background tasks for asynchronous operations.
    :param user: The authenticated editor user, defaults to Depends(editor_user).
    :return: Dictionary with details of created and existing target compounds.
    """
    result = await create_target_compound(
        target_compounds=target_compounds,
        independent_transaction=True,
    )
    response = {
        "message": "Target compounds created successfully",
        "result": {
            "created_compounds_result": len(result["created_compounds"]),
            "existing_compounds_result": len(result["existing_compounds"]),
        },
        "data": {
            "created_compounds": result["created_compounds"],
            "existing_compounds": result["existing_compounds"],
        },
        "message-logs": result["message_logs"],
    }
    return response


@target_compounds_router.patch("")
@api_route(token_access=True)
async def update_target_compound_route(
    target_compounds: List[TargetCompoundUpdate],
    user=Depends(editor_user),
):
    """Update details of target compounds.

    :param target_compounds: List of target compounds with updated data.
    :param user: The authenticated editor user, defaults to Depends(editor_user).
    :return: Dictionary with counts of updated, unchanged, and existing compounds.
    """
    result = await update_target_compound(target_compounds)
    response = {
        "message": "Target compounds updated successfully",
        "result": {
            "not_changed_compounds_results": len(result["not_changed_compounds"]),
            "updated_compounds_results": len(result["updated_compounds"]),
            "not_updated_compounds_results": len(result["not_updated_compounds"]),
            "existing_compounds_results": len(result["existing_compounds"]),
        },
        "data": {
            "not_changed_compounds": result["not_changed_compounds"],
            "updated_compounds": result["updated_compounds"],
            "not_updated_compounds": result["not_updated_compounds"],
            "existing_compounds": result["existing_compounds"],
        },
        "message-logs": result["message_logs"],
    }
    return response


@target_compounds_router.delete("/{target_compound_id}")
@api_route(token_access=True)
async def delete_target_compound_route(
    target_compound_id: str, user=Depends(editor_user)
):
    """Delete a specific target compound by ID.

    :param target_compound_id: The unique identifier of the target compound.
    :param background_tasks: Background tasks for asynchronous operations.
    :param user: The authenticated editor user, defaults to Depends(editor_user).
    :return: Dictionary confirming deletion of the target compound.
    """
    return await delete_target_compound(
        target_compound_id=target_compound_id,
        independent_transaction=True,
    )
