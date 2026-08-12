from fastapi import APIRouter, BackgroundTasks, Depends, Query

from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    copy_sample_items,
    create_sample_items,
    delete_sample_items,
    get_sample_item,
    get_sample_items,
    move_sample_items,
    sample_item_export_peaks,
    update_sample_item,
)
from mascope_backend.api.controllers.sample.items.sample_items_process_controller import (
    process_sample_item,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.sample.items.sample_item_pydantic_model import (
    GetSampleItemsQueryParams,
    SampleItemCreate,
    SampleItemsCopyBody,
    SampleItemsDeleteBody,
    SampleItemsMoveBody,
    SampleItemUpdateBody,
)
from mascope_backend.api.new.auth.access_rules import locked_access
from mascope_backend.api.new.auth.dependencies import (
    current_active_user,
)
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access,
    check_sample_access_bulk,
    require_sample_role,
)
from mascope_backend.db import SampleBatch, SampleItem
from mascope_backend.db.id import gen_id


sample_items_router = APIRouter(prefix="/api/sample/items", tags=["Sample Items"])


@sample_items_router.get("")
@api_route()
async def get_sample_items_route(
    query_params: GetSampleItemsQueryParams = Query(),
    user=Depends(current_active_user),
):
    """Retrieve a list of sample items.

    :param query_params: Query parameters for sorting and pagination.
    :param user: The current authenticated user with guest permissions.
    :return: A dictionary containing the total count and list of sample items.
    """
    await check_batch_access(query_params.sample_batch_id, user, "guest")
    return await get_sample_items(**query_params.model_dump())


@sample_items_router.get("/{sample_item_id}")
@api_route()
async def get_sample_item_route(
    sample_item_id: str,
    user=Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
):
    """Retrieve details of a specific sample item by ID.

    :param sample_item_id: The unique identifier of the sample item.
    :param user: The current authenticated user with guest permissions.
    :return: A dictionary containing the sample item details.
    """
    return await get_sample_item(sample_item_id)


@sample_items_router.post("")
@api_route(status_code=201)
async def create_sample_item_route(
    sample_item: SampleItemCreate,
    user=Depends(current_active_user),
):
    """Create a new sample item.

    :param sample_item: The sample item creation data.
    :param user: The current authenticated user with editor permissions.
    :return: A dictionary containing the newly created sample item's details.
    """
    await check_batch_access(sample_item.sample_batch_id, user, "editor")
    return await create_sample_items(
        sample_items=[sample_item], independent_transaction=True
    )


@sample_items_router.patch("/{sample_item_id}")
@api_route()
async def update_sample_item_route(
    sample_item_id: str,
    body: SampleItemUpdateBody,
    user=Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
):
    """Update an existing sample item's details.

    :param sample_item_id: The unique identifier of the sample item.
    :param body: The sample item update body
    :param user: The current authenticated user with editor permissions.
    :return: A dictionary containing the updated sample item details.
    """
    # Check if locked sample item - only owners can update
    await locked_access(user, SampleItem, sample_item_id, min_role="owner")

    return await update_sample_item(
        sample_item_id=sample_item_id,
        sample_item=body.sample_item,
        independent_transaction=True,
    )


@sample_items_router.post("/delete")
@api_route()
async def delete_sample_items_route(
    body: SampleItemsDeleteBody,
    user=Depends(current_active_user),
):
    """Delete a specific sample items by IDs.

    :param body: The sample items delete body.
    :param user: The current authenticated user with editor permissions.
    :return: A dictionary confirming deletion.
    """
    await check_sample_access_bulk(body.sample_item_ids, user, "editor")
    # Check if any sample items are locked - only owners can delete locked items
    await locked_access(user, SampleItem, body.sample_item_ids, min_role="owner")

    return await delete_sample_items(
        sample_item_ids=body.sample_item_ids, independent_transaction=True
    )


@sample_items_router.delete("/{sample_item_id}")
@api_route()
async def delete_sample_item_route(
    sample_item_id: str,
    user=Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
):
    """Delete a specific sample item by ID.

    :param sample_item_id: The unique identifier of the sample item.
    :param user: The current authenticated user with editor permissions.
    :return: A dictionary confirming deletion.
    """
    # Check if locked sample item - only owners can delete
    await locked_access(user, SampleItem, sample_item_id, min_role="owner")

    return await delete_sample_items(
        sample_item_ids=[sample_item_id], independent_transaction=True
    )


@sample_items_router.post("/copy")
@api_route(status_code=202)
async def copy_sample_items_route(
    body: SampleItemsCopyBody,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """Copy an existing sample item to a new sample batch.

    :param sample_item_id: The unique identifier of the sample item.
    :param body: The data for copying the sample item.
    :param background_tasks: Background tasks for processing the copy.
    :param user: The current authenticated user with editor permissions.
    :return: A dictionary confirming the copy process has started.
    """
    # Check ACL on source items
    await check_sample_access_bulk(body.sample_item_ids, user, "guest")
    # Check ACL on destination batch
    await check_batch_access(body.sample_batch_id, user, "editor")
    # Can't copy to locked sample batch
    await locked_access(user, SampleBatch, body.sample_batch_id)

    process_id = gen_id(8)
    background_tasks.add_task(
        copy_sample_items,
        sample_item_ids=body.sample_item_ids,
        sample_batch_id=body.sample_batch_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Copying {len(body.sample_item_ids)} samples, please wait.",
        "process_id": process_id,
    }


@sample_items_router.post("/move")
@api_route(status_code=202)
async def move_sample_items_route(
    body: SampleItemsMoveBody,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """Copy an existing sample item to a new sample batch.

    :param sample_item_id: The unique identifier of the sample item.
    :param body: The data for copying the sample item.
    :param background_tasks: Background tasks for processing the copy.
    :param user: The current authenticated user with editor permissions.
    :return: A dictionary confirming the copy process has started.
    """
    # Check ACL on source items
    await check_sample_access_bulk(body.sample_item_ids, user, "editor")
    # Check ACL on destination batch
    await check_batch_access(body.sample_batch_id, user, "editor")
    # Cant move locked sample items
    await locked_access(user, SampleItem, body.sample_item_ids)

    # Can't move to locked sample batch
    await locked_access(user, SampleBatch, body.sample_batch_id)

    process_id = gen_id(8)
    background_tasks.add_task(
        move_sample_items,
        sample_item_ids=body.sample_item_ids,
        sample_batch_id=body.sample_batch_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Moving {len(body.sample_item_ids)} samples, please wait.",
        "process_id": process_id,
    }


@sample_items_router.post("/process")
@api_route(status_code=202)
async def process_sample_item_route(
    sample_item: SampleItemCreate,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
):
    """Process a sample item, including creation and matching.

    :param sample_item: The data for processing the sample item.
    :param background_tasks: Background tasks for processing the item.
    :param user: The current authenticated user with editor permissions.
    :return: A dictionary confirming the processing has started.
    """
    await check_batch_access(sample_item.sample_batch_id, user, "editor")
    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        process_sample_item,
        sample_item=sample_item,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    return {
        "message": f"Processing sample '{sample_item.sample_item_name}', please wait.",
        "process_id": process_id,
    }


@sample_items_router.post("/{sample_item_id}/export_peak_data")
@api_route(status_code=202)
async def sample_item_export_peaks_route(
    sample_item_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
):
    """Export peak data for a specific sample item.

    :param sample_item_id: The unique identifier of the sample item.
    :type sample_item_id: str
    :param background_tasks: Background task handler.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: A dictionary containing a message and process ID.
    :rtype: dict
    """
    # Verify the existance of sample item
    sample_item_result = await get_sample_item(sample_item_id)
    sample_item_name = sample_item_result.get("data").get("sample_item_name")

    process_id = gen_id(8)

    background_tasks.add_task(
        sample_item_export_peaks,
        sample_item_id=sample_item_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Exporting peak data for a sample item '{sample_item_name}', please wait."
        ),
        "process_id": process_id,
    }
