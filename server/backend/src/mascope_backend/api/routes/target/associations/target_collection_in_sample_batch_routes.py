from fastapi import APIRouter, Depends

from mascope_backend.api.controllers.target.associations.target_collection_in_sample_batch_controller import (
    get_target_collections_in_sample_batch,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    GetTargetCollectionsInSampleBatchQueryParams,
)
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access,
    check_target_collection_access,
)


target_collection_in_sample_batch_router = APIRouter(
    prefix="/api/target/associations/target_collections_in_sample_batch",
    tags=["Target Collection Associations"],
)


@target_collection_in_sample_batch_router.get("")
@api_route(token_access=True)
async def get_target_collections_in_sample_batch_route(
    query_params: GetTargetCollectionsInSampleBatchQueryParams = Depends(),
    user=Depends(current_active_user),
):
    """Retrieve target collection ids associated with a sample batch.

    At least one of ``sample_batch_id`` or ``target_collection_id`` must be
    provided so that workspace ACL can be enforced.

    :param query_params: Query parameters for filtering, sorting, and pagination.
    :param user: The current authenticated user.
    :return: A dictionary containing the total count and list of target collections in
             sample batch.
    """
    if not query_params.sample_batch_id and not query_params.target_collection_id:
        raise ForbiddenAccessException()
    if query_params.sample_batch_id:
        await check_batch_access(query_params.sample_batch_id, user, "guest")
    if query_params.target_collection_id:
        await check_target_collection_access(
            query_params.target_collection_id, user, "guest"
        )
    return await get_target_collections_in_sample_batch(**query_params.model_dump())
