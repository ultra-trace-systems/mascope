import asyncio
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.api.controllers.ionization_mechanisms.ionization_mechanisms_controller import (
    get_ionization_mechanisms,
)
from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.target.ions.target_ions_controller import (
    create_target_ions,
)
from mascope_backend.api.controllers.target.lib.fetch.target_compounds_fetch import (
    fetch_compound_collections_and_batches,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
    TargetCompoundSortColumn,
    TargetCompoundUpdate,
)
from mascope_backend.db import (
    IonizationMechanism,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    async_session,
)
from mascope_backend.db.id import gen_id
from mascope_backend.socket.records.service import emit_record_reload
from mascope_file.string import norm


# TODO_target_compound_management refactor to use same strucutre as other controllers


@api_controller()
async def get_target_compounds(
    target_compound_name: Optional[str] = None,
    target_compound_formula: Optional[str] = None,
    sample_batch_id: Optional[str] = None,
    target_collection_id: Optional[str] = None,
    show_target_collection: bool = False,
    sort: str = None,
    order: str = None,
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a list of target compounds optionally filtered by name, formula, sample batch,
    or target collection ID. The results can include related collection data, be sorted,
    and paginated according to the provided parameters.

    Steps:
    1. Construct the initial query based on the TargetCompound model.
    2. Apply filters for compound name and formula if provided.
    3. Extend the query to include related collections if required by filters or flags.
    4. Apply sorting if specified.
    5. Count the total results for pagination.
    6. Apply pagination settings and execute the query.
    7. Format the fetched data into a list of dictionaries for the response.

    :param target_compound_name: Filter compounds by their name, defaults to None.
    :type target_compound_name: str | None, optional
    :param target_compound_formula: Filter compounds by their chemical formula, defaults to None.
    :type target_compound_formula: str | None, optional
    :param sample_batch_id: Filter compounds associated with a specific sample batch ID, defaults to None.
    :type sample_batch_id: str | None, optional
    :param target_collection_id: Filter compounds associated with a specific target collection ID, defaults to None.
    :type target_collection_id: str | None, optional
    :param show_target_collection: Include target collection data in the response, defaults to False.
    :type show_target_collection: bool, optional
    :param sort: Column name to sort by, defaults to None.
    :type sort: str, optional
    :param order: Direction to sort the results ('asc' or 'desc'), defaults to None.
    :type order: str, optional
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None, optional
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None, optional
    :return: A dictionary containing the total number of results, and a list of compounds.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        # Step 1: Define the main query for target compounds
        stmt = select(TargetCompound)

        # Step 2: Apply name and formula filters
        if target_compound_name:
            stmt = stmt.filter(
                TargetCompound.target_compound_name == target_compound_name
            )
        if target_compound_formula:
            stmt = stmt.filter(
                TargetCompound.target_compound_formula == target_compound_formula
            )

        # Step 3: Adjust the query based non-basic filters
        if sample_batch_id or target_collection_id or show_target_collection:
            stmt = stmt.join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_compound_id
                == TargetCompound.target_compound_id,
            ).distinct()

            # Filter compounds by sample_batch_id if specified
            if sample_batch_id:
                stmt = stmt.join(
                    TargetCollectionInSampleBatch,
                    TargetCollectionInSampleBatch.target_collection_id
                    == TargetCompoundInTargetCollection.target_collection_id,
                ).where(
                    TargetCollectionInSampleBatch.sample_batch_id == sample_batch_id
                )
            # Filter compounds by target_collection_id if specified
            if target_collection_id:
                stmt = stmt.filter(
                    TargetCompoundInTargetCollection.target_collection_id
                    == target_collection_id
                )

            # Add the target_collection_id to be shown
            if show_target_collection:
                stmt = stmt.join(
                    TargetCollection,
                    TargetCollection.target_collection_id
                    == TargetCompoundInTargetCollection.target_collection_id,
                )
                stmt = stmt.add_columns(
                    TargetCompoundInTargetCollection.target_collection_id,
                    TargetCollection.target_collection_name,
                    TargetCollection.target_collection_type,
                )

        # Step 4: Apply sorting if specified
        if sort:
            stmt = stmt.order_by(
                order_by_column(TargetCompound, sort, order, TargetCompoundSortColumn)
            )

        # Step 5: Count total results
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = await session.scalar(count_stmt)

        # Step 6: Apply pagination and execute
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        result = await session.execute(stmt)

    # Step 7: Construct the response data
    data = []
    for row in result.all():
        # When show_target_collection is true, include target_collection_id
        compound_data = row.TargetCompound.to_dict()
        if show_target_collection:
            compound_data["target_collection_id"] = row.target_collection_id
            compound_data["target_collection_name"] = row.target_collection_name
            compound_data["target_collection_type"] = row.target_collection_type
        data.append(compound_data)

    return {
        "message": "Target compounds retrieved successfully.",
        "results": total,
        "data": data,
    }


@api_controller()
async def get_target_compound(target_compound_id: str) -> dict:
    """
    Retrieves a single target compound by its unique ID.

    Steps:
    1. Execute a query to fetch the target compound with the specified ID.
    2. Check if the target compound exists. If not, raise a NotFoundException.
    3. Return the target compound's details as a dictionary.

    :param sample_batch_id: Unique identifier of the target compound to retrieve.
    :type sample_batch_id: str
    :raises NotFoundException: If the target compound with the given ID is not found.
    :return: The requested target compound's details.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch target compound by ID
        target_compound = await session.get(TargetCompound, target_compound_id)

        if not target_compound:
            # Step 2: If target compound not found, raise exception
            raise NotFoundException(
                f"Target compound with ID '{target_compound_id}' not found"
            )
    # Step 3: Return target compound details
    return {
        "message": f"Details for target compound '{target_compound.target_compound_name}' retrieved.",
        "data": target_compound.to_dict(),
    }


@api_controller()
async def create_target_compound(
    target_compounds: list[TargetCompoundBase],
    independent_transaction: bool = False,
    session: AsyncSession | None = None,
) -> dict:
    """Function to create a target compound record(s) and derived target ions and isotopes

    For each target compound to create:
    1. Check for existing records in the database with either similar name+formula or CAS number
        - If no matching records, move to step 2
        - If one matching record exists
            - If match is based on name+formula
              AND existing record is missing CAS number
              AND new record has CAS number
              THEN update the record with CAS number
            - No need to create new record, continue to next compound
        - If > 1 matching records exist
            - Check for actual duplicates in the database (similar name+formula+CAS number)
              - If duplicates, raise RuntimeError
              - Else, no need to create new record, continue to next compound
    2. Create new target compound record and derived target ions and isotopes
    3. Notify clients about new target compounds

    TODO: Different kinds of inconsistencies between new and existing records could be handled better. E.g. if the new record has similar
    name+formula but different CAS number than an existing record, the existing record will be used and the inconsistency between CAS
    numbers is silently ignored.

    :param target_compounds: list of target compounds to create
    :type target_compounds: list[TargetCompoundBase]
    :param independent_transaction: Flag indicating whether the create target compound is an independent transaction, defaults to False
    :type independent_transaction: bool, optional
    :param session: Database session, must be given if not an independent transaction, defaults to None
    :type session: SQLAlchemy.AsyncSession, optional
    :raises RuntimeError: Database is malformed
    :return: Return created target compounds, skipped compounds (already existing) and message log
    :rtype: dict
    """
    if independent_transaction:
        session = async_session()

    # initialize list of targets to return
    target_compound_ids = []
    existing_target_compounds = []
    # initalized lists of targets to create
    target_compounds_to_create = []
    # Initialize message log
    message_log = {}
    # Ionization mechanisms are the same for every compound in this call;
    # fetched once on first need instead of per created compound
    ionization_mechanisms: list[IonizationMechanism] | None = None

    for i, target_compound in enumerate(target_compounds):
        # Initialize messages list
        message_log[i + 1] = {
            "status_code": 0,
            "messages": [],
        }
        # STEP 1: check if the compound record is already in the database (similar name and formula or CAS number)
        existing_compounds = await session.execute(
            select(TargetCompound).filter(
                or_(
                    # Similar CAS number
                    and_(
                        TargetCompound.cas_number is not None,
                        (
                            TargetCompound.cas_number
                            == norm(target_compound.cas_number)
                            if target_compound.cas_number
                            else None
                        ),
                    ),
                    # Similar name and formula
                    and_(
                        func.lower(TargetCompound.target_compound_formula)
                        == norm(target_compound.target_compound_formula, lower=True),
                        func.lower(TargetCompound.target_compound_name)
                        == norm(target_compound.target_compound_name, lower=True),
                    ),
                )
            )
        )
        existing_compounds = existing_compounds.scalars().all()
        existing_target_compounds += existing_compounds
        if len(existing_compounds) == 0:
            # save the new compound for creation if it doesn't exist
            target_compound = TargetCompound(
                target_compound_id=gen_id(),
                target_compound_name=norm(target_compound.target_compound_name),
                target_compound_formula=norm(target_compound.target_compound_formula),
                cas_number=(
                    norm(target_compound.cas_number)
                    if target_compound.cas_number
                    else None
                ),
            )

            target_compounds_to_create.append(target_compound)
            target_compound_ids.append(target_compound.target_compound_id)

            message_log[i + 1]["status_code"] = 201
            message_log[i + 1]["messages"].append(
                f"New target compound with target_compound_id: {target_compound.target_compound_id} created"
            )
        elif len(existing_compounds) == 1:
            # use the existing compound record if it does exist
            target_compound_old = existing_compounds[0]
            # Check if CAS number update needed
            if (
                target_compound_old.cas_number is None
                and target_compound.cas_number is not None
            ):
                target_compound_old.cas_number = target_compound.cas_number
                await update_target_compound(
                    [TargetCompoundUpdate(**target_compound_old.to_dict())]
                )
            target_compound = target_compound_old
            target_compound_ids.append(target_compound.target_compound_id)

            message_log[i + 1]["status_code"] = 200
            message_log[i + 1]["messages"].append(
                f"Existing target compound {target_compound.target_compound_name} with target_compound_id: {target_compound.target_compound_id} used"
            )
            continue  # as ions & isotopes are already there in this case
        else:
            # More than one matching compound in the database
            # It is possible to arrive here if there are compound(s) that:
            #   1) Have the same CAS number as the one to be created; AND
            #   2) Another compound that has the same name and formula but not CAS number as the one to be created
            # Let's check to be sure there are no actual duplicates in the database
            target_compound = existing_compounds[0]
            # Convert to dicts
            existing_compounds = [
                existing_compound.to_dict() for existing_compound in existing_compounds
            ]
            # Pop target compound ids for dict comparison afterwards
            _ = [
                existing_compound.pop("target_compound_id")
                for existing_compound in existing_compounds
            ]
            # Check for identical target compounds
            for i, existing_compound in enumerate(existing_compounds[:-1]):
                if any(
                    existing_compound == another_existing_compound
                    for another_existing_compound in existing_compounds[i + 1 :]
                ):
                    # the database is inconsistent with two identical target compounds
                    raise RuntimeError("Duplicate target compound in database")
            # No duplicates, let's proceed
            target_compound_ids.append(target_compound.target_compound_id)
            message_log[i + 1]["status_code"] = 200
            message_log[i + 1]["messages"].append(
                f"Existing target compound {target_compound.target_compound_name} with target_compound_id: {target_compound.target_compound_id} used"
            )
            continue  # as ions & isotopes are already there in this case

        # STEP2: Proceed to creating new target compound record and generating target ions and isotopes for the compound
        # Add the compound to session (before creating ions that reference it)
        session.add(target_compound)

        # Create target ions for the compound
        if ionization_mechanisms is None:
            ionization_mechanisms_data = await get_ionization_mechanisms()
            ionization_mechanisms = [
                IonizationMechanism(**ionization_mechanism_dict)
                for ionization_mechanism_dict in ionization_mechanisms_data["data"]
            ]

        await create_target_ions(
            target_compound=target_compound,
            ionization_mechanisms=ionization_mechanisms,
            independent_transaction=False,
            session=session,
        )

    if independent_transaction:
        await session.commit()

        # reload target.compound list (compound not in any collections yet)
        await emit_record_reload(record_type="target_compound")
    else:
        await session.flush()

    return {
        "target_compound_ids": target_compound_ids,
        "created_compounds": target_compounds_to_create,
        "existing_compounds": existing_target_compounds,
        "message_logs": message_log,
    }


@api_controller()
async def update_target_compound(
    target_compounds: list[TargetCompoundUpdate],
) -> dict[str, list | dict]:
    """
    Updates multiple target compounds with validation and change tracking.

    Handles formula changes by recreating compounds and re-associating them
    with target collections. Emits appropriate reload events for affected
    collections.

    Steps:
    - Validate each compound exists
    - Check if changes were made
    - For formula changes: delete old, create new, re-associate collections
    - For other changes: update fields directly
    - Track affected target collections
    - Emit reload events to update UI

    :param target_compounds: List of target compound updates to process
    :type target_compounds: list[TargetCompoundUpdate]
    :return: Dictionary containing categorized compounds and message logs
    :rtype: dict[str, list | dict]
    """
    not_changed_target_compounds = []
    not_updated_target_compounds = []
    existing_target_compounds = []
    updated_target_compounds = []
    affected_target_collection_ids = set()
    rematch_batch_ids = set()
    message_log = {}
    async with async_session() as session:
        for i, target_compound in enumerate(target_compounds):
            # Initialize message log for this compound
            message_log[i + 1] = {"status_code": 0, "messages": []}

            # Check if target compound exists
            existing_compound = await session.execute(
                select(TargetCompound).where(
                    TargetCompound.target_compound_id
                    == target_compound.target_compound_id
                )
            )
            existing_compound = existing_compound.scalar_one_or_none()

            if not existing_compound:
                message_log[i + 1]["status_code"] = 404
                message_log[i + 1]["messages"].append(
                    f"Target compound not found. ID: {target_compound.target_compound_id}, Name: {target_compound.target_compound_name}"
                )
                continue

            # Check if target compound was actually edited
            update_data = target_compound.model_dump(exclude_unset=True)
            if all(
                getattr(existing_compound, key, None) == value
                for key, value in update_data.items()
            ):
                not_changed_target_compounds.append(existing_compound)  # no change
                message_log[i + 1]["status_code"] = 304
                message_log[i + 1]["messages"].append(
                    f"No changes in compound {existing_compound.target_compound_name} detected."
                )
                continue  # Skip this compound update, proceed to the next

            # Get affected target collections and batches before any modifications
            (
                compound_batch_ids,
                target_collections_ids,
            ) = await fetch_compound_collections_and_batches(
                target_compound.target_compound_id
            )
            affected_target_collection_ids.update(target_collections_ids)

            # Handle formula changes (requires compound recreation)
            if (
                target_compound.target_compound_formula
                and existing_compound.target_compound_formula
                != target_compound.target_compound_formula
            ):
                # Check if the new formula already exists in other TargetCompound records with the same name
                existing_formula_compound = await session.execute(
                    select(TargetCompound).where(
                        and_(
                            func.lower(TargetCompound.target_compound_formula)
                            == norm(
                                target_compound.target_compound_formula, lower=True
                            ),
                            func.lower(TargetCompound.target_compound_name)
                            == norm(target_compound.target_compound_name, lower=True),
                        )
                    )
                )
                existing_formula_compound = (
                    existing_formula_compound.scalar_one_or_none()
                )

                if existing_formula_compound:
                    not_updated_target_compounds.append(target_compound)
                    existing_target_compounds.append(existing_formula_compound)
                    message_log[i + 1]["status_code"] = 409
                    message_log[i + 1]["messages"].append(
                        f"The compound with name {target_compound.target_compound_name} and formula {target_compound.target_compound_formula} is already exists as {existing_formula_compound.target_compound_name} (target_compound_id: {existing_formula_compound.target_compound_id}). Use this compound instead of {target_compound.target_compound_name}"
                    )
                    continue  # Skip this compound update, proceed to the next

                # If compound formula has changed, delete the compound and recreate it with new formula
                await delete_target_compound(
                    target_compound_id=target_compound.target_compound_id,
                    independent_transaction=False,
                    session=session,
                )

                # Create new compound with updated formula
                new_compound_result = await create_target_compound(
                    target_compounds=[target_compound],
                    independent_transaction=False,
                    session=session,
                )

                # Process creation result
                if (
                    "created_compounds" in new_compound_result
                    and len(new_compound_result["created_compounds"]) == 1
                ):
                    new_compound = new_compound_result["created_compounds"][0]
                    message_log[i + 1]["status_code"] = 201
                    message_log[i + 1]["messages"].append(
                        f"New target compound '{new_compound.target_compound_name}' created "
                        f"(ID: {new_compound.target_compound_id})"
                    )
                elif (
                    "existing_compounds" in new_compound_result
                    and len(new_compound_result["existing_compounds"]) == 1
                ):
                    new_compound = new_compound_result["existing_compounds"][0]
                    message_log[i + 1]["status_code"] = 200
                    message_log[i + 1]["messages"].append(
                        f"Existing target compound '{new_compound.target_compound_name}' used "
                        f"(ID: {new_compound.target_compound_id})"
                    )

                else:
                    raise HTTPException(
                        status_code=500,
                        detail="Error creating target compound",
                    )

                # Check if the compound already exists in target collections
                existing_target_compound_in_target_collection = await session.execute(
                    select(TargetCompoundInTargetCollection).where(
                        and_(
                            TargetCompoundInTargetCollection.target_compound_id
                            == new_compound.target_compound_id,
                            TargetCompoundInTargetCollection.target_collection_id.in_(
                                target_collections_ids
                            ),
                        )
                    )
                )
                existing_target_compound_in_target_collection = (
                    existing_target_compound_in_target_collection.scalar_one_or_none()
                )

                # Re-add the new compound to target collections if the new compound is not already associated with the target collections
                if not existing_target_compound_in_target_collection:
                    for target_collection_id in target_collections_ids:
                        new_target_compound_in_target_collection = (
                            TargetCompoundInTargetCollection(
                                target_compound_id=new_compound.target_compound_id,
                                target_collection_id=target_collection_id,
                            )
                        )
                        session.add(new_target_compound_in_target_collection)
                updated_target_compounds.append(new_compound)
                # The delete+recreate cascade removed every match row derived
                # from the old compound; flag the batches so a refresh
                # recomputes the new formula's ions.
                rematch_batch_ids.update(compound_batch_ids)

            else:
                # If compound formula has not changed, just update the fields
                for key, value in update_data.items():
                    setattr(existing_compound, key, value)

                updated_target_compounds.append(existing_compound)
                message_log[i + 1]["status_code"] = 200
                message_log[i + 1]["messages"].append(
                    f"Compound '{existing_compound.target_compound_name}' updated"
                )

        await session.commit()

        # Formula changes wiped match data via cascade; mark the affected
        # batches pending rematch so the loss is visible and recomputable.
        if rematch_batch_ids:
            await update_sample_batch_status(
                list(rematch_batch_ids), "rematch", independent_transaction=True
            )

        # -- Emit reload events ---
        reload_events = []

        # 1. Reload for each affected collection (both stores subscribed to same rooms)
        for collection_id in affected_target_collection_ids:
            reload_events.extend(
                [
                    emit_record_reload(
                        record_type="target_collection", room=collection_id
                    ),  # target.collection store detailed
                    emit_record_reload(
                        record_type="match_ion", room=collection_id
                    ),  # match.ion store list
                ]
            )

        # 2. Reload compound list globally
        reload_events.append(emit_record_reload(record_type="target_compound"))

        if reload_events:
            await asyncio.gather(*reload_events)

        return {
            "not_changed_compounds": not_changed_target_compounds,
            "updated_compounds": updated_target_compounds,
            "not_updated_compounds": not_updated_target_compounds,
            "existing_compounds": existing_target_compounds,
            "message_logs": message_log,
        }


@api_controller()
async def delete_target_compound(
    target_compound_id: str,
    independent_transaction: bool = False,
    session: AsyncSession | None = None,
) -> dict[str, str]:
    """
    Deletes a target compound and emits reload events for affected collections.

    When a compound is deleted, all target collections that contained this
    compound need to reload their ion data. The compound-collection associations
    are automatically removed via cascade delete.

    Steps:
    - Fetch the target compound to verify existence
    - Find all target collections containing this compound
    - Delete the compound (cascade removes associations and match rows)
    - Flag batches using those collections for rematch (standalone deletes only;
      nested callers set the flag themselves)
    - Emit reload events to affected collections

    :param target_compound_id: ID of the target compound to delete
    :type target_compound_id: str
    :param independent_transaction: Whether to manage transaction independently
    :type independent_transaction: bool
    :param session: Optional SQLAlchemy session for nested transactions
    :type session: AsyncSession | None
    :raises NotFoundException: When target compound is not found
    :return: Success message with deleted compound name
    :rtype: dict[str, str]
    """
    if independent_transaction:
        session = async_session()

    # Fetch the target compound
    target_compound = await session.get(TargetCompound, target_compound_id)
    if not target_compound:
        raise NotFoundException(
            f"Target compound with ID '{target_compound_id}' not found"
        )

    # Fetch the target collections where the deleting compound was present
    result = await session.execute(
        select(TargetCompoundInTargetCollection.target_collection_id).filter(
            TargetCompoundInTargetCollection.target_compound_id == target_compound_id
        )
    )
    affected_target_collection_ids = result.scalars().all()

    # Resolve the batches using those collections before the delete cascades
    affected_batch_ids: list[str] = []
    if affected_target_collection_ids:
        result = await session.execute(
            select(TargetCollectionInSampleBatch.sample_batch_id)
            .where(
                TargetCollectionInSampleBatch.target_collection_id.in_(
                    affected_target_collection_ids
                )
            )
            .distinct()
        )
        affected_batch_ids = result.scalars().all()

    # Delete the compound (associations removed by cascade)
    await session.delete(target_compound)

    if independent_transaction:
        await session.commit()

        # The cascade wiped the compound's match rows in every batch using it;
        # flag those batches pending rematch. Nested callers (e.g. formula
        # updates, collection deletion) set the flag themselves.
        if affected_batch_ids:
            await update_sample_batch_status(
                affected_batch_ids, "rematch", independent_transaction=True
            )

        # Emit reload events
        reload_events = []

        # 1. Reload for each affected collection (both stores subscribed to same rooms)
        for collection_id in affected_target_collection_ids:
            reload_events.extend(
                [
                    emit_record_reload(
                        record_type="target_collection", room=collection_id
                    ),  # target.collection store detailed
                    emit_record_reload(
                        record_type="match_ion", room=collection_id
                    ),  # match.ion store list
                ]
            )

        # 2. Reload compound list globally
        reload_events.append(emit_record_reload(record_type="target_compound"))
        if reload_events:
            await asyncio.gather(*reload_events)
    else:
        await session.flush()

    return {
        "message": f"Target compound '{target_compound.target_compound_name}' was deleted.",
    }
