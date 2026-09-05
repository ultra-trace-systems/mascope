import asyncio
from datetime import datetime, timezone
from typing import cast

import numpy as np
import pandas as pd
from sqlalchemy import (
    asc,
    delete,
    desc,
    func,
    insert,
    select,
)

import mascope_file.io as m_io
import mascope_signal.compute as m_compute
from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.sample.lib.fetch_affected_sample_data import (
    fetch_affected_sample_data,
)
from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.controllers.sample.lib.sample_items_copy import (
    CopyMatches,
    copy_sample_items_match_data,
)
from mascope_backend.api.controllers.sample.lib.sample_modified_timestamps_manager import (
    update_sample_batches_modified_timestamp,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.controllers.samples.samples_controller import get_sample
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    NotFoundException,
)
from mascope_backend.api.lib.utils import generate_copy_name
from mascope_backend.api.models.sample.items.config import sample_item_config
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SampleItemBase,
    SampleItemCreate,
    SampleItemRead,
    SampleItemUpdate,
)
from mascope_backend.api.new.temp.storage import user_temp_path
from mascope_backend.db import (
    Sample,
    SampleFile,
    SampleItem,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_backend.socket.records.service import (
    emit_record_created,
    emit_record_reload,
    emit_record_updated,
)
from mascope_file.name import get_instrument_type
from mascope_thermo.thermo import NoScansFoundError


@api_controller()
async def get_sample_items(
    sample_batch_id: str | None = None,
    sample_file_id: str | None = None,
    sample_item_type: list[str] | None = None,
    polarity: list[str] | None = None,
    sort: str = "sample_item_utc_created",
    order: str = "asc",
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a paginated list of sample items, optionally sorted by a specified column in either ascending or descending order.

    Steps:
    1. Construct a SQLAlchemy query to select all sample items.
    2. Apply filtering if specified by the parameters.
    3. Apply sorting if specified by the sort and order parameters.
    4. Apply pagination based on the provided page and limit parameters.
    5. Convert the results into a list of dictionaries for JSON serialization.

    :param sample_batch_id: The sample batch ID for which you want to fetch the sample items, defaults to None
    :type sample_batch_id: str | None, optional
    :param sample_file_id: The sample file ID for which you want to fetch the sample items, defaults to None
    :type sample_file_id: str | None, optional
    :param sample_item_type: Filter by sample item types, can specify multiple types, defaults to None
    :type sample_item_type: list[str] | None
    :param polarity: Filter by ion polarity mode of the sample item, '+' for positive or '-' for negative
    :type polarity: list[str] | None
    :param sort:  Column to sort by, defaults to "sample_item_utc_created"
    :type sort: str, optional
    :param order: Sorting order ('asc' for ascending, 'desc' for descending), defaults to "asc"
    :type order: str, optional
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None, optional
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None, optional
    :return: A dictionary with the total count and a list of sample items.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        stmt = select(SampleItem)

        # Step 1: Apply filters if specified
        if sample_batch_id:
            stmt = stmt.filter(SampleItem.sample_batch_id == sample_batch_id)

        if sample_file_id:
            stmt = stmt.filter(SampleItem.sample_file_id == sample_file_id)

        if sample_item_type:
            stmt = stmt.filter(SampleItem.sample_item_type.in_(sample_item_type))

        if polarity:
            stmt = stmt.filter(SampleItem.polarity.in_(polarity))

        # Step 2: Apply sorting if specified
        if sort:
            if order == "desc":
                stmt = stmt.order_by(desc(getattr(SampleItem, sort)))
            else:
                stmt = stmt.order_by(asc(getattr(SampleItem, sort)))

        # Step 3: Get total count for pagination
        count_stmt = select(func.count()).select_from(stmt)
        total = await session.scalar(count_stmt)

        # Step 4: Apply pagination
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        result = await session.execute(stmt)
        sample_items = result.scalars().all()

        # Step 5: Return the total count and the list of sample items
        return {
            "message": "Sample items retrieved successfully.",
            "results": total,
            "data": [
                SampleItemRead.model_validate(sample_item).model_dump()
                for sample_item in sample_items
            ],
        }


@api_controller()
async def get_sample_item(sample_item_id: str) -> dict:
    """
    Retrieves a single sample item by its unique ID.

    Steps:
    1. Execute a query to fetch the sample item with the specified ID.
    2. Check if the sample item exists. If not, raise a NotFoundException.
    3. Return the sample item's details as a dictionary.

    :param sample_item_id: Unique identifier of the sample item to retrieve.
    :type sample_item_id: str
    :raises NotFoundException: If the sample item with the given ID is not found.
    :return: The requested sample item's details.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch sample item by ID
        sample_item = await session.get(SampleItem, sample_item_id)

        # Step 2: If sample item not found, raise exception
        if not sample_item:
            raise NotFoundException(f"Sample item with ID '{sample_item_id}' not found")
    # Step 3: Return sample item details
    return {
        "message": f"Sample item '{sample_item.sample_item_name}' retrieved successfully.",
        "data": SampleItemRead.model_validate(sample_item).model_dump(),
    }


@api_controller()
async def create_sample_items(
    sample_items: list[SampleItemCreate], independent_transaction: bool = False
) -> dict:
    """
    Creates multiple sample items in bulk after verifying associated sample files exist.

    Steps:
    - Validate all sample files exist.
    - Process each sample item and conditionally compute missing TIC, t0, t1 fields.
    - Bulk create new sample items.
    - Fetch created samples (with filename) for response and affected sample batches.
    - Update modified timestamps for affected batches.
    - Emit creation events

    :param sample_items: List of sample item details for bulk creation
    :type sample_items: list[SampleItemCreate]
    :param independent_transaction: Flag for independent transaction, defaults to False.
    :type independent_transaction: bool, optional
    :return: Details of created sample items with full sample data (including filename)
    :rtype: dict
    :raises NotFoundException: If any associated sample file does not exist
    """
    if not sample_items:
        return {
            "message": "No sample items to create.",
            "results": 0,
            "data": [],
        }
    async with async_session() as session:
        # --- Validate all sample files exist and fetch metadata ---
        sample_file_ids = {si.sample_file_id for si in sample_items}

        sample_files = (
            (
                await session.execute(
                    select(SampleFile).where(
                        SampleFile.sample_file_id.in_(sample_file_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

        # Create lookup map: sample_file_id → SampleFile
        sample_files_map = {sf.sample_file_id: sf for sf in sample_files}

        # Check for missing files
        found_ids = set(sample_files_map.keys())
        if missing_ids := list(sample_file_ids - found_ids):
            raise NotFoundException(f"Sample files not found: {missing_ids}")

        # --- Process each sample item and conditionally compute missing TIC, t0, t1 fields ---
        sample_items_data = []

        for sample_item in sample_items:
            sample_file = sample_files_map[sample_item.sample_file_id]

            # Determine if TIC computation is needed
            tic_computation_needed = (
                sample_item.tic is None
                or sample_item.t0 is None
                or sample_item.t1 is None
            )

            # Compute TIC data if needed
            computed_tic = computed_t0 = computed_t1 = None
            if tic_computation_needed:
                try:
                    # kwargs, not a lambda: sample_file is an attached ORM
                    # instance inside a live session, so its attributes must
                    # be read here on the loop, not in the worker thread.
                    _, tic_values = await asyncio.to_thread(
                        m_compute.get_tic_per_scan,
                        base_filename=sample_file.filename,
                        polarity=sample_item.polarity,  # sample_item polarity (+ or -)
                    )
                    computed_tic = float(np.sum(tic_values))
                    # The window spans every scan type, not just the MS1 scans
                    # the TIC is taken over: an acquisition that records its
                    # MS2 scans as a block after the MS1 ones would otherwise
                    # get a window that ends before the first of them, and its
                    # MS2 data would be invisible to every endpoint that
                    # selects within [t0, t1]. Worth the second read of the
                    # file, which happens once per sample item created.
                    computed_t0, computed_t1 = await asyncio.to_thread(
                        m_compute.get_acquisition_window,
                        base_filename=sample_file.filename,
                        polarity=sample_item.polarity,
                    )
                except (TypeError, NoScansFoundError) as e:
                    # NoScansFoundError is what the raw readers raise for a
                    # polarity the file does not carry; without it here that
                    # request came back a 500 rather than the message below,
                    # and only the TOF path's TypeError was ever caught.
                    verbose_polarity = (
                        "positive" if sample_item.polarity == "+" else "negative"
                    )
                    raise NotFoundException(
                        f"No scans with '{verbose_polarity}' polarity were found "
                        f"in the file '{sample_file.filename}'."
                    ) from e

            sample_item_dict = {
                "sample_item_id": gen_id(),
                **sample_item.model_dump(),
                "tic": sample_item.tic if sample_item.tic is not None else computed_tic,
                "t0": sample_item.t0 if sample_item.t0 is not None else computed_t0,
                "t1": sample_item.t1 if sample_item.t1 is not None else computed_t1,
                "locked": (
                    1
                    if sample_item.sample_item_type == "ACQUISITION"
                    and sample_item_config.ACQUISITION_AUTO_LOCK
                    else 0
                ),
                "sample_item_utc_created": datetime.now(timezone.utc),
            }

            sample_items_data.append(sample_item_dict)

        # --- Bulk insert to avoid event listeners ---
        await session.execute(insert(SampleItem).values(sample_items_data))
        await session.commit()

    # --- Fetch created samples and affected sample batches ---
    created_item_ids = [si["sample_item_id"] for si in sample_items_data]

    affected = await fetch_affected_sample_data(
        sample_item_ids=created_item_ids,
        include_objects=True,
    )
    affected_sample_batch_ids = affected.affected_sample_batch_ids
    affected_samples = cast(list[Sample], affected.affected_samples)

    # Preserve insertion order
    samples_by_id = {s.sample_item_id: s for s in affected_samples}
    created_samples = [samples_by_id[item_id] for item_id in created_item_ids]

    # --- Update modified timestamps for affected batches ---
    await update_sample_batches_modified_timestamp(
        sample_batch_ids=affected_sample_batch_ids
    )

    # --- Convert to response format (includes filename from Sample view) ---
    created_samples_data = [
        {
            column.name: getattr(sample, column.name)
            for column in Sample.__table__.columns
        }
        for sample in created_samples
    ]

    # --- Emit creation events ---
    if independent_transaction:
        for sample in created_samples_data:
            await emit_record_created(
                record_type="sample",
                record_id=sample["sample_item_id"],
                record=sample,
                room=sample["sample_batch_id"],
            )

    message = f"Successfully created {len(created_samples)} sample items."
    runtime.logger.debug(message)

    return {
        "message": message,
        "results": len(created_samples),
        "data": created_samples_data,
    }


@api_controller()
async def update_sample_item(
    sample_item_id: str,
    sample_item: SampleItemUpdate,
    independent_transaction: bool = False,
) -> dict:
    """
    Updates an existing sample item with new data provided in the sample item update request body.

    Steps:
    - Fetch the existing sample item by its ID from the database.
    - If the sample item is found, update its properties with the new data provided.
    - Set the sample item's modification timestamp to the current UTC time.
    - Commit the updated sample item to the database.
    - Reload of sample batch happens in the end of update operation if only basic fields were updated.

    :param sample_item_id: The unique identifier of the sample item to update.
    :type sample_item_id: str
    :param sample_item: The new data for the sample item update.
    :type sample_item: SampleItemUpdate
    :param independent_transaction: Flag to indicate if the operation should be treated as an independent transaction.
    :type independent_transaction: bool
    :raises NotFoundException: If no sample item is found with the provided ID.
    :return: The updated sample item data as a dictionary.
    :rtype: dict[str, Any]
    """
    # --- Fetch the existing sample item ---
    async with async_session() as session:
        existing_sample_item = await session.get(SampleItem, sample_item_id)
        if not existing_sample_item:
            raise NotFoundException(f"Sample item with ID '{sample_item_id}' not found")

        # --- Update the sample item properties if anything changed ---
        update_data = sample_item.model_dump(exclude_unset=True)
        changed_fields = {
            key: value
            for key, value in update_data.items()
            if getattr(existing_sample_item, key) != value
        }
        if changed_fields:
            # Update only the changed fields
            for key, value in changed_fields.items():
                setattr(existing_sample_item, key, value)

            # --- Update modification timestamp ---
            existing_sample_item.sample_item_utc_modified = datetime.now(timezone.utc)

            # --- Commit the updates ---
            await session.commit()
            await session.refresh(existing_sample_item)

    # --- Emit update event if fields changed ---
    sample_item_data = SampleItemRead.model_validate(existing_sample_item).model_dump()
    if changed_fields and independent_transaction:
        sample = (await get_sample(sample_item_id)).get("data")
        await emit_record_updated(
            record_type="sample",
            record_id=existing_sample_item.sample_item_id,
            record=sample,
            room=existing_sample_item.sample_batch_id,
        )

    return {
        "message": f"Sample '{existing_sample_item.sample_item_name}' was updated.",
        "data": sample_item_data,
    }


@api_controller()
async def delete_sample_items(
    sample_item_ids: list[str], independent_transaction: bool = False
):
    """
    Deletes a sample item by its unique identifier.

    Steps:
    1. Check no duplicate sample item ids were provided
    2. Check sample items to be deleted exist
    3. Retrieve affected batch ids
    4. Delete samples

    :param sample_item_id: The unique identifier of the sample item to delete.
    :type sample_item_id: str
    :raises NotFoundException: If no sample item is found with the provided ID.
    """
    # Step 1: Check no duplicate sample item ids were provided
    if len(set(sample_item_ids)) < len(sample_item_ids):
        raise ValueError("delete sample items: sample item IDs must be unique")
    async with async_session() as session:
        # Step 2: Check sample items to delete exist
        result = await session.execute(
            select(SampleItem).where(SampleItem.sample_item_id.in_(sample_item_ids))
        )
        sample_items = result.scalars().all()
        if missing_ids := list(
            set(sample_item_ids) - {s.sample_item_id for s in sample_items}
        ):
            s = "s" if len(missing_ids) > 1 else ""
            raise NotFoundException(
                f"Failed to find {len(missing_ids)} sample item{s}: {missing_ids}"
            )
        # Step 3: Retrieve affected batch ids
        _, affected_sample_batch_ids, *_ = await fetch_affected_sample_data(
            sample_item_ids=sample_item_ids
        )
        # Step 4: Delete the sample items
        delete_query = delete(SampleItem).where(
            SampleItem.sample_item_id.in_(sample_item_ids)
        )
        await session.execute(delete_query)
        await session.commit()

    # Step 5: Update modified timestamps for affected batches
    await update_sample_batches_modified_timestamp(
        sample_batch_ids=affected_sample_batch_ids
    )

    # Emit reload events to each affected sample batch
    if independent_transaction:
        for sample_batch_id in affected_sample_batch_ids:
            await emit_record_reload(
                record_type="sample",
                room=sample_batch_id,
            )

    s = "s" if len(sample_item_ids) > 1 else ""
    message = f"Deleted {len(sample_item_ids)} sample item{s}."
    runtime.logger.debug(message)
    return {
        "message": message,
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def copy_sample_items(
    sample_item_ids: list[str],
    sample_batch_id: str,
    always_copy_matches: bool = False,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Copies specified sample items to a target batch.
    - Copies match data if copying within same batch or always_copy_matches=True
    - Sets target batch to "rematch" status if copied samples need new match computation
    - May be a part of the copy sample batch operation or independent.

    :param sample_item_ids: ID of the original sample items to be copied.
    :type sample_item_ids: list[str]
    :param sample_batch_id: ID of the sample batch where the new items will be placed.
    :type sample_batch_id: str
    :param always_copy_matches: Whether to copy matches even when copying between different batches (used in batch copy controller)
    :type always_copy_matches: bool
    :param independent_transaction: Flag indicating whether the sample item copy is an independent transaction and if the operation should emit a reload event for the sample batch and if the sample should be rematched for new batch targets, defaults to False
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None
    :param parent_id: Parent process identifier
    :type parent_id: str | None
    :raises NotFoundException: When batch or samples not found
    :raises ValueError: When sample_item_ids are not unique
    :return: Copy results with created sample data
    :rtype: dict
    """
    # Validate unique IDs
    if len(set(sample_item_ids)) < len(sample_item_ids):
        raise ValueError("sample_item_ids to be copied must be unique")

    # Fetch and validate source samples
    async with async_session() as session:
        result = await session.execute(
            select(Sample).where(Sample.sample_item_id.in_(sample_item_ids))
        )
        source_samples = result.scalars().all()

        missing_ids = set(sample_item_ids) - {s.sample_item_id for s in source_samples}
        if missing_ids:
            raise NotFoundException(
                f"Sample items not found: {', '.join(list(missing_ids))}"
            )

    # Validate target batch
    target_batch = await fetch_sample_batch(sample_batch_id)

    # Prepare sample items for creation
    sample_items_to_create = []
    for source_sample in source_samples:
        sample_item_create = SampleItemCreate(
            **SampleItemBase.model_validate(source_sample).model_dump(
                exclude={
                    "sample_batch_id",
                    "sample_item_name",
                    "sample_item_type",
                }
            ),
            sample_batch_id=sample_batch_id,
            sample_item_name=(
                generate_copy_name(source_sample.sample_item_name)
                if source_sample.sample_batch_id == sample_batch_id
                else source_sample.sample_item_name
            ),
            sample_item_type=(
                "UNKNOWN"
                if source_sample.sample_item_type == "ACQUISITION"
                else source_sample.sample_item_type
            ),
        )

        sample_items_to_create.append(sample_item_create)

    # Bulk create new sample items
    created_samples = (
        await create_sample_items(
            sample_items=sample_items_to_create,
            independent_transaction=False,
        )
    ).get("data", [])

    # Sanity check
    if len(created_samples) != len(sample_item_ids):
        raise ValueError(
            f"Created item count mismatch: expected {len(sample_item_ids)}, "
            f"got {len(created_samples)}"
        )

    # Prepare match operations using zip
    match_copy_commands = []
    requires_rematch = False

    for source_sample, created_sample in zip(source_samples, created_samples):
        new_sample_item_id = created_sample["sample_item_id"]

        # Verify correspondence between source and created sample
        if (
            source_sample.filename != created_sample["filename"]
            or source_sample.polarity != created_sample["polarity"]
        ):
            runtime.logger.error(
                f"Sample item correspondence mismatch detected: "
                f"source {source_sample.sample_item_id} "
                f"(filename='{source_sample.filename}', polarity='{source_sample.polarity}') "
                f"does not match created {new_sample_item_id} "
                f"(filename='{created_sample['filename']}', polarity='{created_sample['polarity']}'). "
                f"Skipping match data copy for this sample."
            )
            requires_rematch = True
            continue

        if source_sample.sample_batch_id == sample_batch_id or always_copy_matches:
            match_copy_commands.append(
                CopyMatches(source_sample.sample_item_id, new_sample_item_id)
            )
        else:
            requires_rematch = True

    # Copy match data if needed
    if match_copy_commands:
        notification = UserNotification(
            process_id=process_id,
            parent_id=parent_id,
            type="copy_sample_items",
            status="pending",
            message=f"Copying match records for {len(sample_item_ids)} samples.",
            data={
                "sample_match_copies": [cmd._asdict() for cmd in match_copy_commands],
                "sample_batch_id": sample_batch_id,
                "_user_id": user_id,
            },
        )
        await copy_sample_items_match_data(
            match_copy_commands,
            notification,
        )

    # Emit reload event to the target sample batch
    if independent_transaction:
        await emit_record_reload(
            record_type="sample",
            room=sample_batch_id,
        )

    # Step 6: Set rematch status if samples need recomputation
    if requires_rematch:
        await update_sample_batch_status(
            sample_batch_ids=[sample_batch_id],
            status="rematch",
            independent_transaction=True,
        )

    # Step 7: Return the copied sample and message
    message = (
        f"Copied {len(created_samples)} samples successfully "
        f"to batch '{target_batch.sample_batch_name}'."
    )

    if match_copy_commands:
        message += " Match data was copied."

    if requires_rematch:
        message += " This batch may have different targets, please refresh the matches."

    return {
        "status": "success",
        "results": len(created_samples),
        "message": message,
        "data": created_samples,
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def move_sample_items(
    sample_item_ids: list[str],
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Move a set of samples to a specific batch. Leverages the copy_sample_items
    and delete_sample_items controllers:

    Steps:
    1. Validate batch existence
    2. Validate samples existence
    3. Validate move is between different batches
    4. Copy sample items over to the batch
    5. Delete the original sample items if successful

    :param sample_item_ids: ID of the original sample items to be moved.
    :type sample_item_ids: list[str[]
    :param sample_batch_id: ID of the sample batch where the items will be placed.
    :type sample_batch_id: str
    :param independent_transaction: Flag indicating whether the sample item copy is an independent transaction and if the operation should emit a reload event for the sample batch and if the sample should be rematched for new batch targets, defaults to False
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None
    :param parent_id: Parent process identifier
    :type parent_id: str | None
    :raises NotFoundException: If the original sample item is not found.
    :return: The newly created sample item dict.
    :rtype: dict
    """

    # Step 1. Validate batch existence
    batch = await fetch_sample_batch(sample_batch_id)

    # Step 2. Validate samples existence
    async with async_session() as session:
        stmt = select(SampleItem).where(SampleItem.sample_item_id.in_(sample_item_ids))
        result = await session.execute(stmt)
        original_samples = result.scalars().all()
        original_sample_item_ids = [
            original.sample_item_id for original in original_samples
        ]

        missing_sample_item_ids = [
            id for id in sample_item_ids if id not in original_sample_item_ids
        ]
        for missing_sample_item_id in missing_sample_item_ids:
            raise NotFoundException(
                f"Sample item with ID '{missing_sample_item_id}' not found"
            )

    # Step 3. Validate move is between different batches
    _, affected_sample_batch_ids, *_ = await fetch_affected_sample_data(
        sample_item_ids=sample_item_ids
    )
    if sample_batch_id in affected_sample_batch_ids:
        raise ValueError(
            "Move sample items: some of the samples you are trying to move are already in the requested batch"
        )

    # Step 4: copy sample items over
    copy_result = await copy_sample_items(
        sample_item_ids=sample_item_ids,
        sample_batch_id=sample_batch_id,
        independent_transaction=True,
        user_id=user_id,
        process_id=gen_id(8),
        parent_id=process_id,
    )
    moved_samples = copy_result["data"]

    # Step 5. Delete original samples if copy successful
    if moved_samples and copy_result["status"] == "success":
        await delete_sample_items(
            sample_item_ids=sample_item_ids,
            independent_transaction=True,
        )
    message = f"Moved {len(sample_item_ids)} samples successfully to batch '{batch.sample_batch_name}'."

    return {
        "status": "success",
        "results": len(moved_samples),
        "message": message,
        "data": moved_samples,
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def sample_item_export_peaks(
    sample_item_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id=None,
    parent_id=None,
):
    """Exports peak data for a specific sample item to a CSV file. This process involves loading sample file
    as a sample view, detecting peaks, and compiling peak data into a DataFrame before saving it to a file.

    Peak data is exported as a CSV file with the following columns:
    - datetime: The date and time of the scan in the local timezone.
    - datetime_utc: The date and time of the scan in UTC.
    - tic: The total ion current for the scan.
    - mz: The mass-to-charge ratio of the peak.
    - intensity: The intensity of the peak in each scan.
    - unit: The unit of the intensity value (ions for TOF or relative for Orbitrap).
    - sample_batch_name: The name of the sample batch.
    - sample_item_name: The name of the sample item.
    - filename: The filename of the sample file.
    - filter_id: The ID of the filter used.
    - sample_item_type: The type of the sample item.
    - sample_file_id: The ID of the sample file.
    - sample_item_id: The ID of the sample item.
    - instrument: The type of the instrument used for the sample file.

    Raises StalePeakStoreError for a sample file whose peak store was
    allocated against scans it no longer reads back: every column above but
    the TIC comes from that store, so an export built on it would be wrong
    throughout.

    :param sample_item_id: ID of the sample item.
    :type sample_item_id: str
    :param independent_transaction: Flag to indicate if the operation should be treated as an independent transaction, defaults to False.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None
    :param parent_id: Parent process identifier
    :type parent_id: str | None
    """
    sample = await fetch_sample(sample_item_id)
    sample_batch = await fetch_sample_batch(sample.sample_batch_id)

    # Prepare notification
    notification = UserNotification(
        process_id=process_id,
        parent_id=parent_id,
        type="sample_item_export_peaks",
        status="pending",
        message=f"Exporting peak data for sample item '{sample.sample_item_name}'",
        data={
            "sample_item_id": sample_item_id,
            "sample_batch_id": sample_batch.sample_batch_id,
            "_user_id": user_id,
        },
    )

    await send_progress_user_notification(notification, 0.1)

    # Nothing here is caught on the way out: an exception is logged with its
    # traceback by the exception pipeline, and reported to the user by the
    # background-task decorator, so there is nothing for this level to add.
    filename = sample.filename
    instrument_type = get_instrument_type(filename)

    await send_progress_user_notification(notification, 0.1)

    if instrument_type == "orbi":
        peak_data_type = "peak_heights"
    elif instrument_type == "tof":
        peak_data_type = "peak_areas"
    else:
        # get_instrument_type returns None when it cannot resolve one, and two
        # independent ifs left peak_data_type unbound for that - an
        # UnboundLocalError in place of a message naming the file.
        raise ValueError(f"Unknown instrument type: {instrument_type}")

    # dropna returns a lazy selection, so without the .compute() inside the
    # thread the peak matrix would still be read on the loop - twice, once
    # here and again at the .values below.
    def _load_peak_data():
        sample_file = m_io.load_peak_data(filename)
        return sample_file[peak_data_type].dropna(dim="mz", how="all").compute()

    sample_peak_data = await asyncio.to_thread(_load_peak_data)

    await send_progress_user_notification(notification, 0.8)

    # File creation timestamp
    base_datetime = sample.datetime
    # Get sample peak timestamps local
    sample_peak_time = sample_peak_data.time.values
    # Convert peak time to timedelta
    sample_peak_timedelta = pd.to_timedelta(sample_peak_time, unit="s")
    # Get scan timestamps relative to the base datetime
    scan_timestamps = sample_peak_timedelta + pd.Timestamp(base_datetime)
    # Get scan timestamps UTC
    base_datetime_utc = sample.datetime_utc
    scan_timestamps_utc = sample_peak_timedelta + pd.Timestamp(base_datetime_utc)

    # Get ticks for each time scan. Every other column of the frame comes from
    # the peak store, and this one is read from the sample file - so the two
    # are only pairable by position once the store's scan axis is known to be
    # the one the file still reads back.
    def _read_tic():
        tic_time, tic_per_scan = m_compute.get_tic_per_scan(filename)
        m_compute.check_stored_scan_axis(tic_time, sample_peak_time)
        return tic_per_scan

    scan_tics = await asyncio.to_thread(_read_tic)

    mz_values = sample_peak_data.mz.values
    intensities = sample_peak_data.values

    # Create arrays for the repeated values
    repeated_datetimes = np.repeat(
        scan_timestamps.values[:, np.newaxis], len(mz_values), axis=1
    )
    repeated_datetimes_utc = np.repeat(
        scan_timestamps_utc.values[:, np.newaxis], len(mz_values), axis=1
    )
    repeated_tics = np.repeat(scan_tics[:, np.newaxis], len(mz_values), axis=1)
    repeated_mz = np.repeat(mz_values, len(scan_timestamps))

    # Create the final DataFrame
    sample_peak_df = pd.DataFrame(
        {
            "datetime": repeated_datetimes.T.flatten(),
            "datetime_utc": repeated_datetimes_utc.T.flatten(),
            "tic": repeated_tics.T.flatten(),
            "mz": repeated_mz.flatten(),
            "intensity": intensities.flatten(),
        }
    ).assign(
        unit="ions" if instrument_type == "tof" else "counts",
        sample_batch_name=sample_batch.sample_batch_name,
        sample_item_name=sample.sample_item_name,
        filename=filename,
        filter_id=sample.filter_id,
        sample_item_type=sample.sample_item_type,
        sample_file_id=sample.sample_file_id,
        sample_item_id=sample.sample_item_id,
        instrument=sample.instrument,
    )

    await send_progress_user_notification(notification, 1)

    # Get the current date and time as a string for a filename
    dt_str = datetime.now().isoformat().replace("-", "").replace(":", "").split(".")[0]

    # Save the peak data to a CSV file
    peakfile_filename = "_".join(
        [dt_str, "peak_data", sample.sample_item_name.replace(" ", "_") + ".csv"]
    )
    runtime.logger.info(f"Writing peak data to file {peakfile_filename}")
    sample_peak_df.to_csv(
        user_temp_path(user_id, peakfile_filename), index=False, sep=";"
    )
    message = f"Peak data for sample item '{sample.sample_item_name}' was exported to file '{peakfile_filename}'."
    runtime.logger.info(message)

    # Return the status message
    return {
        "message": message,
        "data": {"filename": peakfile_filename},
        "_notification_data": {
            "sample_item_id": sample_item_id,
            "download": peakfile_filename,
        },
    }
