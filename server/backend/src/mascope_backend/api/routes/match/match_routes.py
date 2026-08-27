from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.params import Query

from mascope_backend.api.controllers.dataset.dataset_controller import get_dataset
from mascope_backend.api.controllers.match.match_controller import (
    match_compute_batch,
    match_compute_sample,
    match_remove_all,
    match_remove_batch,
    match_remove_sample,
    rematch_batch,
    rematch_batches,
    rematch_sample,
)
from mascope_backend.api.controllers.sample.lib.sample_batches_fetch import (
    fetch_dataset_sample_batch_ids,
    fetch_sample_batch,
)
from mascope_backend.api.controllers.samples.lib.samples_fetch import fetch_sample
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.models.match.match_pydantic_model import RematchBatchesBody
from mascope_backend.api.new.auth.dependencies import admin_user, current_active_user
from mascope_backend.api.new.workspaces.dependencies import (
    check_batch_access_bulk,
    require_batch_role,
    require_dataset_role,
    require_sample_role,
)
from mascope_backend.db import User
from mascope_backend.db.id import gen_id


match_router = APIRouter(prefix="/api/match", tags=["Match Management"])


@match_router.post("/rematch/dataset/{dataset_id}")
@api_route(status_code=202)
async def rematch_dataset_route(
    dataset_id: str,
    background_tasks: BackgroundTasks,
    full_remove: Annotated[
        bool,
        Query(
            description="If True, removes all existing matches before recomputing. "
            "If False, removes only orphaned matches.",
        ),
    ] = False,
    force: Annotated[
        bool,
        Query(
            description="If True, bypasses status checks and forces rematch",
        ),
    ] = False,
    user: User = Depends(current_active_user),
    membership=Depends(require_dataset_role("editor")),
):
    """
    Rematch every sample batch in a dataset, one batch after another.

    The same operation as rematching each batch by hand, applied to the whole
    dataset: batches are processed newest first - the recent end of a dataset
    is the end someone is usually waiting on - and each one decides for itself
    whether it has anything to do, so batches that are already matched are
    skipped and a batch that is mid-processing is reported as locked rather
    than interrupted.

    :param dataset_id: The unique identifier of the dataset.
    :type dataset_id: str
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the dataset.
    :type membership: WorkspaceMember
    """
    # Verify the existence of the dataset (a superuser passes the ACL check
    # above whether or not the dataset is real, so this is what raises 404)
    dataset = await get_dataset(dataset_id)
    dataset_name = dataset["data"]["dataset_name"]

    sample_batch_ids = await fetch_dataset_sample_batch_ids(dataset_id)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        rematch_batches,
        sample_batch_ids=sample_batch_ids,
        full_remove=full_remove,
        force=force,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    total_batches = len(sample_batch_ids)
    return {
        "message": (
            f"Rematching {total_batches} sample "
            f"batch{'es' if total_batches != 1 else ''} "
            f"in dataset '{dataset_name}'..."
        ),
        "process_id": process_id,
    }


@match_router.post("/rematch/batches")
@api_route(status_code=202)
async def rematch_batches_route(
    body: RematchBatchesBody,
    background_tasks: BackgroundTasks,
    full_remove: Annotated[
        bool,
        Query(
            description="If True, removes all existing matches before recomputing. "
            "If False, removes only orphaned matches.",
        ),
    ] = False,
    force: Annotated[
        bool,
        Query(
            description="If True, bypasses status checks and forces rematch",
        ),
    ] = False,
    user: User = Depends(current_active_user),
):
    """Rematch multiple sample batches.

    :param body: Request body containing sample batch IDs to rematch.
    :type body: RematchBatchesBody
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    """
    await check_batch_access_bulk(body.sample_batch_ids, user, "editor")

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        rematch_batches,
        sample_batch_ids=body.sample_batch_ids,
        full_remove=full_remove,
        force=force,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    total_batches = len(body.sample_batch_ids)
    return {
        "message": (
            f"Rematching {total_batches} "
            f"sample batch{'es' if total_batches != 1 else ''}..."
        ),
        "process_id": process_id,
    }


@match_router.post("/rematch/batch/{sample_batch_id}")
@api_route(status_code=202)
async def rematch_batch_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    full_remove: Annotated[
        bool,
        Query(
            description="If True, removes all existing matches before recomputing. "
            "If False, removes only orphaned matches.",
        ),
    ] = False,
    force: Annotated[
        bool,
        Query(
            description="If True, bypasses status checks and forces rematch",
        ),
    ] = False,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """
    Rematch a specific sample batch by removing orphaned/all matches and recompute
    for all samples in the batch

    - Processing batches cannot be rematched
    - Ready batches require force=true to rematch

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the batch.
    :type membership: WorkspaceMember
    """
    # Verify the existance of sample batch
    sample_batch = await fetch_sample_batch(sample_batch_id)

    # Early status check - processing is never bypassable
    msg = f"Sample batch '{sample_batch.sample_batch_name}' is "
    notification_data = {"sample_batch_id": sample_batch_id}
    match sample_batch.status:
        case "processing":
            msg += (
                "currently processing. Please wait for completion and try again later."
            )
            raise ApiException(msg, notification_data, 409)
        case "ready" if not force:
            msg += (
                "already matched - please use 'rematch' option if you want to recompute"
            )
            raise ApiException(msg, notification_data, 409)
        case _:
            # "rematch" status or force=True with "ready" - proceed
            pass

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        rematch_batch,
        sample_batch_id=sample_batch_id,
        full_remove=full_remove,
        force=force,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Rematching sample batch '{sample_batch.sample_batch_name}'...",
        "process_id": process_id,
    }


@match_router.post("/compute/batch/{sample_batch_id}")
@api_route(status_code=202)
async def match_compute_batch_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """Compute matches for a specific sample batch.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the batch.
    :type membership: WorkspaceMember
    """
    # Verify the existance of sample batch
    sample_batch = await fetch_sample_batch(sample_batch_id)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        match_compute_batch,
        sample_batch_id=sample_batch_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Computing matches for sample batch '{sample_batch.sample_batch_name}'..."
        ),
        "process_id": process_id,
    }


@match_router.delete("/remove/batch/{sample_batch_id}")
@api_route(status_code=202)
async def match_remove_batch_route(
    sample_batch_id: str,
    background_tasks: BackgroundTasks,
    full_remove: Annotated[
        bool,
        Query(
            description="If True, removes all existing matches before recomputing. "
            "If False, removes only orphaned matches.",
        ),
    ] = False,
    user: User = Depends(current_active_user),
    membership=Depends(require_batch_role("editor")),
):
    """
    Remove orphaned/all matches for a specific sample batch.

    By default, removes only orphaned matches.
    Use full_remove=true to remove all matches.

    :param sample_batch_id: The unique identifier of the sample batch.
    :type sample_batch_id: str
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the batch.
    :type membership: WorkspaceMember
    """
    # Verify the existance of sample batch
    sample_batch = await fetch_sample_batch(sample_batch_id)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        match_remove_batch,
        sample_batch_id=sample_batch_id,
        full_remove=full_remove,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": (
            f"Removing matches for sample batch '{sample_batch.sample_batch_name}'..."
        ),
        "process_id": process_id,
    }


@match_router.post("/rematch/sample/{sample_item_id}")
@api_route(status_code=202, token_access=True)
async def rematch_sample_route(
    sample_item_id: str,
    background_tasks: BackgroundTasks,
    full_remove: Annotated[
        bool,
        Query(
            description="If True, removes all existing matches before recomputing. "
            "If False, removes only orphaned matches.",
        ),
    ] = False,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
):
    """
    Rematch a specific sample by removing orphaned/all matches and recomputing.

    By default, performs partial rematching by removing only orphaned matches.
    Use full_remove=true for complete reset and rematch.

    :param sample_item_id: The unique identifier of the sample.
    :type sample_item_id: str
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the sample.
    :type membership: WorkspaceMember
    """
    # Verify the existence of sample item
    sample = await fetch_sample(sample_item_id)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        rematch_sample,
        sample_item_id=sample_item_id,
        full_remove=full_remove,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    return {
        "message": f"Rematching sample '{sample.sample_item_name}', please wait.",
        "process_id": process_id,
    }


@match_router.delete("/remove/sample/{sample_item_id}")
@api_route(status_code=202)
async def match_remove_sample_route(
    sample_item_id: str,
    background_tasks: BackgroundTasks,
    full_remove: Annotated[
        bool,
        Query(
            description="If True, removes all existing matches before recomputing. "
            "If False, removes only orphaned matches.",
        ),
    ] = False,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
):
    """
    Remove orphaned/all matches for a specific sample.

    By default, removes only orphaned matches.
    Use full_remove=true to remove all matches.

    :param sample_item_id: The unique identifier of the sample.
    :type sample_item_id: str
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the sample.
    :type membership: WorkspaceMember
    """
    # Verify the existance of sample item
    sample = await fetch_sample(sample_item_id)

    # Get data for notifications
    process_id = gen_id(8)

    background_tasks.add_task(
        match_remove_sample,
        sample_item_id=sample_item_id,
        full_remove=full_remove,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Removing matches for sample '{sample.sample_item_name}'...",
        "process_id": process_id,
    }


@match_router.post("/compute/sample/{sample_item_id}")
@api_route(status_code=202)
async def match_compute_sample_route(
    sample_item_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("editor")),
):
    """Compute matches for a specific sample.

    :param sample_item_id: The unique identifier of the sample.
    :type sample_item_id: str
    :param user: The current authenticated user. Requires workspace editor role.
    :type user: User
    :param membership: Workspace membership with editor role on the sample.
    :type membership: WorkspaceMember
    """
    # Verify the existance of sample item
    sample = await fetch_sample(sample_item_id)

    # Get data for notifications
    process_id = gen_id(8)
    background_tasks.add_task(
        match_compute_sample,
        sample_item_id=sample_item_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Computing matches for sample '{sample.sample_item_name}'...",
        "process_id": process_id,
    }


@match_router.delete("/remove/all")
@api_route()
async def match_remove_all_route(user=Depends(admin_user)):
    """
    Endpoint to delete all match data across the system.
    """
    return await match_remove_all()
