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
from mascope_backend.api.new.auth.dependencies import admin_user, guest_user
from mascope_backend.db.id import gen_id


calibration_router = APIRouter(prefix="/api/calibration", tags=["Calibration"])


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
    user=Depends(admin_user),
):
    """Initiate m/z fitting for a sample.

    :param mz_calibration_params: Parameters for m/z calibration.
    :type mz_calibration_params: MzCalibrationParams
    :param background_tasks: Background tasks for async processing.
    :type background_tasks: BackgroundTasks
    :param sample_item_id: The sample item ID.
    :type sample_item_id: str
    :param user: The current authenticated admin, defaults to Depends(admin_user).
    :type user: User, optional
    :return: Message confirming start of m/z fit calibration.
    :rtype: dict
    """
    # Verify the existance of sample item
    sample_data = await get_sample_item(sample_item_id)
    sample = sample_data.get("data")
    sample_item_name = sample["sample_item_name"]

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
        "message": f"Started to m/z fit sample '{sample_item_name}', please wait.",
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
    user=Depends(admin_user),
):
    """Apply m/z calibration to a sample file.

    :param body: The calibration apply body.
    :type body: CalibrationMzApplyBody
    :param background_tasks: Background tasks for async processing.
    :type background_tasks: BackgroundTasks
    :param filename: The filename to apply m/z calibration.
    :type filename: str
    :param user: The current authenticated admin, defaults to Depends(admin_user).
    :type user: User, optional
    :return: Message confirming application of m/z calibration.
    :rtype: dict
    """
    # Verify the existance of sample file
    sample_file_data = await get_sample_files(filename=filename)
    if not sample_file_data["data"][0]:
        raise NotFoundException(f"Sample file '{filename}' not found")

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        calibration_mz_apply,
        filename=filename,
        fit=body.fit,
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
    user=Depends(admin_user),
):
    """m/z calibrate specific sample.

    :param sample_item_id: The ID of the sample item.
    :type sample_item_id: str
    :param mz_calibration_params: Calibration parameters.
    :type mz_calibration_params: MzCalibrationParams
    :param background_tasks: Background tasks for async processing.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated admin, defaults to Depends(admin_user).
    :type user: User, optional
    :return: Message confirming start of sample calibration.
    :rtype: dict
    """
    # Verify the existance of sample item
    sample_data = await get_sample_item(sample_item_id)
    sample = sample_data.get("data")
    sample_item_name = sample["sample_item_name"]

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        calibration_mz_calibrate_sample,
        sample_item_id=sample_item_id,
        mz_calibration_params=mz_calibration_params,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Started to m/z calibrate sample '{sample_item_name}', please wait.",
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
    user=Depends(admin_user),
):
    """
    m/z calibrate all samples in a batch.
    - Processing batches cannot be calibrated
    - Sets batch status to "processing" during calibration and "rematch" after completion

    :param sample_batch_id: The sample batch ID.
    :type sample_batch_id: str
    :param mz_calibration_params: Calibration parameters.
    :type mz_calibration_params: MzCalibrationParams
    :param background_tasks: Background tasks for async processing.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated admin, defaults to Depends(admin_user).
    :type user: User, optional
    :return: Message confirming start of batch calibration.
    :rtype: dict
    """
    # Verify the existance of sample batch and check status
    sample_batch = await fetch_sample_batch(sample_batch_id)

    if sample_batch.status == "processing":
        msg = f"Sample batch '{sample_batch.sample_batch_name}' is currently processing. Please wait for completion and try again later."
        notification_data = {"sample_batch_id": sample_batch_id}
        raise ApiException(msg, notification_data, 409)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        calibration_mz_calibrate_batch,
        sample_batch_id=sample_batch_id,
        mz_calibration_params=mz_calibration_params,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Started to m/z calibrate sample batch '{sample_batch.sample_batch_name}', please wait.",
        "process_id": process_id,
    }
