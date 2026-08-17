import os
import shutil
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
)
from tuspyserver import create_tus_router

from mascope_backend.api.controllers.sample.files.process.service import (
    auto_process_sample_file,
    re_process_sample_files,
)
from mascope_backend.api.controllers.sample.files.sample_files_controller import (
    compute_sample_file_peaks,
    create_sample_file,
    delete_sample_file,
    delete_sample_files,
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
        auto_process_sample_file,
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


@sample_files_router.post("/upload")
@api_route(status_code=201, token_access=True)
async def upload_sample_files_route(
    files: list[UploadFile] = File(..., description="Multiple files to upload"),
    user=Depends(current_active_user),
) -> dict:
    """
    Uploads multiple sample files to the server in a single batch operation.

    Checks that the user has editor access to each file's instrument workspace
    before uploading.  The instrument is derived from the filename prefix.

    :param files: List of files to be uploaded via multipart form data
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
    )


def get_upload_handler(
    user=Depends(current_active_user),
):
    """Get the upload handler for TUS file uploads.

    Checks that the user has editor access to the instrument workspace
    derived from the uploaded filename before processing.

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
# enforced here.
_tus_max_upload_bytes = runtime.config.tus_max_upload_gb * 1024**3


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


_tus_upload_router = create_tus_router(
    files_dir=_tus_files_dir,
    upload_complete_dep=get_upload_handler,
    prefix="api/sample/files/upload/tus",
    max_size=_tus_max_upload_bytes,
    pre_create_hook=_reject_oversized_upload,
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

# Intended to authenticate every generated tus route before the route body runs:
# tuspyserver's own `auth=` hook is declared after the chunk-writing dependency on
# PATCH, so an unauthenticated PATCH would stream its body to disk before the 401,
# and a router-level dependency normally runs ahead of a route's own.
#
# It does not achieve that, and an earlier version of this comment claimed it did.
# Measured against a running backend, unauthenticated, with `GET /api/workspaces`
# answering 401 from the same client as a control:
#
#   POST    /api/sample/files/upload/tus/          -> 401
#   PATCH   .../{uuid}                             -> 401
#   HEAD    .../{uuid}                             -> 404   (handler ran)
#   OPTIONS /api/sample/files/upload/tus/          -> 204
#   DELETE  .../{uuid}                             -> 204
#
# POST and PATCH are gated because `get_upload_handler` - passed above as
# `upload_complete_dep` - itself depends on `current_active_user`. HEAD, OPTIONS
# and DELETE do not use that dependency and reach their handlers with no identity.
# Why the router-level dependency below fails to reach them is unresolved; it is
# not visible from this file, and static reading of it has now produced two wrong
# answers. Do not infer the auth posture of these routes from this source - probe
# them. UPLOAD-03 in the pentest suite does exactly that on every run.
#
# Tracked, with the options, in #1814. Left in place rather than deleted because
# whether it does anything at all is part of what that issue has to settle.
sample_files_upload_router = APIRouter(dependencies=[Depends(current_active_user)])
sample_files_upload_router.include_router(_tus_upload_router)
