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
from mascope_backend.api.new.auth.dependencies import (
    current_active_user,
    current_superuser,
)
from mascope_backend.api.new.peak_assignments.batch import assign_sample_batch_peaks
from mascope_backend.api.new.peak_assignments.config import peak_assignment_enabled
from mascope_backend.api.new.peak_assignments.schemas import (
    AssignmentVerificationsResponse,
    AssignSamplePeaksBody,
    CompositionFitBody,
    CompositionVisualizeBody,
    PeakAssignmentDetailResponse,
    PeakAssignmentQueryParams,
    PeakAssignmentRunsResponse,
    PeakAssignmentsResponse,
    RecalibrateResponse,
    VerifyAssignmentBody,
)
from mascope_backend.api.new.peak_assignments.service import (
    assign_sample_peaks,
    create_verification,
    get_peak_assignment_detail,
    get_peak_assignment_runs,
    get_peak_assignments,
    get_verifications,
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
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202, token_access=True)
async def assign_sample_peaks_route(
    sample_item_id: str,
    background_tasks: BackgroundTasks,
    body: AssignSamplePeaksBody | None = None,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
) -> dict:
    """
    Launch a peak assignment run for a sample.

    Assigns a composition to every observed peak: first from the known target
    library (Stage A), then via untargeted composition search for the
    remainder (Stage B, configurable). Results are persisted as a new
    PeakAssignmentRun and readable via the sibling GET endpoints.

    Returns 403 when peak assignment is not enabled for this environment.

    :param sample_item_id: The unique identifier of the sample.
    :param body: Optional run configuration overrides.
    :param user: The current authenticated user. Requires workspace editor role.
    :param membership: Workspace membership with editor role on the sample.
    :return: Acknowledgement message with the background process id.
    """
    # Verify the existence of the sample item before queueing the task
    sample = await fetch_sample(sample_item_id)

    process_id = gen_id(8)
    background_tasks.add_task(
        assign_sample_peaks,
        sample_item_id=sample_item_id,
        config=body.config if body else None,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Assigning peaks for sample '{sample.sample_item_name}', please wait."
        ),
        "process_id": process_id,
    }


@peak_assignments_router.post(
    "/batch/{sample_batch_id}/assign",
    dependencies=[Depends(require_peak_assignment_enabled)],
)
@api_route(status_code=202, token_access=True)
async def assign_sample_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    body: AssignSamplePeaksBody | None = None,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
) -> dict:
    """
    Launch a peak assignment run for every sample in a sample batch.

    Assigns a composition to every observed peak of each sample: first from the
    known target library (Stage A), then via untargeted composition search for
    the remainder (Stage B, configurable). Each sample gets its own
    PeakAssignmentRun, readable via the sample GET endpoints.

    Because a batch multiplies per-sample cost by the number of samples, it
    defaults to **Stage A only**; pass a config with ``run_untargeted: true`` to
    include the untargeted stage. Blank samples and samples whose m/z
    calibration is unverified are skipped. A batch already being assigned by
    this worker is refused rather than queued.

    Returns 403 when peak assignment is not enabled for this environment.

    :param sample_batch_id: The unique identifier of the sample batch.
    :param body: Optional run configuration overrides applied to every sample.
    :param user: The current authenticated user. Requires workspace editor role.
    :param membership: Workspace membership with editor role on the batch.
    :return: Acknowledgement message with the background process id.
    """
    # Verify the existence of the sample batch before queueing the task
    sample_batch = await fetch_sample_batch(sample_batch_id)

    process_id = gen_id(8)
    background_tasks.add_task(
        assign_sample_batch_peaks,
        sample_batch_id=sample_batch_id,
        config=body.config if body else None,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Assigning peaks for sample batch '{sample_batch.sample_batch_name}', "
            "please wait."
        ),
        "process_id": process_id,
    }


@peak_assignments_router.post("/sample/{sample_item_id}/fit/aggregate")
@api_route(token_access=True)
async def composition_fit_aggregate_route(
    sample_item_id: str,
    body: CompositionFitBody,
    user: User = Depends(current_active_user),
) -> dict:
    """
    Fit-view isotope table for an assigned composition.

    Scores an assigned neutral formula + ionization mechanism against the
    sample on the fly (no persisted target ion), returning the same nested
    match_ions / match_isotopes shape the Fit view consumes - so an untargeted
    assignment (which has no target_ion_id) can be verified.

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
    Launch the Fit-view visualization for an assigned composition.

    Emits the sum-spectrum and time-series traces (same socket events the
    targeted ion_focus visualization uses) for an on-the-fly composition,
    so untargeted assignments render in the Fit view like targeted ones.

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
