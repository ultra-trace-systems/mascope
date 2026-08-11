from mascope_backend.api.controllers.match.match_controller import (
    match_compute_sample,
)
from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    create_sample_items,
)
from mascope_backend.api.controllers.sample.lib.sample_file_fetch import (
    fetch_sample_file,
)
from mascope_backend.api.controllers.samples.samples_controller import get_sample
from mascope_backend.api.lib.api_features import (
    api_controller_background_task,
)
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    SampleItemCreate,
)
from mascope_backend.api.new.peak_assignments.service import (
    auto_assign_sample_peaks,
)
from mascope_backend.db.id import gen_id
from mascope_backend.socket.notifications import (
    UserNotification,
    send_progress_user_notification,
)


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    success_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
    error_notification_rooms=["user_id"],
    error_reload=[
        ("match", "affected_sample_batch_ids"),
        ("peak_assignment", "affected_sample_batch_ids"),
    ],
)
async def process_sample_item(
    sample_item: SampleItemCreate,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
) -> dict:
    """
    TODO_api_circular_import  destinguish sample and sample_item controller, should be moved to samples_controller.py?
    Automates the process of sample item creation, calibration, and match computation
    as a single workflow. This process ensures that once a sample item is created, it is
    then calibrated and matches are computed without requiring manual intervention.
    NOTE that the sample_file record with the same filename should already exist in the database.

    Steps:
    - Create a new sample item
    - Compute matches for the sample item
    - Fetch processed sample details including match data

    :param sample_item: Details of the sample item to be created.
    :type sample_item: SampleItemCreate
    :param independent_transaction: Indicates whether this operation should be treated as a standalone transaction.
    :type independent_transaction: bool, optional
    :param user_id: Current user triggered operation (for user notifications)
    :type user_id: int | None, optional
    :param process_id: Process identifier for progress tracking
    :type process_id: str | None, optional
    :raises RuntimeError: Raised if calibration or match computation fails.
    :return: Details of the processed sample including matches.
    :rtype: dict
    """

    sample_file = await fetch_sample_file(sample_file_id=sample_item.sample_file_id)

    notification = UserNotification(
        process_id=process_id,
        type="process_sample_item",
        status="pending",
        message=f"Processing sample item '{sample_item.sample_item_name}', filename '{sample_file.filename}'.",
        data={
            "filename": sample_file.filename,
            "sample_batch_id": sample_item.sample_batch_id,
            "_user_id": user_id,
        },
    )
    await send_progress_user_notification(notification, 0.1)

    # --- Create a new sample item --- #
    created_sample_item = (
        await create_sample_items(
            sample_items=[sample_item], independent_transaction=True
        )
    ).get("data")[0]
    created_sample_item_id = created_sample_item["sample_item_id"]

    notification.message = f"Sample '{sample_item.sample_item_name}' record created with ID: {created_sample_item_id}."
    notification.data = {
        "sample_item_id": created_sample_item_id,
        "filename": sample_file.filename,
        "sample_batch_id": sample_item.sample_batch_id,
        "_user_id": user_id,
    }
    await send_progress_user_notification(notification, 0.2)

    # --- Compute matches for the sample item --- #
    await match_compute_sample(
        sample_item_id=created_sample_item_id,
        independent_transaction=False,
        user_id=user_id,
        process_id=gen_id(8),
        parent_id=process_id,
    )

    # --- Assign peaks (Stage A / database-first only) for the sample item --- #
    await auto_assign_sample_peaks(
        sample_item_id=created_sample_item_id,
        user_id=user_id,
        parent_id=process_id,
    )

    notification.message = (
        f"Matches computed for sample '{sample_item.sample_item_name}'."
    )
    await send_progress_user_notification(notification, 0.9)

    # --- Fetch processed sample details including match data --- #
    sample = (
        await get_sample(
            sample_item_id=created_sample_item_id,
        )
    )["data"]

    # Prepare affected IDs for notifications
    affected_sample_batch_ids = [sample_item.sample_batch_id]

    return {
        "message": f"Sample '{sample['sample_item_name']}' was successfully processed.",
        "data": sample,
        "_notification_data": {
            "sample_item_id": created_sample_item_id,
            "sample_file_id": sample["sample_file_id"],
            "affected_sample_batch_ids": affected_sample_batch_ids,
            "affected_sample_item_ids": [created_sample_item_id],
        },
    }
