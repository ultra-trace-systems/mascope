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
from mascope_backend.api.models.match.samples.match_sample_pydantic_model import (
    MATCH_SAMPLE_SORT_COLUMNS,
    MatchSampleBase,
)
from mascope_backend.db import MatchSample, SampleItem, async_session
from mascope_backend.runtime import runtime


@api_controller()
async def get_match_samples(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    match_category_min: int | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a list of match samples based on filter criteria, including sample item ID and batch ID.
    Results can be sorted and paginated according to the provided parameters.

    Steps:
    - Construct a query to fetch match samples.
    - Apply filtering based on sample item ID, sample batch ID, and match category if specified.
    - Apply sorting if specified.
    - Count the total matching samples for pagination.
    - Apply pagination and execute the query.
    - Format the fetched match samples into a list of dictionaries for response.

    :param sample_item_id: Filter match samples by sample item ID, defaults to None.
    :type sample_item_id: str | None, optional
    :param sample_batch_id: Filter match samples by sample batch ID, defaults to None.
    :type sample_batch_id: str | None, optional
    :param match_category_min: Filter by match_category to include specified category and higher (e.g., 1 includes categories 1 and higher), defaults to None.
    :type match_category_min: int | None, optional
    :param sort: Column to sort the results by, defaults to None.
    :type sort: str | None, optional
    :param order: Sort order, either 'asc' or 'desc', defaults to None.
    :type order: str | None, optional
    :param page: Page number for pagination, defaults to None.
    :type page: int | None, optional
    :param limit: Number of items per page, defaults to None (return all).
    :type limit: int | None, optional
    :return: A dictionary containing the total count and a list of match samples.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        # Construct base query
        query = select(MatchSample)

        # Apply filters
        if sample_item_id:
            query = query.filter(MatchSample.sample_item_id == sample_item_id)
        if match_category_min is not None:
            query = query.filter(MatchSample.match_category == match_category_min)
        if sample_batch_id:
            query = query.join(
                SampleItem, SampleItem.sample_item_id == MatchSample.sample_item_id
            ).filter(SampleItem.sample_batch_id == sample_batch_id)

        # Apply sorting
        if sort:
            query = query.order_by(
                order_by_column(MatchSample, sort, order, MATCH_SAMPLE_SORT_COLUMNS)
            )

        # Count total matching samples
        count_stmt = select(func.count()).select_from(query.subquery())
        total = await session.scalar(count_stmt)

        # Apply pagination
        if page is not None and limit is not None:
            query = query.offset(page * limit).limit(limit)

        # Execute the query
        result = await session.execute(query)
    data = [item.to_dict() for item in result.scalars().all()]

    return {
        "message": "Match samples retrieved successfully",
        "results": total,
        "data": data,
    }


@api_controller()
async def get_match_sample(match_sample_id: str) -> dict:
    """
    Retrieves information for a specific match sample by its unique identifier.

    Steps:
    1. Fetch the match sample using the provided ID to ensure it exists.
    2. Check if the match sample is found; if not, raise a NotFoundException.
    3. Return the match sample's details as a dictionary.

    :param match_sample_id: Unique identifier of the match sample to retrieve.
    :type match_sample_id: str
    :raises NotFoundException: If the match sample with the given ID is not found.
    :return: The detailed information of the match sample.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Fetch match sample by ID
        sample = await session.get(MatchSample, match_sample_id)

    # Step 2: Check if the sample is found
    if not sample:
        raise NotFoundException(f"Match sample with ID '{match_sample_id}' not found")

    # Step 3: Return sample details
    return {"message": "Match sample retrieved successfully", "data": sample.to_dict()}


@api_controller()
async def create_match_samples(
    match_samples: list[MatchSampleBase],
    independent_transaction: bool = False,
) -> dict:
    """
    Creates or updates match samples for a given sample based on the provided list of
    aggregated match sample data.
    Updates existing records if data differs, skips if identical, creates if new.

    :param match_samples: List of match sample data for creating matches
    :type match_samples: List[MatchSampleBase]
    :param independent_transaction: Indicates if operation should be independent
    :type independent_transaction: bool
    :return: Creation results with counts of new vs existing records
    :rtype: dict
    """
    if not match_samples:
        return {"message": "No match samples provided", "data": []}

    async with async_session() as session:
        # Serialize with every other match writer of the affected batches;
        # holds until commit, making the upsert below race-free.
        await acquire_match_write_locks(
            session, {row_value(ms, "sample_item_id") for ms in match_samples}
        )
        # Bulk upsert on the natural key (one row per sample): inserts missing
        # rows, updates only rows whose values changed, leaves identical rows
        # untouched.
        created_count, updated_count, changed_rows = await bulk_upsert_match_level(
            session,
            MatchSample,
            id_column="match_sample_id",
            natural_key=("sample_item_id",),
            value_columns=(
                "match_score",
                "match_category",
                "sample_peak_intensity_sum",
            ),
            utc_created_column="match_sample_utc_created",
            utc_modified_column="match_sample_utc_modified",
            rows=match_samples,
        )
        await session.commit()

    # Generate result message
    total_requested = len(match_samples)
    unchanged_count = total_requested - created_count - updated_count

    if created_count > 0 and (updated_count > 0 or unchanged_count > 0):
        status = "partial"
        message = f"Processed {total_requested} match samples: {created_count} created, {updated_count} updated, {unchanged_count} unchanged"
    elif created_count > 0 or updated_count > 0:
        status = "success"
        action = "created" if created_count > 0 else "updated"
        count = created_count if created_count > 0 else updated_count
        message = f"{action.title()} {count}/{total_requested} match sample{'s' if count != 1 else ''}"
    else:
        status = "skipped"
        message = f"All {unchanged_count} match samples unchanged"

    runtime.logger.info(message)

    return {
        "status": status,
        "message": message,
        "data": changed_rows,
    }


@api_controller()
async def delete_match_samples(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    sample_item_ids: list[str] | None = None,
):
    """
    Deletes match samples for specified sample items.

    Steps:
    1. Resolve the target sample item IDs: an explicit `sample_item_ids` list
       takes precedence (used by partial/orphan removal to narrow the delete
       to affected samples only); otherwise resolve from `sample_item_id` /
       `sample_batch_id` with the utility function.
    2. Construct and execute a delete query for match samples based on the sample item IDs.
    3. Commit the transaction and report the number of deleted records.

    :param sample_item_id: ID of the single sample item, optional.
    :param sample_batch_id: ID of the sample batch, optional.
    :param sample_item_ids: Explicit sample item IDs to delete for; overrides
        the other scope parameters when provided.
    :return: A message indicating the outcome of the deletion process.
    """
    if sample_item_ids is not None:
        sample_ref = (
            f"{len(sample_item_ids)} sample item"
            f"{'s' if len(sample_item_ids) != 1 else ''}"
        )
    else:
        sample_item_ids, sample_ref = await fetch_sample_item_ids(
            sample_item_id, sample_batch_id
        )
    async with async_session() as session:
        await acquire_match_write_locks(session, sample_item_ids)
        query = delete(MatchSample).where(
            MatchSample.sample_item_id.in_(sample_item_ids)
        )
        result = await session.execute(query)
        await session.commit()
        deleted_count = result.rowcount

    message = f"{deleted_count} match sample{'s' if deleted_count != 1 else ''} deleted for {sample_ref}."
    runtime.logger.info(message)
    return {"message": message}
