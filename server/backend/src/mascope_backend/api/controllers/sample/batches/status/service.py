"""
Service to handle the status of sample batches.
"""

from datetime import datetime, timezone

from sqlalchemy import case, exists, or_, select, update

from mascope_backend.api.lib.api_features import (
    api_controller,
)
from mascope_backend.api.models.sample.batches.config import sample_batch_config
from mascope_backend.db import SampleBatch, SampleItem, async_session
from mascope_backend.runtime import runtime
from mascope_backend.socket.records import emit_record_updated


# The pending statuses from weakest to strongest. A stronger one covers a
# weaker: a recalibration ends in a rematch.
PENDING_STATUS_RANK = ["rematch", "recalibrate"]


@api_controller()
async def update_sample_batch_status(
    sample_batch_ids: list[str],
    status: str,
    independent_transaction: bool = False,
    never_lower: bool = False,
    only_over: list[str] | None = None,
) -> dict:
    """
    Updates the status of multiple sample batches and emits targeted partial updates.

    :param sample_batch_ids: List of sample batch IDs to update
    :type sample_batch_ids: list[str]
    :param status: New status to set for all specified batches
    :type status: str
    :param independent_transaction: Flag indicating if operation is independent transaction
    :type independent_transaction: bool
    :param never_lower: Leave a batch with samples alone when it already waits
        on a stronger pending status (``PENDING_STATUS_RANK``), so a
        ``rematch`` request keeps a pending ``recalibrate``. For flaggers that
        ask for work; a re-process that clears the flag leaves it off.
    :type never_lower: bool
    :param only_over: Write only over these statuses, and leave a batch at
        any other alone. For a run that set ``processing`` and finishes:
        an edit that flagged the batch meanwhile asked for work after the
        run's, so its flag stays.
    :type only_over: list[str] | None
    :raises NotFoundException: When batches are not found
    :raises ValueError: When status is invalid
    :return: Update results with count of affected batches
    :rtype: dict
    """
    if status not in sample_batch_config.SAMPLE_BATCH_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Must be one of: {', '.join(sample_batch_config.SAMPLE_BATCH_STATUSES)}"
        )

    has_samples = exists().where(
        SampleItem.sample_batch_id == SampleBatch.sample_batch_id
    )
    guards = []
    if never_lower and status in PENDING_STATUS_RANK:
        stronger = PENDING_STATUS_RANK[PENDING_STATUS_RANK.index(status) + 1 :]
        if stronger:
            guards.append(or_(SampleBatch.status.not_in(stronger), ~has_samples))
    if only_over is not None:
        guards.append(SampleBatch.status.in_(only_over))

    async with async_session() as session:
        result = await session.execute(
            update(SampleBatch)
            .where(
                SampleBatch.sample_batch_id.in_(sample_batch_ids),
                *guards,
                # update if current status would change
                SampleBatch.status
                != case(
                    (has_samples, status),
                    else_="ready",
                ),
            )
            .values(
                status=case(
                    (has_samples, status),  # has samples: use requested status
                    else_="ready",  # no samples: always set to "ready"
                ),
                sample_batch_utc_modified=datetime.now(timezone.utc),
            )
        )

        updated_count = result.rowcount
        await session.commit()

        # Get affected datasets for room targeting, and the status each batch
        # holds now: a guarded or sample-less batch did not take ``status``
        batches = (
            await session.execute(
                select(
                    SampleBatch.sample_batch_id,
                    SampleBatch.dataset_id,
                    SampleBatch.status,
                ).where(SampleBatch.sample_batch_id.in_(sample_batch_ids))
            )
        ).all()

        # Emit targeted partial (status field) update events for each batch
        if independent_transaction:
            for batch_id, dataset_id, batch_status in batches:
                await emit_record_updated(
                    record_type="batch",
                    record_id=batch_id,
                    record={
                        "status": batch_status,
                    },  # Partial record - only changed field
                    room=dataset_id,
                    changed_fields=["status"],  # Signals frontend to merge
                )

        message = f"Updated {updated_count}/{len(sample_batch_ids)} batches to status '{status}'"
        if updated_count > 0:
            runtime.logger.info(message)

        return {
            "status": "success",
            "message": message,
            "data": {
                "total_batches": len(sample_batch_ids),
                "updated_batches": updated_count,
                "skipped_batches": len(sample_batch_ids) - updated_count,
                "target_status": status,
            },
        }
