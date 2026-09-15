from sqlalchemy import (
    delete,
    func,
    select,
)

from mascope_backend.api.controllers.match.lib.match_upsert import (
    bulk_upsert_match_level,
    row_value,
)
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
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.match.collections.match_collection_pydantic_model import (
    MATCH_COLLECTION_SORT_COLUMNS,
    MatchCollectionBase,
)
from mascope_backend.db import MatchCollection, SampleItem, async_session
from mascope_backend.runtime import runtime


@api_controller()
async def get_match_collections(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_collection_id: str | None = None,
    match_category_min: int | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a list of matched collections based on filter criteria.
    Results can be sorted and optionally paginated according to the provided parameters.

    Steps:
    1. Construct the base query to select from MatchCollection.
    2. Apply filters based on provided criteria such as sample item ID, batch ID, target collection ID, and match category.
    3. Apply sorting if specified.
    4. Count the total matched collections for pagination.
    5. Apply pagination if specified and execute the query to fetch results.
    6. Convert the fetched match collections into a list of dictionaries for response.

    :param sample_item_id: Filter match collections by the ID of the sample item, defaults to None.
    :type sample_item_id: str | None, optional
    :param sample_batch_id: Filter match collections by the ID of the sample batch, defaults to None.
    :type sample_batch_id: str | None, optional
    :param target_collection_id: Filter match collections by the ID of the target collection, defaults to None.
    :type target_collection_id: str | None, optional
    :param match_category_min: Filter by match_category to include specified category and higher (e.g., 1 includes categories 1 and higher), defaults to None.
    :type match_category_min: int | None, optional
    :param sort: Column to sort the results by, defaults to None.
    :type sort: str | None, optional
    :param order: Sort order, either 'asc' for ascending or 'desc' for descending, defaults to None.
    :type order: str | None, optional
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None, optional
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None, optional
    :return: A dictionary containing the total count and a list of matched collections.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        # Step 1: Construct base query
        query = select(MatchCollection)

        # Step 2: Apply filters
        if sample_item_id:
            query = query.filter(MatchCollection.sample_item_id == sample_item_id)
        if target_collection_id:
            query = query.filter(
                MatchCollection.target_collection_id == target_collection_id
            )
        if match_category_min is not None:
            query = query.filter(MatchCollection.match_category == match_category_min)
        if sample_batch_id:
            query = query.join(
                SampleItem, SampleItem.sample_item_id == MatchCollection.sample_item_id
            ).filter(SampleItem.sample_batch_id == sample_batch_id)

        # Step 3: Apply sorting
        if sort:
            query = query.order_by(
                order_by_column(
                    MatchCollection, sort, order, MATCH_COLLECTION_SORT_COLUMNS
                )
            )

        # Step 4: Count total matching collections
        count_stmt = select(func.count()).select_from(query.subquery())
        total = await session.scalar(count_stmt)

        # Step 5: Apply pagination
        if page is not None and limit is not None:
            query = query.offset(page * limit).limit(limit)
        # Step 6: Execute the query and fetch results
        result = await session.execute(query)
    data = [item.to_dict() for item in result.scalars().all()]

    return {
        "message": "Match collections retrieved successfully",
        "results": total,
        "data": data,
    }


@api_controller()
async def get_match_collection(match_collection_id: str) -> dict:
    """
    Retrieves information for a specific match collection by its ID.

    Steps:
    1. Fetch the match collection using the provided ID to ensure it exists.
    2. Check if the match collection is found; if not, raise a NotFoundException.
    3. Return the match collection's details as a dictionary.

    :param match_collection_id: Unique identifier of the match collection to retrieve.
    :type match_collection_id: str
    :raises NotFoundException: If the match collection with the given ID is not found.
    :return: The detailed information of the match collection.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch match collection by ID
        collection = await session.get(MatchCollection, match_collection_id)

    # Step 2: Check if the collection is found
    if not collection:
        raise NotFoundException(
            f"Match collection with ID '{match_collection_id}' not found"
        )

    # Step 3: Return collection details
    return {
        "message": "Match collection retrieved successfully",
        "data": collection.to_dict(),
    }


@api_controller()
async def create_match_collections(
    match_collections: list[MatchCollectionBase],
    independent_transaction: bool = False,
) -> dict:
    """
    Creates match collections for a given samples based on the provided list of
    aggregated match collection data.
    Updates existing records if data differs, skips if identical, creates if new.

    :param match_collections: List of match collection data for creating matches
    :type match_collections: list[MatchCollectionBase]
    :param independent_transaction: Indicates if operation should be independent
    :type independent_transaction: bool
    :return: Creation results with counts of new vs existing records
    :rtype: dict
    """
    if not match_collections:
        return {"message": "No match collections provided", "data": []}

    async with async_session() as session:
        # Serialize with every other match writer of the affected batches;
        # holds until commit, making the upsert below race-free.
        await acquire_match_write_locks(
            session, {row_value(mc, "sample_item_id") for mc in match_collections}
        )
        # Bulk upsert on the natural key: inserts missing rows, updates only
        # rows whose values changed, leaves identical rows untouched.
        created_count, updated_count, changed_rows = await bulk_upsert_match_level(
            session,
            MatchCollection,
            id_column="match_collection_id",
            natural_key=("sample_item_id", "target_collection_id"),
            value_columns=(
                "match_score",
                "match_category",
                "sample_peak_intensity_sum",
            ),
            utc_created_column="match_collection_utc_created",
            utc_modified_column="match_collection_utc_modified",
            rows=match_collections,
        )
        await session.commit()

    # Generate result message
    total_requested = len(match_collections)
    unchanged_count = total_requested - created_count - updated_count

    if created_count > 0 and (updated_count > 0 or unchanged_count > 0):
        status = "partial"
        message = f"Processed {total_requested} match collections: {created_count} created, {updated_count} updated, {unchanged_count} unchanged"
    elif created_count > 0 or updated_count > 0:
        status = "success"
        action = "created" if created_count > 0 else "updated"
        count = created_count if created_count > 0 else updated_count
        message = f"{action.title()} {count}/{total_requested} match collection{'s' if count != 1 else ''}"
    else:
        status = "skipped"
        message = f"All {unchanged_count} match collections unchanged"

    runtime.logger.info(message)

    return {
        "status": status,
        "message": message,
        "data": changed_rows,
    }


@api_controller()
async def delete_match_collections(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_collections_ids: list[str] | None = None,
):
    """
    Deletes match collections for specified sample items, optionally filtered by target collection IDs.

    Steps:
    1. Fetch sample item IDs using the utility function.
    2. Construct and execute a delete query for match collections based on the sample item IDs.
       Apply an additional filter to restrict the deletion to specific target collections if these IDs are provided.
    3. Commit the transaction and report the number of deleted records.

    :param sample_item_id: ID of the single sample item, optional.
    :param sample_batch_id: ID of the sample batch, optional.
    :param target_collections_ids: Optional list of target collection IDs.
    :return: A message indicating the outcome of the deletion process.
    """
    sample_item_ids, sample_ref = await fetch_sample_item_ids(
        sample_item_id, sample_batch_id
    )
    async with async_session() as session:
        await acquire_match_write_locks(session, sample_item_ids)
        query = delete(MatchCollection).where(
            MatchCollection.sample_item_id.in_(sample_item_ids)
        )
        if target_collections_ids:
            query = query.where(
                MatchCollection.target_collection_id.in_(target_collections_ids)
            )
        result = await session.execute(query)
        await session.commit()
        deleted_count = result.rowcount

    message = f"{deleted_count} match collection{'s' if deleted_count != 1 else ''} deleted for {sample_ref}."
    if target_collections_ids:
        message += f" Limited by specified {len(target_collections_ids)} target collection{'s' if len(target_collections_ids) != 1 else ''}."

    runtime.logger.info(message)
    return {"message": message}
