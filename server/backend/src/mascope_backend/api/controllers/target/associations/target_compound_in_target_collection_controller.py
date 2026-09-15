from sqlalchemy import (
    func,
    select,
)

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.target.compounds.target_compound_pydantic_model import (
    TARGET_COMPOUND_IN_TARGET_COLLECTION_SORT_COLUMNS,
)
from mascope_backend.db import (
    TargetCompoundInTargetCollection,
    async_session,
)


@api_controller()
async def get_target_compound_in_target_collection(
    target_compound_id: str | None = None,
    target_collection_id: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int | None = None,
    limit: int | None = None,
):
    """
    Retrieves a (paginated) list of target compounds in target collection, optionally filtered and sorted.

    :param target_compound_id: Filter by target compound ID, defaults to None.
    :type target_compound_id: str | None
    :param target_collection_id: Filter by target collection ID, defaults to None.
    :type target_collection_id: str | None
    :param sort: Column to sort by, defaults to None.
    :type sort: str | None
    :param order: Sorting order ('asc' or 'desc'), defaults to None.
    :type order: str | None
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None
    :param limit: Number of items per page, defaults to None (no pagination).
    :type limit: int | None
    :return: Dictionary with total count and list of entries.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        stmt = select(TargetCompoundInTargetCollection)

        if target_compound_id:
            stmt = stmt.filter(
                TargetCompoundInTargetCollection.target_compound_id
                == target_compound_id
            )

        if target_collection_id:
            stmt = stmt.filter(
                TargetCompoundInTargetCollection.target_collection_id
                == target_collection_id
            )

        if sort:
            stmt = stmt.order_by(
                order_by_column(
                    TargetCompoundInTargetCollection,
                    sort,
                    order,
                    TARGET_COMPOUND_IN_TARGET_COLLECTION_SORT_COLUMNS,
                )
            )

        # Get total count
        count_stmt = select(func.count()).select_from(stmt)
        total = await session.scalar(count_stmt)

        # Apply pagination if provided
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        result = await session.execute(stmt)
        target_compound_in_target_collections = result.scalars().all()

        return {
            "message": "Target compounds in target collection retrieved successfully.",
            "results": total,
            "data": [
                entry.to_dict() for entry in target_compound_in_target_collections
            ],
        }
