"""
Sample batches fetch helper

This module contains helper functions for fetching and processing
sample batch-related data.
"""

from sqlalchemy import desc, select

from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.db import SampleBatch, async_session


async def fetch_sample_batch(sample_batch_id: str) -> SampleBatch:
    """
    Fetches the  SampleBatch object.

    :param sample_batch_id: ID of the sample batch to fetch.
    :type sample_batch_id: str
    :return: A SampleBatch object containing the sample batch data.
    :rtype: SampleBatch
    :raises NotFoundException: If the sample batch with the specified ID is not found.
    """
    async with async_session() as session:
        sample_batch = await session.get(SampleBatch, sample_batch_id)
        if not sample_batch:
            raise NotFoundException(
                f"Sample batch with ID '{sample_batch_id}' not found"
            )
        return sample_batch


async def fetch_dataset_sample_batch_ids(dataset_id: str) -> list[str]:
    """
    Fetches the IDs of every sample batch in a dataset, newest batch first.

    Only the IDs are read: the callers of this feed them to a batch-by-batch
    operation and never need the batch rows themselves, so the whole dataset's
    batches are not materialised.

    :param dataset_id: ID of the dataset whose batches to list.
    :type dataset_id: str
    :return: Sample batch IDs ordered by creation time, newest first.
    :rtype: list[str]
    """
    async with async_session() as session:
        result = await session.execute(
            select(SampleBatch.sample_batch_id)
            .where(SampleBatch.dataset_id == dataset_id)
            .order_by(desc(SampleBatch.sample_batch_utc_created))
        )
        return list(result.scalars().all())
