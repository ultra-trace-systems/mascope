"""
Match records API routes for retrieving target collections and ions with match data.

Provides endpoints for loading match records at collection and ion levels,
supporting both sample-specific and batch-level queries with optional filtering.
"""

from fastapi import APIRouter, Depends, Path, Query

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import (
    NotFoundException,
)
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.match.records import (
    get_match_collection_records,
    get_match_ion_records,
    get_match_ion_series,
    get_match_isotope_records,
)
from mascope_backend.api.new.match.records.schemas import (
    MatchIonRecordsBody,
    MatchIonSeriesBody,
    MatchIsotopeRecordsQueryParams,
    MatchRecordsQueryParams,
    MatchRecordsResponse,
    MatchRecordsSingleResponse,
)
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access,
    check_sample_access,
    check_sample_access_bulk,
)
from mascope_backend.db import User


match_records_router = APIRouter(prefix="/api/match/records", tags=["Match Records"])


@match_records_router.get("/collection", response_model=MatchRecordsResponse)
@api_route()
async def get_match_collection_records_route(
    query_params: MatchRecordsQueryParams = Query(),
    user: User = Depends(current_active_user),
) -> MatchRecordsResponse:
    """
    Retrieve target collections with match collection data.

    Supports both sample-level (actual match data) and batch-level (placeholder data)
    queries.

    :param query_params: Query parameters including sample/batch ID and optional filters
    :type query_params: MatchRecordsQueryParams
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: Target collections with match data
    :rtype: MatchRecordsResponse
    """
    if query_params.sample_item_id:
        await check_sample_access(query_params.sample_item_id, user, "guest")
    elif query_params.sample_batch_id:
        await check_batch_access(query_params.sample_batch_id, user, "guest")
    else:
        raise ValueError("Either sample_item_id or sample_batch_id must be provided.")
    result = await get_match_collection_records(**query_params.model_dump())
    return MatchRecordsResponse.model_validate(result)


@match_records_router.get(
    "/collection/{target_collection_id}", response_model=MatchRecordsSingleResponse
)
@api_route()
async def get_match_collection_record_route(
    target_collection_id: str = Path(..., description="Target collection ID"),
    query_params: MatchRecordsQueryParams = Query(),
    user: User = Depends(current_active_user),
) -> MatchRecordsSingleResponse:
    """
    Retrieve a single target collection with match collection data by ID.

    Supports both sample-level (actual match data) and batch-level (placeholder data)
    queries.

    Returns 404 if collection not found in the specified sample/batch.

    :param target_collection_id: Target collection ID to retrieve
    :type target_collection_id: str
    :param query_params: Query parameters including sample/batch ID
    :type query_params: MatchRecordsQueryParams
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: Single target collection with match data
    :rtype: MatchRecordsSingleResponse
    """
    if query_params.sample_item_id:
        await check_sample_access(query_params.sample_item_id, user, "guest")
    elif query_params.sample_batch_id:
        await check_batch_access(query_params.sample_batch_id, user, "guest")
    else:
        raise ValueError("Either sample_item_id or sample_batch_id must be provided.")
    result = await get_match_collection_records(
        target_collection_id=target_collection_id, **query_params.model_dump()
    )

    if not result["data"]:
        entity_type = "sample" if query_params.sample_item_id else "batch"
        entity_id = query_params.sample_item_id or query_params.sample_batch_id
        raise NotFoundException(
            f"Collection '{target_collection_id}' not found for {entity_type} "
            f"'{entity_id}'"
        )

    # Extract single record from list
    single_record = result["data"][0]

    return MatchRecordsSingleResponse(
        status=result["status"], message=result["message"], data=single_record
    )


@match_records_router.post("/ion", response_model=MatchRecordsResponse)
@api_route()
async def get_match_ion_records_route(
    body: MatchIonRecordsBody, user: User = Depends(current_active_user)
) -> MatchRecordsResponse:
    """
    Retrieve target ions with match ion data.

    Supports both sample-level and batch-level queries with optional target collection
    filtering.

    Returns target compound and target ion data with nested match ion information.

    :param body: Request body including sample/batch ID and optional filters
    :type body: MatchIonRecordsBody
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: Target ions with match data
    :rtype: MatchRecordsResponse
    """
    if body.sample_item_ids:
        await check_sample_access_bulk(body.sample_item_ids, user, "guest")
    elif body.sample_batch_id:
        await check_batch_access(body.sample_batch_id, user, "guest")
    else:
        raise ValueError("Either sample_item_ids or sample_batch_id must be provided.")
    result = await get_match_ion_records(**body.model_dump())
    return MatchRecordsResponse.model_validate(result)


@match_records_router.post("/ion/series", response_model=MatchRecordsResponse)
@api_route(token_access=True)
async def get_match_ion_series_route(
    body: MatchIonSeriesBody, user: User = Depends(current_active_user)
) -> MatchRecordsResponse:
    """
    Retrieve per-sample match ion data in a compact columnar form.

    Returns one record per requested target ion with its metadata and a
    `match_series` object of parallel arrays (sample item IDs, intensity sums,
    match categories). Intended for chart data loads over many samples, where
    the row-per-(ion, sample) shape of `/ion` would repeat the ion metadata
    thousands of times.

    :param body: Request body including sample/batch scope and ion scope
    :type body: MatchIonSeriesBody
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: Target ions with columnar per-sample match data
    :rtype: MatchRecordsResponse
    """
    if body.sample_item_ids:
        await check_sample_access_bulk(body.sample_item_ids, user, "guest")
    elif body.sample_batch_id:
        await check_batch_access(body.sample_batch_id, user, "guest")
    else:
        raise ValueError("Either sample_item_ids or sample_batch_id must be provided.")
    result = await get_match_ion_series(**body.model_dump())
    return MatchRecordsResponse.model_validate(result)


@match_records_router.get("/isotope", response_model=MatchRecordsResponse)
@api_route()
async def get_match_isotopes_records_route(
    query_params: MatchIsotopeRecordsQueryParams = Query(),
    user: User = Depends(current_active_user),
) -> MatchRecordsResponse:
    """
    Retrieve target isotopes with match isotope data.

    Supports both sample-level and batch-level queries with optional filtering
    by target collection and target ion.

    Returns target compound, target ion, and target isotope data with
    nested match isotope information.

    :param query_params: Query parameters including sample/batch ID and optional filters
    :type query_params: MatchIsotopeRecordsQueryParams
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: Target isotopes with match data
    :rtype: MatchRecordsResponse
    """
    if query_params.sample_item_id:
        await check_sample_access(query_params.sample_item_id, user, "guest")
    elif query_params.sample_batch_id:
        await check_batch_access(query_params.sample_batch_id, user, "guest")
    else:
        raise ValueError("Either sample_item_id or sample_batch_id must be provided.")
    result = await get_match_isotope_records(**query_params.model_dump())
    return MatchRecordsResponse.model_validate(result)
