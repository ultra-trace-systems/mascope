"""
FastAPI routes of the metadata field registry, ``/api/metadata/fields``.

A registry field is shared reference data: the global editor role or higher
creates, changes, retires and deletes one, and a guest reads (decision 14 of
``docs/dev/sample_filters_and_metadata.md``, MAS-104), as for ionization modes
and instrument configurations (``docs/authorization.md``).
"""

from fastapi import APIRouter, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import editor_user, guest_user
from mascope_backend.api.new.metadata.schema import (
    GetMetadataFieldsQueryParams,
    MetadataFieldCreate,
    MetadataFieldUpdate,
)
from mascope_backend.api.new.metadata.service import (
    create_metadata_field,
    delete_metadata_field,
    get_metadata_field,
    get_metadata_fields,
    update_metadata_field,
)


metadata_router = APIRouter(prefix="/api/metadata", tags=["metadata"])


@metadata_router.get("/fields")
@api_route()
async def get_metadata_fields_route(
    query_params: GetMetadataFieldsQueryParams = Depends(),
    user=Depends(guest_user),
):
    """
    List the registry's fields, ordered by label; the retired ones only when
    asked for. Each says whether it is ``retired`` and whether it is
    ``in_use``, which decides whether it can be deleted or only retired.
    """
    return await get_metadata_fields(**query_params.model_dump())


@metadata_router.get("/fields/{metadata_field_id}")
@api_route()
async def get_metadata_field_route(
    metadata_field_id: str,
    user=Depends(guest_user),
):
    """
    Retrieve one registry field by id.
    """
    return await get_metadata_field(metadata_field_id)


@metadata_router.post("/fields")
@api_route(status_code=201)
async def create_metadata_field_route(
    body: MetadataFieldCreate,
    user=Depends(editor_user),
):
    """
    Add a field to the registry. Its key is unique and never changes; 409 if
    a field, retired or not, already has it.
    """
    return await create_metadata_field(body)


@metadata_router.patch("/fields/{metadata_field_id}")
@api_route()
async def update_metadata_field_route(
    metadata_field_id: str,
    body: MetadataFieldUpdate,
    user=Depends(editor_user),
):
    """
    Change a field's label, type, unit, allowed values or description, or
    retire (``retired: true``) or restore it. 409 for a type change on a
    field in use, and for a change to the key, type or allowed values of a
    field Mascope ships, or retiring one.
    """
    return await update_metadata_field(metadata_field_id, body)


@metadata_router.delete("/fields/{metadata_field_id}")
@api_route()
async def delete_metadata_field_route(
    metadata_field_id: str,
    user=Depends(editor_user),
):
    """
    Delete a field nothing references. 409 for a field in use, which is
    retired instead, and for a field Mascope ships.
    """
    return await delete_metadata_field(metadata_field_id)
