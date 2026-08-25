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
from mascope_backend.api.models.match.compounds.match_compound_pydantic_model import (
    MatchCompoundBase,
)
from mascope_backend.db import (
    MatchCompound,
    Sample,
    TargetCollection,
    TargetCollectionInSampleBatch,
    TargetCompound,
    TargetCompoundInTargetCollection,
    async_session,
)
from mascope_backend.runtime import runtime
from mascope_file.name import resolve_instrument_type


@api_controller()
async def get_match_compounds(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_compound_id: str | None = None,
    match_category_min: int | None = None,
    deduplicate: bool = False,
    show_target_collection: bool = False,
    show_target_compound: bool = False,
    sort: str | None = None,
    order: str | None = None,
    page: int = 0,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a list of matched compounds based on filtering criteria. This function allows
    for querying with options to include additional related data such as target compounds and collections.

    Steps:
    1. Construct the base query for fetching match compounds from the database.
    2. Apply filters based on provided sample item ID, sample batch ID, target compound ID, and match category.
    3. Optionally join with the TargetCompound and TargetCollection tables if related data is requested.
    4. Apply sorting if a sort column and order are specified.
    5. Count the total entries that match the criteria for pagination purposes.
    6. Limit the query for pagination and fetch the results.
    7. Format the fetched data into a dictionary for the response.
    8. If deduplication is requested and `show_target_collection` is True, deduplicate the compounds.

    :param sample_item_id: Filter matches by the associated sample item's ID, defaults to None.
    :type sample_item_id: str | None, optional
    :param sample_batch_id: Filter matches by the associated sample batch's ID, defaults to None.
    :type sample_batch_id: str | None, optional
    :param target_compound_id: Filter matches by the associated target compound's ID, defaults to None.
    :type target_compound_id: str | None, optional
    :param match_category_min: Filter by match_category to include specified category and higher (e.g., 1 includes categories 1 and higher), defaults to None.
    :type match_category_min: int | None, optional
    :param deduplicate: Flag to indicate whether compound deduplication should be applied when show_target_collection is True, defaults to False.
    :type deduplicate: bool
    :param show_target_collection: Include additional data about the target collections, defaults to False.
    :type show_target_collection: bool, optional
    :param show_target_compound: Include additional data about the target compounds, defaults to False.
    :type show_target_compound: bool, optional
    :param sort: Column name to sort by, defaults to None.
    :type sort: str | None, optional
    :param order: Order of sorting, 'asc' for ascending or 'desc' for descending, defaults to None.
    :type order: str | None, optional
    :param page: Page number for pagination, starts from 0, defaults to 0.
    :type page: int, optional
    :param limit: Maximum number of results per page, defaults to None (return all).
    :type limit: int, optional
    :return: A dictionary containing total results count and the paginated list of match compounds.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Define the main query for match compounds
        query = select(MatchCompound)

        # Step 2: Apply basic fields filters if provided
        if sample_item_id:
            query = query.filter(MatchCompound.sample_item_id == sample_item_id)
            if not sample_batch_id:
                query = query.join(Sample, Sample.sample_item_id == sample_item_id)
        if target_compound_id:
            query = query.filter(MatchCompound.target_compound_id == target_compound_id)
        if match_category_min is not None:
            query = query.filter(MatchCompound.match_category >= match_category_min)

        # Join with TargetCompound table to include target_compound data if requested
        if show_target_compound:
            query = query.join(
                TargetCompound,
                TargetCompound.target_compound_id == MatchCompound.target_compound_id,
            ).add_columns(
                TargetCompound.target_compound_name,
                TargetCompound.target_compound_formula,
            )

        if sample_batch_id:
            query = query.join(
                Sample, Sample.sample_item_id == MatchCompound.sample_item_id
            ).where(Sample.sample_batch_id == sample_batch_id)

        if sample_batch_id or sample_item_id:
            query = query.add_columns(Sample.instrument)

        # Join with TargetCompoundInTargetCollection to include target_collection data
        if show_target_collection:
            query = (
                query.join(
                    TargetCompoundInTargetCollection,
                    TargetCompoundInTargetCollection.target_compound_id
                    == MatchCompound.target_compound_id,
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
                .distinct()
            )
        # Step 4: Apply sorting
        if sort:
            sort_expression = getattr(MatchCompound, sort, None)
            if sort_expression:
                if order == "desc":
                    query = query.order_by(sort_expression.desc())
                else:
                    query = query.order_by(sort_expression.asc())

        # Step 5: Count total
        count_stmt = select(func.count()).select_from(query.subquery())
        total = await session.scalar(count_stmt)

        # Step 6: Execute the paginated query
        if limit is not None:
            query = query.offset(page * limit).limit(limit)
        result = await session.execute(query)

    # Step 7: Construct response data
    data = []
    for row in result.all():
        match_compound_data = row.MatchCompound.to_dict()

        try:
            # Resolve correct intensity units based on the instrument type of the sample
            instrument_type = resolve_instrument_type(row.instrument)
            if instrument_type == "tof":
                unit = "ions"
            else:
                unit = "counts"
            match_compound_data["unit"] = unit
        except AttributeError:
            pass

        if show_target_compound:
            match_compound_data["target_compound_name"] = row.target_compound_name
            match_compound_data["target_compound_formula"] = row.target_compound_formula
        if show_target_collection:
            match_compound_data["target_collection_id"] = row.target_collection_id
            match_compound_data["target_collection_name"] = row.target_collection_name
            match_compound_data["target_collection_type"] = row.target_collection_type
        data.append(match_compound_data)

    # Step 8: Deduplicate if requested and `show_target_collection` is True
    if deduplicate and show_target_collection:
        data_df = pd.DataFrame(data)
        data_df = deduplicate_match_df(
            data_df, id_keys=("target_compound_id", "sample_item_id")
        )
        # Nullable label columns come back as NaN under pandas 3's str dtype,
        # which starlette cannot serialize.
        data = strings_json_safe(data_df).to_dict(orient="records")
        # Update total after deduplication
        total = len(data)

    return {
        "message": "Match compounds retrieved successfully",
        "results": total,
        "data": data,
    }


@api_controller()
async def get_match_compound(match_compound_id: str) -> dict:
    """
    Retrieves information for a specific match compound identified by its ID.

    Steps:
    1. Fetch the match compound from the database using its ID to ensure it exists.
    2. If not found, raise a NotFoundException.
    3. Return the details of the match compound as a dictionary.

    :param match_compound_id: Unique identifier of the match compound to retrieve.
    :type match_compound_id: str
    :raises NotFoundException: If no match compound is found with the specified ID.
    :return: Detailed information of the match compound.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch match compound by ID
        compound = await session.get(MatchCompound, match_compound_id)

    # Step 2: Check if the compound exists
    if not compound:
        raise NotFoundException(
            f"Match compound with ID '{match_compound_id}' not found"
        )

    # Step 3: Return compound details
    return {
        "message": "Match compound retrieved successfully",
        "data": compound.to_dict(),
    }


@api_controller()
async def create_match_compounds(
    match_compounds: list[MatchCompoundBase],
    independent_transaction: bool = False,
) -> dict:
    """
    Creates match compounds for a given samples based on the provided list of
    aggregated match compound data.
    Updates existing records if data differs, skips if identical, creates if new.

    :param match_compounds: List of match compound data for creating matches
    :type match_compounds: list[MatchCompoundBase]
    :param independent_transaction: Indicates if operation should be independent
    :type independent_transaction: bool
    :return: Creation results with counts of new vs existing records
    :rtype: dict
    """
    if not match_compounds:
        return {"message": "No match compounds provided", "data": []}

    async with async_session() as session:
        # Serialize with every other match writer of the affected batches;
        # holds until commit, making the upsert below race-free.
        await acquire_match_write_locks(
            session, {row_value(mc, "sample_item_id") for mc in match_compounds}
        )
        # Bulk upsert on the natural key: inserts missing rows, updates only
        # rows whose values changed, leaves identical rows untouched.
        created_count, updated_count, changed_rows = await bulk_upsert_match_level(
            session,
            MatchCompound,
            id_column="match_compound_id",
            natural_key=("sample_item_id", "target_compound_id"),
            value_columns=(
                "match_score",
                "match_category",
                "sample_peak_intensity_sum",
            ),
            utc_created_column="match_compound_utc_created",
            utc_modified_column="match_compound_utc_modified",
            rows=match_compounds,
        )
        await session.commit()

    # Generate result message
    total_requested = len(match_compounds)
    unchanged_count = total_requested - created_count - updated_count

    if created_count > 0 and (updated_count > 0 or unchanged_count > 0):
        status = "partial"
        message = f"Processed {total_requested} match compounds: {created_count} created, {updated_count} updated, {unchanged_count} unchanged"
    elif created_count > 0 or updated_count > 0:
        status = "success"
        action = "created" if created_count > 0 else "updated"
        count = created_count if created_count > 0 else updated_count
        message = f"{action.title()} {count}/{total_requested} match compound{'s' if count != 1 else ''}"
    else:
        status = "skipped"
        message = f"All {unchanged_count} match compounds unchanged"

    runtime.logger.info(message)

    return {
        "status": status,
        "message": message,
        "data": changed_rows,
    }


@api_controller()
async def delete_match_compounds(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_compound_ids: list[str] | None = None,
):
    """
    Deletes match compounds for specified sample items, optionally filtered by target compound IDs.
    This operation can be performed on a single sample item or a batch of items and can be restricted to specific compounds if target compound IDs are provided.

    Steps:
    1. Fetch sample item IDs using the utility function.
    2. Construct and execute a delete query for match compounds based on the sample item IDs.
       Apply an additional filter to restrict the deletion to specific target compounds if these IDs are provided.
    3. Commit the transaction and report the number of deleted records.

    :param sample_item_id: ID of the single sample item, optional.
    :param sample_batch_id: ID of the sample batch, optional.
    :param target_compound_ids: Optional list of target compound IDs.
    :return: A message indicating the outcome of the deletion process.
    """
    sample_item_ids, sample_ref = await fetch_sample_item_ids(
        sample_item_id, sample_batch_id
    )
    async with async_session() as session:
        await acquire_match_write_locks(session, sample_item_ids)
        query = delete(MatchCompound).where(
            MatchCompound.sample_item_id.in_(sample_item_ids)
        )
        if target_compound_ids:
            query = query.where(
                MatchCompound.target_compound_id.in_(target_compound_ids)
            )
        result = await session.execute(query)
        await session.commit()
        deleted_count = result.rowcount

    message = f"{deleted_count} match compound{'s' if deleted_count != 1 else ''} deleted for {sample_ref}."
    if target_compound_ids:
        message += f" Limited by {len(target_compound_ids)} specified target compound{'s' if len(target_compound_ids) != 1 else ''}."

    runtime.logger.info(message)
    return {"message": message}
