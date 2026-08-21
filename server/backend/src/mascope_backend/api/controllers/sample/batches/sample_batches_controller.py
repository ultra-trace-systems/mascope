from datetime import datetime, timezone

import numpy as np
import pandas as pd
import xarray as xr
from sqlalchemy import (
    and_,
    asc,
    delete,
    desc,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

import mascope_file.name as m_name
from mascope_backend.api.controllers.match.match_controller import (
    match_compute_samples,
)
from mascope_backend.api.controllers.sample.batches.lib.util import (
    collect_spectra_per_ionization_mode,
    detect_update_batch_changes,
    load_existing_batch_cache,
)
from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    copy_sample_items,
    create_sample_items,
)
from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_sample_batch,
)
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.api.controllers.sample.lib.sample_items_fetch import (
    fetch_sample_item_ids,
)
from mascope_backend.api.controllers.samples.samples_controller import get_sample
from mascope_backend.api.controllers.target.collections.target_collections_controller import (
    get_target_collections,
)
from mascope_backend.api.controllers.target.compounds.target_compounds_controller import (
    get_target_compounds,
)
from mascope_backend.api.controllers.target.ions.target_ions_controller import (
    get_target_ions,
)
from mascope_backend.api.controllers.target.isotopes.target_isotopes_controller import (
    get_target_isotopes,
)
from mascope_backend.api.controllers.target.lib.fetch.target_collections_fetch import (
    validate_collections_for_batch,
    validate_collections_scope_for_batch,
)
from mascope_backend.api.lib.api_features import (
    api_controller,
    api_controller_background_task,
)
from mascope_backend.api.lib.exceptions.api_exceptions import (
    NotFoundException,
)
from mascope_backend.api.models.sample.batches.config import sample_batch_config
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    SampleBatchCreate,
    SampleBatchRead,
    SampleBatchUpdate,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SampleItemCreate,
)
from mascope_backend.api.new.ionization.modes.util import (
    resolve_ionization_modes_by_tokens,
)
from mascope_backend.api.new.temp.storage import user_temp_path
from mascope_backend.db import (
    Dataset,
    SampleBatch,
    SampleFile,
    TargetCollectionInSampleBatch,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)
from mascope_backend.socket.records import (
    emit_record_created,
    emit_record_deleted,
    emit_record_reload,
    emit_record_updated,
)
from mascope_file import io as m_io
from mascope_signal import compute as m_compute
from mascope_signal.peak import get_peaks
from mascope_tools.alignment.calibration import Spectra


@api_controller()
async def get_sample_batches(
    dataset_id: str | None = None,
    sample_batch_name: str | None = None,
    sample_batch_type: list[str] | None = None,
    status: list[str] | None = None,
    polarity: list[str] | None = None,
    sort: str = "sample_batch_utc_created",
    order: str = "asc",
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a paginated list of sample batches, optionally filtered by dataset ID, and sorted by a specified column.

    Steps:
    1. Construct a SQLAlchemy query to select all sample batches.
    2. Apply optional dataset ID filtering if specified.
    3. Apply sorting based on the provided sort and order parameters.
    4. Apply pagination based on the provided page and limit parameters.
    5. Execute the query to fetch the results.
    6. Convert the results into a list of dictionaries for JSON serialization.

    :param dataset_id: ID of the dataset to filter sample batches by, defaults to None.
    :type dataset_id: str, optional
    :param sample_batch_name: Name of the sample batch to filter by, defaults to None.
    :type sample_batch_name: str, optional
    :param sample_batch_type: Type of sample batch to filter by (ACQUISITION or ANALYSIS), defaults to None.
    :type sample_batch_type: list[str], optional
    :param polarity: Polarity to filter by (+, -, or +-), defaults to None.
    :type polarity: list[str], optional
    :param sort: Column name to sort the results by, defaults to "sample_batch_utc_created".
    :type sort: str, optional
    :param order: Sorting order, "asc" for ascending or "desc" for descending, defaults to "asc".
    :type order: str, optional
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None, optional
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None, optional
    :return: A dictionary containing the total count of sample batches and a list of sample batch details.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        # Step 1: Construct base query
        stmt = select(SampleBatch)

        # Step 2: Filter by provided parameters
        if dataset_id:
            stmt = stmt.filter(SampleBatch.dataset_id == dataset_id)

        if sample_batch_name:
            stmt = stmt.filter(SampleBatch.sample_batch_name == sample_batch_name)

        if sample_batch_type:
            stmt = stmt.filter(SampleBatch.sample_batch_type.in_(sample_batch_type))

        if status:
            stmt = stmt.filter(SampleBatch.status.in_(status))

        if polarity:
            stmt = stmt.filter(SampleBatch.polarity.in_(polarity))

        # Step 3: Apply sorting
        if sort:
            if order == "desc":
                stmt = stmt.order_by(desc(getattr(SampleBatch, sort)))
            else:
                stmt = stmt.order_by(asc(getattr(SampleBatch, sort)))

        # Step 4: Apply pagination
        count_stmt = select(func.count()).select_from(stmt)
        total = await session.scalar(count_stmt)
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)

        # Step 5: Execute the query
        result = await session.execute(stmt)
        sample_batches = result.scalars().all()

    # Step 6: Return the total count and the list of validated sample batches
    return {
        "message": "Sample batches retrieved successfully",
        "results": total,
        "data": [
            SampleBatchRead.model_validate(sample_batch).model_dump()
            for sample_batch in sample_batches
        ],
    }


@api_controller()
async def get_sample_batch(sample_batch_id: str) -> dict:
    """
    Retrieves a single sample batch by its unique ID.

    Steps:
    1. Execute a query to fetch the sample batch with the specified ID.
    2. Check if the sample batch exists. If not, raise a NotFoundException.
    3. Return the sample batch's details as a dictionary.

    :param sample_batch_id: Unique identifier of the sample batch to retrieve.
    :type sample_batch_id: str
    :raises NotFoundException: If the sample batch with the given ID is not found.
    :return: The requested sample batch's details.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch sample batch by ID
        sample_batch = await session.get(SampleBatch, sample_batch_id)

        if not sample_batch:
            # Step 2: If sample batch not found, raise exception
            raise NotFoundException(
                f"Sample batch with ID '{sample_batch_id}' not found"
            )
    # Step 3: Return sample batch details
    return {
        "message": f"Sample batch '{sample_batch.sample_batch_name}' retrieved successfully",
        "data": SampleBatchRead.model_validate(sample_batch).model_dump(),
    }


@api_controller()
async def get_batch_targets(sample_batch_id: str, deduplicate: bool = False) -> dict:
    """
    Retrieves targets associated with a specific sample batch, including collections, compounds, ions, and isotopes.

    Steps:
    1. Retrieve target collections, compounds, ions, and isotopes using the existing controllers.
    2. Optionally deduplicate the results based on the deduplicate flag.
    3. Return a comprehensive dictionary including counts and details for target collections, compounds, ions, and isotopes.

    :param sample_batch_id: ID of the sample batch for which targets are being retrieved.
    :type sample_batch_id: str
    :param deduplicate: Flag to indicate whether duplicates should be removed.
    :type deduplicate: bool
    :return: A dictionary containing counts and details of associated targets, with a success message.
    :rtype: dict
    """
    # Step 1: Verify existence of sample batch
    sample_batch_result = await get_sample_batch(sample_batch_id)
    sample_batch = sample_batch_result.get("data")
    sample_batch_name = sample_batch["sample_batch_name"]

    # Step 2: Fetch target collections
    collections_response = await get_target_collections(sample_batch_id=sample_batch_id)
    target_collections = collections_response["data"]

    # Step 3: Fetch target compounds
    compounds_response = await get_target_compounds(
        sample_batch_id=sample_batch_id,
        show_target_collection=not deduplicate,
    )
    target_compounds = compounds_response["data"]

    # Step 4: Fetch target ions
    ions_response = await get_target_ions(
        sample_batch_id=sample_batch_id,
        show_target_collection=not deduplicate,
    )
    target_ions = ions_response["data"]

    # Step 5: Fetch target isotopes
    isotopes_response = await get_target_isotopes(
        sample_batch_id=sample_batch_id,
        show_target_collection=not deduplicate,
    )
    target_isotopes = isotopes_response["data"]

    # Step 6: Compile response
    return {
        "message": (
            f"Sample batch '{sample_batch_name}' targets:"
            f"{len(target_collections)} collections, "
            f"{len(target_compounds)} compounds, "
            f"{len(target_ions)} ions, "
            f"{len(target_isotopes)} isotopes."
        ),
        "result": {
            "target_collections": len(target_collections),
            "target_compounds": len(target_compounds),
            "target_ions": len(target_ions),
            "target_isotopes": len(target_isotopes),
        },
        "data": {
            "target_collections": target_collections,
            "target_compounds": list(target_compounds),
            "target_ions": list(target_ions),
            "target_isotopes": list(target_isotopes),
        },
    }


async def _insert_sample_batch(
    sample_batch: SampleBatchCreate,
    independent_transaction: bool = False,
) -> dict:
    """Validate and insert one sample batch, letting ``IntegrityError`` through.

    Split out of :func:`create_sample_batch` so a caller that recovers from a
    natural-key collision - :func:`get_or_create_acquisition_batch` - can still
    see the ``IntegrityError``. The ``@api_controller`` decorator on the public
    entry point rewrites every exception into an ``ApiException`` (a
    ``SQLAlchemyError`` becomes a generic 500), which erases the distinction
    between "another worker already created this row" and a real database
    fault, and 500 is not in ``_RECOVERABLE_STATUS_CODES`` either.

    :param sample_batch: Data for creating the sample batch.
    :type sample_batch: SampleBatchCreate
    :param independent_transaction: Whether to emit the creation event here.
    :type independent_transaction: bool, optional
    :raises IntegrityError: When the insert violates a constraint - notably
        ``uq_sample_batch_acquisition_natural_key`` on a concurrent create.
    :return: The created sample batch data.
    :rtype: dict
    """
    async with async_session() as session:
        # --- Validate dataset type for ACQUISITION batches ---
        if sample_batch.sample_batch_type == "ACQUISITION":
            if not (dataset := await session.get(Dataset, sample_batch.dataset_id)):
                raise NotFoundException(
                    f"Dataset with ID '{sample_batch.dataset_id}' not found"
                )

            if dataset.dataset_type != "ACQUISITION":
                raise ValueError(
                    "ACQUISITION sample batches can only be created in ACQUISITION datasets. "
                    f"Dataset '{dataset.dataset_name}' is of type '{dataset.dataset_type}'"
                )

        # Validate target collection type and scope constraints
        await validate_collections_for_batch(
            target_collection_ids=sample_batch.target_collection_ids,
            sample_batch_type=sample_batch.sample_batch_type,
        )
        await validate_collections_scope_for_batch(
            target_collection_ids=sample_batch.target_collection_ids,
            dataset_id=sample_batch.dataset_id,
        )

        # --- Construct new sample batch object ---
        new_sample_batch = SampleBatch(
            sample_batch_id=gen_id(16),
            **sample_batch.model_dump(
                exclude={"target_collection_ids"}
            ),  # Exclude collections associations from unpacking
            locked=(
                1
                if sample_batch.sample_batch_type == "ACQUISITION"
                and sample_batch_config.ACQUISITION_AUTO_LOCK
                else 0
            ),  # Auto-lock acquisition
            sample_batch_utc_created=datetime.now(timezone.utc),
        )

        session.add(new_sample_batch)

        # --- Associate with target collections if provided ---
        for target_collection_id in sample_batch.target_collection_ids:
            new_target_collection_in_sample_batch = TargetCollectionInSampleBatch(
                target_collection_id=target_collection_id,
                sample_batch_id=new_sample_batch.sample_batch_id,
            )
            session.add(new_target_collection_in_sample_batch)

        # --- Commit transaction and refresh instance ---
        await session.commit()
        await session.refresh(new_sample_batch)

    # --- Emit creation event ---
    batch_data = SampleBatchRead.model_validate(new_sample_batch).model_dump()
    if independent_transaction:
        await emit_record_created(
            record_type="batch",
            record_id=new_sample_batch.sample_batch_id,
            record=batch_data,
            room=new_sample_batch.dataset_id,
        )

    # --- Return created sample batch data ---
    return {
        "message": f"Sample batch '{new_sample_batch.sample_batch_name}' was created.",
        "data": batch_data,
    }


@api_controller()
async def create_sample_batch(
    sample_batch: SampleBatchCreate,
    independent_transaction: bool = False,
) -> dict:
    """
    Creates a new sample batch with the specified details.
    Validates constraints for ACQUISITION batches.

    Steps:
    - Validate batch constraints:
        - dataset type constraints for ACQUISITION batches
        - target collection type constraints for the sample batch type
        - ionization mechanism polarity compatibility
    - Construct a new SampleBatch object with the provided details and a generated unique ID.
    - Associate the new sample batch with target collections if any are provided in the request.
    - Commit the transaction to persist the new sample batch in the database.
    - Return the details of the created sample batch as a dictionary.

    :param sample_batch: Data for creating the sample batch.
    :type sample_batch: SampleBatchCreate
    :param independent_transaction: Flag indicating if the operation is an independent transaction, defaults to False.
    :type independent_transaction: bool, optional
    :return: The created sample batch data.
    :rtype: dict
    """
    return await _insert_sample_batch(
        sample_batch=sample_batch,
        independent_transaction=independent_transaction,
    )


async def _find_acquisition_batch(
    dataset_id: str, sample_batch_name: str, polarity: str
) -> dict | None:
    """Look up the daily ACQUISITION batch for one dataset, name and polarity.

    Duplicate-tolerant: databases that predate
    ``uq_sample_batch_acquisition_natural_key`` can hold duplicate daily
    batches from get-or-create races, so return the oldest and break ties on
    the primary key. Without that tiebreaker two workers recovering from the
    same race can pick *different* rows - equal ``sample_batch_utc_created``
    values are the likely case, since the duplicates were inserted racing the
    same read - and the day's samples keep splitting instead of converging.

    Polarity is part of the lookup because ``ionization_mode_name`` carries no
    uniqueness (only ``ionization_mode_token`` does), so an admin who names the
    positive and negative variant of a mode alike renders one batch name for
    both. Matching on it too keeps the two polarities in their own batches
    rather than filing the second one under the first one's polarity and
    target collections. Two modes that share a name *and* a polarity still
    collapse - fixing that needs the mode on the batch, not just its name.

    :param dataset_id: ID of the ACQUISITION dataset the batch belongs to.
    :type dataset_id: str
    :param sample_batch_name: Generated daily batch name.
    :type sample_batch_name: str
    :param polarity: Ionization mode polarity the batch was created for.
    :type polarity: str
    :return: The matching batch, or None.
    :rtype: dict | None
    """
    async with async_session() as session:
        batches = (
            (
                await session.execute(
                    select(SampleBatch)
                    .where(
                        SampleBatch.dataset_id == dataset_id,
                        SampleBatch.sample_batch_type == "ACQUISITION",
                        SampleBatch.sample_batch_name == sample_batch_name,
                        SampleBatch.polarity == polarity,
                    )
                    .order_by(
                        SampleBatch.sample_batch_utc_created.asc().nulls_last(),
                        SampleBatch.sample_batch_id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )

    if len(batches) > 1:
        # Recovered data anomaly: worth one grouped warning issue
        runtime.logger.warning(
            f"{len(batches)} duplicate ACQUISITION batches found for "
            f"'{sample_batch_name}' ({polarity}) in dataset {dataset_id}, "
            "using the oldest"
        )
    return SampleBatchRead.model_validate(batches[0]).model_dump() if batches else None


@api_controller()
async def get_or_create_acquisition_batch(sample_batch: SampleBatchCreate) -> dict:
    """Get or create the daily ACQUISITION batch for one dataset and mode.

    Concurrent ingest of files that share a day and an ionization mode resolves
    to a single batch name, so a plain read-then-write lets several callers
    read "absent" and all create. The recovery is the same shape as
    :func:`~mascope_backend.api.controllers.dataset.acquisition.service.get_acquisition_dataset`
    one call frame up: insert, and on the natural-key collision fetch the row
    the winner committed. It holds across processes, which matters because
    production runs several uvicorn workers (``workers = "auto"``) and each
    converted file arrives as its own load-balanced request - so the files of
    one watcher scan routinely land on *different* workers, where an
    in-process lock is several unrelated objects.

    :param sample_batch: Data for creating the batch if it does not exist.
    :type sample_batch: SampleBatchCreate
    :raises ValueError: When called with a non-ACQUISITION batch type.
    :return: dict with ``"data"`` holding the batch and ``"created"`` saying
        whether this call is the one that inserted it.
    :rtype: dict
    """
    if sample_batch.sample_batch_type != "ACQUISITION":
        raise ValueError(
            "get_or_create_acquisition_batch only handles ACQUISITION batches; "
            f"got '{sample_batch.sample_batch_type}'"
        )

    existing = await _find_acquisition_batch(
        dataset_id=sample_batch.dataset_id,
        sample_batch_name=sample_batch.sample_batch_name,
        polarity=sample_batch.polarity,
    )
    if existing is not None:
        return {
            "message": (
                f"Sample batch '{sample_batch.sample_batch_name}' already exists."
            ),
            "data": existing,
            "created": False,
        }

    try:
        created = await _insert_sample_batch(
            sample_batch=sample_batch, independent_transaction=True
        )
        return {**created, "created": True}
    except IntegrityError:
        # Another worker committed the same natural key first - adopt its row.
        existing = await _find_acquisition_batch(
            dataset_id=sample_batch.dataset_id,
            sample_batch_name=sample_batch.sample_batch_name,
            polarity=sample_batch.polarity,
        )
        if existing is None:
            # Not the natural-key collision (e.g. a foreign-key violation) -
            # surface the original IntegrityError instead of masking it.
            raise
        runtime.logger.debug(
            f"ACQUISITION batch '{sample_batch.sample_batch_name}' created "
            f"concurrently, reusing {existing['sample_batch_id']}"
        )
        return {
            "message": (
                f"Sample batch '{sample_batch.sample_batch_name}' already exists."
            ),
            "data": existing,
            "created": False,
        }


@api_controller()
async def update_sample_batch(
    sample_batch_id: str,
    sample_batch_update: SampleBatchUpdate,
    independent_transaction: bool = False,
) -> dict:
    """
    Updates the specified sample batch with new information and associations.
    Changes to ion mechanisms, calibration parameters, or target collections set batch status to "rematch"

    Steps:
    1. Fetch existing batch data and validate existence
    2. Detect changes between current and proposed state
    3. Validate new configurations when changes are detected
    4. Update batch data using automatic basic field handling
    5. Set rematch status when recomputation is needed
    6. Emit appropriate reload events based on change types

    :param sample_batch_id: ID of the sample batch to update
    :type sample_batch_id: str
    :param sample_batch_update: Updated data for the sample batch
    :type sample_batch_update: SampleBatchUpdate
    :param independent_transaction: Controls sio event emission behavior and error handling.
    :type independent_transaction: bool
    :raises NotFoundException: When sample batch is not found
    :raises ValueError: When validation fails
    :return: Updated sample batch data with success message
    :rtype: dict
    """
    # Step 1: Fetch existing batch with target associations
    async with async_session() as session:
        if not (
            batch := await session.get(
                SampleBatch,
                sample_batch_id,
                options=[joinedload(SampleBatch.target_collection)],
            )
        ):
            raise NotFoundException(
                f"Sample batch with ID '{sample_batch_id}' not found"
            )

        # Step 2: Detect changes between current and proposed state
        changes = detect_update_batch_changes(batch, sample_batch_update)

        # Step 3: Validate new configurations when changes detected
        if changes["collections"]:
            # Validate target collection type and scope constraints
            await validate_collections_for_batch(
                target_collection_ids=sample_batch_update.target_collection_ids,
                sample_batch_type=batch.sample_batch_type,
            )
            await validate_collections_scope_for_batch(
                target_collection_ids=sample_batch_update.target_collection_ids,
                dataset_id=batch.dataset_id,
            )

        # Step 4: Update batch data
        basic_fields = sample_batch_update.model_dump(
            exclude={"target_collection_ids"}, exclude_unset=True
        )
        for field, value in basic_fields.items():
            if hasattr(batch, field):
                setattr(batch, field, value)

        # Update complex fields only when changed
        if changes["collections"]:
            # Remove specific collections
            if changes["collections_to_remove"]:
                await session.execute(
                    delete(TargetCollectionInSampleBatch).where(
                        and_(
                            TargetCollectionInSampleBatch.sample_batch_id
                            == sample_batch_id,
                            TargetCollectionInSampleBatch.target_collection_id.in_(
                                changes["collections_to_remove"]
                            ),
                        )
                    )
                )

            # Add specific collections
            for collection_id in changes["collections_to_add"]:
                session.add(
                    TargetCollectionInSampleBatch(
                        target_collection_id=collection_id,
                        sample_batch_id=sample_batch_id,
                    )
                )

        await session.commit()
        await session.refresh(batch)

    # Step 5: Set rematch status when recomputation needed
    needs_rematch = changes["collections"]
    if needs_rematch:
        await update_sample_batch_status(
            sample_batch_ids=[sample_batch_id],
            status="rematch",
            independent_transaction=True,  # TODO_reload fix when the reload is working properly
        )
        batch.status = "rematch"  # update in-memory obj to reflect status change avoid extra db call

    # Step 6: Emit update event
    batch_data = SampleBatchRead.model_validate(batch).model_dump()

    if independent_transaction:
        await emit_record_updated(
            record_type="batch",
            record_id=sample_batch_id,
            record=batch_data,
            room=batch.dataset_id,
        )

        # If collections changed, also emit match.collection reload
        if changes["collections"]:
            await emit_record_reload(
                record_type="match_collection",
                room=sample_batch_id,
            )

    return {
        "status": "success",
        "message": f"Sample batch '{batch.sample_batch_name}' was updated"
        + (" and flagged for rematch" if needs_rematch else ""),
        "data": batch_data,
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def delete_sample_batch(
    sample_batch_id: str,
    dataset_id: str = None,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id=None,
):
    """
    Deletes a sample batch by its unique ID and optionally emits relevant events.

    Steps:
    - Fetch the sample batch by its ID from the database to verify its existence.
    - Delete the sample batch from the database if it exists.
    - Remove any cached batch data associated with the sample batch.
    - Emit a deletion event if independent_transaction is True.

    :param sample_batch_id: Unique identifier of the sample batch to delete.
    :type sample_batch_id: str
    :param dataset_id: ID of the dataset associated with the sample batch, used for event emission.
    :type dataset_id: str, optional
    :param independent_transaction: Indicates if the deletion should be considered an independent transaction, which affects event emission.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :raises NotFoundException: If no sample batch is found with the provided ID.
    """
    async with async_session() as session:
        # --- Fetch and verify sample batch existence ---
        sample_batch = await session.get(SampleBatch, sample_batch_id)
        if not sample_batch:
            raise NotFoundException(
                f"Sample batch with ID '{sample_batch_id}' not found"
            )
        # --- Delete sample batch and commit changes ---
        await session.delete(sample_batch)
        await session.commit()

    # --- Cleanup batch cache ---
    m_io.delete_batch_cache(sample_batch_id)

    # --- Emit deletion event if independent transaction ---
    if independent_transaction:
        await emit_record_deleted(
            record_type="batch", record_id=sample_batch_id, room=dataset_id
        )

    return {
        "message": f"Sample batch '{sample_batch.sample_batch_name}' was deleted.",
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[("match", "affected_sample_batch_ids")],
    error_notification_rooms=["user_id"],
    error_reload=[("match", "affected_sample_batch_ids")],
)
async def import_sample_items(
    sample_batch_id: str,
    sample_items: list[SampleItemCreate],
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
):
    """
    Imports sample items to a specified batch by creating provided sample items and computing matches.

    Steps:
    - Verify that all sample items are for the same instrument
    - Resolve ionization methods for the sample items to be created
    - Create provided sample items and save them to the database
    - Match imported sample items
    - Return the status message

    :param sample_batch_id: ID of the sample batch where sample items will be imported.
    :type sample_batch_id: str
    :param sample_items: List of sample items to be created and imported.
    :type sample_items: list[SampleItemCreate]
    :param independent_transaction: Flag to indicate if the operation should be treated as an independent transaction, defaults to False
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None, optional
    """
    sample_batch = await fetch_sample_batch(sample_batch_id)
    sample_batch_name = sample_batch.sample_batch_name

    # Fetch all sample files by ID
    async with async_session() as session:
        sample_files = (
            (
                await session.execute(
                    select(SampleFile).where(
                        SampleFile.sample_file_id.in_(
                            [si.sample_file_id for si in sample_items]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )

    # --- Verify that all samples are for the same instrument --- #
    instrument_types = {m_name.get_instrument_type(sf.filename) for sf in sample_files}
    if len(instrument_types) > 1:
        raise ValueError(
            "Importing samples from different instruments is not supported, please import samples for each instrument separately"
        )

    notification = UserNotification(
        process_id=process_id,
        type="import_sample_items",
        status="pending",
        message=f"Importing {len(sample_items)} sample{'s' if len(sample_items) > 1 else ''}.",
        data={
            "sample_batch_id": sample_batch_id,
            "_user_id": user_id,
        },
    )
    await send_progress_user_notification(notification, 0.1)

    # --- Resolve ionization methods for the sample items to be created --- #
    for item in sample_items:
        sample_file = await fetch_sample_file(sample_file_id=item.sample_file_id)
        ionization_modes = await resolve_ionization_modes_by_tokens(sample_file)
        # Filter ionization modes by polarity
        ionization_modes = [
            im
            for im in ionization_modes
            if im.ionization_mode_polarity == item.polarity
        ]
        if not ionization_modes:
            raise ValueError(
                f"Could not resolve ionization mode for file '{sample_file.filename}'. "
                "No valid ionization mode token in the filename for the selected polarity."
            )
        if len(ionization_modes) > 1:
            raise ValueError(
                f"Could not resolve ionization mode for file '{sample_file.filename}'. "
                "Multiple ionization mode tokens matched the filename."
            )
        item.ionization_mode_id = ionization_modes[0].ionization_mode_id

    await send_progress_user_notification(notification, 0.15)

    # --- Create provided sample items and save them to the database --- #
    created_sample_items_data = (
        await create_sample_items(
            sample_items=sample_items, independent_transaction=False
        )
    ).get("data", [])
    created_sample_item_ids = [
        item["sample_item_id"] for item in created_sample_items_data
    ]

    notification.message = f"Created {len(sample_items)} sample{'s records' if len(sample_items) > 1 else 'record'}."
    await send_progress_user_notification(notification, 0.2)

    # --- Match imported sample items --- #
    await match_compute_samples(
        sample_item_ids=created_sample_item_ids,
        independent_transaction=False,
        user_id=user_id,
        process_id=gen_id(8),
        parent_id=process_id,
    )

    notification.message = (
        f"Matched {len(created_sample_item_ids)} imported sample items."
    )
    await send_progress_user_notification(notification, 0.9)

    # --- Prepare affected IDs for notifications --- #
    affected_sample_batch_ids = [sample_batch_id]

    # --- Return the status message --- #
    return {
        "message": f"{len(sample_items)} sample{'s' if len(sample_items) > 1 else ''} was imported to the sample batch '{sample_batch_name}'.",
        "_notification_data": {
            "affected_sample_item_ids": created_sample_item_ids,
            "affected_sample_batch_ids": affected_sample_batch_ids,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def copy_sample_batch(
    sample_batch_id: str,
    dataset_id: str,
    sample_batch_name: str,
    sample_batch_description: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id=None,
) -> dict:
    """
    Copies a sample batch, including its associated sample items and target collections, into a specified dataset with a new name and description.
    The function ensures all related entities like sample items and target collections are also copied over to maintain the integrity of the sample batch data.
    Called as a background task from the endpoint, so it also handles sio notification and dataset reloading upon successful copying or if any errors occur.

    Steps:
    1. Validate the dataset into which the sample batch is being copied.
    2. Fetch and validate the original sample batch from the database.
    3. Prepare TargetCollectionInSampleBatch records associated with the original sample batch.
    4. Create a new sample batch with updated information and copy all other data.
    5. Copy associated sample items from the original to the new sample batch.

    :param sample_batch_id: ID of the original sample batch to be copied.
    :type sample_batch_id: str
    :param dataset_id: ID of the dataset where the new sample batch will be placed.
    :type dataset_id: str
    :param sample_batch_name: Name for the new copied sample batch.
    :type sample_batch_name: str
    :param sample_batch_description: Description for the new copied sample batch.
    :type sample_batch_description: str
    :param independent_transaction: Flag to indicate if the operation should be treated as an independent transaction, defaults to False
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None, optional
    :raises NotFoundException: If the dataset or original sample batch is not found.
    """
    async with async_session() as session:
        # Step 1: Validate the dataset into which the sample batch is being copied.
        dataset = await session.get(Dataset, dataset_id)

        if not dataset:
            raise NotFoundException(f"Dataset with ID '{dataset_id}' not found")

        # Step 2: Fetch and validate the original sample batch from the database with related TargetCollectionInSampleBatch and SampleItem records
        stmt = (
            select(SampleBatch)
            .options(
                joinedload(SampleBatch.target_collection),
                joinedload(SampleBatch.sample_items),
            )
            .filter(SampleBatch.sample_batch_id == sample_batch_id)
        )
        result = await session.execute(stmt)
        original_sample_batch = result.scalars().first()

        if not original_sample_batch:
            raise NotFoundException(
                f"Sample batch with ID '{sample_batch_id}' not found"
            )

    # Step 3: Prepare TargetCollectionInSampleBatch records associated with the original sample batch
    target_collection_ids = [
        tc.target_collection_id for tc in original_sample_batch.target_collection
    ]

    # Step 4: Create a new sample batch with type conversion
    # batch type: ACQUISITION → ANALYSIS, polarity "+" or "-" to "+-".
    new_sample_batch_body = SampleBatchCreate(
        dataset_id=dataset_id,
        sample_batch_name=sample_batch_name,
        sample_batch_description=sample_batch_description,
        sample_batch_type=(
            sample_batch_config.DEFAULT_SAMPLE_BATCH_TYPE
            if original_sample_batch.sample_batch_type == "ACQUISITION"
            else original_sample_batch.sample_batch_type
        ),
        polarity=(
            sample_batch_config.ANALYSIS_POLARITY
            if original_sample_batch.sample_batch_type == "ACQUISITION"
            else original_sample_batch.polarity
        ),
        target_collection_ids=target_collection_ids,
    )

    # Create the new sample batch
    new_sample_batch = (
        await create_sample_batch(
            sample_batch=new_sample_batch_body, independent_transaction=True
        )
    ).get("data")

    # Step 5: Copy sample items associated with the original sample batch
    await copy_sample_items(
        sample_item_ids=[s.sample_item_id for s in original_sample_batch.sample_items],
        sample_batch_id=new_sample_batch["sample_batch_id"],
        always_copy_matches=True,
        independent_transaction=False,
        user_id=user_id,
        process_id=gen_id(8),
        parent_id=process_id,
    )

    # Step 6: Return the copied batch and message
    sample_batch_name = new_sample_batch["sample_batch_name"]
    new_sample_batch_id = new_sample_batch["sample_batch_id"]
    return {
        "data": new_sample_batch,
        "message": f"Sample batch '{sample_batch_name}' was successfully copied to dataset '{dataset.dataset_name}'.",
        "_notification_data": {
            "sample_item_id": new_sample_batch_id,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def sample_batch_export_peaks(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id=None,
    parent_id=None,
):
    """
    Exports peak data for a specific sample batch to a CSV file. This process involves loading sample files,
    detecting peaks, and compiling peak data into a DataFrame before saving it to a file.

    Steps:
    1. Fetch sample items belonging to the specified sample batch.
    2. Iterate over each sample item, load its file, and perform peak detection.
    3. Compile detected peaks into a DataFrame.
    4. Save the DataFrame to a CSV file named with the sample batch name and current datetime.
    5. If independent_transaction is True, emit a 'batch_export_peak_data_finished' event with the session ID.

    :param sample_batch_id: ID of the original sample batch to be copied.
    :type sample_batch_id: str
    :param independent_transaction: Flag to indicate if the operation should be treated as an independent transaction, defaults to False.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None, optional
    :param parent_id: Parent process identifier for progress tracking
    :type parent_id: str | None, optional
    """
    # Get sample batch name
    sample_batch_result = await get_sample_batch(sample_batch_id)
    sample_batch = sample_batch_result.get("data")
    sample_batch_name = sample_batch["sample_batch_name"]

    sample_item_ids, _ = await fetch_sample_item_ids(sample_batch_id=sample_batch_id)

    sample_views_dict_list = []
    for sample_item_id in sample_item_ids:
        result = await get_sample(sample_item_id)
        sample_views_dict_list.append(result["data"])

    sample_views_df = pd.DataFrame(sample_views_dict_list)

    peak_data = []
    total_samples = len(sample_views_df)

    for index, row in sample_views_df.iterrows():
        # Prepare progress user notification.
        notification = UserNotification(
            process_id=process_id,
            parent_id=parent_id,
            type="sample_batch_export_peaks",
            status="pending",
            message=f"Exporting peak data for batch '{sample_batch_name}'.",
            data={
                "_user_id": user_id,
                "_total_samples": total_samples,
                "_item_index": index,
            },
        )

        try:
            filename = row["filename"]

            await send_progress_user_notification(notification, 0.1)

            # Assign peak abundance units
            instrument_type = m_name.get_instrument_type(filename)
            unit = ""
            if instrument_type == "orbi":
                unit = "height"
            if instrument_type == "tof":
                unit = "area"

            await send_progress_user_notification(notification, 0.9)

            sample_file = m_io.load_peak_data(filename)
            peak_data_item = get_peaks(sample_file, unit).sum(dim="time").compute()

            await send_progress_user_notification(notification, 1)
        except Exception:
            runtime.logger.exception(
                "Failed to compute peak data for a sample file - skipping it"
            )
            continue

        for peak in peak_data_item:
            peak_data.append(
                {
                    "mz": peak.mz.item(),
                    "intensity": peak.item(),
                    "unit": "ions" if instrument_type == "tof" else "counts",
                    "sample_batch_name": sample_batch_name,
                    "sample_item_name": row["sample_item_name"],
                    "filename": row["filename"],
                    "filter_id": row["filter_id"],
                    "sample_item_type": row["sample_item_type"],
                    "datetime": row["datetime"],
                    "datetime_utc": row["datetime_utc"],
                    "sample_file_id": row["sample_file_id"],
                    "sample_item_id": row["sample_item_id"],
                    "tic": row["tic"],
                    "instrument": row["instrument"],
                }
            )

    dt_str = datetime.now().isoformat().replace("-", "").replace(":", "").split(".")[0]

    peakfile_name = "_".join(
        [dt_str, "peaks", sample_batch_name.replace(" ", "_") + ".csv"]
    )
    runtime.logger.info(f"Writing peak data to file {peakfile_name}")
    # Save peak data to dataframe and then to csv file
    batch_peak_df = pd.DataFrame(peak_data)
    batch_peak_df.to_csv(
        user_temp_path(user_id, peakfile_name),
        index=False,
        sep=";",
    )
    message = f"Peak data for sample batch '{sample_batch_name}' was exported to file '{peakfile_name}'."
    runtime.logger.info(message)

    # Return the status message
    return {
        "message": message,
        "data": {"filename": peakfile_name},
        "_notification_data": {
            "sample_batch_id": sample_batch_id,
            "download": peakfile_name,
        },
    }


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def get_sample_batch_peaks(
    sample_batch_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
):
    """
    Retrieves aligned peak data for all sample items within a specified sample batch.

    Algorithm Steps:
    - Validate the existence of the sample batch.
    - Attempt to load existing cached peak data for the batch.
    - If no cached data is found, fetch all sample items in the batch.
    - Validate that all sample items belong to the same instrument type.
    - Infer the appropriate intensity variable based on the instrument type.
    - Load resolution functions for each sample file.
    - Prepare CentroidedSpectrum objects for each sample item, grouped by ionization mode.
    - Align peaks for each ionization mode using the MassSpecAligner (virtual lock mass).
    - Compile aligned peak data into a structured response.

    :param sample_batch_id: ID of the sample batch to retrieve peak data from.
    :type sample_batch_id: str
    :param independent_transaction: Flag indicating if the operation is an independent transaction, defaults to False.
    :type independent_transaction: bool, optional
    :param sid: Session ID for targeting clients when emitting events, defaults to None.
    :type sid: str, optional
    :param process_id: Process ID for tracking the operation, defaults to None.
    :type process_id: str, optional
    :param parent_id: Parent process ID for tracking the operation, defaults to None.
    :type parent_id: str, optional
    :return: Aligned peak data including m/z values, intensities, and alignment ranges.
    :rtype: dict
    """
    # --- Validate batch existence --- #
    batch_response = await get_sample_batch(sample_batch_id)
    sample_batch = batch_response.get("data")
    sample_batch_id = sample_batch["sample_batch_id"]

    # --- Try to load batch peak cache --- #
    try:
        batch_data_response = load_existing_batch_cache(sample_batch)
        runtime.logger.info("Loaded existing batch peaks.")
        return batch_data_response
    except FileNotFoundError:
        runtime.logger.info(
            "No existing batch peaks found. "
            "Computing and aggregating aligned peaks from batch samples."
        )

    # --- Fetch sample items in the batch --- #
    spectra, intensity_variable = await collect_spectra_per_ionization_mode(
        sample_batch_id
    )

    # --- Align and sum spectra per ionization mode --- #
    runtime.logger.debug("Aligning and summing sample spectra...")
    peak_per_mode = dict()
    vlm_min_mzs, vlm_max_mzs = set(), set()
    for ion_mode, specs in spectra.items():
        peak_collection = Spectra(specs, timestamps=np.arange(len(specs)))

        aligned_peak_sum, vlm_min_mz, vlm_max_mz = m_compute.sum_peak_collection(
            peak_collection
        )
        peak_per_mode[ion_mode] = aligned_peak_sum
        vlm_min_mzs.add(vlm_min_mz)
        vlm_max_mzs.add(vlm_max_mz)

    # --- Concatenate spectra from different ionization modes --- #
    combined_mz = np.array([])
    combined_intensity = np.array([])
    combined_peak_ids = np.array([])
    for ion_mode, peaks in peak_per_mode.items():
        combined_mz = np.concatenate((combined_mz, peaks.mz))
        combined_intensity = np.concatenate((combined_intensity, peaks.intensity))
        combined_peak_ids = np.concatenate((combined_peak_ids, peaks.peak_id))

    # --- Sort by m/z --- #
    sorted_indices = np.argsort(combined_mz)
    combined_mz = combined_mz[sorted_indices]
    combined_intensity = combined_intensity[sorted_indices]
    combined_peak_ids = combined_peak_ids[sorted_indices]

    # Can't store 2D array as variable, join peak IDs per m/z into comma-separated strings
    combined_peak_ids = [",".join(id_list) for id_list in combined_peak_ids]
    sample_batch_utc_modified = str(sample_batch["sample_batch_utc_modified"])

    # --- Save batch peaks --- #
    runtime.logger.debug("Saving batch peaks cache...")
    batch_peaks = xr.Dataset(
        {
            "intensity": (("mz",), combined_intensity),
            "peak_id": (("mz",), combined_peak_ids),
        },
        coords={
            "mz": (("mz",), combined_mz),
        },
        attrs={
            "sample_batch_utc_modified": sample_batch_utc_modified,
            "min_aligned_mz": float(max(vlm_min_mzs)),
            "max_aligned_mz": float(min(vlm_max_mzs)),
            "intensity_variable": intensity_variable,
        },
    )
    m_io.write_batch_cache(sample_batch_id, "peaks", batch_peaks)
    runtime.logger.debug("Batch peaks cache saved.")

    # --- Return aggregated peak data --- #
    return {
        "data": {
            "mzs": combined_mz.tolist(),
            "intensities": combined_intensity.tolist(),
            "peak_ids": combined_peak_ids,
            "min_aligned_mz": max(vlm_min_mzs),
            "max_aligned_mz": min(vlm_max_mzs),
            "intensity_variable": intensity_variable,
        },
        "message": f"Retrieved aggregated peak data for sample batch with ID '{sample_batch_id}'.",
    }
