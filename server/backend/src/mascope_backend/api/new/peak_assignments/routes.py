"""
Peak assignments API routes.

Exposes the peak-centric assignment results ("every peak in a sample with its
formula and confidence") and the endpoint that launches an assignment run.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.new.auth.dependencies import (
    current_active_user,
    current_superuser,
)
from mascope_backend.api.new.peak_assignments.admission import (
    assignment_claim,
    in_flight_run_id,
    in_flight_run_ids,
)
from mascope_backend.api.new.peak_assignments.batch import (
    assign_sample_batch_peaks,
    partition_batch_samples,
)
from mascope_backend.api.new.peak_assignments.config import (
    MAX_IMPORT_BODY_BYTES,
    MAX_IMPORT_ROWS_PER_REQUEST,
    peak_assignment_enabled,
)
from mascope_backend.api.new.peak_assignments.import_service import (
    abandon_import_run,
    import_assignment_run,
)
from mascope_backend.api.new.peak_assignments.schemas import (
    AssignBatchResponse,
    AssignmentVerificationsResponse,
    AssignSamplePeaksBody,
    AssignSampleResponse,
    CompositionFitBody,
    CompositionVisualizeBody,
    ImportRunBody,
    PeakAssignmentDetailResponse,
    PeakAssignmentImportResponse,
    PeakAssignmentQueryParams,
    PeakAssignmentRunsResponse,
    PeakAssignmentsResponse,
    RecalibrateResponse,
    VerifyAssignmentBody,
)
from mascope_backend.api.new.peak_assignments.service import (
    assign_sample_peaks,
    create_pending_run,
    create_verification,
    get_peak_assignment_detail,
    get_peak_assignment_runs,
    get_peak_assignments,
    get_verifications,
    ineligible_reason,
    recalibrate_instrument,
)
from mascope_backend.api.new.peak_assignments.visualization import (
    aggregate_composition_fit,
    visualize_composition_focus,
)
from mascope_backend.api.new.workspaces.dependencies import (
    check_sample_access,
    require_batch_role,
    require_sample_role,
)
from mascope_backend.db import User
from mascope_backend.db.id import gen_id


peak_assignments_router = APIRouter(
    prefix="/api/peak-assignments", tags=["Peak Assignments"]
)


async def reject_oversized_import(request: Request) -> None:
    """Refuse an import body above the byte cap, naming the limit.

    The row cap bounds how many rows a request carries but not how many bytes:
    a row's `alternatives` and `provenance` are client JSON of no fixed size.

    On a deployed stack nginx is what actually stops an oversized body, before
    it reaches this process (`client_max_body_size` on the peak-assignment
    location). This is the same limit stated where a client can act on it: a
    caller talking to the backend directly - the SDK against a dev server, a
    test - gets a 413 that names the cap and the row limit instead of a slow
    parse of a body the deployed path would have rejected outright.
    """
    declared = request.headers.get("content-length")
    if declared is None or not declared.isdigit():
        return
    if int(declared) > MAX_IMPORT_BODY_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Import body is {int(declared)} bytes, above the "
                f"{MAX_IMPORT_BODY_BYTES}-byte limit. Send fewer rows per "
                f"request (at most {MAX_IMPORT_ROWS_PER_REQUEST}); an import is "
                "assembled from several requests."
            ),
        )


async def require_peak_assignment_enabled() -> None:
    """Reject writes when peak-centric assignment is not enabled for this env.

    The read endpoints stay open so a deployment that turns the feature off
    again can still inspect (and prune) ledgers written while it was on. The
    write endpoints - launching runs, recording verdicts, refitting the
    calibration - are what make "off means off" hold: without this, any
    workspace editor could accumulate per-peak ledgers on a deployment that
    never opted in. Tests exercise the writes by setting the
    ``MASCOPE_PEAK_ASSIGNMENT`` env override.
    """
    if not peak_assignment_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Peak assignment is disabled for this environment. Set "
                "peak_assignment = true under [meta] in the env config (or "
                "MASCOPE_PEAK_ASSIGNMENT=1) to enable it."
            ),
        )


@peak_assignments_router.get(
    "/sample/{sample_item_id}", response_model=PeakAssignmentsResponse
)
@api_route(token_access=True)
async def get_peak_assignments_route(
    sample_item_id: str,
    query_params: PeakAssignmentQueryParams = Query(),
    user: User = Depends(current_active_user),
) -> PeakAssignmentsResponse:
    """
    Retrieve peaks-with-assignments for a sample.

    Returns one row per observed peak from the requested run (or the latest
    completed run), each carrying the committed formula, adduct, evidence,
    confidence tier, and optional reference to the curated target library.

    Rows are a slim projection: the `alternatives` and `provenance` JSON are
    inspector detail (~74% of a full row's bytes) and are served per assignment
    by the sibling detail endpoint instead.

    Returns 404 for a run id this sample does not have, and 409 (with the code
    `run_still_assembling`) for one whose import has not finished - a partial
    ledger is not served as a ledger.

    :param sample_item_id: The unique identifier of the sample.
    :param query_params: Optional run id and tier/role/source filters.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Per-peak assignment records (one row per observed peak). Each row
        carries its run id; run metadata is served by the runs endpoint.
    """
    await check_sample_access(sample_item_id, user, "guest")
    result = await get_peak_assignments(
        sample_item_id=sample_item_id, **query_params.model_dump()
    )
    return PeakAssignmentsResponse.model_validate(result)


@peak_assignments_router.get(
    "/sample/{sample_item_id}/assignment/{peak_assignment_id}",
    response_model=PeakAssignmentDetailResponse,
)
@api_route(token_access=True)
async def get_peak_assignment_detail_route(
    sample_item_id: str,
    peak_assignment_id: str,
    user: User = Depends(current_active_user),
) -> PeakAssignmentDetailResponse:
    """
    Retrieve one assignment in full, including `alternatives` and `provenance`.

    The complement of the paged list endpoint, whose rows are a slim
    projection: the peak inspector fetches this when a peak is selected.

    :param sample_item_id: The unique identifier of the sample.
    :param peak_assignment_id: The unique identifier of the assignment.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: The full assignment record.
    """
    await check_sample_access(sample_item_id, user, "guest")
    result = await get_peak_assignment_detail(
        sample_item_id=sample_item_id, peak_assignment_id=peak_assignment_id
    )
    return PeakAssignmentDetailResponse.model_validate(result)


@peak_assignments_router.get(
    "/sample/{sample_item_id}/runs", response_model=PeakAssignmentRunsResponse
)
@api_route(token_access=True)
async def get_peak_assignment_runs_route(
    sample_item_id: str,
    user: User = Depends(current_active_user),
) -> PeakAssignmentRunsResponse:
    """
    Retrieve all peak assignment runs for a sample, newest first.

    :param sample_item_id: The unique identifier of the sample.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Run records with status, engine version, and configuration.
    """
    await check_sample_access(sample_item_id, user, "guest")
    result = await get_peak_assignment_runs(sample_item_id=sample_item_id)
    return PeakAssignmentRunsResponse.model_validate(result)


@peak_assignments_router.get(
    "/sample/{sample_item_id}/verifications",
    response_model=AssignmentVerificationsResponse,
)
@api_route(token_access=True)
async def get_verifications_route(
    sample_item_id: str,
    user: User = Depends(current_active_user),
) -> AssignmentVerificationsResponse:
    """
    Retrieve the verification verdicts recorded for a sample, newest first.

    Append-only history; the current verdict for an assignment is the latest by
    ``verified_utc`` for its observed peak + formula + adduct.

    :param sample_item_id: The unique identifier of the sample.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Verification records for the sample.
    """
    await check_sample_access(sample_item_id, user, "guest")
    result = await get_verifications(sample_item_id=sample_item_id)
    return AssignmentVerificationsResponse.model_validate(result)


@peak_assignments_router.post(
    "/sample/{sample_item_id}/verify",
    response_model=AssignmentVerificationsResponse,
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=201, token_access=True)
async def verify_assignment_route(
    sample_item_id: str,
    body: VerifyAssignmentBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
) -> AssignmentVerificationsResponse:
    """
    Record a verification verdict on an assignment (confirm / reject / unsure).

    Snapshots the assignment's score at verification time and stores the verdict + evidence
    level as an append-only label -- the honest source for refitting the confidence
    calibration later (verification-calibration loop, V1).

    Returns 403 when peak assignment is not enabled for this environment.

    :param sample_item_id: The unique identifier of the sample.
    :param body: The assignment id, verdict, evidence level, and optional note.
    :param user: The current authenticated user. Requires workspace editor role.
    :param membership: Workspace membership with editor role on the sample.
    :return: The created verification record.
    """
    result = await create_verification(
        sample_item_id=sample_item_id,
        peak_assignment_id=body.peak_assignment_id,
        verdict=body.verdict,
        evidence_level=body.evidence_level,
        note=body.note,
        user_id=user.id,
    )
    return AssignmentVerificationsResponse.model_validate(result)


@peak_assignments_router.post(
    "/calibration/{instrument}/recalibrate",
    response_model=RecalibrateResponse,
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(token_access=True)
async def recalibrate_instrument_route(
    instrument: str,
    user: User = Depends(current_superuser),
) -> RecalibrateResponse:
    """
    Refit an instrument's confidence calibration from the accumulated verification labels (V2).

    Instrument-wide: it rewrites the active calibration curve that every assignment's P(correct)
    reads, so it is restricted to superusers. The curve stays provisional unless enough
    reference-grade labels back it. No-op (``recalibrated: false``) when there are too few labels.

    Returns 403 when peak assignment is not enabled for this environment.

    :param instrument: Instrument class to recalibrate (e.g. "orbi").
    :param user: The current authenticated user. Requires superuser.
    :return: Whether it recalibrated, with before/after ECE and label counts.
    """
    result = await recalibrate_instrument(instrument=instrument)
    return RecalibrateResponse.model_validate(result)


@peak_assignments_router.post(
    "/sample/{sample_item_id}/assign",
    response_model=AssignSampleResponse,
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202, token_access=True)
async def assign_sample_peaks_route(
    sample_item_id: str,
    background_tasks: BackgroundTasks,
    body: AssignSamplePeaksBody | None = None,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
) -> AssignSampleResponse:
    """
    Launch a peak assignment run for a sample.

    Assigns a composition to every observed peak: first from the known target
    library (Stage A), then via untargeted composition search for the
    remainder (Stage B, configurable). Results are persisted as a new
    PeakAssignmentRun and readable via the sibling GET endpoints.

    **The outcome is decided here, not behind the response.** Eligibility and
    admission are both synchronous questions - a pure function of the sample row
    and one indexed query - so answering 202 unconditionally and settling them
    inside the background task would leave a headless client with nothing to read
    but a socket notification it cannot receive. Instead:

    - **202** carries the id of the run this request created. The run exists
      before the response does, so a client polls one known run rather than
      diffing run sets to guess which of them is its own - and the engine adopts
      that run instead of minting a second one.
    - **409** when another run for this sample is still in flight, naming it, so
      a client can follow the run that is actually producing the ledger. (A race
      with another worker's creation can leave the id absent.)
    - **422** when the sample cannot usefully be assigned, carrying the reason.
    - **403** when peak assignment is not enabled for this environment.

    :param sample_item_id: The unique identifier of the sample.
    :param body: Optional run configuration overrides.
    :param user: The current authenticated user. Requires workspace editor role.
    :param membership: Workspace membership with editor role on the sample.
    :return: The created run's id and status.
    """
    # Verify the existence of the sample item before queueing the task
    sample = await fetch_sample(sample_item_id)

    if (reason := ineligible_reason(sample)) is not None:
        raise ApiException(
            f"Peak assignment is not possible for sample "
            f"'{sample.sample_item_name}': {reason}.",
            {"sample_item_id": sample_item_id, "reason": reason},
            422,
        )

    config = body.config if body else None
    # Under the claim, so the admission read and the run creation that follows it
    # cannot interleave with another worker's pair. The run then holds the sample
    # durably from this commit onwards, which is what covers the window between
    # the response and the background task starting.
    async with assignment_claim("sample", sample_item_id) as acquired:
        blocking_run_id = await in_flight_run_id(sample_item_id)
        if not acquired or blocking_run_id is not None:
            raise ApiException(
                f"Peak assignment is already running for sample "
                f"'{sample.sample_item_name}'.",
                {
                    "sample_item_id": sample_item_id,
                    "peak_assignment_run_id": blocking_run_id,
                },
                409,
            )
        run = await create_pending_run(sample_item_id, config)

    process_id = gen_id(8)
    background_tasks.add_task(
        assign_sample_peaks,
        sample_item_id=sample_item_id,
        config=config,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
        run_id=run.peak_assignment_run_id,
    )
    return {
        "status": "success",
        "message": (
            f"Assigning peaks for sample '{sample.sample_item_name}', please wait."
        ),
        "results": 1,
        "data": [
            {
                "sample_item_id": sample_item_id,
                "peak_assignment_run_id": run.peak_assignment_run_id,
                "run_status": run.status,
            }
        ],
        "process_id": process_id,
    }


@peak_assignments_router.post(
    "/batch/{sample_batch_id}/assign",
    response_model=AssignBatchResponse,
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202, token_access=True)
async def assign_sample_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    body: AssignSamplePeaksBody | None = None,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> AssignBatchResponse:
    """
    Launch a peak assignment run for every sample in a sample batch.

    Assigns a composition to every observed peak of each sample: first from the
    known target library (Stage A), then via untargeted composition search for
    the remainder (Stage B, configurable). Each sample gets its own
    PeakAssignmentRun, readable via the sample GET endpoints.

    Because a batch multiplies per-sample cost by the number of samples, it
    defaults to **Stage A only**; pass a config with ``run_untargeted: true`` to
    include the untargeted stage.

    **What the 202 carries is the eligibility partition, not run ids.** A batch
    has no run row of its own, and an all-skipped batch produces none at all - so
    without the partition a client cannot tell "nothing to do" from "refused",
    and cannot know how many runs to wait for. The partition is cheap here (the
    batch's samples plus a pure function of each row) and is handed to the
    background task, so what is reported is what executes.

    Runs are deliberately *not* pre-created. A run exists only from the moment
    the batch reaches its sample: created up front it would be a non-terminal run
    for a sample nothing is working on, which durable admission would then refuse
    - and a batch that stops early (cancellation propagates out of the loop)
    would strand one blocking row per sample it never reached.

    - **202** with the admitted sample ids and the skipped ones with reasons.
    - **409** when a run is already in flight for any admitted sample, naming
      those samples and the runs holding them.
    - **403** when peak assignment is not enabled for this environment.

    A batch already being assigned by this worker is refused by the task's own
    in-flight set and advisory claim, which guard the window between this
    response and the first run's creation.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: Optional run configuration overrides applied to every sample.
    :param user: The current authenticated user. Requires workspace editor role.
    :param membership: Workspace membership with editor role on the batch.
    :return: The samples that will be assigned, and the ones that will be skipped.
    """
    # Verify the existence of the sample batch before queueing the task
    sample_batch = await fetch_sample_batch(sample_batch_id)

    partition = await partition_batch_samples(sample_batch_id)

    if blocked := await in_flight_run_ids(partition.admitted):
        raise ApiException(
            f"Peak assignment is already running for "
            f"{len(blocked)} sample{'s' if len(blocked) != 1 else ''} of sample "
            f"batch '{sample_batch.sample_batch_name}'.",
            {
                "sample_batch_id": sample_batch_id,
                "blocked": [
                    {
                        "sample_item_id": sample_item_id,
                        "peak_assignment_run_id": run_id,
                    }
                    for sample_item_id, run_id in blocked.items()
                ],
            },
            409,
        )

    process_id = gen_id(8)
    background_tasks.add_task(
        assign_sample_batch_peaks,
        sample_batch_id=sample_batch_id,
        config=body.config if body else None,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
        partition=partition,
    )
    return {
        "status": "success",
        "message": (
            f"Assigning peaks for {len(partition.admitted)} sample"
            f"{'s' if len(partition.admitted) != 1 else ''} of sample batch "
            f"'{sample_batch.sample_batch_name}' "
            f"({len(partition.skipped)} skipped), please wait."
        ),
        "results": 1,
        "data": [
            {
                "sample_batch_id": sample_batch_id,
                "admitted": list(partition.admitted),
                "skipped": partition.skipped_payload(),
            }
        ],
        "process_id": process_id,
    }


@peak_assignments_router.post(
    "/sample/{sample_item_id}/runs/import",
    response_model=PeakAssignmentImportResponse,
    dependencies=[
        Depends(require_peak_assignment_enabled),
        Depends(reject_oversized_import),
    ],
)
@api_route(token_access=True)
async def import_assignment_run_route(
    sample_item_id: str,
    body: ImportRunBody,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
) -> PeakAssignmentImportResponse:
    """
    Import an assignment run computed by an external engine.

    Publishes a finished ledger into this sample's run history as a first-class
    run - same tables, same read model, same batch fold-in as a run this server
    computed - stamped with the producing `engine` so a reader always knows
    which engine's judgement they are looking at.

    **One import, one or more requests.** A dense sample's ledger is too large
    for one body, so `rows` is capped per request and the run assembles: send
    the first request with no `chunk.run_id` to create the run and receive its
    id, follow up with that id and the next `chunk.index`, and set
    `chunk.complete` on the last one (which may be the first, for a slim
    ledger). Every response reports `max_rows_per_request`, so size chunks from
    that rather than from a hardcoded guess.

    **Retries are safe.** `chunk.index` is an offset in **rows**: it must equal
    the `rows` count the previous response reported, and re-sending the last
    chunk is an idempotent no-op that reports that count again. That covers
    appends and the finalize, but not the request that *creates* the run, which
    has no id yet to be idempotent about - which is why `chunk.import_id` (any
    id unique to this import) is required, and why a retried create returns the
    run it already made instead of a second one.

    **What is accepted.** Each row is a ledger record minus the fields this
    server owns: it mints the ids, resolves `owner_sample_peak_id` into the
    owner's assignment id when the import finalizes, and leaves the calibrated
    P(correct) columns empty because those are its own judgement, not the
    importer's. `tier_bands` and `calibration` are required: tiers are validated
    against the bands the engine actually tiered with, and an import bypasses
    the m/z verification gate, so what it calibrated against goes on the record.
    `config` is opaque and stored verbatim.

    **Partial imports are allowed, and they replace.** A run may cover a subset
    of the sample's peaks, but the batch overview takes the sample's whole
    contribution from the latest completed run - so publishing a handful of rows
    of interest withdraws that sample's other peaks from the batch view.

    Returns 403 when peak assignment is not enabled for this environment, 409
    when another run for this sample is still in flight (naming it) or a chunk
    arrives out of order, 413 when one request's body exceeds the byte cap, and
    422 when the payload is well-formed but refused.

    :param sample_item_id: The unique identifier of the sample.
    :param body: The run metadata and this chunk's assignment rows.
    :param user: The current authenticated user. Requires workspace editor role.
    :param membership: Workspace membership with editor role on the sample.
    :return: The run id, its status, and the rows it now holds.
    """
    result = await import_assignment_run(
        sample_item_id=sample_item_id, body=body, user_id=user.id
    )
    return PeakAssignmentImportResponse.model_validate(result)


@peak_assignments_router.delete(
    "/sample/{sample_item_id}/runs/{peak_assignment_run_id}",
    response_model=PeakAssignmentImportResponse,
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(token_access=True)
async def abandon_import_run_route(
    sample_item_id: str,
    peak_assignment_run_id: str,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
) -> PeakAssignmentImportResponse:
    """
    Abandon an unfinished import, deleting it with its staged rows.

    A client that dies mid-upload - or that simply loses the run id it was
    handed - leaves an `importing` run that blocks every later import *and*
    in-app assignment for the sample, because admission refuses on any run still
    in flight and the startup reaper deliberately leaves imports alone. Retention
    reclaims it eventually; this releases it now.

    Deliberately restricted to runs in `importing`: a completed run is ledger
    data, and removing that is retention's business rather than a client's.
    Anything else is refused with 409.

    Returns 403 when peak assignment is not enabled for this environment.

    :param sample_item_id: The unique identifier of the sample.
    :param peak_assignment_run_id: The unfinished import to delete.
    :param user: The current authenticated user. Requires workspace editor role.
    :param membership: Workspace membership with editor role on the sample.
    :return: The abandoned run id and the number of staged rows reclaimed.
    """
    result = await abandon_import_run(
        sample_item_id=sample_item_id,
        peak_assignment_run_id=peak_assignment_run_id,
    )
    return PeakAssignmentImportResponse.model_validate(result)


@peak_assignments_router.post("/sample/{sample_item_id}/fit/aggregate")
@api_route(token_access=True)
async def composition_fit_aggregate_route(
    sample_item_id: str,
    body: CompositionFitBody,
    user: User = Depends(current_active_user),
) -> dict:
    """
    Isotope-table data for an assigned composition.

    Scores an assigned neutral formula + ionization mechanism against the
    sample on the fly (no persisted target ion), returning the same nested
    match_ions / match_isotopes shape the targeted ion aggregate returns - so
    an untargeted assignment (which has no target_ion_id) can be verified.
    API/SDK surface: no in-app view calls this endpoint.

    :param sample_item_id: The unique identifier of the sample.
    :param body: Composition (assigned formula + ionization mechanism).
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Aggregated match ion / isotope data for the composition.
    """
    await check_sample_access(sample_item_id, user, "guest")
    return await aggregate_composition_fit(
        sample_item_id=sample_item_id,
        assigned_formula=body.assigned_formula,
        ionization_mechanism_id=body.ionization_mechanism_id,
    )


@peak_assignments_router.post("/sample/{sample_item_id}/fit/visualize")
@api_route(status_code=202, token_access=True)
async def composition_fit_visualize_route(
    sample_item_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: CompositionVisualizeBody,
    user: User = Depends(current_active_user),
) -> dict:
    """
    Launch the fit visualization for an assigned composition.

    Emits the sum-spectrum and time-series traces (same socket events the
    targeted ion_focus visualization uses) for an on-the-fly composition,
    so an untargeted assignment visualizes like a targeted ion.
    API/SDK surface: no in-app view calls this endpoint.

    :param sample_item_id: The unique identifier of the sample.
    :param body: Composition + visualization tolerances.
    :param user: The current authenticated user. Requires workspace guest role.
    :return: Acknowledgement with the background process id.
    """
    await check_sample_access(sample_item_id, user, "guest")
    sample = await fetch_sample(sample_item_id)

    process_id = gen_id(8)
    sid = request.headers.get("x-sid", None)
    background_tasks.add_task(
        visualize_composition_focus,
        sample_item_id=sample_item_id,
        assigned_formula=body.assigned_formula,
        ionization_mechanism_id=body.ionization_mechanism_id,
        peak_min_intensity=body.peak_min_intensity,
        mz_tolerance=body.mz_tolerance,
        isotope_ratio_tolerance=body.isotope_ratio_tolerance,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
        sid=sid,
    )
    return {
        "message": (
            f"Visualizing composition '{body.assigned_formula}' in sample "
            f"'{sample.sample_item_name}', please wait."
        ),
        "process_id": process_id,
    }
