from fastapi import APIRouter, Depends, Query

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import guest_user
from mascope_backend.api.new.dataset_tombstones.service import get_tombstones


dataset_tombstones_router = APIRouter(
    prefix="/api/datasets/tombstones", tags=["Dataset tombstones"]
)


@dataset_tombstones_router.get("")
@api_route(token_access=True)
async def get_tombstones_route(
    page: int | None = Query(None, ge=0, description="Zero-based page, with limit."),
    limit: int | None = Query(None, ge=1, description="Rows per page, with page."),
    user=Depends(guest_user),
):
    """
    List what deleted datasets were: one snapshot per deleted dataset.

    Each row is written when the dataset is deleted, alone or with its
    workspace, and outlives both: its name, description, type, instrument,
    descriptive metadata, how many batches, samples and files it held, who
    deleted it, when, through which path, and the provenance block of the build
    that did. Newest delete first.

    A member of a workspace reads the rows of its datasets; a global admin or
    owner reads every row, those of deleted workspaces included. Open to an
    API token with its service name, because the SDK reads it.

    :param page: Zero-based page number, given together with ``limit``.
    :param limit: Rows per page, given together with ``page``.
    :param user: The currently authenticated user.
    :return: ``{"data": [...], "total": N}``.
    :rtype: dict
    """
    return await get_tombstones(user=user, page=page, limit=limit)
