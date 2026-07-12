"""Batch-peak read routes -- the batch overview's peak-centric data feed."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.base_pydantic_model import RequestBodyModel
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    get_batch_peak_series,
)
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access,
    check_sample_access_bulk,
)
from mascope_backend.db import User

batch_peaks_router = APIRouter(prefix="/api/batch-peaks", tags=["Batch Peaks"])


class BatchPeakSeriesBody(RequestBodyModel):
    """Scope + filters for a batch-peak series request."""

    sample_batch_id: str | None = Field(
        default=None, description="Batch whose batch peaks to load (full-batch load)."
    )
    sample_item_ids: list[str] | None = Field(
        default=None,
        description="Restrict to batch peaks seen in these samples, and each "
        "series to these samples (single-sample slice for incremental append).",
    )
    batch_peak_ids: list[str] | None = Field(
        default=None, description="Restrict to these batch peaks."
    )
    tier: str | None = Field(default=None, description="Filter by consensus tier.")
    min_n_present: int = Field(
        default=2,
        ge=1,
        description="Occupancy filter: keep only batch peaks present in at least "
        "this many samples (applied to the full-batch load only).",
    )

    @model_validator(mode="after")
    def validate_scope(self):
        """Require exactly one sample scope so access control is unambiguous."""
        if not self.sample_batch_id and not self.sample_item_ids:
            raise ValueError(
                "Please specify either sample_batch_id or sample_item_ids."
            )
        if self.sample_batch_id and self.sample_item_ids:
            raise ValueError(
                "Please specify only one: sample_batch_id or sample_item_ids, not both."
            )
        if self.sample_item_ids is not None and len(self.sample_item_ids) == 0:
            raise ValueError("sample_item_ids cannot be empty if provided.")
        return self


class BatchPeakRecordsResponse(BaseModel):
    """Response model for batch-peak series records."""

    status: str = Field(description="Response status")
    message: str = Field(description="Response message")
    results: int = Field(description="Number of batch peaks returned")
    data: list[dict] = Field(description="Batch-peak series records")


@batch_peaks_router.post("/records/series", response_model=BatchPeakRecordsResponse)
@api_route()
async def get_batch_peak_series_route(
    body: BatchPeakSeriesBody, user: User = Depends(current_active_user)
) -> BatchPeakRecordsResponse:
    """Retrieve per-sample batch-peak data in a compact columnar form.

    Returns one record per batch peak with its consensus (m/z, formula, tier) and a
    ``peak_series`` object of parallel arrays (sample item IDs, intensities, tiers)
    -- the batch-overview trace for that peak. The peak-centric counterpart of
    ``POST /api/match/records/ion/series``.

    :param body: Request body including sample scope and optional filters
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Batch peaks with columnar per-sample series data
    """
    if body.sample_batch_id:
        await check_batch_access(body.sample_batch_id, user, "guest")
    else:
        await check_sample_access_bulk(body.sample_item_ids, user, "guest")
    result = await get_batch_peak_series(**body.model_dump())
    return BatchPeakRecordsResponse.model_validate(result)
