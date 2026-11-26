"""The batch-peaks reads of one batch peak's members (MAS-1301, MAS-1315).

Their own router, under the batch-peaks prefix: ``batch_peaks_routes`` is at
the structure check's cap on module length, and these two reads share one
controller module (``batch_level_sources``) and one member query.
"""

from fastapi import APIRouter, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.peak_assignments.batch_level_sources import (
    BatchPeakLevelSourcesResponse,
    BatchPeakMemberReadingsResponse,
    get_batch_peak_level_sources,
    get_batch_peak_member_readings,
)
from mascope_backend.api.new.workspaces.dependencies import check_batch_access
from mascope_backend.db import User


batch_member_router = APIRouter(prefix="/api/batch-peaks", tags=["Batch Peaks"])


@batch_member_router.get(
    "/batch/{sample_batch_id}/levels/{batch_peak_id}",
    response_model=BatchPeakLevelSourcesResponse,
)
@api_route()
async def get_batch_peak_level_sources_route(
    sample_batch_id: str, batch_peak_id: str, user: User = Depends(current_active_user)
) -> BatchPeakLevelSourcesResponse:
    """Where a ledger row's level comes from: one batch peak's members whose
    run states a level of its consensus formula, with each level's evidence
    and lift, brightest first, and the members stating none counted by why
    (``batch_level_sources``). Read on the reader's click, per batch peak.
    Requires workspace guest role; 404 for a batch peak of another batch."""
    await check_batch_access(sample_batch_id, user, "guest")
    result = await get_batch_peak_level_sources(sample_batch_id, batch_peak_id)
    return BatchPeakLevelSourcesResponse.model_validate(result)


@batch_member_router.get(
    "/batch/{sample_batch_id}/members/{batch_peak_id}",
    response_model=BatchPeakMemberReadingsResponse,
)
@api_route()
async def get_batch_peak_member_readings_route(
    sample_batch_id: str, batch_peak_id: str, user: User = Depends(current_active_user)
) -> BatchPeakMemberReadingsResponse:
    """Every member of one batch peak, brightest first, on any formula or
    none: what its run row reads, its level, why it states no level of the
    consensus, and what the batch's decisions did to the row
    (``batch_level_sources``). Read per batch peak, all members in one
    response. Requires workspace guest role; 404 for a batch peak of another
    batch."""
    await check_batch_access(sample_batch_id, user, "guest")
    result = await get_batch_peak_member_readings(sample_batch_id, batch_peak_id)
    return BatchPeakMemberReadingsResponse.model_validate(result)
