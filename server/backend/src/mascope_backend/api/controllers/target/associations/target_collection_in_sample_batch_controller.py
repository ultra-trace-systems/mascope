from sqlalchemy import (
    func,
    select,
)

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.sorting import order_by_column
from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    TargetCollectionInSampleBatchSortColumn,
)
from mascope_backend.db import TargetCollectionInSampleBatch, async_session


@api_controller()
async def get_target_collections_in_sample_batch(
    sample_batch_id: str | None = None,
    target_collection_id: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int | None = None,
    limit: int | None = None,
):
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        stmt = select(TargetCollectionInSampleBatch)

        if sample_batch_id:
            stmt = stmt.filter(
                TargetCollectionInSampleBatch.sample_batch_id == sample_batch_id
            )

        if target_collection_id:
            stmt = stmt.filter(
                TargetCollectionInSampleBatch.target_collection_id
                == target_collection_id
            )

        if sort:
            stmt = stmt.order_by(
                order_by_column(
                    TargetCollectionInSampleBatch,
                    sort,
                    order,
                    TargetCollectionInSampleBatchSortColumn,
                )
            )

        # Get total count
        count_stmt = select(func.count()).select_from(stmt)
        total = await session.scalar(count_stmt)

        # Get paginated results
        if page is not None and limit is not None:
            stmt = stmt.offset(page * limit).limit(limit)
        result = await session.execute(stmt)
        target_collections_in_sample_batch = result.scalars().all()

        return {
            "message": "Target collections in sample batch retrieved successfully.",
            "results": total,
            "data": [
                target_collection_in_sample_batch.to_dict()
                for target_collection_in_sample_batch in target_collections_in_sample_batch
            ],
        }
