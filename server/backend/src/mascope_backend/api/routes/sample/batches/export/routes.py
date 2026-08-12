"""
Sample batch export API routes.
"""

from fastapi import APIRouter, BackgroundTasks, Depends

from mascope_backend.api.controllers.sample.batches.export.service import (
    sample_batch_export_spreadsheet,
)
from mascope_backend.api.controllers.sample.batches.sample_batches_controller import (
    get_sample_batch,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.workspaces.dependencies import require_batch_role
from mascope_backend.db.id import gen_id


sample_batches_export_router = APIRouter(tags=["Sample Batches Export"])


@sample_batches_export_router.post("/{sample_batch_id}/export/spreadsheet")
@api_route(status_code=202)
async def sample_batch_export_spreadsheet_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Export batch data and matches to Excel spreadsheet.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param background_tasks: Background task handler.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: A dictionary containing a message and process ID.
    :rtype: dict
    :raises NotFoundException: If sample batch not found
    """
    # Verify the existence of sample batch
    sample_batch_result = await get_sample_batch(sample_batch_id)
    sample_batch = sample_batch_result.get("data")
    sample_batch_name = sample_batch["sample_batch_name"]

    process_id = gen_id(8)

    background_tasks.add_task(
        sample_batch_export_spreadsheet,
        sample_batch_id=sample_batch_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Exporting spreadsheet for batch '{sample_batch_name}', please wait.",
        "process_id": process_id,
    }
