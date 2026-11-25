"""
FastAPI routes of the environment registry, ``/api/environments``.

An environment is shared reference data, as a registry field is: the global
editor role or higher creates, changes, retires and deletes one, and a guest
reads (decision 14 of ``docs/dev/sample_filters_and_metadata.md``, MAS-104),
as for ionization modes and instrument configurations
(``docs/authorization.md``).
"""

from fastapi import APIRouter, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import editor_user, guest_user
from mascope_backend.api.new.metadata.environment_schema import (
    EnvironmentCreate,
    EnvironmentUpdate,
    GetEnvironmentsQueryParams,
)
from mascope_backend.api.new.metadata.environment_service import (
    create_environment,
    delete_environment,
    get_environment,
    get_environments,
    get_regimes,
    update_environment,
)


environments_router = APIRouter(prefix="/api/environments", tags=["metadata"])


@environments_router.get("")
@api_route()
async def get_environments_route(
    query_params: GetEnvironmentsQueryParams = Depends(),
    user=Depends(guest_user),
):
    """
    List the registry's environments, ordered by label; the retired ones only
    when asked for.
    """
    return await get_environments(**query_params.model_dump())


@environments_router.get("/regimes")
@api_route()
async def get_regimes_route(user=Depends(guest_user)):
    """
    The regimes an environment may name: the vocabulary the reference lists
    declare theirs in, each with what it names.
    """
    return get_regimes()


@environments_router.get("/{environment_id}")
@api_route()
async def get_environment_route(environment_id: str, user=Depends(guest_user)):
    """
    Retrieve one environment by id.
    """
    return await get_environment(environment_id)


@environments_router.post("")
@api_route(status_code=201)
async def create_environment_route(
    body: EnvironmentCreate,
    user=Depends(editor_user),
):
    """
    Add an environment. Its key is unique, never changes, and becomes a value
    of the environment field; 409 if an environment, retired or not, already
    has it. 422 for a chemistry context Mascope does not ship or a regime no
    reference list can declare.
    """
    return await create_environment(body)


@environments_router.patch("/{environment_id}")
@api_route()
async def update_environment_route(
    environment_id: str,
    body: EnvironmentUpdate,
    user=Depends(editor_user),
):
    """
    Change an environment's label, chemistry context or regimes, or retire
    (``retired: true``) or restore it.
    """
    return await update_environment(environment_id, body)


@environments_router.delete("/{environment_id}")
@api_route()
async def delete_environment_route(
    environment_id: str,
    user=Depends(editor_user),
):
    """
    Delete an environment. 409 for one Mascope ships, which is retired instead.
    """
    return await delete_environment(environment_id)
