import asyncio

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import joinedload

from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.target.collections.lib.util import (
    detect_target_collection_changes,
    validate_batch_scope_for_collection,
    validate_scope_change,
)
from mascope_backend.api.controllers.target.compounds.target_compounds_controller import (
    create_target_compound,
    delete_target_compound,
)
from mascope_backend.api.controllers.target.lib.fetch.target_collections_fetch import (
    fetch_target_collection,
    validate_sample_batches_for_collection,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    NotFoundException,
)
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    TargetCollectionCreate,
    TargetCollectionSortColumn,
    TargetCollectionUpdate,
)
from mascope_backend.db import (
    IonizationMode,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import (
    emit_record_reload,
)


@api_controller()
async def get_target_collections(
    target_collection_name: str | None = None,
    sample_batch_id: str | None = None,
    target_collection_type: list[str] | None = None,
    workspace_id: str | None = None,
    accessible_workspace_ids: set[str] | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a paginated list of target collections, optionally sorted by a specified
    column in either ascending or descending order.

    Steps:
    - Construct a SQLAlchemy query to select all target collections.
    - Apply filtering if specified by the parameters.
    - Apply sorting if specified by the sort and order parameters.
    - Apply pagination based on the page and limit parameters.
    - Execute the query to fetch the results.
    - Convert the results into a list of dictionaries for JSON serialization.

    :param target_collection_name: The name of the target collection for which you want
                                   to fetch the target collections, defaults to None
    :type target_collection_name: str | None, optional
    :param sample_batch_id: Filter collections associated with a specific sample batch
                            ID, defaults to None.
    :type sample_batch_id: str | None, optional
    :param workspace_id: Filter collections by workspace ID. Null-workspace (global)
                         collections are always included. Defaults to None.
    :type workspace_id: str | None, optional
    :param target_collection_type: Filter by target collection types, can specify
    multiple types, defaults to None
    :type target_collection_type: list[str] | None, optional
    :param sort:  Column to sort by, defaults to "sample_item_utc_created"
    :type sort: str | None, optional
    :param order: Sorting order ('asc' for ascending, 'desc' for descending), defaults
                  to "asc"
    :type order: str | None, optional
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None, optional
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None, optional
    :return: A dictionary with the total count and a list of target collections.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        stmt = select(TargetCollection)

        # Apply filters if specified
        if target_collection_name:
            stmt = stmt.where(
                TargetCollection.target_collection_name == target_collection_name
            )

        if sample_batch_id:
            stmt = stmt.join(
                TargetCollectionInSampleBatch,
                TargetCollectionInSampleBatch.target_collection_id
                == TargetCollection.target_collection_id,
            ).where(TargetCollectionInSampleBatch.sample_batch_id == sample_batch_id)

        if target_collection_type:
            stmt = stmt.where(
                TargetCollection.target_collection_type.in_(target_collection_type)
            )

        if workspace_id:
            stmt = stmt.where(
                or_(
                    TargetCollection.workspace_id == workspace_id,
                    TargetCollection.workspace_id.is_(None),
                )
            )

        # Restrict to collections the caller can see (workspace ACL).
        # accessible_workspace_ids is None for superusers (no restriction).
        if accessible_workspace_ids is not None:
            stmt = stmt.where(
                or_(
                    TargetCollection.workspace_id.in_(accessible_workspace_ids),
                    TargetCollection.workspace_id.is_(None),
                )
            )
        # Apply sorting if specified
        if sort:
            stmt = stmt.order_by(
                order_by_column(
                    TargetCollection, sort, order, TargetCollectionSortColumn
                )
            )

        # Get total count for pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = await session.scalar(count_stmt)

        # Apply pagination
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        result = await session.execute(stmt)
        target_collections = result.scalars().all()

        return {
            "message": "Target collections retrieved successfully.",
            "results": total,
            "data": [
                target_collection.to_dict() for target_collection in target_collections
            ],
        }


@api_controller()
async def get_target_collection(target_collection_id: str) -> dict:
    """
    Retrieves a detailed targert collection by its unique ID.

    Steps:
    1. Initialize a database session and construct a query to fetch the target
       collection by the specified ID, including associated target compounds and sample
       batches.
    2. Execute the query and retrieve the first result.
    3. Check if the target collection exists. If not, raise a NotFoundException.
    4. Convert the target collection and its associations (target compounds and sample
       batches) to dictionaries.
    5. Return the target collection's details as a dictionary.

    :param target_collection_id: Unique identifier of the target collection to retrieve.
    :type target_collection_id: str
    :raises NotFoundException: If the target collection with the given ID is not found.
    :return: The requested target collection's details including associated target
             compounds and sample batches.
    :rtype: dict
    """
    # Step 1: Initialize session and construct query
    async with async_session() as session:
        stmt = (
            select(TargetCollection)
            .options(
                # Load target compounds associated with target collection
                joinedload(TargetCollection.target_compound).joinedload(
                    TargetCompoundInTargetCollection.target_compound
                ),
                # Load sample batches associated with target collection
                joinedload(TargetCollection.sample_batch).joinedload(
                    TargetCollectionInSampleBatch.sample_batch
                ),
            )
            .filter(TargetCollection.target_collection_id == target_collection_id)
        )
        # Step 2: Execute query and fetch result
        result = await session.execute(stmt)
        target_collection = result.scalars().first()

        # Step 3: Check for target collection existence
        if not target_collection:
            raise NotFoundException(
                f"Target collection with ID '{target_collection_id}' not found"
            )

        # Step 4: Convert target collection and associations to dictionaries
        target_collection_dict = target_collection.to_dict()

        # Add associated target compounds to the target collection dictionary
        target_compounds = [
            tc.target_compound.to_dict() for tc in target_collection.target_compound
        ]

        # Add associated sample batches to the target collection dictionary
        sample_batches = [
            {
                "dataset_id": sb.sample_batch.dataset_id,
                "sample_batch_id": sb.sample_batch.sample_batch_id,
                "sample_batch_name": sb.sample_batch.sample_batch_name,
            }
            for sb in target_collection.sample_batch
        ]

        target_collection_dict["target_compounds_count"] = len(target_compounds)
        target_collection_dict["sample_batches_count"] = len(sample_batches)
        target_collection_dict["target_compounds"] = target_compounds
        target_collection_dict["sample_batches"] = sample_batches

        # Step 5: Return target collection details
        return {
            "message": (
                "Details for target collection "
                f"'{target_collection.target_collection_name}' retrieved."
            ),
            "data": target_collection_dict,
        }


@api_controller()
async def create_target_collection(
    target_collection_create: TargetCollectionCreate,
    independent_transaction: bool = False,
) -> dict:
    """
    Creates a new target collection with associated compounds and sample batches.
    Affected sample batches are set to "rematch" status.

    Steps:
    1. Validate sample batch type constraints
    2. Create new target compounds if provided
    3. Verify existing target compound IDs and combine with created IDs
    4. Create new target collection with basic fields
    5. Associate compounds and sample batches with the collection
    6. Set rematch status for affected sample batches
    7. Emit appropriate reload events

    :param target_collection_create: Data for creating the new target collection
    :type target_collection_create: TargetCollectionCreate
    :param independent_transaction: Controls transaction and sio event behavior
    :type independent_transaction: bool
    :return: Created target collection data with success message
    :rtype: dict
    """
    # Unpack create fields
    sample_batch_ids = target_collection_create.sample_batch_ids or []
    target_compound_ids = target_collection_create.target_compound_ids or []
    target_compounds_to_create = target_collection_create.target_compounds_create or []

    # Step 1: Validate sample batch type and scope constraints
    if sample_batch_ids:
        await validate_sample_batches_for_collection(
            sample_batch_ids=sample_batch_ids,
            target_collection_type=target_collection_create.target_collection_type,
        )
        await validate_batch_scope_for_collection(
            sample_batch_ids=sample_batch_ids,
            workspace_id=target_collection_create.workspace_id,
        )

    # Step 2: Create new target compounds if provided
    created_target_compound_ids = []
    if target_compounds_to_create:
        compounds_result = await create_target_compound(
            target_compounds=target_compounds_to_create,
            independent_transaction=True,
        )
        created_target_compound_ids = compounds_result.get("target_compound_ids", [])

    # Step 3: Verify existing target compound IDs and combine with created IDs
    verified_target_compound_ids = []
    if target_compound_ids:
        async with async_session() as session:
            existing_compounds = set(
                (
                    await session.execute(
                        select(TargetCompound.target_compound_id).where(
                            TargetCompound.target_compound_id.in_(target_compound_ids)
                        )
                    )
                ).scalars()
            )
            verified_target_compound_ids = list(existing_compounds)

            # Log excluded compounds if any
            excluded_count = len(set(target_compound_ids) - existing_compounds)
            if excluded_count > 0:
                runtime.logger.info(
                    f"{excluded_count} added compound ID(s) were not found in the db"
                )

    # Combine all (created and added) compound IDs
    # Empty set is valid - allows creating an empty collection to be
    # populated later via peak assignment or update.
    all_target_compound_ids = set(
        created_target_compound_ids + verified_target_compound_ids
    )

    # Step 4: Create new target collection with associations
    async with async_session() as session:
        # Create the target collection
        new_collection = TargetCollection(
            target_collection_id=gen_id(16),
            target_collection_name=target_collection_create.target_collection_name,
            target_collection_description=target_collection_create.target_collection_description,
            target_collection_type=target_collection_create.target_collection_type,
            workspace_id=target_collection_create.workspace_id,
        )
        session.add(new_collection)

        # Step 5: Associate compounds with the collection
        for target_compound_id in all_target_compound_ids:
            session.add(
                TargetCompoundInTargetCollection(
                    target_compound_id=target_compound_id,
                    target_collection_id=new_collection.target_collection_id,
                )
            )

        # Associate sample batches with the collection
        for sample_batch_id in sample_batch_ids:
            session.add(
                TargetCollectionInSampleBatch(
                    target_collection_id=new_collection.target_collection_id,
                    sample_batch_id=sample_batch_id,
                )
            )

        await session.commit()
        await session.refresh(new_collection)

    # Step 6: Set rematch status for affected sample batches
    # Skip when collection is empty - no targets changed
    batch_status_result = None
    if sample_batch_ids and all_target_compound_ids:
        batch_status_result = await update_sample_batch_status(
            sample_batch_ids=sample_batch_ids,
            status="rematch",
            independent_transaction=True,
        )

    # Step 7: Emit reload events
    if independent_transaction:
        # Update target.collection list
        await emit_record_reload(record_type="target_collection")

        # Update match.collection list (MatchBrowser table)
        for batch_id in sample_batch_ids:
            await emit_record_reload(
                record_type="match_collection",
                room=batch_id,
            )

    # Fetch the created collection with associations for return
    created_collection = await fetch_target_collection(
        new_collection.target_collection_id
    )

    # Generate summary message
    message = (
        f"Target collection '{created_collection.target_collection_name}' was created"
    )

    if created_target_compound_ids:
        count = len(created_target_compound_ids)
        message += f", {count} target compound{'s' if count != 1 else ''} created"

    if sample_batch_ids and batch_status_result:
        message = f"{message}. {batch_status_result.get('message', '')}"

    runtime.logger.info(message)

    return {
        "status": "success",
        "message": message,
        "data": created_collection,
    }


@api_controller()
async def update_target_collection(
    target_collection_id: str,
    target_collection_update: TargetCollectionUpdate,
    independent_transaction: bool = False,
) -> dict:
    """
    Updates a target collection's basic fields and modifies its associated
    sample batches or target compounds. Changes that affect matching set
    affected batches to "rematch" status.

    Steps:
    1. Fetch existing target collection with associations
    2. Validate new configurations when provided
    3. Create new target compounds if provided
    4. Verify existing target compound IDs and combine with created IDs
    5. Detect changes between current and proposed state
    6. Update collection data and associations
    7. Set rematch status for affected sample batches
    8. Emit appropriate reload events based on change types

    :param target_collection_id: ID of the target collection to update
    :type target_collection_id: str
    :param target_collection_update: Updated data for the target collection
    :type target_collection_update: TargetCollectionUpdate
    :param independent_transaction: Controls transaction and event behavior
    :type independent_transaction: bool
    :raises NotFoundException: When target collection is not found
    :raises ValueError: When validation fails
    :return: Updated target collection data with success message
    :rtype: dict
    """
    # Unpack update fields
    sample_batches_update = target_collection_update.sample_batch_ids
    target_compounds_update = target_collection_update.target_compound_ids
    target_compounds_to_create = target_collection_update.target_compounds_create

    # Step 1: Fetch existing collection with associations
    target_collection_db = await fetch_target_collection(target_collection_id)
    sample_batches_db = {
        association.sample_batch_id for association in target_collection_db.sample_batch
    }

    # Step 1b: Validate scope change against existing batch associations
    if "workspace_id" in target_collection_update.model_fields_set:
        await validate_scope_change(
            target_collection_db, target_collection_update.workspace_id
        )

    # Step 2: Validate sample batch type constraints for new sample batch assignments
    if sample_batches_update is not None:
        # Use new collection type if being updated, otherwise existing
        collection_type = (
            target_collection_update.target_collection_type
            if target_collection_update.target_collection_type
            else target_collection_db.target_collection_type
        )
        await validate_sample_batches_for_collection(
            sample_batch_ids=sample_batches_update,
            target_collection_type=collection_type,
        )
        # Batches must stay within the collection's (possibly updated) workspace
        effective_workspace_id = (
            target_collection_update.workspace_id
            if "workspace_id" in target_collection_update.model_fields_set
            else target_collection_db.workspace_id
        )
        await validate_batch_scope_for_collection(
            sample_batch_ids=sample_batches_update,
            workspace_id=effective_workspace_id,
        )

    # Step 3: Create new target compounds if provided
    created_target_compound_ids = []
    if target_compounds_to_create and len(target_compounds_to_create) > 0:
        created_target_compound_ids = (
            await create_target_compound(
                target_compounds=target_compounds_to_create,
                independent_transaction=True,
            )
        ).get("target_compound_ids", [])

    # Step 4: Verify provided target compound IDs and combine with created IDs
    verified_target_compound_ids = []
    if target_compounds_update:
        async with async_session() as session:
            existing_compounds = set(
                (
                    await session.execute(
                        select(TargetCompound.target_compound_id).where(
                            TargetCompound.target_compound_id.in_(
                                target_compounds_update
                            )
                        )
                    )
                ).scalars()
            )
            verified_target_compound_ids = list(existing_compounds)
            # Log excluded compounds if any
            excluded_compounds_count = len(
                set(target_compounds_update) - set(verified_target_compound_ids)
            )
            if excluded_compounds_count > 0:
                runtime.logger.info(
                    f"{excluded_compounds_count} provided compound ID(s) were not found"
                )

    # Step 5: Detect changes between current and proposed state
    updated_compound_ids = None
    if target_compounds_update is not None or target_compounds_to_create:
        updated_compound_ids = set(
            created_target_compound_ids + verified_target_compound_ids
        )

    changes = detect_target_collection_changes(
        target_collection_db, target_collection_update, updated_compound_ids
    )

    # Collect all affected sample batches based on change type
    affected_sample_batch_ids = set()

    if changes["compounds"]:
        # For compound changes, all currently associated batches need rematch
        affected_sample_batch_ids.update(sample_batches_db)

    if changes["batches"]:
        # For batch changes, only the batches being added/removed need rematch
        affected_sample_batch_ids.update(changes["batches_to_add"])
        affected_sample_batch_ids.update(changes["batches_to_remove"])

    if any([changes["collection_type"], changes["basic_fields"]]):
        # For basic field changes, all batches (current + new) need rematch
        affected_sample_batch_ids.update(sample_batches_db)
        if changes["batches"] and target_collection_update.sample_batch_ids:
            affected_sample_batch_ids.update(target_collection_update.sample_batch_ids)

    # Step 6: Update collection data and associations
    async with async_session() as session:
        # Re-fetch collection in this session for updates
        target_collection_db = await session.get(
            TargetCollection,
            target_collection_id,
            options=[
                joinedload(TargetCollection.sample_batch),
                joinedload(TargetCollection.target_compound),
            ],
        )
        if not target_collection_db:
            raise NotFoundException(
                f"Target collection '{target_collection_id}' not found"
            )

        # Update basic fields
        basic_fields = target_collection_update.model_dump(
            exclude={
                "target_compound_ids",
                "sample_batch_ids",
                "target_compounds_create",
            },
            exclude_unset=True,
        )
        for field, value in basic_fields.items():
            if hasattr(target_collection_db, field):
                setattr(target_collection_db, field, value)

        # Update sample batch associations if changed
        if changes["batches"]:
            if changes["batches_to_remove"]:
                await session.execute(
                    delete(TargetCollectionInSampleBatch).where(
                        and_(
                            TargetCollectionInSampleBatch.target_collection_id
                            == target_collection_id,
                            TargetCollectionInSampleBatch.sample_batch_id.in_(
                                changes["batches_to_remove"]
                            ),
                        )
                    )
                )
            for sample_batch_id in changes["batches_to_add"]:
                session.add(
                    TargetCollectionInSampleBatch(
                        target_collection_id=target_collection_id,
                        sample_batch_id=sample_batch_id,
                    )
                )

        # Update target compound associations if changed
        if changes["compounds"]:
            if changes["compounds_to_remove"]:
                await session.execute(
                    delete(TargetCompoundInTargetCollection).where(
                        and_(
                            TargetCompoundInTargetCollection.target_collection_id
                            == target_collection_id,
                            TargetCompoundInTargetCollection.target_compound_id.in_(
                                changes["compounds_to_remove"]
                            ),
                        )
                    )
                )

            for target_compound_id in changes["compounds_to_add"]:
                session.add(
                    TargetCompoundInTargetCollection(
                        target_compound_id=target_compound_id,
                        target_collection_id=target_collection_id,
                    )
                )
        await session.commit()
        await session.refresh(target_collection_db)

        post_update_compound_count = len(target_collection_db.target_compound)

    # Step 7: Set rematch status for affected sample batches
    # TODO_match If collection type changes, all affected batches need new
    # match_collection, match_sample
    needs_rematch = (
        changes["compounds"] or changes["batches"] or changes["collection_type"]
    )

    # Skip rematch when the collection has no compounds and the compound set
    # didn't change in this request (e.g. batches-only update on an empty collection).
    empty_no_op = not changes["compounds"] and post_update_compound_count == 0

    batch_status_result = None
    if needs_rematch and affected_sample_batch_ids and not empty_no_op:
        batch_status_result = await update_sample_batch_status(
            sample_batch_ids=list(affected_sample_batch_ids),
            status="rematch",
            independent_transaction=True,  # batches reloads to show rematch status
        )

    # Step 8: Emit reload events based on change types
    reload_events = []

    if independent_transaction:
        # emit target.collection reload if anything changed
        if any([changes["compounds"], changes["batches"], changes["basic_fields"]]):
            reload_events.append(emit_record_reload(record_type="target_collection"))

        # Reload match_collection for affected batches
        if any([changes["batches"], changes["basic_fields"]]):
            reload_events.extend(
                [
                    emit_record_reload("match_collection", room=batch_id)
                    for batch_id in affected_sample_batch_ids
                ]
            )

    # Reload match.ion table data in the target collection
    if changes["compounds"]:
        reload_events.append(
            emit_record_reload(
                record_type="match_ion",
                room=target_collection_id,
            )
        )

    if reload_events:
        await asyncio.gather(*reload_events)

    # Generate summary message
    message = (
        f"Target collection '{target_collection_db.target_collection_name}' was updated"
    )

    if created_target_compound_ids:
        count = len(created_target_compound_ids)
        message += f", {count} target compound{'s' if count != 1 else ''} created"

    if batch_status_result:
        message += f". {batch_status_result.get('message', '')}"

    runtime.logger.info(message)

    return {
        "status": "success",
        "message": message,
        "data": target_collection_db,
    }


@api_controller()
async def delete_target_collection(
    target_collection_id: str,
    delete_orphan_compounds: bool = False,
    independent_transaction: bool = False,
) -> dict:
    """
    Deletes a target collection and optionally its orphan compounds.
    Affected sample batches are set to "rematch" status.

    Steps:
    - Fetch target collection and identify affected batches, compounds and ionization
      modes
    - Identify orphan compounds if deletion is requested
    - Delete the target collection from database
    - Set rematch status for affected sample batches
    - Delete orphan compounds if requested
    - Emit appropriate socket events

    :param target_collection_id: ID of the target collection to delete
    :type target_collection_id: str
    :param delete_orphan_compounds: Whether to delete orphaned compounds
    :type delete_orphan_compounds: bool
    :param independent_transaction: Controls transaction and event behavior
    :type independent_transaction: bool
    :raises NotFoundException: When target collection is not found
    :return: Deletion results with success message
    :rtype: dict
    """
    # -- Fetch target collection and identify affected data --
    target_collection = await fetch_target_collection(target_collection_id)

    affected_sample_batch_ids = {
        assoc.sample_batch_id for assoc in target_collection.sample_batch
    }
    collection_compound_ids = {
        assoc.target_compound_id for assoc in target_collection.target_compound
    }

    # Query ionization modes that reference this collection (deletion not allowed)
    async with async_session() as session:
        affected_modes_result = await session.execute(
            select(IonizationMode).where(
                or_(
                    IonizationMode.calibration_collection_id == target_collection_id,
                    IonizationMode.diagnostic_collection_id == target_collection_id,
                )
            )
        )
        affected_ionization_modes = list(affected_modes_result.scalars())

    # -- Block deletion if collection is used by any ionization mode --
    if affected_ionization_modes:
        mode_names = [mode.ionization_mode_name for mode in affected_ionization_modes]
        raise ValueError(
            f"Cannot delete '{target_collection.target_collection_name}' because "
            f"it is associated with ionization modes: {', '.join(mode_names)}."
        )

    # -- Identify orphan compounds if their deletion is requested --
    orphan_compound_ids = []
    if delete_orphan_compounds and collection_compound_ids:
        async with async_session() as session:
            for compound_id in collection_compound_ids:
                # Check if compound is used in other collections
                other_usage = await session.scalar(
                    select(func.count()).where(
                        and_(
                            TargetCompoundInTargetCollection.target_compound_id
                            == compound_id,
                            TargetCompoundInTargetCollection.target_collection_id
                            != target_collection_id,
                        )
                    )
                )

                if other_usage == 0:
                    orphan_compound_ids.append(compound_id)

    # -- Delete the target collection from database --
    async with async_session() as session:
        collection_to_delete = await session.get(TargetCollection, target_collection_id)
        await session.delete(collection_to_delete)
        await session.commit()

    # -- Set rematch status for affected sample batches --
    # Skip if the deleted collection had no compounds - no targets changed
    batch_status_result = None
    if affected_sample_batch_ids and collection_compound_ids:
        batch_status_result = await update_sample_batch_status(
            sample_batch_ids=list(affected_sample_batch_ids),
            status="rematch",
            independent_transaction=True,
        )

    # -- Delete orphan compounds if requested --
    deleted_compound_count = 0
    if orphan_compound_ids:
        for compound_id in orphan_compound_ids:
            try:
                await delete_target_compound(
                    target_compound_id=compound_id,
                    independent_transaction=True,
                )
                deleted_compound_count += 1
            except ApiException as e:
                # INFO per compound: fires inside a per-item loop
                runtime.logger.info(
                    f"Failed to delete orphan compound {compound_id}: {e.user_message}"
                )

    # -- Emit reload events for independent transactions --
    if independent_transaction:
        # Update target.collection list
        await emit_record_reload(record_type="target_collection")

        # Update match.collection list (MatchBrowser table)
        for batch_id in affected_sample_batch_ids:
            await emit_record_reload(
                record_type="match_collection",
                room=batch_id,
            )

    # -- Build response message --
    message = (
        f"Target collection '{target_collection.target_collection_name}' was deleted"
    )

    if deleted_compound_count > 0:
        message += f", {deleted_compound_count} orphan compounds deleted"

    if affected_sample_batch_ids and batch_status_result:
        message = f"{message}. {batch_status_result.get('message', '')}"

    runtime.logger.info(message)

    return {
        "status": "success",
        "message": message,
    }
