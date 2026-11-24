"""Batch-peak read routes -- the batch overview's peak-centric data feed."""

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from pydantic import BaseModel, Field, model_validator

from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.controllers.samples.batch_peak_table import (
    PARQUET_MEDIA_TYPE,
    build_batch_peak_table,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_backend.api.models.base_pydantic_model import RequestBodyModel
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.peak_assignments.batch_assignment_status import (
    get_batch_assignment_status,
)
from mascope_backend.api.new.peak_assignments.batch_curation import (
    curate_batch_peak,
    measure_batch_peak_candidate,
    release_batch_peak_curation,
    validate_curation,
)
from mascope_backend.api.new.peak_assignments.batch_export import (
    MEMBER_PAGE_CAP,
    export_batch_ledger,
    get_batch_peak_members,
)
from mascope_backend.api.new.peak_assignments.batch_fold import compute_batch_peaks
from mascope_backend.api.new.peak_assignments.batch_import import (
    import_batch_run,
    validate_batch_import,
)
from mascope_backend.api.new.peak_assignments.batch_peak_verification import (
    get_anchor_context,
    get_batch_peak_verdicts,
    retract_batch_peak_verdict,
    verify_batch_peak,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    get_batch_peak_counterpart,
    get_batch_peak_ledger,
    get_batch_peak_series,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_ASSIGN,
    ACTION_BATCH_CHECKS,
    ACTION_CO_VARIATION,
    ACTION_IMPORT,
    ACTION_ISOTOPOLOGUES,
    ACTION_KNOWN_SPECIES,
    ACTION_SEARCH,
    ACTION_SERIES,
    ACTION_STRIP_ISOTOPOLOGUES,
    ACTION_WATER_LADDERS,
    list_batch_runs,
    request_cancel,
    run_record,
    start_run,
)
from mascope_backend.api.new.peak_assignments.batch_stages import (
    FOLD_AUTO,
    assign_batch,
    check_no_run_in_flight,
    pass_config,
    pass_settings,
    plan_pass,
)
from mascope_backend.api.new.peak_assignments.batch_staleness import BatchStage
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.routes import (
    reject_oversized_import,
    require_peak_assignment_enabled,
)
from mascope_backend.api.new.peak_assignments.sample_status import (
    get_batch_sample_assignment_status,
)
from mascope_backend.api.new.peak_assignments.schemas import (
    AssignmentTier,
    AssignSamplePeaksBody,
    BatchAssignmentStatusResponse,
    BatchPeakCandidateMeasurementResponse,
    BatchPeakRunsResponse,
    BatchPeakVerificationsResponse,
    BatchSampleAssignmentStatusResponse,
    CurateBatchPeakBody,
    ImportBatchRunBody,
    ReleaseBatchPeakCurationBody,
    RetractBatchPeakVerdictBody,
    VerifyBatchPeakBody,
)
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access,
    check_sample_access,
    check_sample_access_bulk,
    require_batch_role,
)
from mascope_backend.db import BatchPeakRun, User, async_session
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
    batch_peak_run_id: str | None = Field(
        default=None,
        description="Read the series as this batch run left them, from its "
        "snapshot; omit, or name the current run, for the live ledger.",
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


class BatchPeakMembersResponse(BaseModel):
    """One page of a batch's ledger as flat member rows."""

    status: str = Field(description="Response status")
    message: str = Field(description="Response message")
    results: int = Field(description="Rows on this page")
    total: int = Field(description="Rows matching the query across every page")
    limit: int = Field(description="Page size applied")
    offset: int = Field(description="Rows skipped")
    data: list[dict] = Field(description="Member rows, batch_export.COLUMNS each")


class BatchPeakRecordsResponse(BaseModel):
    """Response model for batch-peak series records."""

    status: str = Field(description="Response status")
    message: str = Field(description="Response message")
    results: int = Field(description="Number of records returned")
    data: list[dict] = Field(
        description="Batch-peak records: series, ledger rows, or a counterpart "
        "occurrence, depending on the route"
    )
    stages: list[dict] | None = Field(
        None,
        description=(
            "The ledger only: where each batch stage stands, in order - "
            "``stage``, ``label``, ``phase``, ``state`` (current, running, "
            "stale or not_run), ``version``, ``stale_reason``, ``utc_ran``, "
            "``utc_stale`` and ``skipped_samples``, the samples its latest run "
            "could not read (``batch_stages.stage_standing``)."
        ),
    )
    stale_stages: list[str] | None = Field(
        None,
        description="The ledger only: the stale stages by name, in order.",
    )
    batch_groups: list[dict] | None = Field(
        None,
        description=(
            "The ledger only: the rows each of the batch's readings spans, over "
            "the whole batch whatever the filters keep - ``group_id``, ``kind`` "
            "(series, channels, co_variation, check_pair) and its ``members``, "
            "each with ``batch_peak_id``, ``formula``, ``ion_formula``, "
            "``ionization_mechanism_id``, ``mechanism``, ``mz`` and "
            "``consensus_tier`` (a series member also its ``step``) "
            "(``batch_series.batch_groups``)."
        ),
    )


@batch_peaks_router.post("/records/series", response_model=BatchPeakRecordsResponse)
@api_route(token_access=True)
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
@api_route(token_access=True)
async def get_batch_peak_ledger_route(
    sample_batch_id: str,
    tier: AssignmentTier | None = None,
    min_n_present: int = 2,
    batch_peak_run_id: str | None = None,
    engine_tier: AssignmentTier | None = None,
    tier_disagrees: bool | None = None,
    user: User = Depends(current_active_user),
) -> BatchPeakRecordsResponse:
    """List a batch's batch peaks (metadata only) -- the Assignments ledger feed.

    One row per batch peak (consensus m/z, formula, tier, prevalence), without the
    per-sample series, so a large ledger loads cheaply. The chart fetches series
    only for the batch peaks the user selects. A batch peak an import landed on
    also carries the importing engine's own ``engine_tier`` and ``engine_claim``.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param tier: Optional filter by consensus tier.
    :param min_n_present: Occupancy floor (keep peaks seen in >= this many samples).
    :param engine_tier: Optional filter by the importing engine's own tier.
    :param tier_disagrees: True keeps the batch peaks whose engine tier differs
        from the consensus tier, False those where the two agree; a batch peak
        with no engine tier is left out either way.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Batch-peak metadata rows.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    result = await get_batch_peak_ledger(
        sample_batch_id=sample_batch_id,
        tier=tier,
        min_n_present=min_n_present,
        batch_peak_run_id=batch_peak_run_id,
        engine_tier=engine_tier,
        tier_disagrees=tier_disagrees,
    )
    return BatchPeakRecordsResponse.model_validate(result)


@batch_peaks_router.get(
    "/batch/{sample_batch_id}/peaks",
    response_class=Response,
    responses={200: {"content": {PARQUET_MEDIA_TYPE: {}}}},
)
@api_route(token_access=True)
async def get_batch_peak_lists_route(
    sample_batch_id: str,
    sample_item_id: list[str] | None = Query(
        None,
        description="Read only these samples of the batch (repeat the "
        "parameter for each); every sample when omitted. An id that is no "
        "sample of the batch is named among the failures.",
    ),
    matches: bool = Query(
        False,
        description="Add the match columns: one row per match of a peak, a "
        "peak no target matched keeping one row with them null.",
    ),
    satellites: bool = Query(
        False,
        description="Keep the peaks detection flagged satellites, which the "
        "per-sample peak read leaves out; is_satellite says which they are.",
    ),
    average: bool = Query(
        True,
        description="Average each peak's area and height over its scans; "
        "False for the sums.",
    ),
    user: User = Depends(current_active_user),
) -> Response:
    """Every peak of every sample of a batch, as one Parquet table.

    The batch form of ``GET /api/samples/{sample_item_id}/peaks``: each sample
    read as that route reads it with no time or m/z range - its own polarity,
    the whole acquisition - and the rows stacked, in the order the sample
    listing gives the samples. With ``sample_item_id``, only the samples it
    names are read, so a client after a few samples opens only their stores.

    The columns: ``sample_item_id``, ``sample_item_name``, ``datetime_utc``
    (the sample's start), ``peak_id``, ``mz``, ``sparsity``,
    ``signal_to_noise`` (null where the file has no estimate), ``n_scans`` and
    ``n_scans_total`` (how many of the scans averaged into an Orbitrap peak
    hold it, and how many those are; null where the file keeps no count),
    ``segment`` (the scan stream of a stitched sample's peak, null
    otherwise), ``area``,
    ``height`` and ``is_satellite``; with ``matches``, the per-match columns
    the per-sample read nests under ``match``.

    The file's key-value metadata ``mascope`` is a JSON object: ``samples``
    (each sample read and its row count), ``failures`` (each sample whose read
    failed, with ``status_code`` and ``error``: the batch is answered without
    it) and ``segments`` (per sample, the segments of a stitched spectrum, as
    the per-sample read describes them, or null).

    Not behind the peak-assignment flag: it reads the samples' own peak lists.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param sample_item_id: Only these samples of the batch.
    :param matches: Add the match columns.
    :param satellites: Keep the satellite peaks.
    :param average: Average rather than sum.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: The Parquet file.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    content = await build_batch_peak_table(
        sample_batch_id,
        sample_item_ids=sample_item_id,
        matches=matches,
        satellites=satellites,
        average=average,
    )
    return Response(content=content, media_type=PARQUET_MEDIA_TYPE)


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
    """Rebuild a batch's batch peaks from every one of its samples: from a
    completed assignment run where there is one, otherwise from a fresh Stage A
    pass with no run written, the way ingest folds a sample.

    Use this for a batch that predates the ledger, was never assigned, or was
    imported. Runs as a background task and emits ``peak_assignment_reload`` on
    completion so the Assignments chart refreshes.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
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
            f"Rebuilding the batch ledger of '{sample_batch.sample_batch_name}', "
            "please wait."
        ),
        "process_id": process_id,
    }


class AssignBatchBody(RequestBodyModel):
    """Request body for a pass of the batch stages."""

    from_stage: BatchStage | None = Field(
        None,
        description=(
            "The stage to start at; every stage after it runs too. Omitted, the "
            "pass starts at the first stage that is not current, and runs "
            "nothing when every stage is and nothing needs folding."
        ),
    )
    fold: Literal["auto", "always"] = Field(
        FOLD_AUTO,
        description=(
            "Whether the pass folds every sample first, as a rebuild does: "
            "``always``, or ``auto`` where the ledger cannot be a starting "
            "point - no batch peaks, an import current, a target collection "
            "or reference list changed since the last pass, or the last pass "
            "ran under another engine version (``batch_stages.needs_fold``)."
        ),
    )
    config: PeakAssignmentConfig | None = Field(
        None,
        description=(
            "Settings for this pass alone, in place of the batch's own "
            "(``assignment_settings`` on the batch): the pass's run records "
            "them as given in the request (``settings_source`` ``request``). "
            "Omitted, the pass runs on the batch's settings, and on the "
            "engine's defaults where the batch saved none."
        ),
    )


async def _launch_pass(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    body: AssignBatchBody | None,
    user: User,
) -> dict:
    """Refuse or launch a pass of the batch stages, as action ``assign``."""
    sample_batch = await fetch_sample_batch(sample_batch_id)
    # None where none was given: the pass runs on the batch's own settings.
    config = body.config if body is not None and body.config else None
    if config is not None and config.decoy is not None:
        raise ApiException(
            "A decoy is a run of one sample, launched on the sample; the batch "
            "stages take none.",
            {"sample_batch_id": sample_batch_id},
            422,
        )
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    from_stage = body.from_stage if body is not None else None
    fold = body.fold if body is not None else FOLD_AUTO
    # Read once, here: the run this opens records them, and the pass runs
    # what its run records.
    config, settings_source = await pass_settings(sample_batch_id, config)
    reasons, stages = await plan_pass(sample_batch_id, from_stage, fold, config)
    if not reasons and not stages:
        # Answered here with no run, so a press with nothing to do prunes no
        # pass from the history.
        return {
            "status": "success",
            "message": (
                "Every batch stage is current and nothing needs folding; nothing "
                "to run."
            ),
            "results": 0,
            "data": [],
        }
    # Opened here, as the import's run is: a second press refused by
    # ``start_run`` gets the 409 itself, and a run this answers with exists
    # by the time a client polls for it. The pass runs it and closes it.
    run_id = await start_run(
        sample_batch_id,
        ACTION_ASSIGN,
        config=pass_config(from_stage, fold, config, settings_source),
        user_id=user.id,
    )
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=from_stage,
        fold=fold,
        config=config,
        settings_source=settings_source,
        action=ACTION_ASSIGN,
        batch_peak_run_id=run_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    async with async_session() as session:
        record = run_record(await session.get(BatchPeakRun, run_id))
    return {
        "status": "success",
        "message": (
            f"Assigning the batch '{sample_batch.sample_batch_name}', please wait."
        ),
        "results": 1,
        "data": [record],
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/assign",
    # A write into the batch ledger and the samples' rows, gated like each
    # stage's own route. Opened to API tokens for the SDK's
    # ``batch_peaks.assign``, as the search's route is.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202, token_access=True)
async def assign_batch_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    body: AssignBatchBody | None = None,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Assign the batch: run a pass of the batch stages in their fixed order.

    Folds every sample first where ``fold`` asks for it or the ledger cannot
    be a starting point; then water ladders, known species, the untargeted
    search, the isotope lines (strip, then stamp), the series, the
    co-variation, the ratio stability, the batch checks and the probable
    blends: each reads what the stages before it wrote, so they run in that
    order, from ``from_stage`` to the end, or from the first stage a fold or
    an earlier stage left stale. The ledger (``GET /batch-peaks/batch/{id}``)
    says where each stage stands.

    The pass is one batch run, action ``assign``: one snapshot, every stage's
    outcome in its ``summary`` under ``stages`` (``GET .../runs``). Runs as a
    background task, reporting one ``assign_batch`` process with a pending
    packet at each stage's start, and emits ``peak_assignment_reload`` after
    each stage that rewrites members and at the end. ``POST .../assign/cancel``
    stops it between stages.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: Where to start, whether to fold first, and the search's and
        the series' settings.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: The pass's run, opened here and running, with the background
        process id; no run where every stage is current and nothing needs
        folding.
    :raises 409: A pass or another batch run is rewriting this ledger.
    """
    return await _launch_pass(sample_batch_id, background_tasks, body, user)


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/run-stages",
    # MAS-983's entry, now the same pass as ``assign``; not opened to tokens.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def run_batch_stages_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    body: AssignBatchBody | None = None,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Run the batch stages in their fixed order: the pass ``POST
    .../assign`` runs, under the name MAS-983 gave it.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: Where to start, whether to fold first, and the search's and
        the series' settings.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    return await _launch_pass(sample_batch_id, background_tasks, body, user)


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/assign/cancel",
    # Stops a write; gated like the pass it stops, and opened to tokens with
    # it for the SDK's ``batch_peaks.cancel_assign``.
    dependencies=[Depends(require_peak_assignment_enabled)],
    response_model=BatchPeakRunsResponse,
)
@api_route(token_access=True)
async def cancel_assign_batch_route(
    sample_batch_id: str,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Ask the batch's running pass to stop.

    Sets a flag the pass reads between its stages, and inside the untargeted
    search between samples. It does not roll back: the pass ends
    ``cancelled``, keeps what its stages wrote, and the stages it did not
    finish are stale, so the next pass starts at the first of them. A new
    pass can start as soon as it has ended.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: The running pass, with ``cancel_requested_utc`` set.
    :raises 404: No pass is running on the batch.
    """
    await fetch_sample_batch(sample_batch_id)
    run = await request_cancel(sample_batch_id)
    if run is None:
        raise NotFoundException("No pass of the batch stages is running on this batch")
    return {
        "status": "success",
        "message": "Asked the pass to stop; it stops at its next stage or sample.",
        "results": 1,
        "data": [run_record(run)],
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
        bands, the per-sample cap on peaks enumerated), for this pass alone
        in place of the batch's own (``assignment_settings``), which its run
        records as an override. Omitted, the search runs on the batch's
        settings, the engine defaults where it saved none.
        ``run_untargeted`` is implied.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    # None where none was given: the pass runs on the batch's own settings;
    # given, they override the batch's, and its run records that they did.
    config = body.config if body is not None and body.config else None
    if config is not None and config.decoy is not None:
        # A decoy is a run of one sample against that sample's own last run;
        # the batch search writes the batch ledger, which no decoy may reach.
        raise ApiException(
            "A decoy is a run of one sample, launched on the sample; the batch "
            "search takes none.",
            {"sample_batch_id": sample_batch_id},
            422,
        )
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.SEARCH,
        action=ACTION_SEARCH,
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


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/detect-series",
    # A write into the batch ledger, gated exactly like the search above. Not
    # opened to API tokens: no client needs it there, and a token write is
    # opened one route at a time (test_token_access_coverage.py).
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def detect_series_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    body: AssignSamplePeaksBody | None = None,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Detect the batch's homologous series and measure what they propose.

    Walks the repeat units (CH2, O, H2O, CO, CO2, C2H2O, and the siloxane and
    CF2 ladders from an anchor carrying their element) from every assigned
    batch peak, proposes the stepped formula wherever an unassigned or
    candidate batch peak sits at its exact mass, and commits only the units
    whose proposals beat the decoy offsets. A committed proposal is measured
    against every member of its batch peak through the untargeted search's
    propagation chain, and the batch peak carries the series it is in. Writes
    no per-sample run; runs as a background task, as a batch run, and emits
    ``peak_assignment_reload`` on completion.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: Optional settings; the tier bands and the m/z precision are
        read, the rest is the search's and is ignored. Given, they override
        the batch's own for this pass, which its run records; omitted, the
        pass runs on the batch's settings (``assignment_settings``), the
        engine defaults where it saved none.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    # None where none was given: the pass runs on the batch's own settings;
    # given, they override the batch's, and its run records that they did.
    config = body.config if body is not None and body.config else None
    if config is not None and config.decoy is not None:
        raise ApiException(
            "A decoy is a run of one sample, launched on the sample; the series "
            "detection takes none.",
            {"sample_batch_id": sample_batch_id},
            422,
        )
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.SERIES,
        action=ACTION_SERIES,
        config=config,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            "Detecting the homologous series of the batch peaks of "
            f"'{sample_batch.sample_batch_name}', please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/read-co-variation",
    # A write into the batch ledger's anchors, gated like the series detection
    # above and, like it, not opened to API tokens.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def read_co_variation_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Read how the channels of every compound the batch sees through two
    ionization channels follow each other across the batch.

    For each pair of batch peaks reading one neutral through two channels, per
    ionization mode and instrument type, the two members' traces are read in
    log intensity, divided by each spectrum's reagent signal, with the batch's
    common mode removed, and correlated over the spectra both hold: at r 0.6
    or above the two co-vary, below they do not, and under ten shared spectra
    or on a flat trace the pair is untestable. Each batch peak records the
    reading under ``provenance.co_variation`` and the ledger serves it as
    ``co_variation``. Display-only: no tier moves. Runs as a background task,
    as a batch run, and emits ``peak_assignment_reload`` on completion.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.CO_VARIATION,
        action=ACTION_CO_VARIATION,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            "Reading the co-variation of the batch peaks' channels in "
            f"'{sample_batch.sample_batch_name}', please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/recompute-continuity",
    # A write into the batch ledger's anchors, gated like the co-variation
    # reading above and, like it, not opened to API tokens.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def recompute_continuity_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Grade the ratio stability of the batch's related batch peaks.

    For every M0 and its isotope line, and every two channels of one neutral,
    per ionization mode and instrument type, both at an assigned or candidate
    tier, the within-sample ratio of the two members' intensities is read
    across a time-ordered batch: its median, its spread (1.4826 x MAD of the
    log ratio) and its drift (Spearman's rho against the acquisition order
    and the Theil-Sen change over the pair's span). Each pair is graded
    ``stable``, ``drift`` (it drifts while the co-variation reading says the
    two traces co-vary), ``red`` (it drifts otherwise, or jumps),
    ``inconclusive`` or ``insufficient``, and each batch peak records its
    most alarming pair's grade as ``continuity_grade`` with the pairs under
    ``continuity``. A batch that is not time-ordered is graded nothing, and
    its batch peaks say why. Display-only: no tier moves. Runs as a
    background task, not as a batch run, and emits ``peak_assignment_reload``
    on completion. A rebuild of the ledger grades it at its end as well.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    # A run rewriting the ledger would be graded half-way; a rebuild grades
    # it itself when it is done.
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.CONTINUITY,
        action=ACTION_ASSIGN,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            "Grading the ratio stability of the batch peaks of "
            f"'{sample_batch.sample_batch_name}', please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/stamp-isotopologues",
    # A write into the batch ledger's anchors, gated like the co-variation
    # reading above and, like it, not opened to API tokens.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def stamp_isotopologues_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Stamp the diagnostic isotopologues of the batch's committed peaks where
    their height ratio holds across the batch.

    For every committed monoisotopic batch peak, the 15N, 18O, 34S, 29Si, 30Si,
    37Cl and 81Br lines its consensus ion predicts are looked for at the
    parent's m/z plus each line's offset, among the batch peaks of its
    ionization mode and instrument type. In every sample holding both, the
    line's intensity over the parent's is judged against the predicted ratio
    inside the envelope tolerance that sample's run fitted; a line whose ratio
    holds in enough of the samples judged is stamped in each sample where it
    held. The parent records what was predicted, found and stamped under
    ``provenance.diagnostic_isotopologues`` and a stamped line its parent under
    ``provenance.stamped_isotopologue_of``; the ledger serves both as
    ``diagnostic_isotopologues`` and ``stamped_isotopologue_of``, and the
    member rows a stamp's ``stamped_isotopologue_of`` and ``stamp_source``. No
    tier moves. Runs as a background task, as a batch run, and emits
    ``peak_assignment_reload`` on completion.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.ISOTOPE_LINES,
        action=ACTION_ISOTOPOLOGUES,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            "Stamping the predicted isotope lines of the batch peaks in "
            f"'{sample_batch.sample_batch_name}', please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/strip-isotopologues",
    # A write into the batch ledger and the samples' rows, gated like the
    # stamping above and, like it, not opened to API tokens.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def strip_isotopologues_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Strip the batch ledger's rows that are another row's isotope line.

    Every batch peak whose consensus commits a formula as its monoisotopic
    line is read against the 13C, 15N, 18O, 34S, 29Si, 30Si, 37Cl and 81Br
    lines of every brighter such batch peak below it, of its ionization mode
    and instrument type, within four times the batch's mass scatter (1 ppm at
    least). Where its intensity over the parent's has a median of 0.4 to 1.4
    times the line's predicted share over three or more samples, with fewer
    than a tenth of them above twice, it is the parent's isotopologue: each
    sample's member, and the sample's own in-app row, becomes an isotopologue
    child of the parent's, and the batch peak folds under the parent. A
    curated batch peak, one a sample of which is a hand assignment, one that
    carries its own 13C line and one whose samples would not follow the parent
    are held. The stripped row records its parent under
    ``provenance.merged_isotopologue_of`` and the parent its lines under
    ``provenance.merged_isotopologues``; the ledger serves both as
    ``merged_isotopologue_of`` and ``merged_isotopologues``, and the run's
    summary lists the stripped pairs and the held ones. Runs as a background
    task, as a batch run, and emits ``peak_assignment_reload`` on completion.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.ISOTOPE_LINES,
        action=ACTION_STRIP_ISOTOPOLOGUES,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            "Reading the merged rows of "
            f"'{sample_batch.sample_batch_name}' against each other's isotope "
            "lines, please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/read-batch-checks",
    # Writes the batch peaks' records, gated like the stamping it reads and,
    # like it, not opened to API tokens.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def read_batch_checks_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Read the facts only the batch can state of its batch peaks' readings.

    Three, each where it applies. The halogen lock: a committed batch peak
    whose ion carries chlorine or bromine, its 37Cl or 81Br line stamped by
    *Stamp isotopologues* and sitting at the exact spacing, with the 29Si line
    deciding where a 30Si line could stand there instead. The label twin: a
    labelled ion's 14N twin, present or absent at the label's impurity share
    in the samples that would show it. The neutral pair: a uronium neutral's
    protonated and urea-clustered channels, established where *Read
    co-variation* reads them co-varying. Each batch peak records them under
    ``provenance.batch_checks``, which the ledger serves as ``batch_checks``.
    Display-only: no tier, member or sample row moves. Runs as a background
    task, as a batch run, and emits ``peak_assignment_reload`` on completion.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.BATCH_CHECKS,
        action=ACTION_BATCH_CHECKS,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Reading the batch checks of '{sample_batch.sample_batch_name}', "
            "please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/decide-known-species",
    # A write into the batch ledger and the samples' rows, gated like the
    # strip above and, like it, not opened to API tokens.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def decide_known_species_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Decide the batch's known species once, on the evidence of all its samples.

    A known species is a batch peak a sample's reagent pre-pass claimed as an
    ion of the source's library (not a fragment), or one whose registry holds
    a formula a reference list tags as background. Per ionization mode and
    instrument type, its evidence is pooled over the samples: the samples its
    peak is in, and those whose own claim (or a committed reading of the
    listed formula) held. It is present where its peak is in two samples or
    more, in half the samples or more, and one sample's own test held; absent
    where no sample's did; unpooled where it is in too few. Where it is
    present, its member in every sample, and the sample's own in-app row, is
    rewritten as the species - a library ion as the pre-pass claims it, its
    isotope lines with it; a listed compound as the list's reading at
    candidate - but for the samples a hold keeps: a hand assignment, a target
    compound, another ion's line or claim, and a listed compound's reading
    that carries lines of its own. A curated batch peak, and one whose vote
    the claim would not turn, are held. Every species read records its verdict
    under ``provenance.known_species``, which the ledger serves as
    ``known_species``, with the samples kept and their readings; a sample's
    next run claims a present library ion on the batch's word. Runs as a
    background task, as a batch run, and emits ``peak_assignment_reload`` on
    completion.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.KNOWN_SPECIES,
        action=ACTION_KNOWN_SPECIES,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            "Deciding the known species of "
            f"'{sample_batch.sample_batch_name}' over its samples, please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/measure-water-ladders",
    # A write into the batch ledger and the samples' rows, gated like the
    # known-species decision above and, like it, not opened to API tokens.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202)
async def measure_water_ladders_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Measure the reagent ions' water ladders on the batch's own series, and
    claim the rungs that pass.

    Per ionization mode and instrument type, the profile a sample of it
    resolves to declares the cores whose water ladder is read (bromide, its
    dimer, trimer and HBr cluster; nitrate, its acid clusters and nitrite;
    the labelled cores; none on a positive profile). The samples are cut into
    acquisition segments at long gaps, and each core's ladder, core.(H2O)n up
    to 45 waters, is climbed per segment on the batch peaks: a rung passes
    where it and every rung below it are present in half the segment's
    samples, three times as often as at its decoy masses, and, on a TOF, its
    M+1 line shows no carbon. A batch peak at a passing rung is rewritten as
    the reagent's ion in the samples of the segments it passed in - its
    member and the sample's own in-app row, its isotope lines with it - but
    for the samples a hold keeps: a hand assignment, a target compound or a
    reference list's identity, another ion's line or claim, a reading with an
    isotope line the rung cannot have. A curated batch peak is never claimed.
    Each one records the rung under ``provenance.water_ladder``, which the
    ledger serves as ``water_ladder``; the run's summary reports the segments,
    each ladder's reach, the rungs tested and passed, the rows moved and the
    unexplained share of signal before and after. Runs as a background task,
    as a batch run, and emits ``peak_assignment_reload`` on completion.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        assign_batch,
        sample_batch_id=sample_batch_id,
        from_stage=BatchStage.WATER_LADDERS,
        action=ACTION_WATER_LADDERS,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            "Measuring the water ladders of "
            f"'{sample_batch.sample_batch_name}' over its samples, please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.get(
    "/batch/{sample_batch_id}/verdicts",
    response_model=BatchPeakVerificationsResponse,
)
@api_route(token_access=True)
async def get_batch_peak_verdicts_route(
    sample_batch_id: str,
    user: User = Depends(current_active_user),
) -> BatchPeakVerificationsResponse:
    """Every batch-level verdict recorded on the batch's batch peaks, newest first,
    superseded ones included so the history stays inspectable. Each row carries the
    anchor's present claim and a ``stale`` flag for a live verdict about a formula
    the consensus has since left.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: The verdicts.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    result = await get_batch_peak_verdicts(sample_batch_id=sample_batch_id)
    return BatchPeakVerificationsResponse.model_validate(result)


@batch_peaks_router.get(
    "/sample/{sample_item_id}/anchor-context",
    response_model=BatchPeakVerificationsResponse,
)
@api_route()
async def get_anchor_context_route(
    sample_item_id: str,
    user: User = Depends(current_active_user),
) -> BatchPeakVerificationsResponse:
    """The batch-level verdicts that reach a sample: for each of its peaks whose
    batch peak carries a live verdict, that verdict with the peak's id on it.
    Sparse, one query; the per-sample ledger and the inspector both read it.
    Whether a verdict applies to a row is the reader's call - it does when the
    row's own formula and mechanism are the ones judged.

    :param sample_item_id: The unique identifier of the sample.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: The verdicts reaching the sample.
    """
    await check_sample_access(sample_item_id, user, "guest")
    result = await get_anchor_context(sample_item_id=sample_item_id)
    return BatchPeakVerificationsResponse.model_validate(result)


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/verify",
    response_model=BatchPeakVerificationsResponse,
    # A write, gated on the feature flag like the other batch writes.
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=201, token_access=True)
async def verify_batch_peak_route(
    sample_batch_id: str,
    body: VerifyBatchPeakBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> BatchPeakVerificationsResponse:
    """Record a batch-level verdict on a batch peak's species claim: one judgment
    per species at this anchor, covering every sample in the batch whose peak
    folded into it and that has no verdict of its own. The claim is snapshotted
    from the anchor's current consensus, never taken from the client;
    ``expected_formula`` is the guard that the user judged that consensus (409 on
    a mismatch), and an unassigned anchor has no claim to judge (422). The
    verdict this replaces is stamped superseded in the same transaction.

    A confirmation may name a structure (``claimed_inchikey``, ``claimed_name``),
    which makes it an identification claim. The record carries the level the
    verdict states in the shared scale, fixed from its evidence and what that
    evidence showed, as for a per-sample verdict. A level-1 claim names the
    time window its standard covers (``window_start_utc``, ``window_end_utc``,
    422 without); a member acquired outside it names no structure and reads
    the engine's level. A
    rejection on reference-standard evidence records the confirmation it
    refutes.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: The verdict, its evidence, the formula judged and the structure
        a confirmation names, if any.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: The created verdict.
    """
    result = await verify_batch_peak(
        sample_batch_id=sample_batch_id,
        batch_peak_id=body.batch_peak_id,
        verdict=body.verdict,
        expected_formula=body.expected_formula,
        evidence_level=body.evidence_level,
        note=body.note,
        user_id=user.id,
        identification=body,
    )
    return BatchPeakVerificationsResponse.model_validate(result)


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/retract",
    response_model=BatchPeakRecordsResponse,
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(token_access=True)
async def retract_batch_peak_verdict_route(
    sample_batch_id: str,
    body: RetractBatchPeakVerdictBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> BatchPeakRecordsResponse:
    """Withdraw the live batch-level verdict(s) on a batch peak, returning the
    species to unverified in every sample it covered. History is kept: the rows
    are stamped superseded, not deleted. Retracting where nothing is live is not
    an error and reports zero.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: The batch peak, and optionally the one claim, to retract.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: The ids of the verdicts retracted.
    """
    result = await retract_batch_peak_verdict(
        sample_batch_id=sample_batch_id,
        batch_peak_id=body.batch_peak_id,
        assigned_formula=body.assigned_formula,
        ionization_mechanism_id=body.ionization_mechanism_id,
    )
    return BatchPeakRecordsResponse.model_validate(result)


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/curate",
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202, token_access=True)
async def curate_batch_peak_route(
    sample_batch_id: str,
    body: CurateBatchPeakBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Pin one of a batch peak's identities as its species for the whole batch.

    The identity is one the batch peak's members have carried (a registry index,
    as the alternatives of a derived row name them); it is pinned on the anchor,
    measured in every sample holding the peak - a sample where it can be measured
    now reads it with a fit of its own, one where it cannot keeps what it had -
    and the anchor is recomputed. ``expected_formula`` guards the claim the user
    saw (409 on a mismatch); an unknown index is a 422. So is an identity that
    reads the batch peak as an isotope line of an ion whose monoisotopic line
    is elsewhere (``batch_peak_candidate_isotope_line``): the message names the
    batch peak holding that line, where the compound can be pinned instead.
    Runs as a background task on the ``curate_batch_peak`` channel and emits
    ``peak_assignment_reload`` on completion.

    ``sample_item_id`` names the sample the identity was measured on before
    the pin (``measure-candidate``); the task measures it there again and
    records the result on the pin, ``provenance.manual.measured``.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: The batch peak, the identity to pin, the formula seen and the
        sample it was measured on.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: Acknowledgement message with the background process id.
    """
    checked = await validate_curation(
        sample_batch_id=sample_batch_id,
        batch_peak_id=body.batch_peak_id,
        candidate=body.candidate,
        expected_formula=body.expected_formula,
    )
    process_id = gen_id(8)
    background_tasks.add_task(
        curate_batch_peak,
        sample_batch_id=sample_batch_id,
        batch_peak_id=body.batch_peak_id,
        candidate=body.candidate,
        expected_formula=body.expected_formula,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
        measured_sample_item_id=body.sample_item_id,
    )
    return {
        "message": (
            f"Pinning {checked['formula']} on the batch peak at m/z "
            f"{checked['mz']:.4f} and measuring it across the batch, please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.get(
    "/batch/{sample_batch_id}/measure-candidate",
    response_model=BatchPeakCandidateMeasurementResponse,
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route()
async def measure_batch_peak_candidate_route(
    sample_batch_id: str,
    batch_peak_id: str,
    sample_item_id: str,
    candidate: int = Query(ge=0),
    user: User = Depends(current_active_user),
) -> BatchPeakCandidateMeasurementResponse:
    """Measure one of a batch peak's identities on one sample before it is
    pinned: the fit, the envelope's lines and which the spectrum holds, the
    plausibility, the evidence and the tier a pin would write on the sample's
    member, beside what the member reads now. Nothing is written.

    The measurement is the one ``curate`` makes in every sample, so the numbers
    are the ones the pin then writes on this member. An unknown index is a 422;
    a sample holding no member of the batch peak a 404.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param batch_peak_id: The batch peak (anchor).
    :param sample_item_id: The sample whose member to measure.
    :param candidate: Index into the batch peak's identity registry.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: One entry, measured or blocked with a reason.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    await check_sample_access(sample_item_id, user, "guest")
    result = await measure_batch_peak_candidate(
        sample_batch_id=sample_batch_id,
        batch_peak_id=batch_peak_id,
        candidate=candidate,
        sample_item_id=sample_item_id,
    )
    return BatchPeakCandidateMeasurementResponse.model_validate(result)


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/release-curation",
    response_model=BatchPeakRecordsResponse,
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(token_access=True)
async def release_batch_peak_curation_route(
    sample_batch_id: str,
    body: ReleaseBatchPeakCurationBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> BatchPeakRecordsResponse:
    """Undo a batch peak's manual curation: the samples re-measured for the pinned
    identity go back to what they read before where nobody has changed them
    since, the pin is dropped and the batch's vote decides again. A batch peak
    without a curation is a 409.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: The batch peak.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: How many samples were put back and how many left as since changed.
    """
    result = await release_batch_peak_curation(
        sample_batch_id=sample_batch_id, batch_peak_id=body.batch_peak_id
    )
    return BatchPeakRecordsResponse.model_validate(result)


@batch_peaks_router.get(
    "/batch/{sample_batch_id}/members",
    response_model=BatchPeakMembersResponse,
)
@api_route(token_access=True)
async def get_batch_peak_members_route(
    sample_batch_id: str,
    sample_item_id: str | None = None,
    limit: int = Query(1000, ge=1, le=MEMBER_PAGE_CAP),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_active_user),
) -> BatchPeakMembersResponse:
    """One page of a batch's ledger as flat rows: one per member, the anchor's
    consensus beside the member's own reading. The anchor's columns include an
    importing engine's own verdict, ``engine_tier`` and ``engine_claim``, empty
    where no import gave one. ``total`` says how many rows
    match, so a client pages until it has the whole ledger - the SDK's
    ``batch_peaks.members`` does exactly that.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param sample_item_id: Only this sample's members.
    :param limit: Page size, at most the members page cap.
    :param offset: Rows to skip.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: The page, with ``total``.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    if sample_item_id:
        await check_sample_access(sample_item_id, user, "guest")
    result = await get_batch_peak_members(
        sample_batch_id=sample_batch_id,
        sample_item_id=sample_item_id,
        limit=limit,
        offset=offset,
    )
    return BatchPeakMembersResponse.model_validate(result)


@batch_peaks_router.post("/batch/{sample_batch_id}/export")
@api_route(status_code=202, token_access=True)
async def export_batch_ledger_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
) -> dict:
    """Export a batch's ledger to a CSV: every batch peak with every sample's
    member, one row per member, the anchor's consensus beside the member's own
    reading. A background task; the file lands in the user's temp store and the
    completion notification carries its name, which the browser downloads.

    A read, so it is open to every role that can read the batch and is not
    behind the feature flag: a ledger built while the feature was on stays
    exportable after opting out.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Acknowledgement message with the background process id.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    sample_batch = await fetch_sample_batch(sample_batch_id)
    process_id = gen_id(8)
    background_tasks.add_task(
        export_batch_ledger,
        sample_batch_id=sample_batch_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Exporting the batch ledger of '{sample_batch.sample_batch_name}', "
            "please wait."
        ),
        "process_id": process_id,
    }


@batch_peaks_router.get(
    "/batch/{sample_batch_id}/runs", response_model=BatchPeakRunsResponse
)
@api_route(token_access=True)
async def get_batch_peak_runs_route(
    sample_batch_id: str,
    user: User = Depends(current_active_user),
) -> BatchPeakRunsResponse:
    """A batch's runs, newest first: the batch-level operations that rewrote its
    ledger (a rebuild, an untargeted search with its parameters, an import) and
    the folds that built it, the current one flagged. Any other run's ledger and
    series are read with ``batch_peak_run_id`` on the ledger and series routes,
    as its snapshot left them.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: The runs.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    result = await list_batch_runs(sample_batch_id=sample_batch_id)
    return BatchPeakRunsResponse.model_validate(result)


@batch_peaks_router.get(
    "/batch/{sample_batch_id}/assignment-status",
    response_model=BatchAssignmentStatusResponse,
)
@api_route(token_access=True)
async def get_batch_assignment_status_route(
    sample_batch_id: str,
    user: User = Depends(current_active_user),
) -> BatchAssignmentStatusResponse:
    """Where the batch's assignment stands, and why it is behind its last pass.

    ``state`` is the first that holds: ``running`` (a pass or another batch
    run in flight, under ``running`` with its stage, which of how many, and
    who started it), ``failed`` or ``cancelled`` (the newest pass stopped,
    under ``stopped_pass``), ``none`` (no batch peaks), ``folded_only`` (no
    pass has completed: the ledger is what the folds at ingest built),
    ``behind`` (a reason under ``behind`` holds) or ``current``.

    ``behind`` measures the batch against its newest completed pass
    (``last_pass``): ``new_samples`` and ``removed_samples`` against the
    samples it covered, ``resampled`` (an in-app run of a sample completed
    since it started), ``library_changed`` (a target collection or reference
    list since), ``engine_changed`` and ``stages_stale`` (a stage stale for
    any other reason, ``read_again`` among them: a sample it skipped reads
    again). A curation, a verdict or an import is none, and so are the
    samples a stage could not read: it is current with them, and each of
    ``stages`` names them under ``skipped_samples``. ``needs_fold`` says whether the next pass folds every sample first, and
    ``fold_reasons`` why (``batch_stages.needs_fold``). Read-only, and open
    like every read here where the feature is off.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: The batch's status.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    result = await get_batch_assignment_status(sample_batch_id=sample_batch_id)
    return BatchAssignmentStatusResponse.model_validate(result)


@batch_peaks_router.post(
    "/batch/{sample_batch_id}/runs/import",
    response_model=BatchPeakRunsResponse,
    # A write into the batch ledger, gated like the search; the body cap is the
    # per-sample import's, stated here for a client talking to the backend directly.
    dependencies=[
        Depends(require_peak_assignment_enabled),
        Depends(reject_oversized_import),
    ],
)
@api_route(status_code=202, token_access=True)
async def import_batch_run_route(
    sample_batch_id: str,
    body: ImportBatchRunBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """Import an external engine's batch-level result - one identity per m/z -
    onto the batch ledger, as a batch run.

    Each row is matched to the batch peak nearest its m/z within
    ``mz_tolerance_ppm`` and of the polarity its mechanism implies, and the
    composition is then measured against every member of that batch peak by
    this server's seeded scorer, so members carry a fit of their own under the
    engine's name. Curated batch peaks and isotopologue batch peaks are left
    alone; a row without a mechanism cannot be measured. Every skipped row is
    counted by reason in the run's summary.

    The run is opened here and returned at once - the ledger as it was is
    snapshotted under the previous run - and the import completes it in the
    background, or marks it failed. Poll ``GET .../runs`` for its status.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: The engine, its version and record, the tolerance, the rows.
    :param user: The current authenticated user. Requires workspace editor role.
    :return: The run, running, with the background process id in the header.
    :raises 409: A batch-level operation is already rewriting this ledger.
    :raises 422: A reserved engine name, an oversized config, or a mechanism
        this deployment does not have.
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    engine, mechanism_polarity = await validate_batch_import(body)
    await check_no_run_in_flight(sample_batch_id=sample_batch_id)
    run_id = await start_run(
        sample_batch_id,
        ACTION_IMPORT,
        config={
            "engine": engine,
            "mz_tolerance_ppm": body.mz_tolerance_ppm,
            "rows": len(body.rows),
            "client": body.config,
        },
        engine=engine,
        engine_version=body.engine_version,
        user_id=user.id,
    )
    process_id = gen_id(8)
    background_tasks.add_task(
        import_batch_run,
        sample_batch_id=sample_batch_id,
        batch_peak_run_id=run_id,
        engine=engine,
        tolerance_ppm=body.mz_tolerance_ppm,
        rows=body.rows,
        mechanism_polarity=mechanism_polarity,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    async with async_session() as session:
        run = await session.get(BatchPeakRun, run_id)
        record = run_record(run)
    return {
        "status": "success",
        "message": (
            f"Importing {len(body.rows)} {engine} row"
            f"{'s' if len(body.rows) != 1 else ''} into the batch ledger of "
            f"'{sample_batch.sample_batch_name}', please wait."
        ),
        "results": 1,
        "data": [record],
        "process_id": process_id,
    }


@batch_peaks_router.get(
    "/batch/{sample_batch_id}/sample-status",
    response_model=BatchSampleAssignmentStatusResponse,
)
@api_route()
async def get_batch_sample_status_route(
    sample_batch_id: str,
    user: User = Depends(current_active_user),
) -> BatchSampleAssignmentStatusResponse:
    """Every sample of a batch with its assignment status: the latest completed
    assignment run of its own, if any, and what the batch ledger holds for it
    (members, and how many carry an assignment). The sample browser's badge.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: One record per sample, in sample id order.
    """
    await check_batch_access(sample_batch_id, user, "guest")
    result = await get_batch_sample_assignment_status(sample_batch_id=sample_batch_id)
    return BatchSampleAssignmentStatusResponse.model_validate(result)
