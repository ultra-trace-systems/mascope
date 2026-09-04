"""Each sample's assignment status within a batch, for the sample browser.

The sample table lists the batch's samples and says nothing about which of
them have been assigned. Two things answer that, and they are not the same:
whether the sample has an assignment run of its own - an explicit run, or a
published external one - and what the batch ledger holds for it, since a
sample folded into the ledger is served from it even without a run
(``fold_view``). One read per batch gives both for every sample: the latest
completed run, and the sample's members of the ledger with how many carry an
assignment. The browser turns that into one badge per row
(``lib/assignmentStatus.js``).
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import and_, func, select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.db import (
    BatchPeak,
    BatchPeakOccurrence,
    PeakAssignmentRun,
    SampleItem,
    async_session,
)


RUN_COMPLETED = "completed"


def sample_status_record(
    sample_item_id: str, run: Optional[Any], n_members: int, n_assigned: int
) -> dict:
    """One sample's status: its latest completed run, if any, and its ledger
    membership."""
    return {
        "sample_item_id": sample_item_id,
        "run": (
            None
            if run is None
            else {
                "peak_assignment_run_id": run.peak_assignment_run_id,
                "engine": run.engine,
                "engine_version": run.engine_version,
                "peak_assignment_run_utc_created": run.peak_assignment_run_utc_created,
            }
        ),
        "n_members": int(n_members),
        "n_assigned": int(n_assigned),
    }


@api_controller()
async def get_batch_sample_assignment_status(sample_batch_id: str) -> dict:
    """Every sample of a batch with its assignment status.

    :param sample_batch_id: The batch.
    :return: Status envelope; ``data`` is one record per sample of the batch,
        in sample id order, including samples with nothing at all.
    """
    async with async_session() as session:
        sample_ids = (
            (
                await session.execute(
                    select(SampleItem.sample_item_id)
                    .where(SampleItem.sample_batch_id == sample_batch_id)
                    .order_by(SampleItem.sample_item_id)
                )
            )
            .scalars()
            .all()
        )
        # A member's candidate index is null exactly when it carries no
        # assignment, so counting the column counts the assigned members.
        counts = {
            sample_item_id: (members, assigned)
            for sample_item_id, members, assigned in (
                await session.execute(
                    select(
                        BatchPeakOccurrence.sample_item_id,
                        func.count(),
                        func.count(BatchPeakOccurrence.candidate),
                    )
                    .join(
                        BatchPeak,
                        BatchPeak.batch_peak_id == BatchPeakOccurrence.batch_peak_id,
                    )
                    .where(BatchPeak.sample_batch_id == sample_batch_id)
                    .group_by(BatchPeakOccurrence.sample_item_id)
                )
            ).all()
        }
        latest = (
            select(
                PeakAssignmentRun.sample_item_id.label("sample_item_id"),
                func.max(PeakAssignmentRun.peak_assignment_run_utc_created).label(
                    "created"
                ),
            )
            .join(
                SampleItem,
                SampleItem.sample_item_id == PeakAssignmentRun.sample_item_id,
            )
            .where(
                SampleItem.sample_batch_id == sample_batch_id,
                PeakAssignmentRun.status == RUN_COMPLETED,
            )
            .group_by(PeakAssignmentRun.sample_item_id)
            .subquery()
        )
        runs = {
            run.sample_item_id: run
            for run in (
                await session.execute(
                    select(PeakAssignmentRun).join(
                        latest,
                        and_(
                            PeakAssignmentRun.sample_item_id == latest.c.sample_item_id,
                            PeakAssignmentRun.peak_assignment_run_utc_created
                            == latest.c.created,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        }
    data = [
        sample_status_record(
            sample_id, runs.get(sample_id), *counts.get(sample_id, (0, 0))
        )
        for sample_id in sample_ids
    ]
    with_run = sum(1 for record in data if record["run"] is not None)
    from_ledger = sum(
        1 for record in data if record["run"] is None and record["n_assigned"]
    )
    return {
        "status": "success",
        "message": (
            f"{len(data)} sample{'s' if len(data) != 1 else ''}: {with_run} with a run "
            f"of their own, {from_ledger} served from the batch ledger."
        ),
        "results": len(data),
        "data": data,
    }
