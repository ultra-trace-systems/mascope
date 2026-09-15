from typing import List, Optional

from sqlalchemy import (
    String,
    and_,
    cast,
    func,
    select,
)
from sqlalchemy.orm import joinedload

from mascope_backend.api.controllers.match.ions.match_ions_controller import (
    delete_match_ions,
)
from mascope_backend.api.controllers.match.isotopes.match_isotopes_controller import (
    delete_match_isotopes,
)
from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    generate_target_ions_from_composition,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TargetCompoundBase,
)
from mascope_backend.api.models.target.ions.target_ion_pydantic_model import (
    TARGET_ION_SORT_COLUMNS,
    TargetIonUpdate,
)
from mascope_backend.api.new.ionization.modes.util import (
    fetch_batch_ionization_mechanism_ids,
)
from mascope_backend.db import (
    IonizationMechanism,
    IonizationMode,
    SampleBatch,
    SampleFile,
    SampleItem,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompoundInTargetCollection,
    TargetIon,
    TargetIsotope,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import emit_record_reload


@api_controller()
async def get_target_ions(
    target_compound_id: str = None,
    ionization_mechanism_id: str = None,
    sample_batch_id: Optional[str] = None,
    target_collection_id: Optional[str] = None,
    show_target_collection: bool = False,
    show_ionization_mechanism: bool = False,
    target_compound_ids: Optional[List[str]] = None,
    ionization_mechanism_ids: Optional[List[str]] = None,
    target_ion_formula: str = None,
    sort: str = None,
    order: str = None,
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a paginated list of target ions based on various filtering criteria such as target compound,
    ionization mechanism, sample batch, and specific ion formulas. Results can optionally include related
    target collection information and can be ordered and sorted according to specified parameters.

    Steps:
    - Construct the base query for fetching target ions.
    - Apply filters based on target compound ID, ionization mechanism ID, compound list, and ionization mechanism list.
    - If additional context such as sample batch or target collection details are requested, enhance the query to join
       with related tables and filter further based on these details.
    - If 'show_target_collection' or 'show_ionization_mechanism' is true, join with the respective tables to include
       these details in the results.
    - Apply ordering and sorting to the query.
    - Execute the query with pagination.
    - Format the fetched data into a list of dictionaries suitable for JSON serialization and return alongside total results count.

    :param target_compound_id: Filter by specific target compound ID, defaults to None.
    :type target_compound_id: str | None
    :param ionization_mechanism_id: Filter by specific ionization mechanism ID, defaults to None.
    :type ionization_mechanism_id: str | None
    :param sample_batch_id: Filter ions by the ID of the associated sample batch, defaults to None.
    :type sample_batch_id: Optional[str]
    :param target_collection_id: Filter ions by the ID of the target collection they belong to, defaults to None.
    :type target_collection_id: Optional[str]
    :param show_target_collection: Include detailed target collection data in the results, defaults to False.
    :type show_target_collection: bool
    :param show_ionization_mechanism: Include ionization mechanism data in the results, defaults to False.
    :type show_ionization_mechanism: bool
    :param target_compound_ids: List of target compound IDs for broader filtering, defaults to None.
    :type target_compound_ids: Optional[List[str]]
    :param ionization_mechanism_ids: List of ionization mechanism IDs for broader filtering, defaults to None.
    :type ionization_mechanism_ids: Optional[List[str]]
    :param target_ion_formula: Filter ions by their chemical formula, defaults to None.
    :type target_ion_formula: str | None
    :param sort: Field name to sort the results by, defaults to None.
    :type sort: str | None
    :param order: Sorting order, either 'asc' or 'desc', defaults to None.
    :type order: str | None
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None
    :return: A dictionary containing the total number of results and a list of target ions.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )

    async with async_session() as session:
        # Construct the base query
        stmt = select(TargetIon)

        # Apply basic filters
        if target_compound_id:
            stmt = stmt.filter(TargetIon.target_compound_id == target_compound_id)
        if ionization_mechanism_id:
            stmt = stmt.filter(
                TargetIon.ionization_mechanism_id == ionization_mechanism_id
            )
        if target_compound_ids:
            stmt = stmt.filter(TargetIon.target_compound_id.in_(target_compound_ids))
        if ionization_mechanism_ids:
            stmt = stmt.filter(
                TargetIon.ionization_mechanism_id.in_(ionization_mechanism_ids)
            )
        if target_ion_formula:
            stmt = stmt.filter(TargetIon.target_ion_formula == target_ion_formula)

        # Adjust the query based non-basic filters
        if sample_batch_id or target_collection_id or show_target_collection:
            stmt = stmt.join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_compound_id
                == TargetIon.target_compound_id,
            )

            # Filter ions by sample_batch_id if specified
            if sample_batch_id:
                # Fetch sample batch and related ion mechanisms and target collection ids
                result = await session.execute(
                    select(SampleBatch)
                    .options(joinedload(SampleBatch.target_collection))
                    .where(SampleBatch.sample_batch_id == sample_batch_id)
                )
                sample_batch = result.unique().scalar_one_or_none()
                if not sample_batch:
                    raise NotFoundException(
                        f"Sample batch with id '{sample_batch_id}' not found"
                    )

                # Extract ion mechanisms
                ionization_mechanism_ids = await fetch_batch_ionization_mechanism_ids(
                    sample_batch_id
                )
                target_collection_ids = [
                    tc.target_collection_id for tc in sample_batch.target_collection
                ]

                # Filter ions by batch ionization_mechanism_ids and target_collection_ids
                stmt = stmt.where(
                    TargetCompoundInTargetCollection.target_collection_id.in_(
                        target_collection_ids
                    ),
                    TargetIon.ionization_mechanism_id.in_(ionization_mechanism_ids),
                ).distinct(TargetIon.target_ion_id)

            # Filter ions by target_collection_id if specified
            if target_collection_id:
                stmt = stmt.filter(
                    TargetCompoundInTargetCollection.target_collection_id
                    == target_collection_id
                )

            # Include target collection details if requested
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

        # Join IonizationMechanism if show_ionization_mechanism is True
        if show_ionization_mechanism:
            stmt = stmt.join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
            )
            stmt = stmt.add_columns(
                IonizationMechanism.ionization_mechanism,
            )

        # Apply sorting
        if sort:
            stmt = stmt.order_by(
                order_by_column(TargetIon, sort, order, TARGET_ION_SORT_COLUMNS)
            )

        # Get total count
        total = await session.scalar(select(func.count()).select_from(stmt))
        # Apply pagination conditionally
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        result = await session.execute(stmt)

    # Construct the response data
    data = []
    for row in result.all():
        # When show_target_collection is true, include target_collection_id
        ion_data = row.TargetIon.to_dict()
        if show_target_collection and row.target_collection_id:
            ion_data["target_collection_id"] = row.target_collection_id
            ion_data["target_collection_name"] = row.target_collection_name
            ion_data["target_collection_type"] = row.target_collection_type
        if show_ionization_mechanism:
            ion_data["ionization_mechanism"] = row.ionization_mechanism
        data.append(ion_data)

    return {
        "message": "Target ions retrieved successfully.",
        "results": total,
        "data": data,
    }


@api_controller()
async def get_target_ion(target_ion_id: str) -> dict:
    """
    Retrieves a single target ion by its unique ID.

    Steps:
    1. Execute a query to fetch the target ion with the specified ID.
    2. Check if the target ion exists. If not, raise a NotFoundException.
    3. Return the target ion's details as a dictionary.

    :param target_ion_id: Unique identifier of the target ion to retrieve.
    :raises NotFoundException: If the target ion with the given ID is not found.
    :return: The requested target ion's details.
    """
    async with async_session() as session:
        # Step 1: Fetch target ion by ID
        target_ion = await session.get(TargetIon, target_ion_id)

        # Step 2: If target ion not found, raise exception
        if not target_ion:
            raise NotFoundException(f"Target ion with ID '{target_ion_id}' not found")

        # Step 3: Return target ion details
        return {
            "message": "Target ion retrieved successfully.",
            "data": target_ion.to_dict(),
        }


@api_controller()
async def create_target_ions(
    target_compound: TargetCompoundBase,
    ionization_mechanisms: List[IonizationMechanism],
    independent_transaction=False,
    session=None,
) -> dict:
    """Function to create target ion and target isotope records
    derived from a given target compound and list of ionization mechanisms to apply.

    Steps:
    1. Verify input parameters and initialize session if operation is an independent transaction.
    2. Generate target ions and isotopes from the compound composition.
    3. Persist the generated ions and isotopes in the database.
    4. Return created ions, isotopes, and any message logs.

    :param target_compound: Target compound to derive ions and isotopes from
    :type target_compound: TargetCompoundBase
    :param ionization_mechanisms: List of ionization mechanisms to apply to the compound
    :type ionization_mechanisms: List[IonizationMechanism]
    :param independent_transaction: Flag indicating whether the create target ions is an independent transaction, defaults to False
    :type independent_transaction: bool, optional
    :param session: Database session, smust be gicen if not an independent transaction, defaults to None
    :type session: SQLAlchemy.AsyncSession, optional
    :return: Dictionary with created ions, isotopes, and message logs.
    :rtype: dict
    """
    # Step 1: Initialize session if operation is an independent transaction.
    if independent_transaction:
        session = async_session()

    # Step 2: Generate target ions and isotopes from the compound composition.
    (
        target_ions,
        target_isotopes,
    ) = generate_target_ions_from_composition(target_compound, ionization_mechanisms)

    # Step 3: Persist generated ions and isotopes
    for target_isotope in target_isotopes:
        # Add the isotopes to be committed to the db
        session.add(target_isotope)
    for target_ion in target_ions:
        # Add the ions to be committed to the db
        session.add(target_ion)

    if independent_transaction:
        await session.commit()
    else:
        await session.flush()

    # Step 4: Return created entities and message logs
    return {
        "created_ions": [ion.to_dict() for ion in target_ions],
        "created_isotopes": [isotope.to_dict() for isotope in target_isotopes],
        "message_logs": {},  # TODO_target_compound_management Populate with relevant log messages
    }


@api_controller()
async def update_target_ion(target_ion_id: str, target_ion_update: TargetIonUpdate):
    """
    Updates filter parameters for a target ion and triggers re-matching for affected samples.

    Filter parameters are instrument-specific matching settings. When these change,
    the ion's existing match isotopes and match ions for affected samples must be
    deleted (stored isotope rows mark the ion as evaluated and would otherwise
    block recomputation with the new parameters) and the batch status set to
    pending rematch.

    Steps:
    - Fetch the target ion from the database.
    - Determine which instruments are affected by comparing existing vs new filter params.
    - If deleting params for a specific instrument, remove that key from filter_params.
    - If updating params, merge new values and track which instruments changed.
    - Commit target ion changes to the database.
    - For each affected instrument, find all sample items linked to this ion via the
      batch → collection → compound chain, filtered by ionization mechanism.
    - Delete the ion's existing match isotopes and match ions for those samples
      and mark affected batches for rematch.

    :param target_ion_id: The ID of the target ion to update.
    :type target_ion_id: str
    :param target_ion_update: Update payload containing either new match_params
        or a delete_instrument_params field to remove params for a specific instrument.
    :type target_ion_update: TargetIonUpdate
    :raises NotFoundException: If the target ion doesn't exist.
    :return: The updated target ion data and count of affected batches.
    :rtype: dict
    """
    all_affected_batches = set()
    async with async_session() as session:
        target_ion = await session.get(TargetIon, target_ion_id)
        if not target_ion:
            raise NotFoundException(f"Target ion with ID '{target_ion_id}' not found")

        existing_params = target_ion.filter_params or {}
        new_params = existing_params.copy()
        affected_instruments = set()
        # Determine changes to filter params based on update payload
        if target_ion_update.delete_instrument_params:
            instrument = target_ion_update.delete_instrument_params
            if instrument in new_params:
                # Params for this instrument are being deleted
                del new_params[instrument]
                target_ion.filter_params = new_params
                affected_instruments.add(instrument)
        else:
            for instrument, update_params in target_ion_update.match_params.items():
                update_dict = update_params.model_dump()
                if (
                    instrument not in existing_params
                    or existing_params[instrument] != update_dict
                ):
                    # Params for this instrument are new or changed
                    new_params[instrument] = update_dict
                    target_ion.filter_params = new_params
                    affected_instruments.add(instrument)

        if not affected_instruments:
            return {
                "data": target_ion.to_dict(),
                "message": f"Target ion `{target_ion.target_ion_formula}` unchanged.",
            }
        # Update modified target ion db record
        await session.commit()
        await session.refresh(target_ion)

    # Find affected samples and delete match data for the updated ion. The
    # ion's match_isotope rows must go too: changed params can widen the
    # eligible isotope set (e.g. a lowered abundance threshold), and stored
    # rows mark the ion as evaluated, which would block recomputation in
    # fetch_sample_unmatched_target_isotopes.
    ion_mechanism_id = target_ion.ionization_mechanism_id
    async with async_session() as session:
        ion_isotope_ids = (
            (
                await session.execute(
                    select(TargetIsotope.target_isotope_id).where(
                        TargetIsotope.target_ion_id == target_ion_id
                    )
                )
            )
            .scalars()
            .all()
        )
    for instrument in affected_instruments:
        affected_samples = await _find_samples_for_ion_and_instrument(
            target_ion_id, ion_mechanism_id, instrument
        )
        runtime.logger.debug(
            f"Setting rematch status for the updated ion for {len(affected_samples)} affected samples"
        )

        for sample in affected_samples:
            # Guard the empty case: delete_match_isotopes without isotope ids
            # would wipe the sample's entire match_isotope set.
            if ion_isotope_ids:
                await delete_match_isotopes(
                    sample_item_id=sample.sample_item_id,
                    target_isotope_ids=ion_isotope_ids,
                )
            await delete_match_ions(
                sample_item_id=sample.sample_item_id,
                target_ion_ids=[target_ion_id],
            )

        # Update affected sample batches to rematch status
        affected_batch_ids = {s.sample_batch_id for s in affected_samples}
        all_affected_batches.update(affected_batch_ids)
        await update_sample_batch_status(
            list(affected_batch_ids), "rematch", independent_transaction=True
        )

    await emit_record_reload(
        record_type="match_ion",
        record_id=target_ion_id,
        room=list(affected_batch_ids),
    )

    return {
        "data": target_ion.to_dict(),
        "message": (
            f"Target ion `{target_ion.target_ion_formula}` updated successfully. "
            f"{len(all_affected_batches)} affected batches pending rematch."
        ),
    }


async def _find_samples_for_ion_and_instrument(
    target_ion_id: str, ion_mechanism_id: str, instrument: str
) -> list[SampleItem]:
    """
    Finds sample items linked to a target ion via the batch / target collection chain,
    filtered by instrument and ionization mechanism.
    """
    async with async_session() as session:
        stmt = (
            select(SampleItem)
            .join(
                SampleFile,
                and_(
                    SampleFile.sample_file_id == SampleItem.sample_file_id,
                    SampleFile.instrument == instrument,
                ),
            )
            .join(
                SampleBatch, SampleBatch.sample_batch_id == SampleItem.sample_batch_id
            )
            .join(
                TargetCollectionInSampleBatch,
                TargetCollectionInSampleBatch.sample_batch_id
                == SampleBatch.sample_batch_id,
            )
            .join(
                TargetCompoundInTargetCollection,
                TargetCompoundInTargetCollection.target_collection_id
                == TargetCollectionInSampleBatch.target_collection_id,
            )
            .join(
                TargetIon,
                TargetIon.target_compound_id
                == TargetCompoundInTargetCollection.target_compound_id,
            )
            .join(
                IonizationMode,
                IonizationMode.ionization_mode_id == SampleItem.ionization_mode_id,
            )
            .where(TargetIon.target_ion_id == target_ion_id)
            .where(
                cast(IonizationMode.ionization_mechanism_ids, String).contains(
                    f'"{ion_mechanism_id}"'
                )
            )
            .distinct(SampleItem.sample_item_id)
        )
        result = await session.execute(stmt)
        return result.scalars().all()
