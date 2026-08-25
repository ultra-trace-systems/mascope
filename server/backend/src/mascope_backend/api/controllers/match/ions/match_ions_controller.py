import pandas as pd
from sqlalchemy import (
    delete,
    func,
    select,
)

from mascope_backend.api.controllers.match.lib.match_upsert import (
    bulk_upsert_match_level,
    row_value,
)
from mascope_backend.api.controllers.match.lib.match_util import deduplicate_match_df
from mascope_backend.api.controllers.match.lib.match_write_lock import (
    acquire_match_write_locks,
)
from mascope_backend.api.controllers.sample.lib.sample_items_fetch import (
    fetch_sample_item_ids,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    NotFoundException,
)
from mascope_backend.api.lib.utils import strings_json_safe
from mascope_backend.api.models.match.ions.match_ion_pydantic_model import (
    MatchIonBase,
)
from mascope_backend.db import (
    IonizationMechanism,
    MatchIon,
    Sample,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    TargetIon,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_file.name import resolve_instrument_type


@api_controller()
async def get_match_ions(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_ion_id: str | None = None,
    ionization_mechanism_id: str | None = None,
    match_category_min: int | None = None,
    deduplicate: bool = False,
    show_target_collection: bool = False,
    show_target_compound: bool = False,
    show_target_ion: bool = False,
    show_ionization_mechanism: bool = False,
    sort: str | None = None,
    order: str | None = None,
    page: int = 0,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a list of matched ions based on specified filtering criteria, supporting optional inclusion
    of related data (e.g., target collections, compounds, ions, and ionization mechanisms). The function
    supports sorting and pagination.

    Steps:
    1. Construct the main query for fetching matched ions from the database.
    2. Apply filters based on the provided `sample_item_id`, `target_ion_id`, and `match_category_min`.
    3. Join the `Sample` table if `sample_batch_id` is provided to filter matches by sample batch.
    4. Join the `TargetIon` table if required based on `ionization_mechanism_id`, `show_target_ion`,
    `show_ionization_mechanism`, or `show_target_collection` flags.
    5. Apply the filter for `ionization_mechanism_id` if provided.
    6. Join the `TargetCompound` table and add target compound details if `show_target_compound` is True.
    7. Add target ion details if `show_target_ion` is True.
    8. Join the `IonizationMechanism` table and add ionization mechanism details if `show_ionization_mechanism` is True.
    9. Join with target collection tables if `show_target_collection` is True to include related collection data.
    10. Apply sorting based on the specified `sort` column and `order` direction.
    11. Count the total number of matched ions for pagination.
    12. Limit the query for pagination and execute it to fetch the results.
    13. Format the fetched data into a list of dictionaries for the response.
    14. If deduplication is requested and `show_target_collection` is True, deduplicate the ions.

    :param sample_item_id: Filter matches by the sample item ID, defaults to None.
    :type sample_item_id: str | None, optional
    :param sample_batch_id: Filter matches by the sample batch ID, defaults to None.
    :type sample_batch_id: str | None, optional
    :param target_ion_id: Filter matches by the target ion ID, defaults to None.
    :type target_ion_id: str | None, optional
    :param ionization_mechanism_id: Filter matches by the ionization mechanism ID, defaults to None.
    :type ionization_mechanism_id: str | None, optional
    :param match_category_min: Filter by match_category to include specified category and higher (e.g., 1 includes categories 1 and higher), defaults to None.
    :type match_category_min: int | None, optional
    :param deduplicate: Flag to indicate whether ion deduplication should be applied when `show_target_collection` is True, defaults to False.
    :type deduplicate: bool
    :param show_target_collection: Whether to include target collection details, defaults to False.
    :type show_target_collection: bool, optional
    :param show_target_compound: Include additional data about the target compounds, defaults to False.
    :type show_target_compound: bool, optional
    :param show_target_ion: Include additional data about the target ions, defaults to False.
    :type show_target_ion: bool, optional
    :param show_ionization_mechanism: Include ionization mechanism data in the results, defaults to False.
    :type show_ionization_mechanism: bool
    :param sort: Column name to sort by, defaults to None.
    :type sort: str | None, optional
    :param order: Order of sorting, 'asc' for ascending or 'desc' for descending, defaults to None.
    :type order: str | None, optional
    :param page: Page number for pagination, starts from 0, defaults to 0.
    :type page: int, optional
    :param limit: Maximum number of results per page, defaults to None (return everything).
    :type limit: int, optional
    :return: A dictionary containing the total results count and a paginated list of match ions.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Define the main query for match ions
        query = select(MatchIon)

        # Step 2: Apply filters based on input parameters
        if sample_item_id:
            query = query.filter(MatchIon.sample_item_id == sample_item_id)
            if not sample_batch_id:
                query = query.join(Sample, Sample.sample_item_id == sample_item_id)
        if target_ion_id:
            query = query.filter(MatchIon.target_ion_id == target_ion_id)
        if match_category_min is not None:
            query = query.filter(MatchIon.match_category >= match_category_min)

        # Step 3: Join with Sample table if sample_batch_id is specified
        if sample_batch_id:
            query = query.join(
                Sample, Sample.sample_item_id == MatchIon.sample_item_id
            ).where(Sample.sample_batch_id == sample_batch_id)

        if sample_batch_id or sample_item_id:
            query = query.add_columns(Sample.instrument)

        # Step 4: Join TargetIon if requested
        if (
            ionization_mechanism_id
            or show_target_collection
            or show_target_compound
            or show_target_ion
            or show_ionization_mechanism
        ):
            query = query.join(
                TargetIon, TargetIon.target_ion_id == MatchIon.target_ion_id
            )

        # Step 5: Apply filter for ionization mechanism if provided
        if ionization_mechanism_id:
            query = query.filter(
                TargetIon.ionization_mechanism_id == ionization_mechanism_id
            )

        # Step 6: Add target compound columns if requested
        if show_target_compound:
            query = query.join(
                TargetCompound,
                TargetCompound.target_compound_id == TargetIon.target_compound_id,
            ).add_columns(
                TargetCompound.target_compound_name,
                TargetCompound.target_compound_formula,
            )

        # Step 7: Add target ion columns
        if show_target_ion:
            query = query.add_columns(
                TargetIon.target_compound_id,
                TargetIon.target_ion_formula,
                TargetIon.filter_params,
            )

        # Step 8: Join IonizationMechanism and add columns if requested
        if show_ionization_mechanism:
            query = query.join(
                IonizationMechanism,
                IonizationMechanism.ionization_mechanism_id
                == TargetIon.ionization_mechanism_id,
            )
            query = query.add_columns(
                IonizationMechanism.ionization_mechanism,
            )

        # Step 9: Join with TargetCollection and add columns if requested
        if show_target_collection:
            query = (
                query.join(
                    TargetCompoundInTargetCollection,
                    TargetCompoundInTargetCollection.target_compound_id
                    == TargetIon.target_compound_id,
                )
                .join(
                    TargetCollectionInSampleBatch,
                    TargetCollectionInSampleBatch.target_collection_id
                    == TargetCompoundInTargetCollection.target_collection_id,
                )
                .where(
                    Sample.sample_batch_id
                    == TargetCollectionInSampleBatch.sample_batch_id
                )
                .join(
                    TargetCollection,
                    TargetCollection.target_collection_id
                    == TargetCompoundInTargetCollection.target_collection_id,
                )
                .add_columns(
                    TargetCompoundInTargetCollection.target_collection_id,
                    TargetCollection.target_collection_name,
                    TargetCollection.target_collection_type,
                )
                .distinct(MatchIon.match_ion_id)
            )

        # Step 10: Apply sorting
        if sort:
            sort_expression = getattr(MatchIon, sort, None)
            if sort_expression:
                if order == "desc":
                    query = query.order_by(sort_expression.desc())
                else:
                    query = query.order_by(sort_expression.asc())

        # Step 11: Count total
        count_stmt = select(func.count()).select_from(query.subquery())
        total = await session.scalar(count_stmt)

        # Step 12: Execute the paginated query
        if limit is not None:
            query = query.offset(page * limit).limit(limit)
        result = await session.execute(query)

    # Step 13: Construct response data
    data = []
    for row in result.all():
        ion_data = row.MatchIon.to_dict()

        try:
            # Resolve correct intensity units based on the instrument type of the sample
            instrument_type = resolve_instrument_type(row.instrument)
            if instrument_type == "tof":
                unit = "ions"
            else:
                unit = "counts"
            ion_data["unit"] = unit
        except AttributeError:
            pass

        if show_target_collection:
            ion_data["target_collection_id"] = row.target_collection_id
            ion_data["target_collection_name"] = row.target_collection_name
            ion_data["target_collection_type"] = row.target_collection_type
        if show_target_compound:
            ion_data["target_compound_name"] = row.target_compound_name
            ion_data["target_compound_formula"] = row.target_compound_formula
        if show_target_ion:
            ion_data["target_compound_id"] = row.target_compound_id
            ion_data["target_ion_formula"] = row.target_ion_formula
            ion_data["filter_params"] = row.filter_params
        if show_ionization_mechanism:
            ion_data["ionization_mechanism"] = row.ionization_mechanism
        data.append(ion_data)

    # Step 14: Deduplicate if requested and `show_target_collection` is True
    if deduplicate and show_target_collection:
        data_df = pd.DataFrame(data)
        data_df = deduplicate_match_df(
            data_df, id_keys=("target_ion_id", "sample_item_id")
        )
        # Nullable label columns come back as NaN under pandas 3's str dtype,
        # which starlette cannot serialize.
        data = strings_json_safe(data_df).to_dict(orient="records")
        # Update total after deduplication
        total = len(data)

    return {
        "message": "Match ions retrieved successfully",
        "results": total,
        "data": data,
    }


@api_controller()
async def get_match_ion(match_ion_id: str) -> dict:
    """
    Retrieves detailed information for a specific match ion by its unique ID.

    Steps:
    1. Fetch the match ion from the database using its ID to ensure it exists.
    2. If the match ion is not found, raise a NotFoundException.
    3. Return the details of the match ion as a dictionary.

    :param match_ion_id: Unique identifier of the match ion to retrieve.
    :type match_ion_id: str
    :raises NotFoundException: If no match ion is found with the specified ID.
    :return: Detailed information of the match ion.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch match ion by ID
        ion = await session.get(MatchIon, match_ion_id)

    # Step 2: Check if the ion exists
    if not ion:
        raise NotFoundException(f"Match ion with ID '{match_ion_id}' not found")

    # Step 3: Return ion details
    return {
        "message": "Match ion retrieved successfully",
        "data": ion.to_dict(),
    }


@api_controller()
async def create_match_ions(
    match_ions: list[MatchIonBase],
    independent_transaction: bool = False,
) -> dict:
    """
    Creates match ions for a given sample item based on the provided list of
    aggregated match ion data.
    Updates existing records if data differs, skips if identical, creates if new.

    :param match_ions: List of match ion data for creating matches
    :type match_ions: list[MatchIonBase]
    :param independent_transaction: Indicates if operation should be independent
    :type independent_transaction: bool
    :return: Creation results with counts of new vs existing records; `data`
        holds the written (created or updated) rows' key and value columns.
    :rtype: dict
    """
    if not match_ions:
        return {"message": "No match ions provided", "data": []}

    async with async_session() as session:
        # Serialize with every other match writer of the affected batches;
        # holds until commit, making the upsert below race-free.
        await acquire_match_write_locks(
            session, {row_value(mi, "sample_item_id") for mi in match_ions}
        )
        # Bulk upsert on the natural key: inserts missing rows, updates only
        # rows whose values changed, leaves identical rows untouched.
        created_count, updated_count, changed_rows = await bulk_upsert_match_level(
            session,
            MatchIon,
            id_column="match_ion_id",
            natural_key=("sample_item_id", "target_ion_id"),
            value_columns=(
                "match_score",
                "match_category",
                "sample_peak_intensity_sum",
            ),
            utc_created_column="match_ion_utc_created",
            utc_modified_column="match_ion_utc_modified",
            rows=match_ions,
        )
        await session.commit()

    # Generate result message
    total_requested = len(match_ions)
    unchanged_count = total_requested - created_count - updated_count

    if created_count > 0 and (updated_count > 0 or unchanged_count > 0):
        status = "partial"
        message = f"Processed {total_requested} match ions: {created_count} created, {updated_count} updated, {unchanged_count} unchanged"
    elif created_count > 0 or updated_count > 0:
        status = "success"
        action = "created" if created_count > 0 else "updated"
        count = created_count if created_count > 0 else updated_count
        message = f"{action.title()} {count}/{total_requested} match ion{'s' if count != 1 else ''}"
    else:
        status = "skipped"
        message = f"All {unchanged_count} match ions unchanged"

    runtime.logger.info(message)

    return {
        "status": status,
        "message": message,
        "data": changed_rows,
    }


@api_controller()
async def delete_match_ions(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_ion_ids: list[str] | None = None,
) -> dict:
    """
    Deletes match ions for specified sample items, optionally filtered by target ion IDs.
    This operation supports deletion by either a single sample item ID, a batch of sample items from a sample batch,
    or can be restricted to specific ions if target ion IDs are provided.

    Steps:
    1. Validate the input to ensure that either a sample item ID or a sample batch ID is provided.
    2. If a sample batch ID is provided, fetch the associated sample item IDs.
    3. Construct and execute a delete query for match ions based on the resolved sample item IDs.
       Apply an additional filter to restrict the deletion to specific target ions if these IDs are provided.
    4. Commit the transaction and report the number of deleted records.

    :param sample_item_id: ID of the single sample item for which match ions are to be deleted, optional.
    :type sample_item_id: str | None
    :param sample_batch_id: ID of the sample batch from which sample items are derived for deletion, optional.
    :type sample_batch_id: str | None
    :param target_ion_ids: Optional list of target ion IDs to further filter the match ions to be deleted.
    :type target_ion_ids: list[str] | None
    :return: A message indicating the outcome of the deletion process including the count of deleted records.
    :rtype: dict
    """
    sample_item_ids, sample_ref = await fetch_sample_item_ids(
        sample_item_id, sample_batch_id
    )

    async with async_session() as session:
        await acquire_match_write_locks(session, sample_item_ids)
        query = delete(MatchIon).where(MatchIon.sample_item_id.in_(sample_item_ids))
        if target_ion_ids:
            query = query.where(MatchIon.target_ion_id.in_(target_ion_ids))
        result = await session.execute(query)
        await session.commit()
        deleted_count = result.rowcount

    message = f"{deleted_count} match ion{'s' if deleted_count != 1 else ''} deleted for {sample_ref}."
    if target_ion_ids:
        message += f" Limited by {len(target_ion_ids)} specified target ion{'s' if len(target_ion_ids) != 1 else ''}."

    runtime.logger.info(message)
    return {"message": message}
