from fastapi import APIRouter, Depends

from mascope_backend.api.controllers.match.aggregate.match_aggregate_controller import (
    aggregate_and_create_matches,
    aggregate_and_recreate_matches,
    aggregate_match_isotope_filtered_data,
    aggregate_matches,
)
from mascope_backend.api.controllers.match.lib.match_aggregate import (
    snr_columns_json_safe,
)
from mascope_backend.api.controllers.sample.batches.sample_batches_controller import (
    get_sample_batch,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.match.aggregate.match_aggregate_pydantic_model import (
    AggregateAndCreateMatchesBody,
    AggregateMatchIsotopeFilteredDataBody,
)
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.workspaces.dependencies import require_batch_role
from mascope_backend.db import User


match_aggregate_batch_router = APIRouter(
    prefix="/api/match/aggregate/batch",
    tags=["Match Aggregate Batch"],
)


@match_aggregate_batch_router.post("/{sample_batch_id}/isotope")
@api_route()
async def aggregate_batch_match_isotope_filtered_data_route(
    sample_batch_id: str,
    body: AggregateMatchIsotopeFilteredDataBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("guest")),
):
    """Fetch filtered match isotope data for a specific sample batch.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param body: Filter parameters for match isotope data.
    :type body: AggregateMatchIsotopeFilteredDataBody
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the batch.
    :type membership: WorkspaceMember
    :return: Filtered isotope match data with count and message.
    :rtype: dict
    """
    # Verify the existance of sample batch
    sample_batch_result = await get_sample_batch(sample_batch_id)
    sample_batch = sample_batch_result.get("data")
    sample_batch_name = sample_batch["sample_batch_name"]

    data = await aggregate_match_isotope_filtered_data(
        sample_batch_id=sample_batch_id,
        target_ion_id=body.target_ion_id,
        match_params=body.match_params,
    )
    if not data.empty:
        # NaN signal_to_noise ("no SNR for this row") must serialize as null.
        data_dict = snr_columns_json_safe(data).to_dict("records")
        return {
            "results": len(data_dict),
            "message": (
                f"Filtered match isotope data fetched successfully for batch "
                f"'{sample_batch_name}'"
            ),
            "data": data_dict,
        }
    return {
        "results": 0,
        "message": f"No match isotope data found for batch '{sample_batch_name}'",
        "data": [],
    }


@match_aggregate_batch_router.post("/{sample_batch_id}")
@api_route()
async def aggregate_batch_matches_route(
    sample_batch_id: str,
    body: AggregateMatchIsotopeFilteredDataBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("guest")),
):
    """Aggregate match data for a specific sample batch.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param body: Aggregation parameters for match data.
    :type body: AggregateMatchIsotopeFilteredDataBody
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the batch.
    :type membership: WorkspaceMember
    :return: Aggregated match data with count and message.
    :rtype: dict
    """
    # Verify the existance of sample batch
    sample_batch_result = await get_sample_batch(sample_batch_id)
    sample_batch = sample_batch_result.get("data")
    sample_batch_name = sample_batch["sample_batch_name"]

    result = await aggregate_matches(
        sample_batch_id=sample_batch_id,
        target_ion_id=body.target_ion_id,
        match_params=body.match_params,
    )
    if result.get("results", 0) == 0:
        message = f"No match data found for batch '{sample_batch_name}'"
    else:
        message = f"Match data aggregated successfully for batch '{sample_batch_name}'"

    return {
        "message": message,
        "results": result.get("results", 0),
        "data": result.get("data", []),
    }


@match_aggregate_batch_router.post("/{sample_batch_id}/save")
@api_route()
async def aggregate_and_create_batch_matches_route(
    sample_batch_id: str,
    body: AggregateAndCreateMatchesBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Aggregate and save new matches for a sample batch.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param body: Parameters for creating matches.
    :type body: AggregateAndCreateMatchesBody
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the batch.
    :type membership: WorkspaceMember
    :return: Success message and any associated logs.
    :rtype: dict
    """
    result = await aggregate_and_create_matches(
        sample_batch_id=sample_batch_id,
        target_ion_id=body.target_ion_id,
        match_params=body.match_params,
        match_ions=body.match_ions,
        match_compounds=body.match_compounds,
        match_collections=body.match_collections,
        match_samples=body.match_samples,
    )

    return {
        "status": result.get("status"),
        "message": result.get("message", ""),
    }


@match_aggregate_batch_router.post("/{sample_batch_id}/resave")
@api_route()
async def aggregate_and_recreate_matches_route(
    sample_batch_id: str,
    body: AggregateAndCreateMatchesBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Recreate matches for a sample batch.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param body: Parameters for recreating matches.
    :type body: AggregateAndCreateMatchesBody
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the batch.
    :type membership: WorkspaceMember
    :return: Success message and any associated logs.
    :rtype: dict
    """
    result = await aggregate_and_recreate_matches(
        sample_batch_id=sample_batch_id,
        target_ion_id=body.target_ion_id,
        match_params=body.match_params,
        match_ions=body.match_ions,
        match_compounds=body.match_compounds,
        match_collections=body.match_collections,
        match_samples=body.match_samples,
    )

    return {
        "status": result.get("status"),
        "message": result.get("message", ""),
    }
