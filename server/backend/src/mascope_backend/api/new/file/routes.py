from fastapi import APIRouter, BackgroundTasks, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.file.schema import FileDownloadBody, MzMLExportBody
from mascope_backend.api.new.file.service import download_files, export_mzml
from mascope_backend.api.new.workspaces.dependencies import (
    check_sample_access_bulk,
    check_sample_file_instrument_access,
)
from mascope_backend.db import User
from mascope_backend.db.id import gen_id


file_router = APIRouter(prefix="/api/file", tags=["Parameters"])


@file_router.post("/download")
@api_route(status_code=202)
async def download_file_route(
    body: FileDownloadBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
):
    """Download one or more sample files if available.

    Checks that the user has guest-level access to the instrument workspace
    for each requested file.

    :param body: The request body containing sample file IDs to download.
    :type body: FileDownloadBody
    :param user: The authenticated user.
    :return: Dictionary containing a message and the process ID.
    """
    for fid in body.sample_file_ids:
        await check_sample_file_instrument_access(fid, user, "guest")

    process_id = gen_id(8)

    background_tasks.add_task(
        download_files,
        sample_file_ids=body.sample_file_ids,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": "Downloading sample files, please wait.",
        "process_id": process_id,
    }


@file_router.post("/mzml")
@api_route(status_code=202)
async def export_mzml_route(
    body: MzMLExportBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
):
    """Write sample files, or the time windows of samples, as mzML.

    A sample file is written whole; a sample is its file's scans of its
    polarity between its ``t0`` and ``t1``. Each spectrum holds the scan's
    centroids. Only Thermo raw files can be written.

    A sample file needs the same access as downloading it, a sample guest
    access to its workspace. The files are written in the background, and the
    notification that says they are done names the download.

    :param body: The sample files and samples to write.
    :type body: MzMLExportBody
    :param user: The authenticated user.
    :return: Dictionary containing a message and the process ID.
    """
    for fid in body.sample_file_ids:
        await check_sample_file_instrument_access(fid, user, "guest")
    if body.sample_item_ids:
        await check_sample_access_bulk(body.sample_item_ids, user, "guest")

    process_id = gen_id(8)

    background_tasks.add_task(
        export_mzml,
        sample_file_ids=body.sample_file_ids,
        sample_item_ids=body.sample_item_ids,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": "Writing mzML, please wait.",
        "process_id": process_id,
    }
