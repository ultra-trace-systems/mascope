"""Batch-peak read routes -- the batch overview's peak-centric data feed."""

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field, model_validator

from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.base_pydantic_model import RequestBodyModel
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    compute_batch_peaks,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    get_batch_peak_counterpart,
    get_batch_peak_ledger,
    get_batch_peak_series,
)
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    search_batch_untargeted,
)
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.routes import (
    require_peak_assignment_enabled,
)
from mascope_backend.api.new.peak_assignments.schemas import (
    AssignmentTier,
    AssignSamplePeaksBody,
)
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access,
    check_sample_access,
    check_sample_access_bulk,
    require_batch_role,
)
from mascope_backend.db import User
from mascope_backend.db.id import gen_id


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
    tier: AssignmentTier | None = Field(
        default=None, description="Filter by consensus tier."
    )
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
    results: int = Field(description="Number of records returned")
    data: list[dict] = Field(
        description="Batch-peak records: series, ledger rows, or a counterpart "
        "occurrence, depending on the route"
    )


@batch_peaks_router.post("/records/series", response_model=BatchPeakRecordsResponse)
@api_route()
async def get_batch_peak_series_route(
    body: BatchPeakSeriesBody, user: User = Depends(current_active_user)
) -> BatchPeakRecordsResponse:
    """Retrieve per-sample batch-peak data in a compact columnar form.

    Returns one record per batch peak with its consensus (m/z, formula, tier) and a
    ``peak_series`` object of parallel arrays (sample item IDs, sample peak IDs,
    intensities, tiers) -- the batch-overview trace for that peak, where the sample
    peak IDs say which peak of each sample the point was folded from. The
    peak-centric counterpart of ``POST /api/match/records/ion/series``.

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


@batch_peaks_router.get("/records/counterpart", response_model=BatchPeakRecordsResponse)
@api_route()
async def get_batch_peak_counterpart_route(
    sample_item_id: str,
    sample_peak_id: str,
    target_sample_item_id: str,
    user: User = Depends(current_active_user),
) -> BatchPeakRecordsResponse:
    """Find the peak in one sample that is the same species as a peak in another.

    Sameness is the batch-peak anchor, not m/z proximity: the source peak's
    occurrence names its batch peak, and that batch peak's occurrence in the
    target sample is the answer. The Sample view calls this when the focused
    sample changes, so the focused peak can follow the user across samples.

    A miss is a 200 with ``results: 0``, not a 404. Having no counterpart is the
    ordinary state of a peak that only one sample saw, and the caller's response
    to it is to leave the selection empty and say nothing -- an error status
    would only be something every client has to swallow.

    Both samples are access-checked individually rather than as a list, so an id
    the caller cannot read is refused even when the other one resolves.

    :param sample_item_id: The sample the peak being followed belongs to.
    :param sample_peak_id: The peak to find a counterpart for.
    :param target_sample_item_id: The sample to find it in.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: The counterpart occurrence, or no rows when there is none.
    """
    await check_sample_access(sample_item_id, user, "guest")
    await check_sample_access(target_sample_item_id, user, "guest")
    result = await get_batch_peak_counterpart(
        sample_item_id=sample_item_id,
        sample_peak_id=sample_peak_id,
        target_sample_item_id=target_sample_item_id,
    )
    return BatchPeakRecordsResponse.model_validate(result)


@batch_peaks_router.get(
    "/batch/{sample_batch_id}", response_model=BatchPeakRecordsResponse
)
@api_route()
async def get_batch_peak_ledger_route(
    sample_batch_id: str,
    tier: AssignmentTier | None = None,
    min_n_present: int = 2,
    user: User = Depends(current_active_user),
) -> BatchPeakRecordsResponse:
    """List a batch's batch peaks (metadata only) -- the Assignments ledger feed.

    One row per batch peak (consensus m/z, formula, tier, prevalence), without the
    per-sample series, so a large ledger loads cheaply. The chart fetches series
    only for the batch peaks the user selects.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param tier: Optional filter by consensus tier.
    :param min_n_present: Occupancy floor (keep peaks seen in >= this many samples).
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Batch-peak metadata rows.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    result = await get_batch_peak_ledger(
        sample_batch_id=sample_batch_id, tier=tier, min_n_present=min_n_present
    )
    return BatchPeakRecordsResponse.model_validate(result)


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/backfill",
    # A write: computes and persists batch peaks, so it is gated on the
    # peak_assignment flag exactly like the assign/verify writes. The read
    # routes above stay open so ledgers built while the feature was on remain
    # inspectable after opting out.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202, token_access=True)
async def backfill_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Compute (backfill) a batch's batch peaks from its samples' existing
    completed assignment runs, so the batch overview populates without re-running
    assignment.

    Use this for a batch assigned before batch peaks existed, or after a bulk
    import. Runs as a background task and emits ``peak_assignment_reload`` on
    completion so the Assignments chart refreshes.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        compute_batch_peaks,
        sample_batch_id=sample_batch_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Computing batch peaks for '{sample_batch.sample_batch_name}', "
            "please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/search-untargeted",
    # A write into the batch ledger, gated exactly like the backfill above.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202, token_access=True)
async def search_untargeted_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    body: AssignSamplePeaksBody | None = None,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Run the untargeted composition search once per unassigned batch peak of a
    batch - on each anchor's brightest member's real spectrum - and measure the
    result against the anchor's other members, so every sample the species was
    seen in carries a fit of its own.

    Writes no per-sample run: results live on the batch ledger and reach each
    sample's derived view. An explicit run on a sample still supersedes them
    for that sample. Runs as a background task and emits
    ``peak_assignment_reload`` on completion.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: Optional search settings (formula ranges, tolerance, tier
        bands, the per-sample cap on peaks enumerated); engine defaults when
        omitted. ``run_untargeted`` is implied.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    config = body.config if body is not None and body.config else PeakAssignmentConfig()
    process_id = gen_id(8)
    background_tasks.add_task(
        search_batch_untargeted,
        sample_batch_id=sample_batch_id,
        config=config,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            "Searching untargeted compositions for the unassigned batch peaks of "
            f"'{sample_batch.sample_batch_name}', please wait."
        ),
        "process_id": process_id,
    }
