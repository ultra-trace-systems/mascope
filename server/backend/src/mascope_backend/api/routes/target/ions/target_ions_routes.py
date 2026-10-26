from fastapi import APIRouter, Depends

from mascope_backend.api.controllers.target.ions.target_ions_controller import (
    get_target_ion,
    get_target_ions,
    update_target_ion,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.target.ions.target_ion_pydantic_model import (
    GetTargetIonsQueryParams,
    TargetIonUpdate,
)
from mascope_backend.api.new.auth.dependencies import editor_user, guest_user
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access,
    check_target_collection_access,
)


target_ions_router = APIRouter(prefix="/api/target/ions", tags=["Target Ions"])


@target_ions_router.get("")
@api_route()
async def get_target_ions_route(
    query_params: GetTargetIonsQueryParams = Depends(),
    user=Depends(guest_user),
):
    """Retrieve a list of target ions based on filters, sorting, and pagination.

    A named ``sample_batch_id`` or ``target_collection_id`` needs the guest
    role on its workspace, as the collection and association routes do; a
    global collection is readable by anyone signed in. Without either filter
    the list is the shared compound and ion library.

    :param query_params: Filtering, sorting, and pagination parameters.
    :param user: The authenticated user, defaults to Depends(guest_user).
    :return: Dictionary with the total count and list of target ions.
    """
    if query_params.sample_batch_id:
        await check_batch_access(query_params.sample_batch_id, user, "guest")
    if query_params.target_collection_id:
        await check_target_collection_access(
            query_params.target_collection_id, user, "guest"
        )
    return await get_target_ions(**query_params.model_dump())


@target_ions_router.get("/{target_ion_id}")
@api_route()
async def get_target_ion_route(
    target_ion_id: str,
    user=Depends(guest_user),
):
    """Retrieve details of a specific target ion by ID.

    :param target_ion_id: The unique identifier of the target ion.
    :param user: The authenticated user, defaults to Depends(guest_user).
    :return: Dictionary with detailed information of the target ion.
    """
    return await get_target_ion(target_ion_id=target_ion_id)


@target_ions_router.patch("/{target_ion_id}")
@api_route()
async def update_target_ion_route(
    target_ion_id: str,
    target_ion_update: TargetIonUpdate,
    user=Depends(editor_user),
):
    """Update details of an existing target ion by ID.

    :param target_ion_id: The unique identifier of the target ion.
    :param target_ion_update: Updated data for the target ion.
    :param user: The authenticated editor user, defaults to Depends(editor_user).
    :return: Dictionary with details of the updated target ion.
    """
    return await update_target_ion(
        target_ion_id=target_ion_id,
        target_ion_update=target_ion_update,
    )
