from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    DuplicateException,
    NotFoundException,
)
from mascope_backend.api.models.dataset.config import dataset_config
from mascope_backend.api.models.dataset.dataset_pydantic_model import (
    DatasetCreate,
    DatasetRead,
    DatasetUpdate,
)
from mascope_backend.api.new.workspaces.exceptions import (
    WorkspaceNotFoundException,
)
from mascope_backend.db import Dataset, Workspace, async_session
from mascope_backend.db.id import gen_id
from mascope_backend.socket.records import (
    emit_record_created,
    emit_record_deleted,
    emit_record_reload,
    emit_record_updated,
)


async def _assert_name_available(
    session: AsyncSession,
    workspace_id: str,
    dataset_name: str,
    exclude_dataset_id: str | None = None,
) -> None:
    """Refuse a dataset name that is already taken in this workspace.

    Both sides of the comparison are lower-cased and trimmed, so neither a
    difference in case nor surrounding spaces buys a second dataset that
    reads as the same row in the workspace list. Trimming the stored side
    matters as much as trimming the proposed one: names have only been
    stripped on the way in since this check existed - the validator used to
    reject a whitespace-only name and keep every other name verbatim - so
    older rows can still carry padding, while `DatasetRead` renders every
    name stripped. This mirrors the workspace name check in
    `api/new/workspaces/service.py`.

    The two sides trim differently, and the gap is deliberate rather than
    overlooked: SQL `trim()` removes spaces, while Python's `str.strip()`
    removes every kind of whitespace. A legacy row stored with a leading tab
    therefore still renders stripped and still evades this check. Closing
    that needs the same backfill a unique index would, so it is left with the
    index rather than half-solved here.

    The check is a read followed by a write in a separate statement, so two
    concurrent creates can both pass it and both insert. There is no unique
    index on (workspace_id, dataset_name) to catch the loser - closing that
    race needs one, which is deliberately not part of this change. A
    workspace can therefore already hold two datasets sharing a name: from a
    lost race, from before this check existed, or from ACQUISITION rows,
    which are inserted without coming through here at all. Callers have to
    stay usable in that state rather than assume it away.

    :param session: The session the caller is about to write through.
    :type session: AsyncSession
    :param workspace_id: The workspace the name must be unique within.
    :type workspace_id: str
    :param dataset_name: The proposed name.
    :type dataset_name: str
    :param exclude_dataset_id: Dataset to ignore when matching, defaults to
                               None. A guard rather than a requirement for
                               today's callers: `update_dataset` skips this
                               call entirely when the submitted name is the
                               dataset's own, and `move_dataset` searches a
                               workspace the dataset is not in yet, so
                               neither can match itself even without it.
    :type exclude_dataset_id: str | None, optional
    :raises DuplicateException: If another dataset in the workspace already
                                carries the name.
    """
    stmt = select(Dataset.dataset_id).where(
        Dataset.workspace_id == workspace_id,
        func.lower(func.trim(Dataset.dataset_name)) == dataset_name.strip().lower(),
    )
    if exclude_dataset_id is not None:
        stmt = stmt.where(Dataset.dataset_id != exclude_dataset_id)

    if (await session.execute(stmt)).first() is not None:
        raise DuplicateException(
            f"A dataset named '{dataset_name.strip()}' already exists in this workspace."
        )


@api_controller()
async def get_datasets(
    workspace_id: str | None = None,
    dataset_name: str | None = None,
    dataset_type: list[str] | None = None,
    instrument: list[str] | None = None,
    sort: str = "dataset_utc_created",
    order: str = "asc",
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a paginated list of datasets, optionally sorted by a specified column in
    either ascending or descending order.

    Steps:
    1. Construct a SQLAlchemy query to select all datasets.
    2. Apply sorting if specified by the sort and order parameters.
    3. Apply pagination based on the page and limit parameters.
    4. Execute the query to fetch the results.
    5. Convert the results into a list of dictionaries for JSON serialization.

    :param workspace_id: Optional workspace ID to filter datasets by their associated
                         workspace.
    :type workspace_id: str | None, optional
    :param dataset_name: Filter datasets by name, defaults to None
    :type dataset_name: str | None, optional
    :param dataset_type: Filter datasets by type (ACQUISITION or ANALYSIS), defaults to
                         None
    :type dataset_type: list[str] | None, optional
    :param instrument: Filter datasets by instrument associated with the dataset,
                       defaults to None
    :type instrument: list[str] | None, optional
    :param sort: Column to sort by, defaults to "dataset_utc_created"
    :type sort: str, optional
    :param order: Sorting order ('asc' for ascending, 'desc' for descending),
                  defaults to "asc"
    :type order: str, optional
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None, optional
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None, optional
    :return: A dictionary with the total count and a list of datasets.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        stmt = select(Dataset)

        # Filter by workspace if specified (routes always provide this;
        # internal/system callers may omit for cross-workspace queries)
        if workspace_id is not None:
            stmt = stmt.filter(Dataset.workspace_id == workspace_id)

        # Step 1: Filter by provided parameters
        if dataset_name:
            stmt = stmt.filter(Dataset.dataset_name == dataset_name)

        if dataset_type:
            stmt = stmt.filter(Dataset.dataset_type.in_(dataset_type))

        if instrument:
            stmt = stmt.filter(Dataset.instrument.in_(instrument))

        # Step 2: Apply sorting if specified
        if sort:
            if order == "desc":
                stmt = stmt.order_by(desc(getattr(Dataset, sort)))
            else:
                stmt = stmt.order_by(asc(getattr(Dataset, sort)))

        # Step 3: Get total count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = await session.scalar(count_stmt)

        # Step 4: Apply pagination
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)

        # Step 5: Execute the query
        result = await session.execute(stmt)
        datasets = result.scalars().all()

    # Step 6: Return the total count and the list of validated datasets
    return {
        "message": "Datasets retrieved successfully",
        "results": total,
        "data": [
            DatasetRead.model_validate(dataset).model_dump() for dataset in datasets
        ],
    }


@api_controller()
async def get_dataset(dataset_id: str, workspace_id: str | None = None) -> dict:
    """
    Retrieves a single dataset by its unique ID.

    Steps:
    1. Execute a query to fetch the dataset with the specified ID.
    2. Check if the dataset exists. If not, raise a NotFoundException.
    3. Verify the dataset belongs to the specified workspace.
    4. Return the dataset's details as a dictionary.

    :param dataset_id: Unique identifier of the dataset to retrieve.
    :type dataset_id: str
    :param workspace_id: ID of the workspace the dataset must belong to.
    :type workspace_id: str
    :raises NotFoundException: If the dataset with the given ID is not found.
    :return: The requested dataset's details.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch dataset by ID
        dataset = await session.get(Dataset, dataset_id)

        if not dataset or (
            workspace_id is not None and dataset.workspace_id != workspace_id
        ):
            # Step 2: If dataset not found or wrong workspace, raise exception
            raise NotFoundException(f"Dataset with ID '{dataset_id}' not found")

    # Step 3: Return dataset details
    return {
        "message": f"Dataset '{dataset.dataset_name}' retrieved successfully",
        "data": DatasetRead.model_validate(dataset).model_dump(),
    }


@api_controller()
async def create_dataset(
    workspace_id: str,
    dataset: DatasetCreate,
    independent_transaction: bool = False,
) -> dict:
    """
    Creates a new dataset with the specified details.

    Steps:
    1. Refuse a name another dataset in the workspace already carries.
    2. Create a new Dataset object with the provided details and the generated ID.
    3. Add the new dataset to the session and commit the changes to the database.
    4. Emit a signal to inform clients about the creation of the new dataset.
    5. Return the details of the created dataset.

    :param workspace_id: The ID of the workspace to which the dataset belongs.
    :type workspace_id: str
    :param dataset: Dataset creation details from the request body.
    :type dataset: DatasetCreate
    :param independent_transaction: Flag to indicate if the operation should be treated
                                    as an independent transaction, defaults to False.
    :type independent_transaction: bool, optional
    :raises DuplicateException: If the workspace already has a dataset with
                                this name (ignoring case and surrounding
                                spaces).
    :return: The created dataset's details.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Refuse a name already used in this workspace
        await _assert_name_available(session, workspace_id, dataset.dataset_name)

        # Step 2: Generate unique ID and create new dataset
        new_dataset = Dataset(
            dataset_id=gen_id(16),
            workspace_id=workspace_id,
            **dataset.model_dump(),
            locked=(
                1
                if dataset.dataset_type == "ACQUISITION"
                and dataset_config.ACQUISITION_AUTO_LOCK
                else 0
            ),  # Auto-lock acquisition datasets
            dataset_utc_created=datetime.now(timezone.utc),
        )

        # Step 3: Add to session and commit
        session.add(new_dataset)
        await session.commit()
        await session.refresh(new_dataset)

    # Step 4: Emit creation event to all clients
    dataset_data = DatasetRead.model_validate(new_dataset).model_dump()
    if independent_transaction:
        await emit_record_created(
            record_type="dataset",
            record_id=new_dataset.dataset_id,
            record=dataset_data,
            room=workspace_id,
        )

    # Step 5: Return the new dataset details
    return {
        "message": f"Dataset '{new_dataset.dataset_name}' created successfully.",
        "data": dataset_data,
    }


@api_controller()
async def update_dataset(
    dataset_id: str,
    dataset_update: DatasetUpdate,
    workspace_id: str | None = None,
    independent_transaction: bool = False,
) -> dict:
    """
    Updates an existing dataset with new data provided in the dataset update request
    body.

    Steps:
    1. Fetch the existing dataset by its ID from the database.
    2. Refuse a rename onto a name the workspace already uses. A name equal to
       the dataset's own is not a rename and is not checked.
    3. If the dataset is found, update its properties with the new data provided.
    4. Set the dataset's modification timestamp to the current UTC time.
    5. Commit the updated dataset to the database.
    6. Emit socket.io events to inform clients about the dataset update.

    :param dataset_id: The unique identifier of the dataset to update.
    :type dataset_id: str
    :param dataset_update: The new data for the dataset update.
    :type dataset_update: DatasetUpdate
    :param workspace_id: The workspace the dataset belongs to (optional, used for
                         validation).
    :type workspace_id: str | None, optional
    :param independent_transaction: Flag indicating if operation is independent
                                    transaction, defaults to False.
    :type independent_transaction: bool, optional
    :raises NotFoundException: If no dataset is found with the provided ID.
    :raises DuplicateException: If the rename would collide with another
                                dataset in the same workspace.
    :return: The updated dataset data as a dictionary.
    :rtype: dict
    """
    # Step 1: Fetch the existing dataset
    async with async_session() as session:
        update_data = dataset_update.model_dump(exclude_unset=True)
        existing_dataset = await session.get(Dataset, dataset_id)
        if not existing_dataset or (
            workspace_id is not None and existing_dataset.workspace_id != workspace_id
        ):
            raise NotFoundException(f"Dataset with ID '{dataset_id}' not found")

        # Step 2: Validate ACQUISITION dataset constraints
        if (
            existing_dataset.dataset_type == "ACQUISITION"
            and "dataset_name" in update_data
        ):
            # Acquisition datasets are system-managed; prevent renaming.
            raise ValueError(
                "Acquisition dataset names are managed by the system and cannot be renamed."
            )

        # Step 2b: Refuse a rename onto a name already used in this workspace.
        # Only a name that actually changes is checked, compared the way the
        # check itself compares - ignoring case and padding. The edit dialog
        # always submits the name field, and a workspace can already hold two
        # datasets sharing a name (nothing has ever prevented it: there is no
        # unique index), so querying on an unchanged name would refuse a
        # description-only edit, naming a field the user never touched.
        current_name_key = existing_dataset.dataset_name.strip().lower()
        new_name = update_data.get("dataset_name")
        if new_name is not None and new_name.strip().lower() != current_name_key:
            await _assert_name_available(
                session,
                existing_dataset.workspace_id,
                new_name,
                exclude_dataset_id=dataset_id,
            )

        # Step 3: Update the dataset properties
        for key, value in update_data.items():
            setattr(existing_dataset, key, value)

        # Step 4: Update modification timestamp
        existing_dataset.dataset_utc_modified = datetime.now(timezone.utc)

        # Step 5: Commit the updates
        await session.commit()
        await session.refresh(existing_dataset)

    # Step 6: Emit update event to all clients
    dataset_data = DatasetRead.model_validate(existing_dataset).model_dump()
    if independent_transaction:
        await emit_record_updated(
            record_type="dataset",
            record_id=dataset_id,
            record=dataset_data,
            room=existing_dataset.workspace_id,
        )

    return {
        "message": f"Dataset '{existing_dataset.dataset_name}' updated successfully.",
        "data": dataset_data,
    }


@api_controller()
async def delete_dataset(
    dataset_id: str,
    workspace_id: str | None = None,
    independent_transaction: bool = False,
) -> dict:
    """
    Deletes a dataset by its unique identifier.

    Steps:
    1. Fetch the dataset by its ID from the database.
    2. If the dataset is found, delete it from the session and commit the changes to
       the database.
    3. Emit socket.io events to inform clients about the dataset deletion.

    :param dataset_id: The unique identifier of the dataset to delete.
    :type dataset_id: str
    :param independent_transaction: Flag indicating if operation is independent
                                    transaction, defaults to False.
    :type independent_transaction: bool, optional
    :raises NotFoundException: If no dataset is found with the provided ID.
    """
    # Step 1: Fetch the dataset
    async with async_session() as session:
        dataset = await session.get(Dataset, dataset_id)
        if not dataset or (
            workspace_id is not None and dataset.workspace_id != workspace_id
        ):
            raise NotFoundException(f"Dataset with ID '{dataset_id}' not found")

        # Step 2: Delete the dataset and commit changes
        await session.delete(dataset)
        await session.commit()

    # Step 3: Emit deletion event to all clients
    dataset_name = dataset.dataset_name
    if independent_transaction:
        await emit_record_deleted(
            record_type="dataset",
            record_id=dataset_id,
            room=dataset.workspace_id,
        )

    return {
        "message": f"Dataset '{dataset_name}' deleted successfully.",
    }


@api_controller()
async def move_dataset(
    dataset_id: str,
    source_workspace_id: str,
    target_workspace_id: str,
    independent_transaction: bool = False,
) -> dict:
    """
    Move a dataset into another workspace by reassigning its workspace_id.

    No child rows are modified: batches and samples reference the dataset, and
    workspace ACL resolves dataset -> workspace at query time, so the entire
    subtree's access flips on this single foreign-key write.

    Source ownership is re-verified inside this transaction (not only at the
    route level) to close a TOCTOU window between authorization and mutation.

    Steps:
    - Fetch the dataset and verify it still belongs to the source workspace.
    - Reject ACQUISITION datasets, which are auto-managed across workspaces.
    - Reject a no-op move where the target equals the source.
    - Validate the target workspace exists, is non-system and active.
    - Reject a move whose name is already taken in the target workspace.
    - Reassign workspace_id, bump the modified timestamp and commit.
    - Broadcast a dataset reload so clients re-fetch their workspace list.

    :param dataset_id: The unique identifier of the dataset to move.
    :type dataset_id: str
    :param source_workspace_id: The workspace the dataset must currently belong
                                to (verified inside the move transaction).
    :type source_workspace_id: str
    :param target_workspace_id: The workspace to move the dataset into.
    :type target_workspace_id: str
    :param independent_transaction: Emit a socket reload when standalone,
                                    defaults to False.
    :type independent_transaction: bool, optional
    :raises NotFoundException: If the dataset is missing or no longer in the
                               source workspace.
    :raises WorkspaceNotFoundException: If the target workspace does not exist.
    :raises DuplicateException: If the target workspace already has a dataset
                                with this name.
    :raises HTTPException: 400 for ACQUISITION datasets, no-op moves, or an
                           inactive target; 403 for a system target.
    :return: The moved dataset's details.
    :rtype: dict
    """
    async with async_session() as session:
        # --- Fetch and verify source ownership in the same transaction ---
        dataset = await session.get(Dataset, dataset_id)
        if not dataset or dataset.workspace_id != source_workspace_id:
            raise NotFoundException(f"Dataset with ID '{dataset_id}' not found")

        # --- Acquisition datasets are auto-managed - never relocate ---
        if dataset.dataset_type == "ACQUISITION":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Acquisition datasets cannot be moved between workspaces.",
            )

        # --- Reject no-op move explicitly (client error, not silent pass) ---
        if target_workspace_id == source_workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dataset is already in the target workspace.",
            )

        # --- Validate the target workspace exists and is active/non-system ---
        target = await session.get(Workspace, target_workspace_id)
        if target is None:
            raise WorkspaceNotFoundException(target_workspace_id)
        if target.is_system:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot move datasets into a system workspace.",
            )
        if target.workspace_status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot move datasets into an archived workspace.",
            )

        # --- Refuse a move that would duplicate a name in the target ---
        await _assert_name_available(
            session,
            target_workspace_id,
            dataset.dataset_name,
            exclude_dataset_id=dataset_id,
        )

        # --- Reassign workspace and bump modification timestamp ---
        dataset.workspace_id = target_workspace_id
        dataset.dataset_utc_modified = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(dataset)

    # --- Reload so both source and target workspace lists re-fetch updated data ---
    dataset_data = DatasetRead.model_validate(dataset).model_dump()
    if independent_transaction:
        await emit_record_reload(record_type="dataset", room=source_workspace_id)
        await emit_record_reload(record_type="dataset", room=target_workspace_id)

    return {
        "message": f"Dataset '{dataset.dataset_name}' moved successfully.",
        "data": dataset_data,
    }
