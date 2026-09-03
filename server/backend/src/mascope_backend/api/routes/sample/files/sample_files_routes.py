import os
import shutil
import time
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from tuspyserver import create_tus_router

from mascope_backend.api.controllers.sample.files.process.service import (
    re_process_sample_files,
    spawn_auto_process_sample_file,
)
from mascope_backend.api.controllers.sample.files.sample_files_controller import (
    compute_sample_file_peaks,
    create_sample_file,
    delete_sample_file,
    delete_sample_files,
    ensure_converter_available,
    get_sample_file,
    get_sample_file_metadata,
    get_sample_file_peak_timeseries,
    get_sample_file_peaks,
    get_sample_file_spectrum,
    get_sample_files,
    update_sample_file,
    upload_sample_file,
    upload_sample_files,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    DeleteSampleFilesBody,
    GetRecentSampleFilesQueryParams,
    GetSampleFilePeaksQueryParams,
    GetSampleFilePeakTimeseriesBody,
    GetSampleFilesQueryParams,
    GetSpectrumQueryParams,
    ReprocessSampleFilesBody,
    SampleFileCreate,
    SampleFilesUpload,
    SampleFileUpdate,
)
from mascope_backend.api.new.auth.access_token.service import get_access_token
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.auth.devices.service import record_reported_instrument
from mascope_backend.api.new.workspaces.dependencies import (
    accessible_acquisition_instruments,
    check_instrument_workspace_access,
    check_sample_file_instrument_access,
    check_sample_file_instrument_access_bulk,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_file.name import get_instrument_name, validate_instrument_name


sample_files_router = APIRouter(prefix="/api/sample/files", tags=["Sample Files"])


@sample_files_router.get("")
@api_route(token_access=True)
async def get_sample_files_route(
    query_params: GetSampleFilesQueryParams = Depends(),
    user=Depends(current_active_user),
):
    """Retrieve a list of sample files with optional filtering and pagination.

    Results include files whose instrument belongs to an acquisition workspace
    the user is a member of, plus files linked to sample items in any workspace
    the user has access to.  Superusers see all files.

    :param query_params: Query parameters for filtering, sorting, and pagination.
    :param user: Authenticated user.
    :return: A dictionary with total count and list of sample files.
    """
    allowed = await accessible_acquisition_instruments(user)
    return await get_sample_files(
        **query_params.model_dump(),
        allowed_instruments=allowed,
        user_id=None if allowed is None else user.id,
    )


@sample_files_router.get("/recent")
@api_route()
async def get_recent_sample_files_route(
    query_params: GetRecentSampleFilesQueryParams = Depends(),
    user=Depends(current_active_user),
):
    """Retrieve recent sample files within a specified date range.

    :param query_params: Query parameters including date range in days.
    :param user: Authenticated user.
    :return: A dictionary with recent sample files matching criteria.
    """
    datetime_min = datetime.now(timezone.utc) - timedelta(days=query_params.days)
    query_params_dict = query_params.model_dump(exclude={"days"})
    allowed = await accessible_acquisition_instruments(user)
    query_params_dict.update(
        {
            "datetime_min": datetime_min,
            "allowed_instruments": allowed,
            "user_id": None if allowed is None else user.id,
        }
    )

    return await get_sample_files(**query_params_dict)


@sample_files_router.get("/{sample_file_id}")
@api_route()
async def get_sample_file_route(
    sample_file_id: str,
    user=Depends(current_active_user),
):
    """Retrieve details of a specific sample file by ID.

    :param sample_file_id: ID of the sample file to retrieve.
    :param user: Authenticated user.
    :return: Details of the specified sample file.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "guest")
    return await get_sample_file(sample_file_id)


@sample_files_router.post("")
@api_route(status_code=201, token_access=True)
async def create_sample_file_route(
    sample_file_create: SampleFileCreate,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """Create a new sample file record.

    Checks that the user has editor access to the instrument's acquisition
    workspace before creating the record and triggering auto-processing.

    :param sample_file_create: Data required for creating a sample file.
    :param background_tasks: Background tasks for triggering an automatic processing for
                             sample file after creation.
    :param user: Authenticated user with editor access to the instrument workspace.
    :return: The created sample file's details.
    """
    await check_instrument_workspace_access(
        sample_file_create.instrument, user, "editor", allow_new=True
    )
    validate_instrument_name(sample_file_create.instrument)
    return await create_sample_file(
        sample_file_create=sample_file_create,
        background_tasks=background_tasks,
        user_id=user.id,
        process_id=gen_id(8),
    )


@sample_files_router.patch("/{sample_file_id}")
@api_route()
async def update_sample_file_route(
    sample_file_id: str,
    sample_file: SampleFileUpdate,
    user=Depends(current_active_user),
):
    """Update details of an existing sample file.

    :param sample_file_id: ID of the sample file to update.
    :param sample_file: Data for updating the sample file.
    :param user: Authenticated user with admin access to the file's instrument.
    :return: Updated details of the sample file.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "admin")

    # If the instrument is being changed, also require admin on the target
    if sample_file.instrument:
        await check_instrument_workspace_access(sample_file.instrument, user, "admin")

    return await update_sample_file(sample_file_id, sample_file, user_id=user.id)


@sample_files_router.delete("/{sample_file_id}")
@api_route()
async def delete_sample_file_route(
    sample_file_id: str,
    user=Depends(current_active_user),
):
    """Delete a specific sample file by ID.

    :param sample_file_id: ID of the sample file to delete.
    :param user: Authenticated user with admin access to the file's instrument.
    :return: Confirmation message on deletion.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "admin")
    await delete_sample_file(sample_file_id)


@sample_files_router.post("/delete")
@api_route(token_access=True)
async def delete_sample_files_route(
    body: DeleteSampleFilesBody,
    user=Depends(current_active_user),
):
    """Delete multiple sample files by their IDs or filenames.

    Only deletes files that don't have existing sample items associated with them.
    Returns information about which files were deleted and which were skipped.

    :param body: Request body containing either list of IDs or filenames to delete.
    :param user: Authenticated user with admin access to each file's instrument.
    :return: Information about deleted and skipped files.
    """
    if body.sample_file_ids:
        await check_sample_file_instrument_access_bulk(
            body.sample_file_ids, user, "admin"
        )
    elif body.filenames:
        instruments = {get_instrument_name(os.path.basename(f)) for f in body.filenames}
        for instrument in instruments:
            validate_instrument_name(instrument)
            await check_instrument_workspace_access(instrument, user, "admin")
    return await delete_sample_files(**body.model_dump())


@sample_files_router.get("/{sample_file_id}/peaks")
@api_route(token_access=True)
async def get_sample_file_peaks_route(
    sample_file_id: str,
    query_params: GetSampleFilePeaksQueryParams = Depends(),
    user=Depends(current_active_user),
):
    """Retrieve peaks for a specific sample file.

    :param sample_file_id: ID of the sample file.
    :param query_params: Parameters for retrieving peaks.
    :param user: Authenticated user.
    :return: Peak data for the sample file.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "guest")
    return await get_sample_file_peaks(sample_file_id, **query_params.model_dump())


@sample_files_router.post("/{sample_file_id}/peaks/compute")
@api_route(status_code=202)
async def compute_sample_file_peaks_route(
    sample_file_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """Delegate peak computation for a sample file to the File Converter service.

    :param sample_file_id: ID of the sample file to compute peaks for.
    :param background_tasks: FastAPI background task manager
    :param user: Authenticated user with admin access to the file's instrument.
    :return: Process initiation message.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "admin")
    process_id = gen_id(8)
    access_token = await get_access_token(user=user, service_name="file-converter")

    background_tasks.add_task(
        compute_sample_file_peaks,
        sample_file_id=sample_file_id,
        user=user,
        access_token=access_token,
        process_id=process_id,
        independent_transaction=True,
    )

    return {
        "message": (
            f"Peak detection requested for sample file with ID '{sample_file_id}'. "
            "The file converter service will process it."
        ),
        "process_id": process_id,
    }


@sample_files_router.post("/{sample_file_id}/peaks/timeseries")
@api_route(token_access=True)
async def get_sample_file_peak_timeseries_route(
    sample_file_id: str,
    body: GetSampleFilePeakTimeseriesBody,
    user=Depends(current_active_user),
):
    """Retrieve timeseries for a specific peak in a sample file.

    :param sample_file_id: ID of the sample file.
    :param body: Data including peak m/z and tolerance.
    :param user: Authenticated user.
    :return: Timeseries data for the specified peak.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "guest")
    return await get_sample_file_peak_timeseries(
        sample_file_id=sample_file_id,
        peak_mz=body.peak_mz,
        peak_mz_tolerance_ppm=body.peak_mz_tolerance_ppm,
    )


@sample_files_router.get("/{sample_file_id}/spectrum")
@api_route(token_access=True)
async def get_sample_file_spectrum_route(
    sample_file_id: str,
    query_params: GetSpectrumQueryParams = Depends(),
    user=Depends(current_active_user),
):
    """Retrieve spectrum data for a sample file within a specific range.

    :param sample_file_id: ID of the sample file.
    :param query_params: Parameters for spectrum range.
    :param user: Authenticated user.
    :return: Spectrum data for the sample file.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "guest")
    return await get_sample_file_spectrum(sample_file_id, **query_params.model_dump())


@sample_files_router.get("/{sample_file_id}/metadata")
@api_route(token_access=True)
async def get_sample_file_metadata_route(
    sample_file_id: str,
    user=Depends(current_active_user),
):
    """
    Retrieve metadata for a specific sample file.

    :param sample_file_id: ID of the sample file.
    :param user: Authenticated user.
    :return: Metadata for the sample file.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "guest")
    return await get_sample_file_metadata(sample_file_id)


@sample_files_router.post("/{sample_file_id}/process")
@api_route(status_code=202)
async def process_sample_item_route(
    sample_file_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """Process a sample item, including creation, calibration, and matching.

    :param body: The data for processing the sample item.
    :param background_tasks: Background tasks for processing the item.
    :param user: The current authenticated user with editor permissions.
    :return: A dictionary confirming the processing has started.
    """
    await check_sample_file_instrument_access(sample_file_id, user, "editor")

    # Verify the existence of sample file
    sample_file = (await get_sample_file(sample_file_id)).get("data")

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        spawn_auto_process_sample_file,
        sample_file_id=sample_file_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    return {
        "message": f"Processing file '{sample_file.get('filename')}', please wait.",
        "process_id": process_id,
    }


@sample_files_router.post("/reprocess")
@api_route(status_code=202)
async def reprocess_sample_files_route(
    body: ReprocessSampleFilesBody,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """Reprocess sample files, including calibration and matching.

    :param body: Request body containing sample file IDs to reprocess.
    :param background_tasks: Background tasks for processing the files.
    :param user: The current authenticated user with admin permissions.
    :return: A dictionary confirming the processing has started.
    """
    await check_sample_file_instrument_access_bulk(body.sample_file_ids, user, "admin")

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        re_process_sample_files,
        sample_file_ids=body.sample_file_ids,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    return {
        "message": "Re-processing sample files, please wait.",
        "process_id": process_id,
    }


def _request_device_id(request: Request) -> int | None:
    """The paired device behind the request's bearer token, if any.

    Read from the value the auth layer resolved and validated for this request
    (see auth/backend.py), not from the raw header: one derivation, one
    lookup, and the same answer authentication acted on. Cookie-authenticated
    (web) requests and unbound tokens yield None; the upload is then
    attributed to the user alone.

    :param request: The incoming request.
    :return: The bound device id, or None when the request has none.
    :rtype: int | None
    """
    return getattr(request.state, "token_device_id", None)


@sample_files_router.post("/upload")
@api_route(status_code=201, token_access=True)
async def upload_sample_files_route(
    request: Request,
    files: list[UploadFile] = File(..., description="Multiple files to upload"),
    instrument_timezone: str | None = Form(
        None,
        alias="timezone",
        description="IANA timezone of the uploading machine (agents send this)",
    ),
    user=Depends(current_active_user),
) -> dict:
    """
    Uploads multiple sample files to the server in a single batch operation.

    Checks that the user has editor access to each file's instrument workspace
    before uploading.  The instrument is derived from the filename prefix.

    :param request: The incoming request (for upload attribution).
    :param files: List of files to be uploaded via multipart form data
    :param instrument_timezone: IANA timezone reported by the uploading machine
    :param user: The authenticated user
    :return: A dict response with sample files upload results
    """
    # Check per-instrument access for each file
    for f in files:
        if not f.filename:
            raise ValueError("Upload filename must not be empty")
        # Normalize to basename to prevent path traversal
        f.filename = os.path.basename(f.filename)
        instrument = get_instrument_name(f.filename)
        validate_instrument_name(instrument)
        await check_instrument_workspace_access(
            instrument, user, "editor", allow_new=True
        )

    # Validate files using Pydantic model
    validated_files = SampleFilesUpload(files=files)

    # Single token validation for the entire upload process
    access_token = await get_access_token(user=user, service_name="file-converter")

    return await upload_sample_files(
        files=validated_files.files,
        user=user,
        access_token=access_token,
        device_id=_request_device_id(request),
        instrument_timezone=instrument_timezone,
    )


def get_upload_handler(
    request: Request,
    user=Depends(current_active_user),
):
    """Get the upload handler for TUS file uploads.

    Checks that the user has editor access to the instrument workspace
    derived from the uploaded filename before processing.

    :param request: The incoming request (for upload attribution).
    :param user: The current authenticated user.
    :return: A callable that handles the file upload.
    """

    async def handler(file_path: str, metadata: dict):
        # Sanitize filename to prevent path traversal
        safe_filename = os.path.basename(metadata["filename"])

        # Check per-instrument access
        instrument = get_instrument_name(safe_filename)
        validate_instrument_name(instrument)
        await check_instrument_workspace_access(
            instrument, user, "editor", allow_new=True
        )

        # Rename file from temporary name back to original
        dest_path = os.path.join(os.path.dirname(file_path), safe_filename)
        shutil.move(file_path, dest_path)

        # Single token validation for the entire upload process
        access_token = await get_access_token(user=user, service_name="file-converter")
        # Process the uploaded file
        await upload_sample_file(
            dest_path,
            user=user,
            access_token=access_token,
            device_id=_request_device_id(request),
            instrument_timezone=metadata.get("timezone"),
        )
        # The instrument the agent says it watches, kept on its device row
        # when the row has none yet. It is not what routes this upload - the
        # instrument above still comes from the file name - and attribution
        # must never fail an ingest, so a failure here is logged, not raised.
        try:
            await record_reported_instrument(
                _request_device_id(request), metadata.get("instrument")
            )
        except Exception:
            runtime.logger.exception(
                "Could not record the instrument reported by the uploading device"
            )

    return handler


# In-flight tus uploads get their own subdirectory so they never share a
# namespace with the per-user download dirs under temp/ (served by /api/temp).
# Created eagerly: tuspyserver touches the upload file before running its own
# makedirs (TusUploadFile.__init__ calls create() first), so on a fresh
# environment the very first upload would 500. Every worker imports this
# module after the startup temp reset, so the dir is in place before any
# request.
_tus_files_dir = runtime.env.path("temp", "tus")
os.makedirs(_tus_files_dir, exist_ok=True)

# Cap for a single tus upload, advertised to clients as Tus-Max-Size. The cap
# is per upload - it does not bound how many files a client may transfer -
# and exists so one runaway transfer cannot fill the disk. nginx only bounds
# individual PATCH chunk bodies, so the accumulated upload size must be
# enforced here. It lives in [meta] rather than [backend] because the web
# uploader sizes its own client-side restriction from the same value.
_tus_max_upload_bytes = runtime.meta.tus_max_upload_gb * 1024**3

# Free space that must remain on the spool's filesystem once an upload is
# admitted. The cap above bounds one transfer; it does not bound N concurrent
# ones, which is how a disk fills with entirely legitimate uploads. Same house
# pattern as the update guard's MASCOPE_UPDATE_MIN_FREE_GB (the CLI's
# auto_update) and MIN_FREE_GB in tooling/disk-check.sh - the default matches
# the latter, so uploads start being refused around the point the disk monitor
# already alerts.
_tus_min_free_bytes = runtime.config.tus_min_free_disk_gb * 1024**3

# How long a tus spool entry may go untouched before it is treated as
# abandoned. Keyed on mtime, not on the creation-time expiry tuspyserver
# writes into <uid>.info: a PATCH rewrites the data file, so a slow but live
# multi-hour transfer can never be swept, while tuspyserver's own expiry is
# fixed at creation and would reap it.
_TUS_PARTIAL_MAX_AGE_S = 24 * 60 * 60


def _free_disk_bytes() -> int | None:
    """Free bytes on the tus spool's filesystem, or None if unmeasurable.

    An unmeasurable disk must never block an upload - the same rule the CLI's
    update guard applies before an update.
    """
    try:
        return shutil.disk_usage(_tus_files_dir).free
    except OSError as error:
        runtime.logger.warning(
            f"Could not measure free disk space at {_tus_files_dir}: {error}"
        )
        return None


def _sweep_abandoned_partials() -> None:
    """Delete spool entries untouched for `_TUS_PARTIAL_MAX_AGE_S`.

    A client that starts an upload and never comes back leaves a partial (and
    its .info sidecar) behind. tuspyserver ships a gc for them but nothing
    calls it, and temp/ is otherwise cleared only by the startup reset, so on a
    long-lived deployment partials accumulate until the free-space floor below
    starts refusing everything. Swept at admission rather than on a timer: it
    is the moment the space is about to be needed, it needs no scheduler, and
    upload traffic throttles it naturally.
    """
    cutoff = time.time() - _TUS_PARTIAL_MAX_AGE_S
    try:
        entries = list(os.scandir(_tus_files_dir))
    except OSError as error:
        runtime.logger.warning(f"Could not sweep {_tus_files_dir}: {error}")
        return
    for entry in entries:
        try:
            if not entry.is_file() or entry.stat().st_mtime >= cutoff:
                continue
            os.remove(entry.path)
        except FileNotFoundError:
            continue  # another worker swept it first
        except OSError as error:
            runtime.logger.warning(f"Could not remove {entry.path}: {error}")
        else:
            runtime.logger.info(f"Removed abandoned tus partial: {entry.name}")


def _reject_oversized_upload(metadata: dict, upload_info: dict) -> None:
    """Refuse to create a tus upload that would exceed, or could evade, the cap.

    A declared length over the cap fails creation with 413 so the client
    reports a clear error. A creation that omits Upload-Length (a deferred
    length) is refused with 411: tuspyserver never re-checks the length such an
    upload declares on a later PATCH against ``max_size``, and its streaming
    guard truncates silently (a swallowed 413) instead of erroring, so a
    deferred upload could bypass the cap or complete truncated. No shipped
    client defers length, so requiring it up front closes that gap.
    """
    size = upload_info.get("size")
    if size is None:
        raise HTTPException(
            status_code=411,
            detail="Resumable uploads must declare Upload-Length at creation.",
        )
    if size > _tus_max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Upload of {size / 1024**3:.1f} GB exceeds the per-upload "
                f"limit of {_tus_max_upload_bytes / 1024**3:.1f} GB"
            ),
        )


def _reject_when_disk_is_low(upload_info: dict) -> None:
    """Refuse a creation that cannot fit alongside the free-space floor.

    Call after `_reject_oversized_upload`, which guarantees a declared size.
    The floor is checked per admission, not held as a reservation: several
    creations racing each other all see the same free space. That is
    deliberate - a cross-worker reservation ledger would need Redis - and it
    stays safe because the floor is comfortably larger than the per-upload cap
    by default, and free space does fall as chunks land, so a sustained burst
    starts being refused on its own.

    The refusal is 507 rather than 413: the request is fine, the server has
    nowhere to put it, and the condition clears when space is freed. Clients
    treat 5xx as retryable, so an instrument agent keeps trying rather than
    setting an irreplaceable raw file aside.
    """
    if _tus_min_free_bytes <= 0:
        return
    free = _free_disk_bytes()
    if free is None:
        return
    size = upload_info.get("size") or 0
    if free - size >= _tus_min_free_bytes:
        return
    runtime.logger.warning(
        f"Refusing a {size / 1024**3:.1f} GB upload: only "
        f"{free / 1024**3:.1f} GB free at {_tus_files_dir}, "
        f"{_tus_min_free_bytes / 1024**3:.1f} GB must stay free"
    )
    raise HTTPException(
        status_code=507,
        detail=(
            f"Not enough free disk space for this upload: "
            f"{free / 1024**3:.1f} GB free, the upload needs "
            f"{size / 1024**3:.1f} GB and "
            f"{_tus_min_free_bytes / 1024**3:.1f} GB must remain free. "
            "Retry once space has been freed."
        ),
    )


async def _tus_pre_create_hook(metadata: dict, upload_info: dict) -> None:
    """Refuse a tus upload at creation, before any bytes are transferred.

    tuspyserver marks an upload complete once its final chunk is written and
    only then runs the completion hook, so a refusal at completion cannot
    un-accept the transfer: the client reads the recreated file's offset as done
    and reports success while the bytes are stranded. Every admission check
    therefore runs here, at creation - the per-upload size cap, the free-space
    floor (with a sweep of abandoned partials first, so reclaimed space counts
    toward it), and converter availability, so an upload started while no
    converter is connected is turned away up front instead of transferred in
    full and then dropped.
    """
    _reject_oversized_upload(metadata, upload_info)
    _sweep_abandoned_partials()
    _reject_when_disk_is_low(upload_info)
    await ensure_converter_available()


_tus_upload_router = create_tus_router(
    files_dir=_tus_files_dir,
    upload_complete_dep=get_upload_handler,
    prefix="api/sample/files/upload/tus",
    max_size=_tus_max_upload_bytes,
    pre_create_hook=_tus_pre_create_hook,
)

# The TUS routes are generated by tuspyserver, so they cannot take the
# @api_route decorator. Stamp their endpoints token-accessible so agents
# (file-agent, tof-agent) can upload with a Bearer token, exactly like on
# the legacy POST /upload route; the web app authenticates with its cookie
# either way. Without the stamp, get_enabled_backends rejects every
# Bearer-token request to these routes with 401. Stamp the generated routes
# directly (the wrapper below serves these same endpoint functions).
for _route in _tus_upload_router.routes:
    _route.endpoint.token_access = True

# Authenticates every generated tus route before the route body runs, and it is
# load-bearing: tuspyserver's own `auth=` hook is declared after the chunk-writing
# dependency on PATCH, so an unauthenticated PATCH would stream its body to disk
# before the 401. A router-level dependency runs ahead of a route's own, so it
# covers HEAD/OPTIONS/DELETE too - none of which reach `get_upload_handler`, and
# which would otherwise answer anonymously.
#
# Measured on this branch against a live backend, unauthenticated, with
# `GET /api/workspaces` answering 401 from the same client as a control: OPTIONS,
# HEAD, DELETE and POST all return 401.
#
# Do not remove it on the grounds that `get_upload_handler` already requires a
# session - that only covers POST and PATCH.
#
# One caution for anyone verifying this, learned the hard way: these routes do not
# appear in `fast.routes` by path, only underneath an `original_router`, so
# inspecting the generated router's own dependants shows no identity on
# HEAD/OPTIONS/DELETE and reads as a gap that is not there. Probe a *running*
# backend, and pin which build it is - a probe against a deployment predating this
# comment produced exactly that false conclusion. UPLOAD-03 in the pentest suite
# checks it on every run, and fails against builds released before 2026-08-12.
sample_files_upload_router = APIRouter(dependencies=[Depends(current_active_user)])
sample_files_upload_router.include_router(_tus_upload_router)
