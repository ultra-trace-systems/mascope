"""
Calibration process controllers.

Provides endpoints and background tasks for m/z calibration
and related operations.

Tasks:
- Retrieve existing m/z calibrations by instrument or sample
- Fit and apply m/z calibration to sample files
- Calibrate individual samples, sample sets, and full batches
"""

from typing import cast

from sqlalchemy import and_, func, select

from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_params_factory,
    get_calibration_handler,
)
from mascope_backend.api.controllers.match.match_controller import match_remove_sample
from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.sample.files.sample_files_controller import (
    update_sample_file,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    fetch_affected_sample_data,
)
from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
    raise_api_warning,
)
from mascope_backend.api.models.calibration.calibration_pydantic_model import (
    CalibrationFitParams,
    MzCalibrationParams,
)
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    SampleFileUpdate,
)
from mascope_backend.db import IonizationMode, Sample, SampleBatch, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_signal.compute import get_sum_signal


@api_controller()
async def get_mz_calibration(
    instrument: str | None = None,
    sample_item_id: str | None = None,
):
    """
    Retrieve the m/z calibration for a given instrument or sample item ID.

    :param instrument: (Optional) The instrument name.
    :type instrument: str, optional
    :param sample_item_id: (Optional) The sample item ID.
    :type sample_item_id: str, optional
    :return: The m/z calibration for the given parameters.
    :rtype: dict
    """
    async with async_session() as session:
        stmt = select(Sample.mz_calibration)
        if instrument:
            stmt = select(Sample.mz_calibration).where(
                and_(
                    Sample.instrument == instrument,
                    Sample.mz_calibration.isnot(None),
                    Sample.datetime_utc
                    == select(func.max(Sample.datetime_utc))
                    .where(
                        and_(
                            Sample.instrument == instrument,
                            Sample.mz_calibration.isnot(None),
                        )
                    )
                    .scalar_subquery(),
                )
            )
        elif sample_item_id:
            stmt = stmt.filter(Sample.sample_item_id == sample_item_id)

        result = await session.execute(stmt)
        mz_calibration = result.scalars().first()

    return {
        "message": "m/z calibration retrieved successfully.",
        "data": {"mz_calibration": mz_calibration} if mz_calibration else {},
    }


@api_controller()
async def get_default_calibration_params(sample_item_id: str) -> dict:
    """
    Instrument-appropriate default m/z calibration parameters for a sample.

    The UI calibration dialog seeds its parameter fields from this, so an
    Orbitrap sample starts from the same instrument defaults the automatic
    pipeline uses instead of one hardcoded parameter set.

    :param sample_item_id: ID of the sample item.
    :type sample_item_id: str
    :raises NotFoundException: If the sample is not found.
    :return: Dict with the default parameter values under ``data.params``.
    :rtype: dict
    """
    sample = await fetch_sample(sample_item_id)
    params = calibration_params_factory(filename=sample.filename)
    return {
        "message": "Default calibration parameters retrieved successfully.",
        "data": {"params": params.model_dump()},
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def calibration_mz_fit(
    sample_item_id: str,
    mz_calibration_params: MzCalibrationParams,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Fit m/z calibration parameters for a sample without applying them.

    Fits calibration parameters and collects affected sample data
    for reload events, but does not write calibration to the sample file.

    Steps:
    - Retrieve sample and ionization mode
    - Validate calibration collection is present
    - Fetch affected sample and batch IDs for reload events
    - Build calibration parameters and run the fit
    - Raise ApiException or warning if fit returns error or warning
    - Return fit result with notification data

    :param sample_item_id: ID of the sample item
    :type sample_item_id: str
    :param mz_calibration_params: Calibration parameters
    :type mz_calibration_params: MzCalibrationParams
    :param independent_transaction: Whether to run as independent transaction
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :param parent_id: Parent process ID for tracking
    :type parent_id: str | None, optional
    :raises NotFoundException: If sample, ionization mode or calibration
        collection not found
    :raises ApiException: If m/z fitting fails or produces a warning
    :return: Dict with fit data, message and notification data
    :rtype: dict
    """
    # --- Retrieve and validate sample and ionization mode ---
    sample = await fetch_sample(sample_item_id)
    async with async_session() as session:
        ionization_mode = await session.get(IonizationMode, sample.ionization_mode_id)
        if not ionization_mode:
            raise NotFoundException(
                f"Ionization mode with ID '{sample.ionization_mode_id}' not found"
            )

    # Check if calibration collection is present
    if not ionization_mode.calibration_collection_id:
        raise NotFoundException(
            "Calibration collection not found for ionization mode "
            f"'{ionization_mode.ionization_mode_id}'"
        )

    # --- Fetch affected samples for reload events ---
    (
        affected_sample_item_ids,
        affected_sample_batch_ids,
        *_,
    ) = await fetch_affected_sample_data(sample_file_ids=[sample.sample_file_id])

    # --- Prepare progress user notification ---
    notification = UserNotification(
        process_id=process_id or gen_id(8),
        parent_id=parent_id,
        type="calibration_mz_fit",
        status="pending",
        message=f"m/z fitting sample '{sample.sample_item_name}'.",
        data={
            "sample_item_id": sample_item_id,
            "filename": sample.filename,
            "_room_ids": [user_id],
            "_user_id": user_id,
        },
    )

    # --- Run m/z fit ---
    default_calibration_params = calibration_params_factory(filename=sample.filename)
    resolved_mz_params = mz_calibration_params.with_defaults(default_calibration_params)

    calibration_parameters = CalibrationFitParams(
        calibration_collection_id=ionization_mode.calibration_collection_id,
        ionization_mechanism_ids=ionization_mode.ionization_mechanism_ids,
        polarity=ionization_mode.ionization_mode_polarity,
        **resolved_mz_params.model_dump(),
    )
    calibration_handler = get_calibration_handler(
        sample.filename, calibration_parameters, notification
    )
    await calibration_handler.fit()
    calibration_data = calibration_handler.to_dict()

    # --- Build shared notification payload ---
    notification_data = {
        "affected_sample_item_ids": affected_sample_item_ids,
        "affected_sample_batch_ids": affected_sample_batch_ids,
        "sample_item_id": sample_item_id,
        "sample_file_id": sample.sample_file_id,
        "filename": sample.filename,
    }
    tech_message = {"data": calibration_data, "_notification_data": notification_data}

    # --- Handle fit errors and warnings ---
    if calibration_data["error"] is not None:
        # Expected for poor spectra; the raised ApiException reports it
        runtime.logger.info(calibration_data["error"])
        raise ApiException(
            f"m/z fitting for sample '{sample.sample_item_name}' failed: "
            f"{calibration_data['error']}",
            tech_message,
            422,
        )
    elif calibration_data["warning"] is not None:
        raise_api_warning(
            f"m/z fitting sample '{sample.sample_item_name}' warning: "
            f"{calibration_data['warning']}",
            tech_message,
        )

    return {
        "data": calibration_data,
        "message": f"Finished to m/z fit sample '{sample.sample_item_name}'.",
        "_notification_data": {
            **calibration_data,
            **notification_data,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["user_id"],
    error_reload=[("match", "affected_sample_batch_ids")],
)
async def calibration_mz_apply(
    fit: dict,
    filename: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id=None,
    parent_id=None,
) -> dict:
    """
    Apply m/z calibration to a sample file.
    Steps:
    - Retrieve sample file
    - Get affected sample items and their batches
    - Set non-ACQUISITION batches to "processing"
    - Prepare progress user notification
    - Apply m/z calibration
    - Update sample file database record with new calibration
    - Notify completion for each affected batch and remove existing matches
    - Set non-ACQUISITION batches to "rematch"
    - Return m/z fit result data and message

    :param fit: Fit dictionary.
    :param filename: Name of the sample file.
    :param independent_transaction: Whether to run as independent transaction
    :param user_id: Current user triggered operation (for user notifications)
    :param process_id: Process ID for tracking
    :param parent_id: Parent process ID for tracking
    :return: Dictionary containing fit results and notification data
    """
    # --- Retrieve sample file ---
    sample_file = await fetch_sample_file(filename=filename)

    # --- Get affected sample items and their batches ---
    affected = await fetch_affected_sample_data(
        sample_file_ids=[sample_file.sample_file_id], include_objects=True
    )
    affected_sample_item_ids = affected.affected_sample_item_ids
    affected_sample_batch_ids = affected.affected_sample_batch_ids
    affected_samples = cast(list[Sample], affected.affected_samples)
    affected_sample_batches = cast(list[SampleBatch], affected.affected_sample_batches)

    # --- Set non-ACQUISITION batches to "processing" ---
    non_acquisition_batch_ids = [
        b.sample_batch_id
        for b in affected_sample_batches
        if b.sample_batch_type != "ACQUISITION"
    ]
    # already "processing" batches will not change the status
    await update_sample_batch_status(
        sample_batch_ids=non_acquisition_batch_ids,
        status="processing",
        independent_transaction=True,
    )

    runtime.logger.info(
        f"Set {len(non_acquisition_batch_ids)} non-ACQUISITION batch(es) "
        "to 'processing' for calibration apply"
    )

    # --- Prepare progress user notification. ---
    total_samples = len(affected_sample_item_ids)
    notification = UserNotification(
        process_id=process_id or gen_id(8),
        parent_id=parent_id,
        type="calibration_mz_apply",
        status="pending",
        message=(
            f"Applying m/z fit for sample file '{filename}'. "
            f"Samples affected: {total_samples}."
        ),
        data={
            "sample_item_ids": affected_sample_item_ids,
            "filename": filename,
            "_room_ids": [sample_file.sample_file_id],
            "_user_id": user_id,
        },
    )

    await send_progress_user_notification(notification, 0.1)

    # --- Apply m/z calibration to file ---
    calibration_handler = get_calibration_handler(
        filename=filename, calibration_params=None, notification=notification
    )
    await calibration_handler.apply(fit)
    updated_mz_axis = get_sum_signal(filename).mz.values
    new_mz_range = [updated_mz_axis[0], updated_mz_axis[-1]]

    fit.update({"verified": True})

    await send_progress_user_notification(notification, 0.3)

    # --- Update sample file database record with new calibration ---
    sample_file.mz_calibration = fit
    sample_file.range = new_mz_range
    await update_sample_file(
        sample_file.sample_file_id, SampleFileUpdate(**sample_file.to_dict())
    )

    await send_progress_user_notification(notification, 0.8)

    # --- Per-batch: notify and remove existing matches ---
    for sample_batch in affected_sample_batches:
        sample_batch_id = sample_batch.sample_batch_id
        sample_batch_name = sample_batch.sample_batch_name
        batch_samples = [
            sample
            for sample in affected_samples
            if sample.sample_batch_id == sample_batch_id
        ]

        batch_notification = UserNotification(
            process_id=gen_id(8),
            parent_id=process_id,
            type="calibration_mz_apply",
            status="pending",
            message=(
                f"Applied m/z fit to '{filename}'. "
                f"Affected samples in batch '{sample_batch_name}': "
                f"{len(batch_samples)}."
            ),
            data={
                "sample_batch_id": sample_batch_id,
                "_room_ids": [sample_batch_id],
                "_user_id": user_id,
            },
        )
        await send_progress_user_notification(batch_notification)

        # FAQ_match removes matches in all samples associated with filename
        for sample_item in batch_samples:
            await match_remove_sample(
                sample_item_id=sample_item.sample_item_id,
                full_remove=True,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )
    # --- Set non-ACQUISITION batches to "rematch" ---
    # ACQUISITION batches being matched for the first time
    await update_sample_batch_status(
        sample_batch_ids=non_acquisition_batch_ids,
        status="rematch",
        independent_transaction=True,
    )
    runtime.logger.info(
        f"Set {len(non_acquisition_batch_ids)} non-ACQUISITION batch(es) "
        "to 'rematch' after applying m/z calibration"
    )

    # --- Return m/z fit result data and message ---
    message = (
        f"Applied m/z fit to '{filename}'. Number of affected samples: {total_samples}."
    )
    runtime.logger.info(message)
    return {
        "data": {
            "fit": fit,
        },
        "message": message,
        "_notification_data": {
            "affected_sample_item_ids": affected_sample_item_ids,
            "affected_sample_batch_ids": affected_sample_batch_ids,
            "filename": filename,
            "sample_file_id": sample_file.sample_file_id,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["user_id"],
    error_reload=[("match", "affected_sample_batch_ids")],
)
async def calibration_mz_calibrate_sample(
    sample_item_id: str,
    mz_calibration_params: MzCalibrationParams,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
):
    """
    Performs m/z calibration on a single sample using specified calibration parameters.

    Steps:
    - Retrieve sample and affected sample/batch IDs
    - Fit m/z calibration parameters
    - Apply calibration to the sample file
    - Return notification data for reload events

    :param sample_item_id: The ID of the sample to be calibrated
    :type sample_item_id: str
    :param mz_calibration_params: The calibration parameters to be used
    :type mz_calibration_params: MzCalibrationParams
    :param independent_transaction: Whether to run as independent transaction
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :param parent_id: Parent process ID for tracking
    :type parent_id: str | None, optional
    :raises NotFoundException: If sample not found.
    :raises ValueError: If the sample does not have a valid filename associated with it.
    :raises ApiException: For any exceptions that occur during the calibration process.
    """
    # --- Retrieve sample and affected IDs ---
    sample = await fetch_sample(sample_item_id)
    (
        affected_sample_item_ids,
        affected_sample_batch_ids,
        *_,
    ) = await fetch_affected_sample_data(sample_file_ids=[sample.sample_file_id])

    runtime.logger.info(f"...m/z calibrating sample '{sample.sample_item_name}' ...")

    # --- Prepare progress notification ---
    notification = UserNotification(
        process_id=process_id or gen_id(8),
        parent_id=parent_id,
        type="calibration_mz_calibrate_sample",
        status="pending",
        message=f"m/z calibrating sample '{sample.sample_item_name}'.",
        data={
            "sample_item_id": sample_item_id,
            "filename": sample.filename,
            "_room_ids": [user_id],
            "_user_id": user_id,
        },
    )
    await send_progress_user_notification(notification, 0.1)

    # --- Perform m/z fit ---
    # Errors/warnings raise ApiException with _notification_data
    calibration_mz_fit_result = await calibration_mz_fit(
        sample_item_id=sample_item_id,
        mz_calibration_params=mz_calibration_params,
        independent_transaction=False,
        user_id=user_id,
        process_id=gen_id(8),
        parent_id=process_id,
    )
    fit = calibration_mz_fit_result["data"].get("fit", None)
    await send_progress_user_notification(notification, 0.3)

    # --- Apply m/z calibration ---
    await calibration_mz_apply(
        fit=fit,
        filename=sample.filename,
        independent_transaction=False,
        user_id=user_id,
        process_id=gen_id(8),
        parent_id=process_id,
    )
    await send_progress_user_notification(notification, 0.95)

    return {
        "message": f"Sample '{sample.sample_item_name}' m/z calibrated.",
        "_notification_data": {
            "sample_item_id": sample_item_id,
            "sample_file_id": sample.sample_file_id,
            "filename": sample.filename,
            "affected_sample_item_ids": affected_sample_item_ids,
            "affected_sample_batch_ids": affected_sample_batch_ids,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["user_id"],
    error_reload=[("match", "affected_sample_batch_ids")],
)
async def calibration_mz_calibrate_samples(
    sample_item_ids: list[str],
    mz_calibration_params: MzCalibrationParams,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Perform m/z calibration on a list of samples using specified calibration parameters.

    Steps:
    - Emit progress notification for the batch
    - Calibrate each sample, collecting affected IDs
    - On per-sample failure, log warning and continue
    - Fetch affected batch IDs from all touched sample IDs
    - Raise warning if any samples failed
    - Return calibration summary and notification data

    :param sample_item_ids: List of sample item IDs to be calibrated.
    :type sample_item_ids: Iterable[str]
    :param mz_calibration_params: Calibration parameters to be used.
    :type mz_calibration_params: MzCalibrationParams
    :param independent_transaction: Whether to run as independent transaction.
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for operations tracking.
    :type process_id: str | None, optional
    :param parent_id: Parent process ID for operations tracking.
    :type parent_id: str | None, optional
    :raises NotFoundException: If any sample not found.
    :raises ApiException: If calibration fails.
    :return: A dictionary of calibration results for each sample in the batch.
    :rtype: dict
    """
    runtime.logger.info(f"...m/z calibrating {len(sample_item_ids)} samples ...")

    # --- Prepare progress user notification ---
    notification = UserNotification(
        process_id=process_id or gen_id(8),
        parent_id=parent_id,
        type="calibration_mz_calibrate_samples",
        status="pending",
        message=f"m/z calibrating {len(sample_item_ids)} samples.",
        data={
            "sample_item_ids": sample_item_ids,
            "_user_id": user_id,
        },
    )
    await send_progress_user_notification(notification)

    # --- Calibrate each sample and collect all affected IDs ---
    affected_sample_item_ids = set()
    failed_sample_items = []

    for sample_item_id in sample_item_ids:
        # Wrap in try/except to not break the loop if one item fails
        try:
            # Calibrate sample using specified parameters
            calibration_result = await calibration_mz_calibrate_sample(
                sample_item_id=sample_item_id,
                mz_calibration_params=mz_calibration_params,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )
            # Collect affected items from successful calibration
            affected_sample_item_ids.update(
                calibration_result.get("_notification_data", {}).get(
                    "affected_sample_item_ids", []
                )
            )
        except ApiException as e:
            sample = await fetch_sample(sample_item_id=sample_item_id)

            # INFO per sample: the failures are aggregated into the batch
            # result and shown to the user.
            runtime.logger.info(
                f"Calibrating sample '{sample.sample_item_name}' "
                f"failed: {e.user_message}"
            )
            failed_sample_items.append(
                {
                    "sample_item": {
                        "sample_item_id": sample.sample_item_id,
                        "sample_item_name": sample.sample_item_name,
                        "filename": sample.filename,
                    },
                    "warning_message": e.user_message,
                }
            )
            # Collect affected items from failed calibration
            affected_sample_item_ids.update(
                e.tech_message.get("_notification_data", {}).get(
                    "affected_sample_item_ids", []
                )
            )

    # --- Resolve affected batch IDs ---
    if affected_sample_item_ids:
        _, affected_sample_batch_ids, *_ = await fetch_affected_sample_data(
            sample_item_ids=list(affected_sample_item_ids)
        )
    else:
        affected_sample_batch_ids = []

    # --- Raise warning if any samples failed ---
    if failed_sample_items:
        warning_message = f"Failed to calibrate {len(failed_sample_items)} sample(s)."
        raise_api_warning(
            warning_message,
            {
                "samples_calibrate_failed": failed_sample_items,
                "_notification_data": {
                    "affected_sample_batch_ids": affected_sample_batch_ids,
                    "affected_sample_item_ids": list(affected_sample_item_ids),
                },
            },
        )

    return {
        "message": (
            f"M/z calibrated {len(sample_item_ids)} samples. "
            f"Number of batches affected: {len(affected_sample_batch_ids)}."
        ),
        "_notification_data": {
            "sample_item_ids": sample_item_ids,
            "affected_sample_batch_ids": affected_sample_batch_ids,
            "affected_sample_item_ids": list(affected_sample_item_ids),
        },
    }


@api_controller_background_task(
    success_notification_rooms=["sample_batch_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["sample_batch_id"],
    error_reload=[("match", "affected_sample_batch_ids")],
)
async def calibration_mz_calibrate_batch(
    sample_batch_id: str,
    mz_calibration_params: MzCalibrationParams,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Performs m/z calibration on all samples within a given batch
    using specified calibration parameters.

    Steps:
    - Check if sample batch is currently processed (to prevent concurrent calibration).
    - Fetch all samples associated with the specified sample batch.
    - Set the batch status to "processing" to lock it for calibration.
    - m/z calibrate each sample in the batch using the provided calibration parameters.
    - Collect and aggregate the results and affected sample/batch IDs.
    - Update the status of affected batches to "rematch" after calibration.
    - Return the calibration results, including notification data for UI updates.

    :param sample_batch_id: The ID of the sample batch to be calibrated.
    :type sample_batch_id: str
    :param mz_calibration_params: Calibration parameters to be used.
    :type mz_calibration_params: MzCalibrationParams
    :param independent_transaction: Whether to run as independent transaction.
    :type independent_transaction: bool
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None
    :param parent_id: Parent process identifier
    :type parent_id: str | None
    :raises NotFoundException: If batch or any sample not found.
    :raises ApiException: If calibration fails.
    :return: Calibration results with batch information and notification data
    :rtype: dict
    """
    # --- Retrieve batch and check if it's already processing ---
    sample_batch = await fetch_sample_batch(sample_batch_id)
    sample_batch_name = sample_batch.sample_batch_name

    if sample_batch.status == "processing":
        message = (
            f"Sample batch '{sample_batch_name}' "
            "is currently being processed - calibration is locked."
        )
        # Routine concurrency outcome, reported to the user via the response
        runtime.logger.info(message)
        return {
            "status": "locked",
            "message": message,
            "_notification_data": {"affected_sample_batch_ids": [sample_batch_id]},
        }

    runtime.logger.info(f"Starting m/z calibration for batch '{sample_batch_name}'")

    # --- Fetch samples in the batch ---
    async with async_session() as session:
        result = await session.execute(
            select(Sample).where(Sample.sample_batch_id == sample_batch_id)
        )

        samples = result.scalars().all()
    if not samples:
        raise NotFoundException(f"Sample batch '{sample_batch_name}' has no samples")

    # --- Set current batch status to processing to prevent concurrent operations ---
    await update_sample_batch_status(
        sample_batch_ids=[sample_batch_id],
        status="processing",
        independent_transaction=True,  # reload UI status icons
    )

    # --- Perform calibration on all samples ---
    try:
        calibration_result = await calibration_mz_calibrate_samples(
            sample_item_ids=[sample.sample_item_id for sample in samples],
            mz_calibration_params=mz_calibration_params,
            independent_transaction=False,
            user_id=user_id,
            process_id=gen_id(8),
            parent_id=process_id,
        )
    except ApiException:
        # If calibration of some samples fails, set batch status to rematch
        await update_sample_batch_status(
            sample_batch_ids=[sample_batch_id],
            status="rematch",
            independent_transaction=True,
        )
        # Re-raise the exception to propagate the error
        raise

    # --- Extract notification data from child operation and prepare response ---
    notification_data = calibration_result.get("_notification_data", {})
    affected_sample_batch_ids = notification_data.get("affected_sample_batch_ids", [])
    affected_sample_item_ids = notification_data.get("affected_sample_item_ids", [])

    # --- Update batch statuses ---
    await update_sample_batch_status(
        sample_batch_ids=affected_sample_batch_ids,
        status="rematch",
        independent_transaction=True,
    )

    message = (
        f"Sample batch '{sample_batch_name}' m/z calibrated successfully. "
        f"Affected sample batch count: {len(affected_sample_batch_ids)}. "
    )
    runtime.logger.info(f"{message}Batch status updated to 'rematch'.")

    # --- Return response with notification data ---
    return {
        "status": "success",
        "message": message,
        "_notification_data": {
            "affected_sample_batch_ids": affected_sample_batch_ids,
            "affected_sample_item_ids": affected_sample_item_ids,
        },
    }
