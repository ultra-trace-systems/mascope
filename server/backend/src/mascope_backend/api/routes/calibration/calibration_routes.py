from fastapi import APIRouter, BackgroundTasks, Depends, Query

from mascope_backend.api.controllers.calibration.calibration_controller import (
    calibration_mz_apply,
    calibration_mz_calibrate_batch,
    calibration_mz_calibrate_sample,
    calibration_mz_fit,
    get_default_calibration_params,
    get_mz_calibration,
)
from mascope_backend.api.controllers.sample.files.sample_files_controller import (
    get_sample_files,
)
from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    get_sample_item,
)
from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationMzApplyBody,
    GetMzCalibrationQueryParams,
    MzCalibrationParams,
)
from mascope_backend.api.new.auth.dependencies import current_active_user, guest_user
from mascope_backend.api.new.workspaces.dependencies import (
    access_granted,
    check_batch_access,
    check_filename_file_instrument_access,
    check_sample_access,
    check_sample_batch_file_instrument_access,
    check_sample_item_file_instrument_access,
    check_sample_or_file_instrument_access,
)
from mascope_backend.db.id import gen_id


calibration_router = APIRouter(prefix="/api/calibration", tags=["Calibration"])


async def _sample_label(sample_item_id: str, sample: dict, user) -> str:
    """The sample's name for a message, or an empty string if it is not the
    caller's to read.

    The routes below admit an admin of the file's instrument workspace, which
    says nothing about membership of the workspace holding the sample. Naming
    it unconditionally would report a name the caller cannot reach through any
    read route, so the name is included only when a guest-level read would have
    returned it.
    """
    if await access_granted(check_sample_access(sample_item_id, user, "guest")):
        return f" '{sample['sample_item_name']}'"
    return ""


@calibration_router.get("/mz_calibration")
@api_route()
async def get_mz_calibration_route(
    query_params: GetMzCalibrationQueryParams = Depends(),
    user=Depends(guest_user),
):
    """Retrieve the m/z calibration based on instrument or sample item ID.

    :param query_params: Query parameters for instrument and sample item ID.
    :type query_params: GetMzCalibrationQueryParams, optional
    :param user: The current authenticated user, defaults to Depends(guest_user).
    :type user: User, optional
    :return: The m/z calibration data.
    :rtype: dict
    """
    return await get_mz_calibration(**query_params.model_dump())


@calibration_router.get("/default_params")
@api_route()
async def get_default_calibration_params_route(
    sample_item_id: str = Query(..., description="Sample item ID"),
    user=Depends(guest_user),
):
    """Instrument-appropriate default m/z calibration parameters for a sample.

    :param sample_item_id: The sample item ID.
    :type sample_item_id: str
    :param user: The current authenticated user, defaults to Depends(guest_user).
    :type user: User, optional
    :return: The default calibration parameter values.
    :rtype: dict
    """
    return await get_default_calibration_params(sample_item_id=sample_item_id)


@calibration_router.post("/mz_fit")
@api_route(
    status_code=202,
)
async def calibration_mz_fit_route(
    mz_calibration_params: MzCalibrationParams,
    background_tasks: BackgroundTasks,
    sample_item_id: str = Query(
        ..., description="The sample item ID to query for sample mz_calibration"
    ),
    user=Depends(current_active_user),
):
    """Initiate m/z fitting for a sample.

    A fit is computed and returned but never written to the sample file (see
    ``calibration_mz_fit``), so this is scoped to the sample's own workspace at
    editor level - the same bar as running a match. Writing the result to the
    file is a separate call with a stricter check.

    An admin of the file's instrument workspace is admitted as well, because
    that role may write a calibration onto the file outright: refusing it the
    preview of what it is about to write would leave the calibration dialog
    unusable for the very operator the write was scoped to.

    :param mz_calibration_params: Parameters for m/z calibration.
    :type mz_calibration_params: MzCalibrationParams
    :param background_tasks: Background tasks for async processing.
    :type background_tasks: BackgroundTasks
    :param sample_item_id: The sample item ID.
    :type sample_item_id: str
    :param user: The current authenticated user, defaults to Depends(current_active_user).
    :type user: User, optional
    :return: Message confirming start of m/z fit calibration.
    :rtype: dict
    """
    await check_sample_or_file_instrument_access(
        sample_item_id, user, "editor", "admin"
    )

    # Verify the existance of sample item
    sample_data = await get_sample_item(sample_item_id)
    sample = sample_data.get("data")
    named = await _sample_label(sample_item_id, sample, user)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        calibration_mz_fit,
        sample_item_id=sample_item_id,
        mz_calibration_params=mz_calibration_params,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Started to m/z fit sample{named}, please wait.",
        "process_id": process_id,
    }


@calibration_router.post("/mz_apply")
@api_route(
    status_code=202,
)
async def calibration_mz_apply_route(
    body: CalibrationMzApplyBody,
    background_tasks: BackgroundTasks,
    filename: str = Query(..., description="The filename to aply m/z fit"),
    user=Depends(current_active_user),
):
    """Apply m/z calibration to a sample file.

    Writes the calibration onto the file itself, so every sample item
    referencing it - in any workspace - is affected. Admin in the file's
    instrument workspace is therefore the bar, for the same reason deleting or
    reprocessing the file is governed there. Note the strict form: unlike those
    two, membership of a workspace holding an item that references the file does
    not stand in for the instrument role.

    :param body: The calibration apply body.
    :type body: CalibrationMzApplyBody
    :param background_tasks: Background tasks for async processing.
    :type background_tasks: BackgroundTasks
    :param filename: The filename to apply m/z calibration.
    :type filename: str
    :param user: The current authenticated user, defaults to Depends(current_active_user).
    :type user: User, optional
    :return: Message confirming application of m/z calibration.
    :rtype: dict
    """
    await check_filename_file_instrument_access(filename, user, "admin")

    # Verify the existance of sample file. Indexing into ``data`` before testing
    # it would raise IndexError on the empty list an unknown filename returns -
    # reachable now that a caller who bypasses the instrument ACL gets this far.
    sample_file_data = await get_sample_files(filename=filename)
    if not sample_file_data["data"]:
        raise NotFoundException(f"Sample file '{filename}' not found")

    # Get data for notifications
    process_id = gen_id(8)

    # ``manual``: this route exists for the calibration dialog, so reaching it
    # is an operator overruling the calibration already on the file. It is not
    # taken from the request body - a client cannot ask to be treated as the
    # automatic pipeline. See ``carry_acquisition_drift``.
    background_tasks.add_task(
        calibration_mz_apply,
        filename=filename,
        fit=body.fit,
        manual=True,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Started to apply m/z fit for sample file '{filename}', please wait.",
        "process_id": process_id,
    }


@calibration_router.post("/mz_calibrate/sample/{sample_item_id}")
@api_route(
    status_code=202,
)
async def calibration_mz_calibrate_sample_route(
    sample_item_id: str,
    mz_calibration_params: MzCalibrationParams,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """m/z calibrate specific sample.

    Fits and then applies, so it writes to the sample's underlying file and
    carries the same cross-workspace effect as ``/mz_apply``. Gated on admin in
    that file's instrument workspace.

    :param sample_item_id: The ID of the sample item.
    :type sample_item_id: str
    :param mz_calibration_params: Calibration parameters.
    :type mz_calibration_params: MzCalibrationParams
    :param background_tasks: Background tasks for async processing.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user, defaults to Depends(current_active_user).
    :type user: User, optional
    :return: Message confirming start of sample calibration.
    :rtype: dict
    """
    await check_sample_item_file_instrument_access(sample_item_id, user, "admin")

    # Verify the existance of sample item
    sample_data = await get_sample_item(sample_item_id)
    sample = sample_data.get("data")
    named = await _sample_label(sample_item_id, sample, user)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        calibration_mz_calibrate_sample,
        sample_item_id=sample_item_id,
        mz_calibration_params=mz_calibration_params,
        manual=True,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Started to m/z calibrate sample{named}, please wait.",
        "process_id": process_id,
    }


@calibration_router.post("/mz_calibrate/batch/{sample_batch_id}")
@api_route(
    status_code=202,
)
async def calibration_mz_calibrate_batch_route(
    sample_batch_id: str,
    mz_calibration_params: MzCalibrationParams,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """
    m/z calibrate all samples in a batch.
    - Processing batches cannot be calibrated
    - Sets batch status to "processing" during calibration and "rematch" after completion

    Writes to the file behind every sample in the batch, so admin is required
    in the instrument workspace of each instrument the batch draws on.

    :param sample_batch_id: The sample batch ID.
    :type sample_batch_id: str
    :param mz_calibration_params: Calibration parameters.
    :type mz_calibration_params: MzCalibrationParams
    :param background_tasks: Background tasks for async processing.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user, defaults to Depends(current_active_user).
    :type user: User, optional
    :return: Message confirming start of batch calibration.
    :rtype: dict
    """
    await check_sample_batch_file_instrument_access(sample_batch_id, user, "admin")

    # Verify the existance of sample batch and check status
    sample_batch = await fetch_sample_batch(sample_batch_id)

    # Admin in the instrument workspace does not imply membership of the
    # workspace holding this batch, so its name goes into the messages below
    # only for a caller who could have read that name through the batch routes.
    named = (
        f" '{sample_batch.sample_batch_name}'"
        if await access_granted(check_batch_access(sample_batch_id, user, "guest"))
        else ""
    )

    if sample_batch.status == "processing":
        msg = f"Sample batch{named} is currently processing. Please wait for completion and try again later."
        notification_data = {"sample_batch_id": sample_batch_id}
        raise ApiException(msg, notification_data, 409)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        calibration_mz_calibrate_batch,
        sample_batch_id=sample_batch_id,
        mz_calibration_params=mz_calibration_params,
        manual=True,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Started to m/z calibrate sample batch{named}, please wait.",
        "process_id": process_id,
    }
