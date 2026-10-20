from typing import List

from fastapi import APIRouter, Depends

from mascope_backend.api.controllers.target.compounds.target_compounds_controller import (
    create_target_compound,
    delete_target_compound,
    get_target_compound,
    get_target_compounds,
    update_target_compound,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    GetTargetCompoundsQueryParams,
    TargetCompoundBase,
    TargetCompoundUpdate,
)
from mascope_backend.api.new.auth.dependencies import editor_user, guest_user


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

    :param query_params: Query parameters for filtering, sorting, and pagination.
    :param user: The authenticated user, defaults to Depends(guest_user).
    :return: Dictionary with total results and data list of target compounds.
    """
    return await get_target_compounds(**query_params.model_dump())


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
