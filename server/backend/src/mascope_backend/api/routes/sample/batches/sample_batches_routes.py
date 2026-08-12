from fastapi import APIRouter, BackgroundTasks, Depends, Query

from mascope_backend.api.controllers.sample.batches.sample_batches_controller import (
    copy_sample_batch,
    create_sample_batch,
    delete_sample_batch,
    get_batch_targets,
    get_sample_batch,
    get_sample_batch_peaks,
    get_sample_batches,
    import_sample_items,
    sample_batch_export_peaks,
    update_sample_batch,
)
from mascope_backend.api.controllers.sample.batches.status.service import (
    update_sample_batch_status,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.sample.batches.sample_batch_pydantic_model import (
    GetSampleBatchesQueryParams,
    GetSampleBatchTargetsQueryParams,
    SampleBatchCopyBody,
    SampleBatchCreate,
    SampleBatchImportSamplesBody,
    SampleBatchUpdate,
    SampleBatchUpdateStatusBody,
)
from mascope_backend.api.new.auth.access_rules import locked_access
from mascope_backend.api.new.auth.dependencies import (
    current_active_user,
)
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access_bulk,
    check_dataset_access,
    require_batch_role,
)
from mascope_backend.api.routes.sample.batches.export.routes import (
    sample_batches_export_router,
)
from mascope_backend.db import Dataset, SampleBatch
from mascope_backend.db.id import gen_id


sample_batches_router = APIRouter(prefix="/api/sample/batches", tags=["Sample Batches"])
sample_batches_router.include_router(sample_batches_export_router)


@sample_batches_router.get("")
@api_route(token_access=True)
async def get_sample_batches_route(
    query_params: GetSampleBatchesQueryParams = Query(),
    user=Depends(current_active_user),
):
    """Retrieve a list of sample batches.

    :param query_params: Query parameters for sorting, filtering, and pagination.
    :type query_params: GetSampleBatchesQueryParams
    :param user: The current authenticated user with guest permissions.
    :type user: User
    :return: A dictionary containing total count and list of sample batches.
    :rtype: dict
    """
    await check_dataset_access(query_params.dataset_id, user, "guest")
    return await get_sample_batches(**query_params.model_dump())


@sample_batches_router.get("/{sample_batch_id}")
@api_route()
async def get_sample_batch_route(
    sample_batch_id: str,
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("guest")),
):
    """Retrieve details of a specific sample batch by ID.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param user: The current authenticated user with guest permissions.
    :type user: User
    :return: A dictionary containing the sample batch details.
    :rtype: dict
    """
    return await get_sample_batch(sample_batch_id)


@sample_batches_router.get("/{sample_batch_id}/targets")
@api_route()
async def get_batch_targets_route(
    sample_batch_id: str,
    query_params: GetSampleBatchTargetsQueryParams = Depends(),
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("guest")),
):
    """Retrieve all targets associated with a specific sample batch.

    :param sample_batch_id: ID of the sample batch for which targets are being retrieved
    :type sample_batch_id: str
    :param query_params: Query parameters for deduplication and pagination.
    :type query_params: GetSampleBatchTargetsQueryParams
    :param user: The current authenticated user with guest permissions.
    :type user: User
    :return: A dictionary containing the target collections, compounds, ions, isotopes.
    :rtype: dict
    """
    return await get_batch_targets(sample_batch_id, **query_params.model_dump())


@sample_batches_router.post("")
@api_route(status_code=201)
async def create_sample_batch_route(
    body: SampleBatchCreate,
    user=Depends(current_active_user),
):
    """Create a new sample batch.

    :param body: The data required to create a sample batch.
    :type body: SampleBatchCreate
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: A dictionary containing the newly created sample batch's details.
    :rtype: dict
    """
    await check_dataset_access(body.dataset_id, user, "editor")
    return await create_sample_batch(sample_batch=body, independent_transaction=True)


@sample_batches_router.patch("/status")
@api_route()
async def update_sample_batch_status_route(
    body: SampleBatchUpdateStatusBody,
    user=Depends(current_active_user),
):
    """
    Update the status of multiple sample batches.

    Only batches with different current status
    are updated.

    :param body: Request body containing batch IDs and target status
    :type body: SampleBatchUpdateStatusBody
    :param user: The current authenticated user with editor permissions
    :type user: User
    :return: Update results with count of affected batches and details
    :rtype: dict
    """
    await check_batch_access_bulk(body.sample_batch_ids, user, "editor")
    return await update_sample_batch_status(
        sample_batch_ids=body.sample_batch_ids,
        status=body.status,
        independent_transaction=True,
    )


@sample_batches_router.patch("/{sample_batch_id}")
@api_route()
async def update_sample_batch_route(
    sample_batch_id: str,
    body: SampleBatchUpdate,
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Update details of an existing sample batch.

    :param sample_batch_id: The unique identifier of the sample batch to be updated.
    :type sample_batch_id: str
    :param body: The update data for the sample batch.
    :type body: SampleBatchUpdate
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: Update results details
    :rtype: dict
    """
    # Check if locked sample batch - only admins or higher can update
    await locked_access(user, SampleBatch, sample_batch_id, min_role="admin")

    return await update_sample_batch(
        sample_batch_id=sample_batch_id,
        sample_batch_update=body,
        independent_transaction=True,
    )


@sample_batches_router.delete("/{sample_batch_id}")
@api_route(status_code=202)
async def delete_sample_batch_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Delete a specific sample batch by ID.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param background_tasks: Background task handler.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: A dictionary containing a message and process ID.
    :rtype: dict
    """
    # Fetch sample batch to have access to dataset_id and verify sample batch existence
    sample_batch_result = await get_sample_batch(sample_batch_id)
    sample_batch = sample_batch_result.get("data")
    sample_batch_name = sample_batch["sample_batch_name"]

    # Check if locked sample batch - only owners can delete
    await locked_access(user, SampleBatch, sample_batch_id, min_role="owner")

    process_id = gen_id(8)

    background_tasks.add_task(
        delete_sample_batch,
        sample_batch_id=sample_batch_id,
        dataset_id=sample_batch["dataset_id"],
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    return {
        "message": f"Deleting batch '{sample_batch_name}', please wait.",
        "process_id": process_id,
    }


@sample_batches_router.post("/{sample_batch_id}/import")
@api_route(status_code=202)
async def import_sample_items_route(
    sample_batch_id: str,
    body: SampleBatchImportSamplesBody,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Import sample items into a specific sample batch.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param body: Data for importing sample items.
    :type body: SampleBatchImportSamplesBody
    :param background_tasks: Background task handler.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: A dictionary containing a message and process ID.
    :rtype: dict
    """
    # Can't import to locked sample batch
    await locked_access(user, SampleBatch, sample_batch_id)

    # Ensure that sample_batch_id in path matches sample_batch_id in sample_items
    if any(si.sample_batch_id != sample_batch_id for si in body.sample_items):
        raise ValueError("The sample_batch_id in the route and sample_items must match")

    # Verify the existence of sample batch
    sample_batch_result = await get_sample_batch(sample_batch_id)
    sample_batch = sample_batch_result.get("data")
    sample_batch_name = sample_batch["sample_batch_name"]

    process_id = gen_id(8)

    background_tasks.add_task(
        import_sample_items,
        sample_batch_id=sample_batch_id,
        sample_items=body.sample_items,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Importing {len(body.sample_items)} samples to the sample batch "
            f"'{sample_batch_name}', please wait."
        ),
        "process_id": process_id,
    }


@sample_batches_router.post("/{sample_batch_id}/copy")
@api_route(status_code=202)
async def copy_sample_batch_route(
    sample_batch_id: str,
    body: SampleBatchCopyBody,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Copy an existing sample batch to a new dataset.

    :param sample_batch_id: The unique identifier of the sample batch to copy.
    :type sample_batch_id: str
    :param body: Data required to copy the sample batch.
    :type body: SampleBatchCopyBody
    :param background_tasks: Background task handler.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: A dictionary containing a message and process ID.
    :rtype: dict
    """
    # Check ACL on destination dataset before checking lock status
    await check_dataset_access(body.dataset_id, user, "editor")
    # Can't copy to locked dataset
    await locked_access(user, Dataset, body.dataset_id)

    process_id = gen_id(8)

    background_tasks.add_task(
        copy_sample_batch,
        sample_batch_id=sample_batch_id,
        dataset_id=body.dataset_id,
        sample_batch_name=body.sample_batch_name,
        sample_batch_description=body.sample_batch_description,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Copying batch '{body.sample_batch_name}', please wait.",
        "process_id": process_id,
    }


@sample_batches_router.post("/{sample_batch_id}/export_peaks")
@api_route(status_code=202)
async def sample_batch_export_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Export peak data for a specific sample batch.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param background_tasks: Background task handler.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: A dictionary containing a message and process ID.
    :rtype: dict
    """
    # Verify the existence of sample batch
    sample_batch_result = await get_sample_batch(sample_batch_id)
    sample_batch = sample_batch_result.get("data")
    sample_batch_name = sample_batch["sample_batch_name"]

    process_id = gen_id(8)

    background_tasks.add_task(
        sample_batch_export_peaks,
        sample_batch_id=sample_batch_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Exporting peak data for batch '{sample_batch_name}', please wait.",
        "process_id": process_id,
    }


@sample_batches_router.get("/{sample_batch_id}/peaks")
@api_route(status_code=202)
async def get_sample_batch_peaks_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user=Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Get batch peaks.

    Average peaks are collected from all samples in the batch.
    The peaks are aligned, then total heights are computed if the instrument is orbi
    and total areas if the instrument is tof.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param background_tasks: Background task handler.
    :type background_tasks: BackgroundTasks
    :param user: The current authenticated user with editor permissions.
    :type user: User
    :return: A dictionary containing a message and process ID.
    :rtype: dict
    """
    # Verify the existence of sample batch
    await get_sample_batch(sample_batch_id)

    process_id = gen_id(8)

    background_tasks.add_task(
        get_sample_batch_peaks,
        sample_batch_id=sample_batch_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    return {
        "message": f"Aggregating peaks for the batch '{sample_batch_id}', please wait.",
        "process_id": process_id,
    }
