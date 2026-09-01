import asyncio
import math
import os
import shutil
from datetime import datetime
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException, UploadFile
from sqlalchemy import (
    asc,
    desc,
    exists,
    func,
    or_,
    select,
)

import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.dataset.acquisition.service import (
    create_acquisition_datasets,
    delete_acquisition_datasets,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    fetch_affected_sample_data,
)
from mascope_backend.api.controllers.samples.samples_controller import get_samples
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
    raise_api_warning,
)
from mascope_backend.api.models.sample.files.sample_file_pydantic_model import (
    SampleFileCreate,
    SampleFileUpdate,
)
from mascope_backend.api.new.instruments import get_instruments
from mascope_backend.db import (
    AgentDevice,
    Dataset,
    SampleBatch,
    SampleFile,
    SampleItem,
    User,
    WorkspaceMember,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket import event_emitter
from mascope_backend.socket.notifications import (
    UserNotification,
    emit_user_notification,
)
from mascope_backend.socket.records.service import (
    emit_record_created,
    emit_record_deleted,
    emit_record_updated,
)
from mascope_backend.socket.storage.services import is_service_connected
from mascope_file.io import load_peak_data
from mascope_file.name import parse_path_from_item_filename
from mascope_signal.peak import get_peaks


async def ensure_converter_available() -> None:
    """Refuse work with 503 unless the file-converter service is connected.

    The converter learns of an upload or a peak-detection request only through
    the socket payload emitted when the work is accepted, so admitting it while
    no converter is connected would strand it (the watcher quarantines files it
    has no context for). Callers gate on this *before* accepting the work - and,
    for transfers, before the transfer starts: a refusal only after the bytes
    have moved cannot un-accept them.
    """
    if not await is_service_connected("file-converter"):
        raise HTTPException(
            status_code=503,
            detail="File converter service is not available or not running.",
        )


async def _register_file_with_converter(
    filename: str,
    user: User,
    access_token: str,
    device_id: int | None,
    instrument_timezone: str | None,
) -> None:
    """Tell the converter who uploaded a file, before the file is published.

    Both upload paths call this while the bytes are still staged under a name
    the converter's watcher cannot match, so the context is in place before the
    file becomes visible. Emitting after publication races the watcher's poll
    and strands the file in ``failed_files``.

    :param filename: The file's final name in the filestreams folder.
    :type filename: str
    :param user: The authenticated user performing the upload.
    :type user: User
    :param access_token: Pre-validated access token for the converter.
    :type access_token: str
    :param device_id: The paired device behind the upload, for attribution.
    :type device_id: int | None
    :param instrument_timezone: IANA timezone the uploading machine reported.
    :type instrument_timezone: str | None
    """
    await event_emitter.emit(
        "file-converter.auth",
        {
            "filename": filename,
            "user_id": user.id,
            "username": user.username,
            "role_id": user.role_id,
            "access_token": access_token,
            "device_id": device_id,
            "instrument_timezone": instrument_timezone,
        },
    )


async def request_peak_detection(
    sample_file_id: str,
    filename: str,
    user: User,
    access_token: str,
    process_id: str | None = None,
) -> None:
    """Ask the file converter to rebuild a sample file's peak data.

    The request is queued, not performed: the converter's socket handler
    enqueues it (rejecting a duplicate for a file already in flight), a pool of
    worker threads runs the detection, and on completion the worker rematches
    every sample item of the file. So a caller asks and must not wait - the
    samples repair themselves and come back matched on their own.

    Callers gate on :func:`ensure_converter_available` first; with no converter
    connected the request would be stranded.

    :param sample_file_id: The sample file whose peaks are to be rebuilt.
    :type sample_file_id: str
    :param filename: That file's filename, as the converter addresses it.
    :type filename: str
    :param user: The user the work is done on behalf of; the converter calls
        back into this API as them.
    :type user: User
    :param access_token: That user's file-converter access token.
    :type access_token: str
    :param process_id: Process identifier the progress is reported under.
    :type process_id: str | None
    :return: None
    """
    (
        affected_sample_item_ids,
        _,
        *_,
    ) = await fetch_affected_sample_data(sample_file_ids=[sample_file_id])

    # --- Emit peak detection request to file converter service via Socket.IO event ---
    await event_emitter.emit(
        "file-converter.peak_detection_request",
        {
            "filename": filename,
            "sample_file_id": sample_file_id,
            "affected_sample_item_ids": affected_sample_item_ids,
            "process_id": process_id,
            "user_id": user.id,
            "username": user.username,
            "role_id": user.role_id,
            "access_token": access_token,
        },
    )

    # Send an immediate "pending" notification so the UI shows a progress
    # bar as soon as the request is accepted.
    pending_notification = UserNotification(
        process_id=process_id,
        type="compute_sample_file_peaks",
        status="pending",
        message=f"Peak detection queued for '{filename}'...",
        data={
            "filename": filename,
            "sample_file_id": sample_file_id,
        },
        progress=5,  # Start with 5% to indicate it's in progress
    )
    await emit_user_notification(notification=pending_notification, user_id=user.id)


# TODO_configuration Default sample file upload params
FILE_UPLOAD_CHUNK_SIZE = 2 * 1024 * 1024  # 2 MB


@api_controller()
async def get_sample_files(
    datetime_min: datetime | None = None,
    datetime_max: datetime | None = None,
    instrument: str | None = None,
    filename: str | None = None,
    sort: str = "datetime_utc",
    order: str = "asc",
    page: int | None = None,
    limit: int | None = None,
    allowed_instruments: set[str] | None = None,
    user_id: int | None = None,
) -> dict:
    """
    Retrieves a paginated list of sample files, optionally filtered by date range,
    instrument, or filename, and sorted by a specified column.

    :param datetime_min: Minimum date and time for filtering sample files, optional.
    :param datetime_max: Maximum date and time for filtering sample files, optional.
    :param instrument: Instrument name for filtering sample files, optional.
    :param filename: Filename for filtering sample files, optional.
    :param sort: Column to sort by, defaults to "datetime_utc".
    :param order: Sorting order, "asc" for ascending or "desc" for descending.
    :param page: Page number for pagination, defaults to None (no pagination).
    :param limit: Number of items per page, defaults to None (no pagination).
    :param allowed_instruments: Set of instrument names the user may see via
        acquisition workspace membership.  ``None`` means no filtering (superuser).
    :param user_id: When set, also includes files linked to sample items in
        workspaces the user is a member of (item-based access).
    :return: A dictionary containing the total count and list of sample files.
    """
    async with async_session() as session:
        # --- Construct query
        stmt = select(SampleFile)

        # --- Apply access filters
        if allowed_instruments is not None:
            # Build OR: instrument in allowed set, or file linked to user's workspaces
            instrument_filter = (
                SampleFile.instrument.in_(allowed_instruments)
                if allowed_instruments
                else None
            )
            item_filter = (
                exists(
                    select(SampleItem.sample_item_id)
                    .join(
                        SampleBatch,
                        SampleBatch.sample_batch_id == SampleItem.sample_batch_id,
                    )
                    .join(Dataset, Dataset.dataset_id == SampleBatch.dataset_id)
                    .join(
                        WorkspaceMember,
                        WorkspaceMember.workspace_id == Dataset.workspace_id,
                    )
                    .where(
                        SampleItem.sample_file_id == SampleFile.sample_file_id,
                        WorkspaceMember.user_id == user_id,
                    )
                )
                if user_id is not None
                else None
            )
            conditions = [c for c in (instrument_filter, item_filter) if c is not None]
            if not conditions:
                return {
                    "message": "Sample files retrieved successfully.",
                    "results": 0,
                    "data": [],
                }
            stmt = stmt.where(or_(*conditions))
        if datetime_min:
            stmt = stmt.where(SampleFile.datetime_utc >= datetime_min)
        if datetime_max:
            stmt = stmt.where(SampleFile.datetime_utc <= datetime_max)
        if instrument:
            stmt = stmt.where(SampleFile.instrument == instrument)
        if filename:
            stmt = stmt.where(SampleFile.filename == filename)

        # --- Apply sorting
        stmt = (
            stmt.order_by(desc(getattr(SampleFile, sort)))
            if order == "desc"
            else stmt.order_by(asc(getattr(SampleFile, sort)))
        )

        # --- Apply pagination
        total = await session.scalar(select(func.count()).select_from(stmt))
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)

        # --- Execute query and fetch results
        result = await session.execute(stmt)
        sample_files = result.scalars().all()

        # --- Return results
        return {
            "message": "Sample files retrieved successfully.",
            "results": total,
            "data": [sample_file.to_dict() for sample_file in sample_files],
        }


@api_controller()
async def get_sample_file(sample_file_id: str) -> dict:
    """
    Retrieves a single sample file by its unique ID.

    Steps:
    1. Execute a query to fetch the sample file with the specified ID.
    2. Check if the sample file exists. If not, raise a NotFoundException.
    3. Return the sample file's details as a dictionary.

    :param sample_file_id: Unique identifier of the sample file to retrieve.
    :type sample_file_id: str
    :raises NotFoundException: If the sample file with the given ID is not found.
    :return: The requested sample file's details.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch sample file by ID
        sample_file = await session.get(SampleFile, sample_file_id)

        # Step 2: Check existence
        if not sample_file:
            raise NotFoundException(f"Sample file with ID '{sample_file_id}' not found")

        # Step 3: Return sample file details
        return {
            "message": f"Sample file '{sample_file.filename}' retrieved successfully.",
            "data": sample_file.to_dict(),
        }


@api_controller()
async def create_sample_file(
    sample_file_create: SampleFileCreate,
    background_tasks: BackgroundTasks,
    user_id: int | None = None,
    process_id: str | None = None,
) -> dict:
    """
    Creates a new sample file with the given data.

    Steps:
    1. Check if a sample file with the given filename already exists.
    2. Construct a new SampleFile object with provided data and add it to the session.
    3. Commit the transaction to persist the new sample file in the database.
    4. Refresh the instance to get the created data from the database.
    5. Reload instruments and create acquisition datasets if needed
    6. Trigger automatic processing of the sample file
    7. Return the created sample file data.

    :param sample_file_create: Data for creating the sample file.
    :type sample_file_create: SampleFileCreate
    :param background_tasks:  Background tasks for triggering an automatic processing for sample file after creation.
    :type background_tasks: BackgroundTasks
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process ID for tracking the background task.
    :type process_id: str | None
    :raises NotFoundException: If the new sample file is not found after creation.
    :return: The created sample file data.
    :rtype: dict
    """
    # Step 1: Check if a sample file with the given filename already exists
    existing_files = await get_sample_files(filename=sample_file_create.filename)

    if existing_files["results"] > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Sample file with filename '{sample_file_create.filename}' already exists",
        )

    async with async_session() as session:
        # Step 1: Get instruments
        instruments_response = await get_instruments()
        instruments = instruments_response["data"]
        initial_instruments = [i["instrument"] for i in instruments]

        # The device travels in the body because the converter writes this
        # record back on its own (unbound) token, long after the agent's
        # request ended - so it cannot be derived from the caller's binding.
        # It is therefore only honoured when the caller IS the machine account
        # that device authenticates as; anything else would let any editor
        # stamp a file with another site's instrument. Attribution must never
        # fail an ingest, so a rejected or vanished id degrades to
        # unattributed rather than raising.
        device_id = sample_file_create.uploaded_by_device_id
        if device_id is not None:
            device = await session.get(AgentDevice, device_id)
            if device is None:
                runtime.logger.warning(
                    f"Upload of '{sample_file_create.filename}' referenced "
                    f"unknown device {device_id}; storing without device "
                    "attribution"
                )
                device_id = None
            elif device.machine_user_id != user_id:
                runtime.logger.warning(
                    f"Upload of '{sample_file_create.filename}' claimed device "
                    f"{device_id}, which user {user_id} does not authenticate "
                    "as; storing without device attribution"
                )
                device_id = None

        # Step 2: Construct new sample file. The uploading user comes from
        # the authenticated request (user_id), not from the request body.
        new_sample_file = SampleFile(
            sample_file_id=gen_id(16),
            **sample_file_create.model_dump(exclude={"uploaded_by_device_id"}),
            uploaded_by_device_id=device_id,
            uploaded_by_user_id=user_id,
        )
        session.add(new_sample_file)

        # Step 3: Commit transaction
        await session.commit()

        # Step 4: Refresh instance
        await session.refresh(new_sample_file)

        # Step 5. Emit creation event and handle instrument changes
        await emit_record_created(
            record_type="acquisition",
            record_id=new_sample_file.sample_file_id,
            record=new_sample_file.to_dict(),
            room=new_sample_file.instrument,
        )

        if new_sample_file.instrument not in initial_instruments:
            # New instrument detected - create datasets and emit instrument events
            await create_acquisition_datasets(user_id=user_id)

        # Step 6: Trigger automatic processing of the sample file
        from mascope_backend.api.controllers.sample.files.process.service import (
            spawn_auto_process_sample_file,
        )

        #  TODO Most likely can be moved to module imports after removing circular import
        # with process_instrument_config https://github.com/ultra-trace-systems/mascope/issues/1248

        background_tasks.add_task(
            spawn_auto_process_sample_file,
            sample_file_id=new_sample_file.sample_file_id,
            independent_transaction=True,
            user_id=user_id,
            process_id=process_id,
        )

        # Step 7: Return created sample file
        return {
            "message": f"Sample file '{new_sample_file.filename}' created successfully.",
            "data": new_sample_file.to_dict(),
        }


# ---------------------
# Sample file deletion
# ---------------------


@api_controller()
async def delete_sample_file_db_record(sample_file_id: str) -> dict[str, str]:
    """
    Deletes a sample file database record by its unique identifier.

    Steps:
    - Fetch the sample file by its ID from the database.
    - If the sample file is found, delete it from the session and commit the changes to the database.

    :param sample_file_id: The unique identifier of the sample file to delete.
    :type sample_file_id: str
    :raises NotFoundException: If no sample file is found with the provided ID.
    :return: Dictionary with status and success message.
    :rtype: dict[str, str]
    """
    # --- Fetch the sample file ---
    async with async_session() as session:
        sample_file = await session.get(SampleFile, sample_file_id)
        if not sample_file:
            raise NotFoundException(f"Sample file with ID '{sample_file_id}' not found")

        filename = sample_file.filename

        # --- Delete the sample file and commit changes ---
        await session.delete(sample_file)
        await session.commit()

    # --- Emit deletion event when db record was deleted ---
    await emit_record_deleted(
        record_type="acquisition",
        record_id=sample_file_id,
        room=sample_file.instrument,
    )

    return {
        "status": "success",
        "message": f"Sample file '{filename}' deleted from database successfully.",
    }


async def delete_sample_file_from_filestore(filename: str) -> dict[str, str]:
    """
    Removes a sample file from the filestore directory.

    Steps:
    1. Parse the filestore path from the filename.
    2. Check if the filestore directory exists.
    3. Remove the directory if it exists.

    :param filename: The filename to construct the filestore path.
    :type filename: str
    :return: Dictionary with status and message.
    :rtype: dict[str, str]
    """
    # Step 1: Parse filestore path from filename
    try:
        filestore_path = parse_path_from_item_filename(filename)
    except Exception as e:
        runtime.logger.exception(f"Failed to parse filestore path for '{filename}'")
        return {
            "status": "error",
            "message": f"Failed to parse filestore path for '{filename}': {e}",
        }

    # Step 2: Check if filestore directory exists
    if not os.path.exists(filestore_path):
        runtime.logger.debug(f"Filestore directory {filestore_path} does not exist.")
        raise NotFoundException(f"{filename} does not exist in filestore.")

    # Step 3: Remove the directory
    try:
        # Deleting a sample directory means unlinking every zarr chunk in it,
        # which is thousands of syscalls for a large file.
        await asyncio.to_thread(shutil.rmtree, filestore_path)
        runtime.logger.info(f"Deleted filestore directory: {filestore_path}")
        return {
            "status": "success",
            "message": f"Filestore directory for '{filename}' deleted successfully.",
        }
    except Exception as e:
        runtime.logger.exception(
            f"Failed to delete filestore directory for '{filename}'"
        )
        return {
            "status": "error",
            "message": f"Failed to delete filestore directory for '{filename}': {e}",
        }


@api_controller()
async def delete_sample_file(
    sample_file_id: str | None = None, filename: str | None = None
) -> dict[str, str]:
    """
    Deletes a sample file by either ID or filename, removing both database record and filestore file.
    Performs sample item association check for safety.

    Steps:
    - Validate that exactly one parameter is provided.
    - If sample_file_id is provided, fetch sample file and get filename.
    - If filename is provided, try to find corresponding database record.
    - Check for associated sample items and block deletion if found.
    - Delete database record if exists.
    - Delete filestore file.

    :param sample_file_id: The ID of the sample file to delete (optional).
    :type sample_file_id: str | None
    :param filename: The filename of the sample file to delete (optional).
    :type filename: str | None
    :raises ValueError: If both or neither parameters are provided.
    :raises NotFoundException: If sample_file_id is provided but not found.
    :raises HTTPException: If sample items are associated with this file.
    :return: Dictionary with status and message.
    :rtype: dict[str, str]
    """
    # --- Validate parameters ---
    if not (sample_file_id or filename) or (sample_file_id and filename):
        raise ValueError(
            "Exactly one parameter must be provided: either sample_file_id or filename"
        )
    target_filename = filename

    # --- Handle sample_file_id case ---
    if sample_file_id:
        sample_file_data = (await get_sample_file(sample_file_id))["data"]
        target_filename = sample_file_data["filename"]

    # --- Handle filename case - try to find database record ---
    elif filename:
        async with async_session() as session:
            stmt = select(SampleFile).where(SampleFile.filename == filename)
            result = await session.execute(stmt)
            if sample_file := result.scalar_one_or_none():
                sample_file_id = sample_file.sample_file_id

    # --- Safety check - verify no associated sample items exist ---
    if associated_samples := (await get_samples(filename=target_filename))["data"]:
        sample_item_ids = [sample["sample_item_id"] for sample in associated_samples]
        message = (
            f"Cannot delete sample file '{target_filename}' because it is associated with "
            f"{len(sample_item_ids)} sample item(s). Delete the sample items first."
        )
        data = {"sample_item_ids": sample_item_ids}
        raise_api_warning(message, data, status_code=207)
        return {"status": "error", "message": message}

    # --- Delete database record if exists ---
    db_record_deleted = False
    if sample_file_id:
        try:
            db_record_deleted = (await delete_sample_file_db_record(sample_file_id))[
                "status"
            ] == "success"
        except NotFoundException:
            # Record doesn't exist, continue with filestore deletion
            pass

    # --- Delete filestore file ---
    filestore_deleted = False
    try:
        filestore_result = await delete_sample_file_from_filestore(target_filename)
        filestore_deleted = filestore_result["status"] == "success"
    except NotFoundException:
        pass

    # --- Determine overall status and message ---
    match (db_record_deleted, filestore_deleted):
        case (True, True):
            status = "success"
            message = f"Sample file '{target_filename}' deleted successfully from database and filestore."
        case (True, False):
            status = "partial"
            message = f"Sample file '{target_filename}' deleted from database but filestore deletion failed."
        case (False, True):
            status = "partial"
            message = f"Sample file '{target_filename}' deleted from filestore but no database record found."
        case (False, False):
            status = "error"
            message = (
                f"Sample file '{target_filename}' not found in database or filestore."
            )

    return {"status": status, "message": message}


@api_controller()
async def delete_sample_files(
    sample_file_ids: list[str] | None = None, filenames: list[str] | None = None
) -> dict[str, str | dict]:
    """
    Deletes multiple sample files by their unique IDs or filenames and removes the corresponding filestore directories.
    Only deletes files that don't have existing sample items associated with them.

    Steps:
    - Collect all sample file data and check associations upfront.
    - Delete files that have no associations.
    - Emit socket events for instruments and acquisitions.
    - Return results with information about deleted and skipped files.

    :param sample_file_ids: List of IDs of the sample files to delete (optional).
    :type sample_file_ids: list[str] | None
    :param filenames: List of filenames of the sample files to delete (optional).
    :type filenames: list[str] | None
    :raises ValueError: If both or neither parameters are provided.
    :raises NotFoundException: If any of the sample files with the given IDs are not found.
    :raises ApiException: If any files were skipped due to associated sample items.
    :return: Dictionary with information about deleted and skipped files.
    :rtype: dict[str, str | dict]
    """
    deleted_files = []
    skipped_files_associations = []  # Files skipped due to sample item associations
    skipped_files_not_found = []  # Files skipped because they don't exist
    all_sample_item_ids = []
    instruments_affected = set()
    files_to_delete = []

    # --- Process by sample_file_ids ---
    if sample_file_ids:
        for sample_file_id in sample_file_ids:
            try:
                # Get sample file data
                sample_file_data = (await get_sample_file(sample_file_id))["data"]
                filename, instrument = (
                    sample_file_data["filename"],
                    sample_file_data["instrument"],
                )

                # Check for associated sample items
                if associated_samples := (await get_samples(filename=filename))["data"]:
                    skipped_files_associations.append(sample_file_id)
                    all_sample_item_ids.extend(
                        [sample["sample_item_id"] for sample in associated_samples]
                    )
                else:
                    files_to_delete.append(
                        {
                            "sample_file_id": sample_file_id,
                            "filename": filename,
                            "instrument": instrument,
                        }
                    )

            except NotFoundException:
                skipped_files_not_found.append(sample_file_id)

    # --- Process by filenames ---
    elif filenames:
        for filename in filenames:
            # Check for associated sample items
            if associated_samples := (await get_samples(filename=filename))["data"]:
                skipped_files_associations.append(filename)
                all_sample_item_ids.extend(
                    [sample["sample_item_id"] for sample in associated_samples]
                )
            else:
                # Try to find database record for instrument info
                instrument = None
                async with async_session() as session:
                    stmt = select(SampleFile).where(SampleFile.filename == filename)
                    result = await session.execute(stmt)
                    if sample_file := result.scalar_one_or_none():
                        instrument = sample_file.instrument

                files_to_delete.append(
                    {
                        "sample_file_id": None,
                        "filename": filename,
                        "instrument": instrument,
                    }
                )

    # --- Delete files without sample_item associations ---
    for file_data in files_to_delete:
        try:
            # Call delete_sample_file with appropriate parameter
            if file_data["sample_file_id"]:
                result = await delete_sample_file(
                    sample_file_id=file_data["sample_file_id"]
                )
            else:
                result = await delete_sample_file(filename=file_data["filename"])

            if result["status"] in ["success", "partial"]:
                deleted_files.append(
                    {
                        "sample_file_id": file_data["sample_file_id"],
                        "filename": file_data["filename"],
                    }
                )
                if file_data["instrument"]:
                    instruments_affected.add(file_data["instrument"])
            if result["status"] == "error":
                identifier = file_data["sample_file_id"] or file_data["filename"]
                skipped_files_not_found.append(identifier)

        except Exception:
            identifier = file_data["sample_file_id"] or file_data["filename"]
            runtime.logger.exception(
                f"Unexpected error deleting sample file {identifier}"
            )
            skipped_files_not_found.append(identifier)

    # --- Emit reload events if instruments were affected ---
    if instruments_affected:
        # Get current instruments list after deletions
        final_instruments = [
            instrument["instrument"] for instrument in (await get_instruments())["data"]
        ]

        # Check if any affected instruments are now missing from the database
        # This happens when we deleted the last sample file for an instrument
        if any(
            instrument not in final_instruments for instrument in instruments_affected
        ):
            # Clean up orphaned datasets and emit instrument deletion events
            await delete_acquisition_datasets()

    # --- Prepare response data and message ---
    skipped_files = skipped_files_associations + skipped_files_not_found
    data = {
        "deleted": deleted_files,
        "skipped_files": skipped_files,
        "sample_item_ids": all_sample_item_ids,
    }

    message_parts = []
    if deleted_files:
        count = len(deleted_files)
        plural = "s" if count > 1 else ""
        message_parts.append(f"Deleted {count} sample file{plural}")

    if skipped_files_associations:
        count = len(skipped_files_associations)
        plural = "s" if count > 1 else ""
        item_count = len(all_sample_item_ids)
        item_text = f"{item_count} sample items" if item_count > 1 else "a sample item"
        message_parts.append(
            f"Skipped {count} sample file{plural} because {'they are' if count > 1 else 'it is'} associated with {item_text}"
        )

    if skipped_files_not_found:
        count = len(skipped_files_not_found)
        plural = "s" if count > 1 else ""
        message_parts.append(
            f"Skipped {count} sample file{plural} because {'they were' if count > 1 else 'it was'} not found"
        )

    message = ". ".join(message_parts) + "." if message_parts else "No files deleted."

    # Determine response based on results
    if skipped_files and deleted_files:
        # Partial success - some deleted, some skipped -> 207 Multi-Status
        raise_api_warning(message, data, status_code=207)
    elif skipped_files and not deleted_files:
        # Complete failure - nothing deleted, everything skipped -> 422 Error
        raise ApiException(user_message=message, tech_message=data, status_code=422)
    else:
        # Complete success - all files deleted, nothing skipped -> 200 OK
        return {"message": message, "data": data}


@api_controller()
async def update_sample_file(
    sample_file_id: str,
    sample_file_update_data: SampleFileUpdate,
    user_id: int | None = None,
) -> dict:
    """
    Updates an existing sample file with new data.

    :param sample_file_id: The ID of the sample file to update.
    :type sample_file_id: str
    :param sample_file_update_data: New data for updating the sample file.
    :type sample_file_update_data: SampleFileUpdate
    :raises NotFoundException: If the sample file with the given ID is not found.
    :return: The updated sample file data.
    :rtype: dict
    """
    async with async_session() as session:
        initial_instruments = set(
            [i["instrument"] for i in (await get_instruments())["data"]]
        )

        sample_file = await session.get(SampleFile, sample_file_id)
        if not sample_file:
            raise NotFoundException(f"Sample file with ID '{sample_file_id}' not found")

        # --- Update properties ---
        for key, value in sample_file_update_data.model_dump(
            exclude_unset=True
        ).items():
            setattr(sample_file, key, value)

        await session.commit()
        await session.refresh(sample_file)

    # --- Emit acquisition updated event to instrument room ---
    await emit_record_updated(
        record_type="acquisition",
        record_id=sample_file.sample_file_id,
        record=sample_file.to_dict(),
        room=sample_file.instrument,
    )

    # --- Trigger instruments reload ---
    final_instruments = set(
        [i["instrument"] for i in (await get_instruments())["data"]]
    )
    # Handle instrument changes and handle acquisition datasets creation/deletion
    if final_instruments > initial_instruments:  # Check for added instruments
        await create_acquisition_datasets(user_id=user_id)
    if initial_instruments > final_instruments:  # Check for removed instruments
        await delete_acquisition_datasets()

    return {
        "message": f"Sample file '{sample_file.filename}' updated successfully.",
        "data": sample_file.to_dict(),
    }


# ---------------------
# Sample file upload
# ---------------------


@api_controller()
async def upload_sample_files(
    files: list[UploadFile],
    user: User,
    access_token: str,
    device_id: int | None = None,
    instrument_timezone: str | None = None,
) -> dict:
    """
    Handles upload of multiple sample files to the `filestreams` directory.

    This controller is used by the file upload POST endpoint.

    :param files: List of uploaded files to process.
    :type files: list[UploadFile]
    :param user: The authenticated user performing the upload.
    :type user: User
    :param access_token: Pre-validated user's access token for file converter service.
    :type access_token: str
    :param device_id: The paired device behind the upload, for attribution.
    :type device_id: int | None, optional
    :param instrument_timezone: IANA timezone the uploading machine reported.
    :type instrument_timezone: str | None, optional
    :return: Dictionary with files upload results.
    :rtype: dict
    """
    # Fail before writing anything to the filestore. This endpoint receives the
    # whole request body up front, so this is the earliest point it can refuse.
    await ensure_converter_available()

    successful_uploads: list[dict] = []
    failed_uploads: list[dict] = []

    # Check if filestreams directory exists
    os.makedirs(runtime.config.filestreams, exist_ok=True)

    # Process files sequentially to avoid I/O bottlenecks
    for file in files:
        filename = file.filename

        try:
            file_path = os.path.join(runtime.config.filestreams, filename)

            # The filestreams folder is polled by the file converter's watcher,
            # which queues a file once its size is stable across two ~1s scans.
            # A write that stalls under I/O load therefore gets picked up (and
            # its remaining bytes lost) if it lands under its final name, so
            # write to a temp name the watcher patterns cannot match and
            # publish with an atomic rename.
            #
            # The staging name carries a unique suffix. It used to be a plain
            # "<final>.part", which two uploads of the same filename share - and
            # a client that restarts an interrupted transfer produces exactly
            # that. The second mover then overwrote the first's staged bytes,
            # the first's rename consumed them, and the second's rename failed
            # on a path that was no longer there.
            tmp_path = f"{file_path}.{uuid4().hex}.part"
            try:
                # Write file in chunks to manage memory usage
                with open(tmp_path, "wb") as f:
                    while chunk := file.file.read(FILE_UPLOAD_CHUNK_SIZE):
                        f.write(chunk)

                # Register the file with the converter BEFORE publishing it
                # under the watched name - see upload_sample_file for why the
                # reverse order strands files in failed_files.
                await _register_file_with_converter(
                    filename=filename,
                    user=user,
                    access_token=access_token,
                    device_id=device_id,
                    instrument_timezone=instrument_timezone,
                )

                os.replace(tmp_path, file_path)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            successful_uploads.append(
                {
                    "filename": filename,
                    "message": f"Successfully uploaded {filename}",
                }
            )

            runtime.logger.debug(f"Successfully uploaded file: {filename}")

        except ApiException as e:
            failed_uploads.append(
                {
                    "filename": filename,
                    "error": e.user_message,
                    "message": f"Failed to upload {filename}: {e.user_message}",
                }
            )
            # INFO: the ApiException was already logged by the exception
            # pipeline when it was processed; this only adds per-file context
            runtime.logger.info(f"Failed to upload file {filename}: {e.user_message}")
        except Exception as e:
            error_msg = str(e)
            failed_uploads.append(
                {
                    "filename": filename,
                    "error": error_msg,
                    "message": f"Failed to upload {filename}: {error_msg}",
                }
            )
            runtime.logger.exception(f"Failed to upload file {filename}")

        finally:
            # Check if file handle is properly closed
            if hasattr(file, "file") and file.file:
                file.file.close()

    # Calculate results
    total_files = len(files)
    successful_count = len(successful_uploads)
    failed_count = len(failed_uploads)

    # Determine status and message
    if failed_count == 0:
        status = "success"
        message = f"Successfully uploaded all {successful_count} files"
    elif successful_count == 0:
        status = "error"
        message = f"Failed to upload all {failed_count} files"
    else:
        status = "partial"
        message = f"Uploaded {successful_count} of {total_files} files successfully"

    return {
        "message": message,
        "status": status,
        "results": successful_count,
        "data": {
            "total_files": total_files,
            "successful_uploads": successful_uploads,
            "failed_uploads": failed_uploads,
            "summary": {
                "successful": successful_count,
                "failed": failed_count,
                "total": total_files,
            },
        },
    }


@api_controller()
async def upload_sample_file(
    file_path: str,
    user: User,
    access_token: str,
    device_id: int | None = None,
    instrument_timezone: str | None = None,
) -> dict:
    """
    Handles upload of a single sample file from a given file path to the `filestreams` directory.

    This controller is used by the TUS file upload endpoint.
    The file is moved to the specified directory as defined in the runtime configuration.

    :param file_path: Path to the file to upload.
    :type file_path: str
    :param user: The authenticated user performing the upload.
    :type user: User
    :param access_token: Pre-validated user's access token for file converter service.
    :type access_token: str
    :param device_id: The paired device behind the upload, for attribution.
    :type device_id: int | None, optional
    :param instrument_timezone: IANA timezone the uploading machine reported.
    :type instrument_timezone: str | None, optional
    :return: Dictionary with file upload result.
    :rtype: dict
    """
    # No converter-availability gate here. This is the tus completion handler,
    # run only after the whole file has transferred; tuspyserver has already
    # marked the upload complete, so raising now cannot un-accept it - the
    # client reads the upload as done and the staged bytes are orphaned. The
    # gate runs up front in the tus pre_create hook instead (see
    # sample_files_routes). If a converter that was present at creation dropped
    # mid-transfer, the file still lands in the filestore, where the converter's
    # watcher recovers it on reconnect, rather than being lost.

    # Check if filestreams directory exists
    os.makedirs(runtime.config.filestreams, exist_ok=True)

    filename = os.path.basename(file_path)
    dest_path = os.path.join(runtime.config.filestreams, filename)

    # A cross-filesystem move degrades to a non-atomic copy, which the
    # converter's polling watcher can pick up half-written under its final
    # name. Stage under a temp name the watcher patterns cannot match, then
    # publish with an atomic rename.
    #
    # The staging name carries a unique suffix; see upload_sample_files for the
    # collision a shared "<final>.part" causes when a client restarts an
    # interrupted transfer.
    tmp_path = f"{dest_path}.{uuid4().hex}.part"
    try:
        try:
            shutil.move(file_path, tmp_path)

            # Register the file with the converter BEFORE publishing it under
            # the watched name.
            #
            # The converter learns who uploaded a file only from this event, and
            # it keeps the context in memory keyed by filename. Its watcher
            # queues a file once the size is stable across two ~1s polls, so an
            # event emitted after the rename is racing that pickup. The emit is
            # not local - it crosses Redis pub/sub to whichever worker holds the
            # converter's socket - so under load it can land after the converter
            # has already dequeued the file. _get_file_context then finds
            # nothing, raises, and the file is quarantined into failed_files,
            # where nothing retries it: the watcher globs filestreams/*.raw
            # without recursing.
            #
            # Announcing first costs nothing, because the staged name is
            # invisible to the watcher's *.raw / *.h5 patterns. If the publish
            # below fails the converter is left holding a context for a file
            # that never appears, which is harmless - the entry is overwritten
            # by the next upload of that name and cleared after processing.
            await _register_file_with_converter(
                filename=filename,
                user=user,
                access_token=access_token,
                device_id=device_id,
                instrument_timezone=instrument_timezone,
            )

            os.replace(tmp_path, dest_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        runtime.logger.debug(f"Successfully uploaded file: {filename}")

        return {
            "message": f"Successfully uploaded {filename}",
            "status": "success",
            "data": {
                "filename": filename,
                "file_path": dest_path,
            },
        }

    except ApiException as e:
        # INFO: already logged by the exception pipeline when processed
        runtime.logger.info(f"Failed to upload file {filename}: {e.user_message}")
        return {
            "message": f"Failed to upload {filename}: {e.user_message}",
            "status": "error",
            "error": e.user_message,
        }
    except Exception as e:
        error_msg = str(e)
        runtime.logger.exception(f"Failed to upload file {filename}")
        return {
            "message": f"Failed to upload {filename}: {error_msg}",
            "status": "error",
            "error": error_msg,
        }


# ---------------------
# Sample file peaks controllers
# ---------------------


def finite_or_none(values: list) -> list:
    """
    Replace non-finite floats (NaN, +/-inf) with ``None`` for JSON transport.

    Peak arrays can legitimately carry NaN (e.g. quantities a given instrument
    type does not produce), but strict JSON has no representation for them and
    ``json.dumps`` raises - the whole peaks response used to 400 on a single
    NaN. ``None`` serializes as ``null``, which chart consumers treat as a gap.

    :param values: Peak values as plain Python floats.
    :return: The same list with every non-finite entry replaced by ``None``.
    """
    return [v if v is None or math.isfinite(v) else None for v in values]


def _sync_load_sample_file_peaks(
    filename: str, areas: bool, heights: bool, average: bool
) -> dict:
    """Load a sample file's peaks and materialize them into plain lists.

    Kept as one synchronous unit so the caller can hand the whole thing to a
    worker thread. Splitting it would not help: ``load_peak_data`` returns a
    dask-backed dataset, so the store reads happen at the ``.tolist()`` calls
    here rather than at the load.

    :param filename: Sample file filename
    :param areas: Include peak areas
    :param heights: Include peak heights
    :param average: Average over time instead of summing
    :raises FileNotFoundError: No such sample file, or it is unprocessed
    :return: Response payload with mz, and area/height/sparsity as requested
    """
    sample_file_data = load_peak_data(filename)
    response_data = {}

    if areas:
        peak_areas = get_peaks(sample_file_data, "area")
        peak_areas = (
            peak_areas.mean(dim="time") if average else peak_areas.sum(dim="time")
        )
        response_data["mz"] = peak_areas.mz.values.tolist()
        response_data["area"] = peak_areas.values.tolist()

    if heights:
        peak_heights = get_peaks(sample_file_data, "height")
        peak_heights = (
            peak_heights.mean(dim="time") if average else peak_heights.sum(dim="time")
        )
        # If 'mz' was not populated from areas, populate it from heights
        if "mz" not in response_data:
            response_data["mz"] = peak_heights.mz.values.tolist()
        response_data["height"] = peak_heights.values.tolist()

    # Include sparsity aligned to the same mz coordinate as the response
    if "mz" not in response_data:
        response_data["mz"] = sample_file_data.mz.values.tolist()
        response_data["sparsity"] = sample_file_data.sparsity.values.tolist()
    else:
        filtered_mz = peak_areas.mz if areas else peak_heights.mz
        response_data["sparsity"] = sample_file_data.sparsity.sel(
            mz=filtered_mz
        ).values.tolist()

    return response_data


def _sync_load_peak_timeseries(filename: str, peak_mz: float):
    """Load one peak's timeseries and materialize it.

    Same reasoning as :func:`_sync_load_sample_file_peaks`: the ``.compute()``
    has to sit inside the offloaded unit, not after it.

    :param filename: Sample file filename
    :param peak_mz: m/z of the peak to select, nearest match
    :raises FileNotFoundError: No such sample file, or it is unprocessed
    :return: Computed peak timeseries for the nearest m/z
    """
    sample_file = load_peak_data(filename)
    peaks = get_peaks(sample_file, "height")
    return peaks.sel(mz=peak_mz, method="nearest").compute()


def _sync_get_sum_spectrum(
    filename: str,
    t_min: float | None,
    t_max: float | None,
    mz_min: float | None,
    mz_max: float | None,
) -> tuple[list, list]:
    """Compute the summed spectrum and materialize it into plain lists.

    This is the path that takes the cross-process zarr write lock on a cache
    miss (``mascope_signal.compute._write_cached_sum_signal``), so it is the
    one that most needs to be off the event loop.

    :param filename: Sample file filename
    :param t_min: Start of the time window
    :param t_max: End of the time window
    :param mz_min: Start of the m/z window, or None for all
    :param mz_max: End of the m/z window, or None for all
    :return: (mz values, intensity values) as plain lists
    """
    spectrum = m_compute.get_sum_signal(
        filename, t_min, t_max, average=True, reconstruct=True
    )
    if mz_min is not None and mz_max is not None:
        spectrum = spectrum.sel(mz=slice(mz_min, mz_max)).compute()
    return spectrum.mz.values.tolist(), spectrum.values.tolist()


@api_controller()
async def get_sample_file_peaks(
    sample_file_id: str, areas: bool, heights: bool, average: bool = True
) -> dict:
    """
    Retrieves peaks from a specified sample file, with options to include areas and/or heights.
    The data is averaged across the time dimension by default.

    Steps:
    1. Fetch the sample file details using the provided ID.
    2. Validate whether peak areas and/or heights are requested.
    3. Load the sample file data based on the selected options (areas, heights).
    4. Extract and format the peak data for each requested type.
    5. Return the data in a columnar format with a message.

    :param sample_file_id: The ID of the sample file.
    :type sample_file_id: str
    :param areas: If True, include peak areas in the response.
    :type areas: bool
    :param heights: If True, include peak heights in the response.
    :type heights: bool
    :param average: If True, return averaged peak data, defaults to True.
    :type average: bool, optional
    :raises NotFoundException: If the sample file is not found or hasn't been processed (no peak data available).
    :return: A dictionary with the peak data dict in columnar format:
        - "mz": list of mass/charge (m/z) values for each peak.
        - "area": list of peak areas (if requested).
        - "height": list of peak heights (if requested).
    :rtype: dict
    """

    # Step 1: Fetch the sample file details
    sample_file_data = await get_sample_file(sample_file_id)
    filename = sample_file_data.get("data").get("filename")

    # Step 2-4: Load and format the peak data in one worker thread. The whole
    # block has to be offloaded together, not just load_peak_data: the dataset
    # is dask-backed, so the chunk reads happen at the .tolist() calls below.
    try:
        response_data = await asyncio.to_thread(
            _sync_load_sample_file_peaks, filename, areas, heights, average
        )
    except FileNotFoundError as e:
        raise NotFoundException(
            f"Sample file with name '{filename}' was not found or has not been processed"
        ) from e

    # Step 5: Format the response for the case where no peaks were detected
    if not response_data["mz"]:
        message = (
            f"No peaks found in sample file '{filename}'. "
            f"The file was processed, but no peaks were detected in the target m/z range. "
            f"Consider adjusting the targets or computing all peaks."
        )
        return {
            "message": message,
            "results": 0,
            "data": {
                "mz": [],
                "area": [] if areas else None,
                "height": [] if heights else None,
                "sparsity": [],
            },
        }

    # Step 6: Remove empty fields from the response (if only one type is requested)
    if not areas:
        response_data.pop("area", None)
    if not heights:
        response_data.pop("height", None)

    # Step 7: Make the payload JSON-safe: peak arrays can carry NaN (e.g.
    # quantities the instrument type does not produce) and strict JSON
    # serialization rejects non-finite floats.
    response_data = {
        key: finite_or_none(values) for key, values in response_data.items()
    }

    message = f"Successfully loaded {len(response_data['mz'])} peaks from sample file '{filename}'"
    return {
        "message": message,
        "results": len(response_data["mz"]),
        "data": response_data,
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def compute_sample_file_peaks(
    sample_file_id: str,
    user: User,
    access_token: str,
    independent_transaction: bool = False,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Computes all peak data for a specific sample file, performing the operation as a background task.

    Steps:
    - Fetch the sample file details using the provided ID.
    - Check if File Converter service is connected and get access token for the user.
    - Emit a Socket.IO event to the File Converter service to start peak detection for the sample file.
    - Send an immediate "pending" notification to the user to indicate that peak detection has been queued.

    :param sample_file_id: ID of the sample file for which peaks are to be computed.
    :type sample_file_id: str
    :param user: The authenticated user performing the operation, used for notifications.
    :type user: User
    :param access_token: Pre-validated user's access token for file converter service.
    :type access_token: str
    :param independent_transaction: Flag to indicate if the operation should be treated as an independent transaction.
    :type independent_transaction: bool, optional
    :param process_id: Optional identifier for the processing task, used for tracking.
    :type process_id: Optional[str]
    :param parent_id: Optional identifier of the parent task, if this task is part of a larger workflow.
    :type parent_id: Optional[str]
    :return: A dictionary containing a message with the outcome and the data about the peaks detected.
    :rtype: dict
    """
    # --- Fetch the sample file details using the provided ID ---
    sample_file_data = await get_sample_file(sample_file_id)
    filename = sample_file_data.get("data").get("filename")

    # --- Check if File Converter service is connected ---
    await ensure_converter_available()

    await request_peak_detection(
        sample_file_id=sample_file_id,
        filename=filename,
        user=user,
        access_token=access_token,
        process_id=process_id,
    )

    return {
        "message": f"Peak detection requested for sample file '{filename}'",
        "_notification_data": {
            "sample_file_id": sample_file_id,
            "filename": filename,
        },
    }


@api_controller()
async def get_sample_file_peak_timeseries(
    sample_file_id: str, peak_mz: float, peak_mz_tolerance_ppm: float
) -> dict:
    """Get timeseries of a given peak in a given sample file.

    Returns the timeseries of a closest peak to a given m/z, if found
    within given m/z tolerance.

    Steps:
    1. Fetch the sample file details using the provided ID.
    2. Load the sample file data from its filename.
    3. Select the nearest peak to the requested m/z within the specified tolerance.
    4. Extract and return the timeseries data of the selected peak.

    :param sample_file_id: Sample file ID
    :type sample_file_id: str
    :param peak_mz: m/z of the peak to get timeseries for
    :type peak_mz: float
    :param peak_mz_tolerance_ppm: Tolerance for m/z difference
        for the requested peak and the nearest one found from data
    :type peak_mz_tolerance_ppm: float
    :raises HTTPException: Raised if sample file is not found
    :return: Dictionary with keys:
        "mz": m/z of the peak in sample file (None if no peak within tolerance)
        "height": peak height at time points (empty if no peak within tolerance)
        "time": time coordinates (empty if no peak within tolerance)
    :rtype: dict

    TODO: Remove this function after all notebooks are updated to use get_sample_peak_timeseries
    This is kept for backwards compatibility with existing notebooks
    The new sample-based `get_sample_peak_timeseries` endpoint provides filtering with sample polarity and time limits
    """
    # Step 1: Fetch the sample file details using the provided ID.
    sample_file_data = await get_sample_file(sample_file_id)
    filename = sample_file_data.get("data").get("filename")
    # Step 2-3: Load the sample file and select the nearest peak, in one
    # worker thread - the dataset is lazy, so the read lands on .compute().
    try:
        peak_timeseries = await asyncio.to_thread(
            _sync_load_peak_timeseries, filename, peak_mz
        )
    except FileNotFoundError:
        raise NotFoundException(f"Sample file with name '{filename}' not found")
    peak_mz_data = peak_timeseries.mz.item()
    # Calculate difference of the sample peak m/z to requested peak m/z
    mz_diff = peak_mz_data - peak_mz  # [Th]
    mz_diff_ppm = mz_diff / peak_mz * 1e6  # [ppm]

    # Step 4: Extract and return timeseries data
    if abs(mz_diff_ppm) > peak_mz_tolerance_ppm:
        # No peak found within given m/z tolerance
        return {
            "results": 0,
            "data": {
                "mz": None,
                "height": [],
                "time": [],
            },
        }

    return {
        "message": f"Successfully retrieved timeseries for peak m/z {peak_mz} in '{filename}'.",
        "results": len(peak_timeseries.time.values),
        "data": {
            "mz": peak_mz_data,
            "height": peak_timeseries.values.tolist(),
            "time": peak_timeseries.time.values.tolist(),
        },
    }


# ---------------------
# Sample file spectrum controllers
# ---------------------


@api_controller()
async def get_sample_file_spectrum(
    sample_file_id: str,
    t_min: float = None,
    t_max: float = None,
    mz_min: float = None,
    mz_max: float = None,
) -> dict:
    """
    Retrieves the averaged spectrum from a specified sample file within optional time and m/z ranges.

    Steps:
    1. Fetch the sample file details using the provided ID.
    2. Compute averaged spectrum in the time range.
    3. Filter by m/z range if provided.
    4. Extract m/z values and their corresponding intensities from the spectrum.
    5. Return the spectrum data, including the total number of m/z points and optional metadata.

    :param sample_file_id: Unique identifier for the sample file from which to retrieve the spectrum.
    :type sample_file_id: str
    :param t_min: Start of the optional time range, defaults to None.
    :type t_min: float, optional
    :param t_max: End of the optional time range, defaults to None.
    :type t_max: float, optional
    :param mz_min: Start of the optional m/z range, defaults to None.
    :type mz_min: float, optional
    :param mz_max: End of the optional m/z range, defaults to None.
    :type mz_max: float, optional
    :return: A dictionary containing the total number of m/z points, optional spectrum metadata,
    and arrays of m/z values and their corresponding intensities.
    :rtype: dict
    """
    # Step 1: Fetch sample file info from the database
    sample_file_data = await get_sample_file(sample_file_id)
    filename = sample_file_data.get("data").get("filename")
    intensity_unit = "counts/s"

    # Step 2-4: Compute the averaged spectrum (reconstructed for display so it
    # overlays the centroids), filter it and materialize it, all in one worker
    # thread. This is the path that takes the cross-process zarr write lock on
    # a cache miss.
    mz_values, intensity_values = await asyncio.to_thread(
        _sync_get_sum_spectrum, filename, t_min, t_max, mz_min, mz_max
    )

    # Step 5: Return the total count, optional spectrum count, and data
    message = f"Retrieved spectrum data with {len(mz_values)} m/z points from sample file '{filename}'."
    return {
        "message": message,
        "results": len(mz_values),
        "data": {
            "mz": mz_values,
            "intensity": intensity_values,
            "intensity_unit": intensity_unit,
        },
    }


# ---------------------
# Sample file metadata controller
# ---------------------


@api_controller()
async def get_sample_file_metadata(sample_file_id: str) -> dict:
    """
    Retrieves metadata for a specific sample file.

    Steps:
    1. Fetch the sample file details using the provided ID.
    2. Extract metadata from the file.
    3. Return the metadata as a dictionary.

    :param sample_file_id: Unique identifier for the sample file.
    :type sample_file_id: str
    :raises NotFoundException: If the sample file or its metadata is not found.
    :return: Dictionary containing the sample file metadata.
    :rtype: dict
    """
    # Step 1: Fetch sample file info from the database
    sample_file_data = await get_sample_file(sample_file_id)
    filename = sample_file_data.get("data").get("filename")

    # Step 2: Get metadata
    try:
        metadata = m_compute.get_metadata(filename)
    except Exception as e:
        # str(e) can carry internal file paths; the cause is logged server-side
        # (with traceback) by the @api_controller error handling.
        raise HTTPException(
            status_code=500, detail="Could not read metadata from the sample file."
        ) from e

    # Step 3: Convert metadata to dict
    metadata_dict = await asyncio.to_thread(metadata.to_dict)

    return {
        "message": f"Metadata for sample file '{filename}' retrieved successfully.",
        "data": metadata_dict,
    }
