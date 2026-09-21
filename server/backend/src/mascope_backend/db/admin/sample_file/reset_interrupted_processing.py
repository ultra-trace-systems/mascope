"""
Database operation for sample files whose auto-processing a restart cut short.

Auto-processing runs as a detached task in a server worker and records each
stage it reaches on the sample file (``processing_status``). A worker that
stops mid-run leaves the last stage it recorded: a graceful shutdown cancels
what its drain could not finish, and a killed worker records nothing at all.
Nothing resumes the run afterwards, so the file would say "bound" or
"calibrated" for good, as if it were still being worked on.

This marks those files ``failed`` at startup, mirroring
:func:`~mascope_backend.db.admin.batch.reset_processing_status.reset_stuck_processing_batches`.
Startup runs in the main process before any worker is spawned, so no pipeline
can be running at that moment: every in-progress status is a leftover. The
file keeps whatever its run committed; re-processing it from Raw files starts
over.

Entry Points:
- Async: `reset_interrupted_processing()` for use in async code
- Sync: `run_reset_interrupted_processing()` for CLI and scripts
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import update

from mascope_backend.api.models.sample.files.config import (
    IN_PROGRESS,
    ProcessingStatus,
)
from mascope_backend.db import SampleFile, async_session
from mascope_backend.runtime import runtime


INTERRUPTED_DETAIL = (
    "Processing was interrupted by a server restart. Process the file again "
    "from Raw files."
)


async def reset_interrupted_processing() -> dict:
    """
    Mark sample files left in an in-progress processing status as failed.

    Never raises: reclaiming stale statuses is not worth refusing to boot
    over, and a database not yet upgraded to the columns can legitimately
    lack them. A failure is logged and swallowed.

    :return: Operation results with the count of reset files, and the files
        themselves under ``data.files``
    :rtype: dict
    """
    try:
        async with async_session() as session:
            files = [
                dict(row._mapping)
                for row in await session.execute(
                    update(SampleFile)
                    .where(
                        SampleFile.processing_status.in_(
                            [status.value for status in IN_PROGRESS]
                        )
                    )
                    .values(
                        processing_status=ProcessingStatus.FAILED.value,
                        processing_detail=INTERRUPTED_DETAIL,
                        processing_updated_utc=datetime.now(timezone.utc),
                    )
                    .returning(
                        SampleFile.sample_file_id,
                        SampleFile.filename,
                        SampleFile.instrument,
                        SampleFile.datetime_utc,
                        SampleFile.uploaded_by_user_id,
                    )
                )
            ]
            reset_count = len(files)
            await session.commit()
    except Exception as error:
        message = f"Could not reset interrupted sample file processing: {error}"
        runtime.logger.warning(message)
        return {
            "status": "skipped",
            "message": message,
            "data": {"reset_count": 0, "files": []},
        }

    if reset_count == 0:
        message = "No interrupted sample file processing found"
        runtime.logger.debug(message)
    else:
        message = (
            f"Marked {reset_count} sample file(s) whose processing a restart "
            "interrupted as failed"
        )
        # INFO: each is a file a person has to re-process, so the count
        # belongs in the log a restart leaves
        runtime.logger.info(message)

    return {
        "status": "success",
        "message": message,
        "data": {"reset_count": reset_count, "files": files},
    }


def run_reset_interrupted_processing() -> dict:
    """
    Synchronous wrapper for CLI and script entry points.

    :return: Operation results with the count of reset files
    :rtype: dict
    """
    return asyncio.run(reset_interrupted_processing())
