from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, insert, select

from mascope_backend.api.controllers.match.lib.match_write_lock import (
    acquire_match_write_locks,
)
from mascope_backend.api.controllers.sample.lib.sample_items_fetch import (
    fetch_sample_item_ids,
)
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import (
    DuplicateException,
    NotFoundException,
)
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.match.isotopes.match_isotopes_pydantic_model import (
    MatchIsotopeBase,
    MatchIsotopeSortColumn,
)
from mascope_backend.db import (
    MatchIsotope,
    SampleItem,
    TargetIsotope,
    async_session,
)
from mascope_backend.runtime import runtime


@api_controller()
async def get_match_isotopes(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_isotope_id: str | None = None,
    show_target_isotope: bool = False,
    sort: str | None = None,
    order: str | None = None,
    page: int = 0,
    limit: int | None = None,
) -> dict:
    """
    Retrieves a paginated list of matched isotopes, optionally filtered by sample item ID, target isotope ID, and sorted by a specified column.
    Supports optional inclusion of related target isotope data (e.g., mz, relative_abundance, and target_ion_id).

    Steps:
    1. Construct a SQLAlchemy query to select all matched isotopes.
    2. Apply filtering based on the provided parameters (`sample_item_id`, `target_isotope_id`, `sample_batch_id`).
    3. Optionally join with the `TargetIsotope` table to include related target isotope data if `show_target_isotope` is True.
    4. Apply sorting based on the specified `sort` column and `order`.
    5. Count the total number of matched isotopes for pagination.
    6. Limit the query for pagination and execute it to fetch the results.
    7. Format the fetched data into a list of dictionaries for the response.

    :param sample_item_id: Filter by sample item ID, defaults to None.
    :type sample_item_id: str | None, optional
    :param sample_batch_id: Filter by sample batch ID, defaults to None.
    :type sample_batch_id: str | None, optional
    :param target_isotope_id: Filter by target isotope ID, defaults to None.
    :type target_isotope_id: str | None, optional
    :param show_target_isotope: Include additional data about the target isotopes, defaults to False.
    :type show_target_isotope: bool, optional
    :param sort: Column to sort by, defaults to None.
    :type sort: str, optional
    :param order: Sorting order, 'asc' for ascending or 'desc', defaults to None.
    :type order: str, optional
    :param page: Page number for pagination, defaults to 0.
    :type page: int, optional
    :param limit: Number of items per page, defaults to None (return all).
    :type limit: int, optional
    :return: A dictionary with the total count and a list of matched isotopes.
    :rtype: dict
    """
    async with async_session() as session:
        # Step 1: Construct a SQLAlchemy query to select all matched isotopes
        stmt = select(MatchIsotope)

        # Step 2: Apply filters if specified
        if sample_item_id:
            stmt = stmt.filter(MatchIsotope.sample_item_id == sample_item_id)
        if target_isotope_id:
            stmt = stmt.filter(MatchIsotope.target_isotope_id == target_isotope_id)
        if sample_batch_id:
            stmt = stmt.join(
                SampleItem, SampleItem.sample_item_id == MatchIsotope.sample_item_id
            ).where(SampleItem.sample_batch_id == sample_batch_id)

        # Step 3: Join with TargetIsotope if requested
        if show_target_isotope:
            stmt = stmt.join(
                TargetIsotope,
                TargetIsotope.target_isotope_id == MatchIsotope.target_isotope_id,
            ).add_columns(
                TargetIsotope.mz,
                TargetIsotope.relative_abundance,
                TargetIsotope.target_ion_id,
            )

        # Step 4: Apply sorting
        if sort:
            stmt = stmt.order_by(
                order_by_column(MatchIsotope, sort, order, MatchIsotopeSortColumn)
            )

        # Step 5: Count total
        total = await session.scalar(select(func.count()).select_from(stmt))

        # Step 6: Apply pagination
        if limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)

        # Step 7: Execute query
        result = await session.execute(stmt)
        result = result.all()

    # Step 8: Construct response data
    data = []
    for row in result:
        match_isotope_data = row.MatchIsotope.to_dict()
        if show_target_isotope:
            match_isotope_data.update(
                {
                    "mz": row.mz,
                    "relative_abundance": row.relative_abundance,
                    "target_ion_id": row.target_ion_id,
                }
            )
        data.append(match_isotope_data)

    return {
        "message": "Match isotopes retrieved successfully",
        "results": total,
        "data": data,
    }


@api_controller()
async def get_match_isotope(match_isotope_id: str) -> dict:
    """
    Retrieves a single match by its unique ID.

    Steps:
    1. Execute a query to fetch the match with the specified ID.
    2. Check if the match exists. If not, raise a NotFoundException.
    3. Return the match's details as a dictionary.

    :param match_isotope_id: Unique identifier of the match to retrieve.
    :type match_isotope_id: str
    :return: The requested match's details.
    :rtype: dict
    :raises NotFoundException: If the match with the given ID is not found.
    """
    async with async_session() as session:
        # Step 1: Fetch match by ID
        match = await session.get(MatchIsotope, match_isotope_id)

    # Step 2: Check existence
    if not match:
        raise NotFoundException(f"MatchIsotope with ID '{match_isotope_id}' not found")

    # Step 3: Return match details
    return {"message": "Match isotope retrieved successfully", "data": match.to_dict()}


@api_controller()
async def delete_match_isotopes(
    sample_item_id: str | None = None,
    sample_batch_id: str | None = None,
    target_isotope_ids: list[str] | None = None,
) -> dict:
    """
    Deletes match isotopes for specified sample items, optionally filtered by target isotope IDs.
    This operation supports deletion by either a single sample item ID, a batch of sample items from a sample batch,
    or can be restricted to specific isotopes if target isotope IDs are provided.

    Steps:
    1. Validate the input to ensure that either a sample item ID or a sample batch ID is provided.
    2. If a sample batch ID is provided, fetch the associated sample item IDs.
    3. Construct and execute a delete query for match isotopes based on the resolved sample item IDs.
       Apply an additional filter to restrict the deletion to specific target isotopes if these IDs are provided.
    4. Commit the transaction and report the number of deleted records.

    :param sample_item_id: ID of the single sample item for which match isotopes are to be deleted, optional.
    :type sample_item_id: str | None
    :param sample_batch_id: ID of the sample batch from which sample items are derived for deletion, optional.
    :type sample_batch_id: str | None
    :param target_isotope_ids: Optional list of target isotope IDs to further filter the match isotopes to be deleted.
    :type target_isotope_ids: list[str] | None
    :return: A message indicating the outcome of the deletion process including the count of deleted records.
    :rtype: dict
    """
    sample_item_ids, sample_ref = await fetch_sample_item_ids(
        sample_item_id, sample_batch_id
    )

    async with async_session() as session:
        await acquire_match_write_locks(session, sample_item_ids)
        query = delete(MatchIsotope).where(
            MatchIsotope.sample_item_id.in_(sample_item_ids)
        )
        if target_isotope_ids:
            query = query.where(MatchIsotope.target_isotope_id.in_(target_isotope_ids))
        result = await session.execute(query)
        await session.commit()
        deleted_count = result.rowcount

    message = f"{deleted_count} match isotope{'s' if deleted_count != 1 else ''} deleted for {sample_ref}."
    if target_isotope_ids:
        message += f" Limited by {len(target_isotope_ids)} specified target isotope{'s' if len(target_isotope_ids) != 1 else ''}."

    runtime.logger.info(message)
    return {"message": message}


@api_controller()
async def create_match_isotopes(
    match_isotopes: list[MatchIsotopeBase],
    independent_transaction: bool = False,
) -> dict:
    """
    Creates match isotopes for a given sample item based on the provided list of match isotope data.

    Steps:
    1. Check for existing match isotopes to avoid duplication.
    2. Bulk-insert the new matches into the database and commit the transaction.

    :param match_isotopes: List of match isotope data for creating matches.
    :type match_isotopes: list[MatchIsotopeBase]
    :param independent_transaction: Indicates if the operation should be independent of any ongoing transactions, defaults to False.
    :type independent_transaction: bool, optional
    :return: The created match isotopes data.
    :rtype: dict
    :raises DuplicateException: If match isotopes already exist for the given sample item and target isotopes.
    """
    runtime.logger.info("Saving match isotopes to database")
    sample_item_id = match_isotopes[0].sample_item_id
    target_isotope_ids = [mi.target_isotope_id for mi in match_isotopes]

    # All column values are generated client-side, so the rows can be built up
    # front and inserted in bulk without reading anything back from the database.
    utc_created = datetime.now(timezone.utc)
    rows = [
        {
            **mi.model_dump(),
            "match_isotope_utc_created": utc_created,
            "match_isotope_utc_modified": None,
        }
        for mi in match_isotopes
    ]

    async with async_session() as session:
        # Serialize with every other match writer of the affected batch;
        # holds until commit, making the duplicate check below race-free.
        await acquire_match_write_locks(session, [sample_item_id])
        # Step 1: Check for existing matches to avoid duplication.
        stmt = (
            select(MatchIsotope.match_isotope_id)
            .where(
                and_(
                    MatchIsotope.sample_item_id == sample_item_id,
                    MatchIsotope.target_isotope_id.in_(target_isotope_ids),
                )
            )
            .limit(1)
        )
        # If existing match isotopes are found, raise a DuplicateException to prevent overwriting.
        if await session.scalar(stmt) is not None:
            raise DuplicateException(
                "Match isotopes already exist for the given sample item and target isotopes"
            )

        # Step 2: Bulk-insert the new match isotopes and commit the transaction.
        await session.execute(insert(MatchIsotope), rows)
        await session.commit()

    # Step 3: Return created match isotopes
    message = f"{len(rows)} match isotope(s) created successfully."
    runtime.logger.info(message)
    return {
        "message": message,
        "data": rows,
    }
