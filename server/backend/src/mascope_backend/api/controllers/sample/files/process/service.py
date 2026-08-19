"""
Controller for sample files auto-processing pipeline.

Handles automated creation of ACQUISITION datasets, batches, and sample items, and matching the samples.
"""

import asyncio

from sqlalchemy import delete, select
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from mascope_backend.api.controllers.calibration.calibration_controller import (
    calibration_mz_calibrate_sample,
    reset_mz_calibration,
)
from mascope_backend.api.controllers.calibration.lib.calibration_mz_fit import (
    calibration_params_factory,
)
from mascope_backend.api.controllers.dataset.acquisition.service import (
    get_acquisition_dataset,
)
from mascope_backend.api.controllers.match.match_controller import (
    match_compute_sample,
    rematch_samples,
)
from mascope_backend.api.controllers.sample.batches.sample_batches_controller import (
    create_sample_batch,
    get_sample_batches,
)
from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    create_sample_items,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    fetch_affected_sample_data,
)
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    raise_api_warning,
)
from mascope_backend.api.models.sample.batches.config import sample_batch_config
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SampleBatchCreate,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SampleItemCreate,
)
from mascope_backend.api.new.ionization.modes.util import (
    resolve_ionization_modes_by_tokens,
)
from mascope_backend.api.new.peak_assignments.service import (
    auto_assign_sample_peaks,
)
from mascope_backend.db import (
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    async_session,
    db_semaphore,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import (
    emit_record_deleted,
)


# Number of calibration fitting attempts before giving up
# Chosen so that final m/z error tolerance for TOF would be around 1000 ppm
CALIBRATION_ITERATIONS = 7

#: ApiException status codes a wider m/z error tolerance can actually clear.
#: 200/207 are the fit warnings, where the spectrum did not yield enough
#: matching calibration peaks ("Not enough calibration peaks", "No calibration
#: peaks found"); 422 is a degenerate fit - a zero polynomial coefficient -
#: which more matches can also resolve. Any other status is a fault (missing
#: calibration collection, database failure) that retrying cannot improve.
#: Note this also excludes the transient _RECOVERABLE_STATUS_CODES below:
#: calibrate_with_retry swallows rather than re-raises, so a pool timeout
#: during calibration leaves the sample uncalibrated instead of reaching the
#: pipeline-level retry.
RETRYABLE_CALIBRATION_STATUS = (200, 207, 422)

#: Fire-and-forget rematch tasks. asyncio only keeps weak references, so an
#: unreferenced task can be garbage-collected mid-run; the done-callback below
#: also surfaces failures that would otherwise die as unretrieved exceptions.
_background_tasks: set[asyncio.Task] = set()


def _observe_background_task(task: asyncio.Task) -> None:
    """Log the failure of a background task and release its reference."""
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        runtime.logger.opt(exception=exception).error(
            f"Background task '{task.get_name()}' failed"
        )


# Upper bound on auto-processing pipelines running concurrently in this
# worker. Each pipeline opens several database sessions in turn (batch
# get-or-create, calibration, matching), so an unbounded ingest burst - e.g.
# a whole folder of raw files converted back to back - stacks enough
# concurrent sessions to exhaust the worker's connection pool (pool_size +
# max_overflow), and everything that then waits longer than pool_timeout dies
# with "QueuePool limit reached, connection timed out": the converter's API
# calls fail (files quarantined) and pipelines die between sample_file and
# sample items. Excess files wait here and are processed as slots free up.
_AUTO_PROCESS_CONCURRENCY = 3
_auto_process_gate = asyncio.Semaphore(_AUTO_PROCESS_CONCURRENCY)

# Auto-processing runs as a fire-and-forget background task, so a failed
# pipeline has no caller to retry it and the file is silently lost (converted
# but never producing sample items). Transient infrastructure congestion -
# pool starvation in this worker (SQLAlchemy timeout / 503) or a briefly
# unreachable dependency (502/504) - is retried with growing delays; anything
# else (validation errors, missing records, data corruption) would fail
# identically on every attempt and is raised immediately.
_AUTO_PROCESS_RETRIES = 3
_AUTO_PROCESS_RETRY_DELAYS_S = (30, 60, 120)
_RECOVERABLE_STATUS_CODES = {502, 503, 504}


def _is_recoverable_error(exc: Exception) -> bool:
    """Whether a failed auto-process attempt is worth retrying.

    Nested controllers wrap pool starvation into ApiException 503 (and
    dependency outages into 502/504); database errors from direct session use
    in this module can still surface unwrapped.
    """
    if isinstance(exc, ApiException):
        return exc.status_code in _RECOVERABLE_STATUS_CODES
    return isinstance(exc, (SQLAlchemyTimeoutError, OperationalError, InterfaceError))


async def _delete_partial_acquisition_items(sample_file_id: str) -> None:
    """Delete ACQUISITION sample items a failed pipeline attempt left behind.

    Sample items are committed independently before calibration + matching,
    so a pipeline that failed later leaves them in place and a retry would
    create duplicates. Only ACQUISITION items are removed - user-created
    samples referencing the file are never touched.
    """
    async with async_session() as session:
        result = await session.execute(
            delete(SampleItem).where(
                SampleItem.sample_file_id == sample_file_id,
                SampleItem.sample_item_type == "ACQUISITION",
            )
        )
        await session.commit()
    if result.rowcount:
        runtime.logger.info(
            f"Removed {result.rowcount} partial ACQUISITION sample item(s) "
            f"for sample file {sample_file_id} before retrying"
        )


async def _rematch_when_slot_free(**kwargs) -> dict:
    """
    Run ``rematch_samples`` under the ingest gate.

    Every completed pipeline fires a rematch task for the samples it affected,
    so an ingest burst otherwise piles up unbounded concurrent rematches on
    top of the gated pipelines - the same pool-exhaustion mechanism the gate
    exists to prevent. Spawned as a task, so waiting for a slot here never
    blocks the pipeline that scheduled it.
    """
    async with _auto_process_gate:
        return await rematch_samples(**kwargs)


@api_controller_background_task(
    success_notification_rooms=["instrument"],
    success_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
    error_notification_rooms=["instrument"],
    error_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
)
async def auto_process_sample_file(
    sample_file_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Main orchestrator for automatic sample file processing pipeline.

    Processes uploaded sample files automatically into ACQUISITION datasets,
    creating the all data hierarchy if needed.

    Steps:
    - Validate sample file existence
    - Derive year from sample file datetime (prefers datetime_utc over datetime)
    - Get or create per-instrument workspace and year-based ACQUISITION dataset
      (the uploading user becomes workspace owner if the workspace is newly created)
    - Create ACQUISITION batches and sample items for each sample file ionization mode
    - Perform calibration and match computation for created ACQUISITION samples
      (calibration is skipped for blank files or when no calibration collection is set)
    - Schedule rematch tasks for other affected samples
    - Return processing results with affected IDs or UI reloads

    :param sample_file_id: ID of the uploaded sample file
    :type sample_file_id: str
    :param independent_transaction: Indicates whether this operation should be treated
                                    as a standalone transaction.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :param parent_id: Parent process ID for tracking hierarchical processes
    :type parent_id: str | None, optional
    :return: Processing results with affected IDs
    """
    for attempt in range(_AUTO_PROCESS_RETRIES + 1):
        try:
            async with _auto_process_gate:
                if attempt:
                    # A failed attempt may have committed sample items before
                    # dying in calibration/matching; remove them so the retry
                    # cannot create duplicates.
                    await _delete_partial_acquisition_items(sample_file_id)
                return await _auto_process_sample_file(
                    sample_file_id=sample_file_id,
                    independent_transaction=independent_transaction,
                    user_id=user_id,
                    process_id=process_id,
                    parent_id=parent_id,
                )
        except Exception as e:
            if attempt >= _AUTO_PROCESS_RETRIES or not _is_recoverable_error(e):
                # Terminal. Nothing downstream says which file this was: the
                # sample_file row stays, its batch still settles `ready`, and
                # the only trace is an absence - no matched peaks, and no
                # sample items either when a retry had already cleared the
                # partial ones. Name the file here or the shortfall is only
                # discoverable by counting rows afterwards.
                runtime.logger.error(
                    f"Auto-processing gave up on sample file {sample_file_id} "
                    f"after {attempt + 1} attempt(s); it will have no matched "
                    f"peaks: {e}"
                )
                raise
            delay = _AUTO_PROCESS_RETRY_DELAYS_S[attempt]
            runtime.logger.warning(
                f"Auto-processing attempt {attempt + 1} for sample file "
                f"{sample_file_id} hit a recoverable error ({e}); retrying "
                f"in {delay}s"
            )
            # Sleep outside the gate so a waiting pipeline can use the slot.
            await asyncio.sleep(delay)


async def _auto_process_sample_file(
    sample_file_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Gated body of ``auto_process_sample_file`` - see the public wrapper."""
    # Initialize collector for affected sample items
    all_affected_sample_item_ids = set()

    # --- Validate sample file existence --- #
    sample_file = await fetch_sample_file(sample_file_id=sample_file_id)

    # --- Get ACQUISITION dataset for the instrument --- #
    file_dt = sample_file.datetime_utc or sample_file.datetime
    acquisition_dataset = (
        await get_acquisition_dataset(
            instrument=sample_file.instrument,
            year=file_dt.year if file_dt else None,
            user_id=user_id,
        )
    ).get("data")

    # --- Create ACQUISITION batches and sample items for each ionization mode --- #
    (
        acquisition_samples,
        acquisition_sample_batches,
    ) = await create_acquisition_batches_and_items(
        sample_file=sample_file,
        dataset_id=acquisition_dataset.get("dataset_id"),
    )

    # Extract batch and sample IDs for notifications
    affected_sample_batch_ids = [
        batch.get("sample_batch_id") for batch in acquisition_sample_batches
    ]
    all_affected_sample_item_ids.update(
        sample["sample_item_id"] for sample in acquisition_samples
    )

    # Blank files are stored without an instrument config and should skip calibration.
    is_blank_sample_file = sample_file.instrument_function_id is None

    # --- Perform calibration and matching for created ACQUISITION samples --- #
    for sample in acquisition_samples:
        sample_item_id = sample["sample_item_id"]

        # Get ionization mode to check calibration collection
        async with async_session() as session:
            ionization_mode = await session.get(
                IonizationMode, sample["ionization_mode_id"]
            )

        # Perform calibration only when collection is configured and file is not blank.
        if (
            ionization_mode
            and ionization_mode.calibration_collection_id
            and not is_blank_sample_file
        ):
            calibrated = await calibrate_with_retry(
                sample=sample,
                sample_file_id=sample_file.sample_file_id,
                user_id=user_id,
                process_id=process_id,
            )
            if not calibrated:
                # The failure marker written by calibrate_with_retry would
                # trip the verified gate in match_compute_sample as a raised
                # warning, failing the whole pipeline; skip matching and
                # assignment explicitly - both assume a calibrated m/z axis.
                runtime.logger.info(
                    "Skipping matching and peak assignment for sample "
                    f"'{sample['sample_item_name']}': m/z calibration failed."
                )
                continue
        elif is_blank_sample_file:
            runtime.logger.info(
                "Skipping m/z calibration for blank file "
                f"'{sample['sample_item_name']}'. "
                "Calibration is not applicable."
            )
        else:
            ionization_mode_name = (
                ionization_mode.ionization_mode_name if ionization_mode else "unknown"
            )
            # INFO: reflects the user's collection configuration and fires for
            # every ingested file while unset
            runtime.logger.info(
                f"Skipping m/z calibration for sample '{sample['sample_item_name']}': "
                "Calibration collection is not set for the ionization mode "
                f"'{ionization_mode_name}'."
            )

        await match_compute_sample(
            sample_item_id=sample_item_id,
            independent_transaction=False,
            user_id=user_id,
            process_id=gen_id(8),
            parent_id=process_id,
        )

        # Assign peaks (Stage A / database-first only) for the new sample. Runs
        # after matching, isolates its own failures, and lets the parent emit the
        # peak_assignment_reload below.
        await auto_assign_sample_peaks(
            sample_item_id=sample_item_id,
            user_id=user_id,
            parent_id=process_id,
        )

    # --- Schedule rematch tasks for other affected samples --- #
    acquisition_sample_item_ids = {
        sample["sample_item_id"] for sample in acquisition_samples
    }
    # exclude the processed sample
    other_affected_sample_item_ids = (
        all_affected_sample_item_ids - acquisition_sample_item_ids
    )

    if other_affected_sample_item_ids:
        task = asyncio.create_task(
            _rematch_when_slot_free(
                sample_item_ids=other_affected_sample_item_ids,
                independent_transaction=True,  # Handle reloads independently
                user_id=user_id,
                process_id=gen_id(8),
            )
        )
        # asyncio only holds a weak reference to tasks: keep one so the task
        # cannot be garbage-collected mid-run, and observe its outcome so a
        # failure is logged instead of dying as an unretrieved exception.
        _background_tasks.add(task)
        task.add_done_callback(_observe_background_task)

        runtime.logger.info(
            "Started independent rematch task for "
            f"{len(other_affected_sample_item_ids)} affected samples"
        )

    # --- Return processed results with affected IDs for UI reloads --- #
    acquisition_samples = (
        await fetch_affected_sample_data(
            sample_item_ids=[
                sample["sample_item_id"] for sample in acquisition_samples
            ],
            include_objects=True,
        )
    ).affected_samples

    return {
        "message": (
            f"Auto-processing complete for {sample_file.filename}, processed "
            f"{len(acquisition_samples) if acquisition_samples else 0} samples."
        ),
        "data": acquisition_samples,
        "_notification_data": {
            "affected_sample_batch_ids": affected_sample_batch_ids,
            "affected_sample_item_ids": list(all_affected_sample_item_ids),
            "instrument": sample_file.instrument,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
    error_notification_rooms=["user_id"],
    error_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
)
async def re_process_sample_files(
    sample_file_ids: list[str],
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
) -> dict:
    """
    Re-processes multiple sample files by their unique IDs.

    Steps:
    - Validate all sample files exist and have no user-created samples
    - Delete existing ACQUISITION sample items for all files
    - Run auto-process pipeline for each file
    - Return aggregated results

    :param sample_file_ids: List of IDs of the sample files to re-process
    :type sample_file_ids: list[str]
    :param independent_transaction: Indicates whether this operation should be treated
                                    as a standalone transaction.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :return: Processing results with aggregated data
    :rtype: dict
    """
    processed_files = []
    failed_files = []
    affected_sample_batch_ids = set()
    affected_sample_item_ids = set()

    # --- Validate all sample files exist and collect data --- #
    async with async_session() as session:
        result = await session.execute(
            select(SampleFile).where(SampleFile.sample_file_id.in_(sample_file_ids))
        )
        sample_files = result.scalars().all()

    found_ids = {sf.sample_file_id for sf in sample_files}
    missing_ids = set(sample_file_ids) - found_ids

    for missing_id in missing_ids:
        failed_files.append(
            {
                "sample_file_id": missing_id,
                "filename": "unknown",
                "message": f"Sample file with ID '{missing_id}' not found",
            }
        )

    if not sample_files:
        message = f"None of the {len(sample_file_ids)} sample files found"
        raise ApiException(
            user_message=message,
            tech_message={"failed_files": failed_files},
            status_code=404,
        )

    # --- Check for user-created samples --- #
    async with async_session() as session:
        # Query for found_ids
        result = await session.execute(
            select(SampleItem, SampleBatch)
            .join(
                SampleBatch, SampleItem.sample_batch_id == SampleBatch.sample_batch_id
            )
            .where(
                SampleItem.sample_file_id.in_(found_ids),
                SampleItem.sample_item_type != "ACQUISITION",
            )
        )
        user_created_samples = result.all()

    # Map sample_file_id → (sample_item, batch) for fast lookup
    user_samples_dict = {
        sample_item.sample_file_id: (sample_item, batch)
        for sample_item, batch in user_created_samples
    }

    # --- Validate each file --- #
    valid_sample_files = []

    for sample_file in sample_files:
        # Check for user-created samples
        if sample_file.sample_file_id in user_samples_dict:
            sample_item, batch = user_samples_dict[sample_file.sample_file_id]
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": (
                        "Cannot re-process file as it is associated with user-created "
                        f"sample in the batch {batch.sample_batch_name}."
                    ),
                }
            )
            continue

        # Verify ionization modes are defined properly
        try:
            await resolve_ionization_modes_by_tokens(sample_file)
        except ValueError as ve:
            # Ionization mode resolution failed
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": str(ve),
                }
            )
            continue
        except Exception as e:
            # Other unexpected errors
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": f"Failed to resolve ionization modes: {str(e)}",
                }
            )
            runtime.logger.exception(
                "Unexpected error resolving ionization modes for sample file "
                f"{sample_file.filename}"
            )
            continue

        # Passed all validations
        valid_sample_files.append(sample_file)

    # --- Reset calibration so re-processing starts from the acquisition axis --- #
    # Orbitrap calibration is cumulative (the file's m/z axes are rescaled in
    # place), so without this a re-processed file silently keeps its previous
    # calibration. A failed reset keeps the old calibration; the file still
    # re-processes, so log instead of failing the whole request.
    for sample_file in valid_sample_files:
        try:
            await reset_mz_calibration(sample_file)
        except Exception:  # noqa: BLE001 - reset is best-effort per file
            runtime.logger.exception(
                "Failed to reset m/z calibration for "
                f"'{sample_file.filename}' before re-processing; the previous "
                "calibration remains in effect."
            )

    # --- Delete existing sample items for valid files --- #
    if valid_sample_files:
        valid_sample_file_ids = {sf.sample_file_id for sf in valid_sample_files}

        async with async_session() as session:
            # Collect existing sample items for notifications
            acquisition_sample_items = (
                (
                    await session.execute(
                        select(SampleItem).where(
                            SampleItem.sample_file_id.in_(valid_sample_file_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            # Delete
            await session.execute(
                delete(SampleItem).where(
                    SampleItem.sample_file_id.in_(valid_sample_file_ids)
                )
            )
            await session.commit()

            # Emit deletion notifications
            for sample_item in acquisition_sample_items:
                affected_sample_batch_ids.add(sample_item.sample_batch_id)
                if independent_transaction:
                    await emit_record_deleted(
                        record_type="sample",
                        record_id=sample_item.sample_item_id,
                        room=sample_item.sample_batch_id,
                    )

    # --- Process valid files --- #
    for sample_file in valid_sample_files:
        try:
            result = await auto_process_sample_file(
                sample_file_id=sample_file.sample_file_id,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )

            processed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": f"Successfully processed file {sample_file.filename}.",
                }
            )

            # Collect notification data
            file_notification_data = result.get("_notification_data", {})
            if "affected_sample_batch_ids" in file_notification_data:
                affected_sample_batch_ids.update(
                    file_notification_data["affected_sample_batch_ids"]
                )
            if "affected_sample_item_ids" in file_notification_data:
                affected_sample_item_ids.update(
                    file_notification_data["affected_sample_item_ids"]
                )
        except ApiException as ae:
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": f"Processing failed: {ae.user_message}",
                }
            )
        except Exception as e:
            failed_files.append(
                {
                    "sample_file_id": sample_file.sample_file_id,
                    "filename": sample_file.filename,
                    "message": f"Processing failed: {str(e)}",
                }
            )

    # --- Prepare response --- #
    total_files = len(sample_file_ids)
    processed_count = len(processed_files)
    failed_count = len(failed_files)
    notification_data = {
        "total_files": total_files,
        "processed_files": processed_files,
        "failed_files": failed_files,
        "summary": {
            "processed": processed_count,
            "failed": failed_count,
            "total": total_files,
        },
        "affected_sample_batch_ids": list(affected_sample_batch_ids),
    }
    # Determine status and message
    if failed_count == 0:
        message = f"Successfully re-processed {processed_count} sample files."
        return {
            "message": message,
            "_notification_data": notification_data,
        }
    elif processed_count == 0:
        message = f"Failed to re-process all {total_files} sample files.\n" + "\n".join(
            [f"{failed['filename']}: {failed['message']}" for failed in failed_files]
        )
        raise ApiException(
            user_message=message, tech_message=notification_data, status_code=422
        )
    else:
        message = (
            f"Re-processed {processed_count} files successfully, "
            f"{failed_count} files failed.\n"
            + "\n".join(
                [
                    f"{failed['filename']}: {failed['message']}"
                    for failed in failed_files
                ]
            )
        )
        raise_api_warning(message, notification_data, status_code=207)


async def create_acquisition_batches_and_items(
    sample_file: SampleFile, dataset_id: str
) -> tuple[list[dict], list[dict]]:
    """
    Create ACQUISITION batches and sample items for each ionization mode of sample file.

    For each ionization mode in the sample file:
    - Get or create daily ACQUISITION batch in provided acquisition dataset
    - Create ACQUISITION sample item within the batch
    - Configure batch with appropriate target collections and ionization mechanisms

    :param sample_file: Sample file record containing polarities and metadata
    :type sample_file: SampleFile
    :param dataset_id: ID of ACQUISITION dataset to create batches in
    :type dataset_id: str
    :return: Tuple of (created sample items, created/retrieved batches)
    :rtype: tuple[list[dict], list[dict]]
    """
    sample_items_to_create = []
    acquisition_sample_batches = []

    ionization_modes = await resolve_ionization_modes_by_tokens(sample_file)

    for ionization_mode in ionization_modes:
        # --- Generate daily ACQUISITION batch name for this ionization mode ---
        ion_mode_name = ionization_mode.ionization_mode_name
        batch_name = (
            f"{sample_file.datetime.strftime('%Y-%m-%d')} {ion_mode_name} acquisition"
        )

        # --- Get or create daily ACQUISITION batch for this ionization mode ---
        # Wrapped the entire get-or-create logic in semaphore to prevent race conditions
        async with db_semaphore:
            # Check if batch already exists
            batch_data = (
                await get_sample_batches(
                    dataset_id=dataset_id,
                    sample_batch_type=["ACQUISITION"],
                    sample_batch_name=batch_name,
                )
            ).get("data", [])

            if len(batch_data) > 1:
                # Recovered data anomaly: worth one grouped warning issue
                runtime.logger.warning(
                    f"Multiple ACQUISITION batches found for {batch_name} with "
                    f"ionization mode {ion_mode_name}, using first one."
                )

            if batch_data:
                acquisition_sample_batch = batch_data[0]
                runtime.logger.debug(
                    "Using existing ACQUISITION batch: "
                    f"{acquisition_sample_batch['sample_batch_name']}"
                )
            else:
                # Create new ACQUISITION batch
                # Get DIAGNOSTICS and CALIBRATION target collections for
                # ACQUISITION batches
                target_collection_ids = []
                if ionization_mode.diagnostic_collection_id:
                    target_collection_ids.append(
                        ionization_mode.diagnostic_collection_id
                    )
                if ionization_mode.calibration_collection_id:
                    target_collection_ids.append(
                        ionization_mode.calibration_collection_id
                    )

                if not target_collection_ids:
                    runtime.logger.info(
                        "No "
                        f"{', '.join(sample_batch_config.ACQUISITION_COLLECTION_TYPES)}"
                        " target collections found for ACQUISITION batch"
                    )

                # Create new ACQUISITION batch with defined build params
                acquisition_sample_batch = (
                    await create_sample_batch(
                        sample_batch=SampleBatchCreate(
                            dataset_id=dataset_id,
                            sample_batch_name=batch_name,
                            sample_batch_description=(
                                "Auto-generated daily acquisition batch "
                                f"for {sample_file.instrument}"
                            ),
                            sample_batch_type="ACQUISITION",
                            polarity=ionization_mode.ionization_mode_polarity,
                            target_collection_ids=target_collection_ids,
                        ),
                        independent_transaction=True,
                    )
                ).get("data")

                runtime.logger.debug(
                    "Created new ACQUISITION batch: "
                    f"{acquisition_sample_batch['sample_batch_name']}"
                )

        acquisition_sample_batches.append(acquisition_sample_batch)

        # Prepare ACQUISITION sample item for this ionization mode
        sample_items_to_create.append(
            SampleItemCreate(
                sample_batch_id=acquisition_sample_batch["sample_batch_id"],
                sample_file_id=sample_file.sample_file_id,
                sample_item_name=sample_file.datetime.strftime("%Y-%m-%d %H:%M:%S"),
                sample_item_type="ACQUISITION",
                sample_item_attributes={},
                polarity=ionization_mode.ionization_mode_polarity,
                ionization_mode_id=ionization_mode.ionization_mode_id,
            )
        )
    # Step 3: Create ACQUISITION sample items
    acquisition_samples = (
        await create_sample_items(
            sample_items=sample_items_to_create, independent_transaction=True
        )
    ).get("data", [])

    return acquisition_samples, acquisition_sample_batches


async def _record_calibration_failure(
    sample_file_id: str | None,
    error: Exception,
    attempts: int,
    mz_error_tolerance: float | None,
) -> None:
    """
    Persist a failed-calibration marker on the sample file.

    A given-up calibration otherwise leaves ``mz_calibration`` NULL, which is
    indistinguishable from "calibration not applicable" (blank files, modes
    without a calibration collection): the sample silently matches on the
    uncalibrated axis and nothing in the UI points at it. The marker record
    (``status: "failed"``, ``verified: False``) makes the outcome visible to
    the sample browser and trips the verified gate in the match computation.

    Never overwrites an existing record: an applied fit must survive a later
    failed re-attempt. Best-effort - a database error here is logged, not
    raised, so it cannot fail the surrounding pipeline.
    """
    if sample_file_id is None:
        return
    try:
        async with async_session() as session:
            sample_file = await session.get(SampleFile, sample_file_id)
            if sample_file is None or sample_file.mz_calibration is not None:
                return
            sample_file.mz_calibration = {
                "status": "failed",
                "verified": False,
                "error": str(error),
                "attempts": attempts,
                "mz_error_tolerance": mz_error_tolerance,
            }
            await session.commit()
    except SQLAlchemyError:
        runtime.logger.exception(
            f"Failed to record calibration failure for sample file {sample_file_id}"
        )


async def calibrate_with_retry(
    sample: dict,
    sample_file_id: str | None = None,
    user_id: int | None = None,
    process_id: str | None = None,
) -> bool:
    """Calibrate sample with retry logic

    If no matching calibration peaks are found, the m/z error tolerance is doubled
    and the calibration is retried, up to CALIBRATION_ITERATIONS times. Only the
    failures a wider tolerance can clear are retried (see
    RETRYABLE_CALIBRATION_STATUS); any other failure stops the loop at once.

    When every attempt fails, the outcome is persisted on the sample file via
    :func:`_record_calibration_failure` and ``False`` is returned so the caller
    can skip steps that assume a calibrated m/z axis (matching, assignment).

    :param sample: Sample dict to calibrate
    :type sample: dict
    :param sample_file_id: Sample file to mark when calibration fails
    :type sample_file_id: str | None, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking
    :type process_id: str | None, optional
    :return: True when a fit was applied, False when calibration was given up.
    :rtype: bool
    """
    mz_calibration_params = calibration_params_factory(sample["filename"])
    for i in range(1, CALIBRATION_ITERATIONS + 1):
        try:
            await calibration_mz_calibrate_sample(
                sample_item_id=sample["sample_item_id"],
                mz_calibration_params=mz_calibration_params,
                independent_transaction=False,
                user_id=user_id,
                process_id=gen_id(8),
                parent_id=process_id,
            )
            return True
        except ApiException as e:
            if e.status_code not in RETRYABLE_CALIBRATION_STATUS:
                # A fault rather than a data condition: a wider tolerance
                # cannot clear it, so stop here instead of burning the
                # remaining attempts on it.
                runtime.logger.exception(
                    "Failed to m/z calibrate sample item "
                    f"{sample['sample_item_name']}: {e}"
                )
                await _record_calibration_failure(
                    sample_file_id,
                    e,
                    attempts=i,
                    mz_error_tolerance=mz_calibration_params.mz_error_tolerance,
                )
                return False
            if i == CALIBRATION_ITERATIONS:
                # INFO: an expected data condition (a spectrum too poor to
                # yield calibration peaks), and this fires per sample of every
                # upload. For 200/207 the warning notification has already
                # reached the user; a 422 degenerate fit is recorded only here.
                runtime.logger.info(
                    "Gave up m/z calibration at m/z error tolerance "
                    f"{mz_calibration_params.mz_error_tolerance} "
                    f"for sample item {sample['sample_item_name']}: {e}"
                )
                await _record_calibration_failure(
                    sample_file_id,
                    e,
                    attempts=i,
                    mz_error_tolerance=mz_calibration_params.mz_error_tolerance,
                )
                return False
            else:
                # Double the m/z error tolerance, check refinement window limits, then retry
                old_tolerance = mz_calibration_params.mz_error_tolerance
                mz_calibration_params.mz_error_tolerance *= 2
                if (
                    mz_calibration_params.refine_window
                    <= mz_calibration_params.mz_error_tolerance
                ):
                    mz_calibration_params.refine_window = (
                        mz_calibration_params.mz_error_tolerance + 1
                    )
                # INFO: a retry that usually succeeds; the give-up above is
                # INFO too - only a non-retryable status logs at ERROR
                runtime.logger.info(
                    "Not enough calibration peaks with m/z error tolerance "
                    f"{old_tolerance}, retrying m/z calibration for sample "
                    f"{sample['sample_item_name']} with "
                    f"mz_error_tolerance={mz_calibration_params.mz_error_tolerance}."
                )
    # Unreachable: the final iteration always returns above. Kept so the
    # signature honestly never yields None.
    return False
