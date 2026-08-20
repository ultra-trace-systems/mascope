"""
FastAPI routes for ionization mode CRUD operations.
"""

from fastapi import APIRouter, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import (
    editor_user,
    guest_user,
)
from mascope_backend.api.new.ionization.modes.schema import (
    GetIonizationModesQueryParams,
    IonizationModeCreate,
    IonizationModeUpdate,
)
from mascope_backend.api.new.ionization.modes.service import (
    create_ionization_mode,
    delete_ionization_mode,
    get_ionization_mode,
    get_ionization_modes,
    get_ionization_modes_by_filename,
    update_ionization_mode,
)
from mascope_backend.api.new.workspaces.dependencies import (
    check_target_collection_access,
)
from mascope_backend.db import User


ionization_mode_router = APIRouter(
    prefix="/api/ionization/modes", tags=["ionization modes"]
)


async def _check_referenced_collections(mode_data, user: User) -> None:
    """Refuse a calibration or diagnostic collection the caller cannot read.

    A mode is global reference data, so the collections it names are read by
    every workspace that processes a sample under it. Binding one the caller
    alone can see would publish it to the whole instance through the mode,
    which is not the editor's to grant - and ``validate_scope_change`` refuses
    the same move from the other direction, so a collection cannot be bound
    while global and narrowed afterwards.

    Read (guest) access is the bar: naming a collection is not a mutation of
    it. Only ids the request actually sets are checked, so a mode already
    bound to a workspace collection stays editable in every other respect.

    :param mode_data: The create or update body.
    :param user: The authenticated user.
    :raises ForbiddenAccessException: If a named collection is not readable.
    """
    for field in ("calibration_collection_id", "diagnostic_collection_id"):
        collection_id = getattr(mode_data, field, None)
        if collection_id is not None:
            await check_target_collection_access(collection_id, user, "guest")


@ionization_mode_router.get("/{ionization_mode_id}")
@api_route()
async def get_ionization_mode_route(
    ionization_mode_id: str,
    user: User = Depends(guest_user),
):
    """
    Retrieve a specific ionization mode by ID.
    """
    return await get_ionization_mode(ionization_mode_id)


@ionization_mode_router.get("")
@api_route()
async def get_ionization_modes_route(
    query_params: GetIonizationModesQueryParams = Depends(),
    user=Depends(guest_user),
):
    """
    Retrieve a list of ionization modes with optional filtering.
    """
    return await get_ionization_modes(**query_params.model_dump())


@ionization_mode_router.get("/by_filename/{filename}")
@api_route()
async def get_ionization_mode_by_filename_route(
    filename: str,
    user: User = Depends(guest_user),
):
    """
    Retrieve a specific ionization mode by filename.
    """
    return await get_ionization_modes_by_filename(filename)


@ionization_mode_router.post("")
@api_route(status_code=201)
async def create_ionization_mode_route(
    ionization_mode_data: IonizationModeCreate,
    user=Depends(editor_user),
):
    """
    Create a new ionization mode.
    """
    await _check_referenced_collections(ionization_mode_data, user)
    return await create_ionization_mode(ionization_mode_data)


@ionization_mode_router.patch("/{ionization_mode_id}")
@api_route()
async def update_ionization_mode_route(
    ionization_mode_id: str,
    ionization_mode_data: IonizationModeUpdate,
    user=Depends(editor_user),
):
    """
    Update an existing ionization mode.

    Editor level, matching creation of a mode and the whole instrument-config
    surface, and matching what ``docs/authorization.md`` states for shared
    reference data. Note that an edit is retroactive - it changes how samples
    already processed under this mode are calibrated and matched - so it is a
    heavier action than it looks, but that weight is not a reason to require a
    global role that also carries user administration.
    """
    await _check_referenced_collections(ionization_mode_data, user)
    return await update_ionization_mode(ionization_mode_id, ionization_mode_data)


@ionization_mode_router.delete("/{ionization_mode_id}")
@api_route()
async def delete_ionization_mode_route(
    ionization_mode_id: str,
    user=Depends(editor_user),
):
    """
    Delete an ionization mode. Editor level, as for creating and updating one.
    """
    return await delete_ionization_mode(ionization_mode_id)
