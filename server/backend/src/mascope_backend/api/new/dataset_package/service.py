"""
A dataset's deposit package from the app: "Prepare for publication".

The app adds no packaging of its own. The preview is
:func:`mascope_backend.deposit.preview_package`, and the archive is written by
:func:`mascope_backend.deposit.build_package`, the function the operator
command ``mascope export package`` runs, so the archive a user downloads is
the one the command writes for the same dataset.

The build runs as a background task, as the batch spreadsheet export does.
The archive is written in a directory of the build's own under
``temp/packages/``, then moved into the user's temp directory under the
package's name, where the notification's ``download`` names it. Two builds
of one dataset at once never write the same file, and the download is always
a whole archive: the move replaces the earlier one, never a half-written one.
"""

import asyncio
import os
import shutil

from mascope_backend.api.controllers.dataset.dataset_controller import get_dataset
from mascope_backend.api.lib.api_features import api_controller_background_task
from mascope_backend.api.new.temp.storage import user_temp_path
from mascope_backend.db.id import gen_id
from mascope_backend.deposit import build_package
from mascope_backend.runtime import runtime


@api_controller_background_task(
    success_notification_rooms=["user_id"],
    error_notification_rooms=["user_id"],
)
async def export_dataset_package(
    dataset_id: str,
    independent_transaction: bool = False,
    user_id: int | None = None,
    process_id: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """
    Build a dataset's deposit package and leave it for the user to download.

    :param dataset_id: The dataset to package.
    :param independent_transaction: Whether this task reports its own outcome.
    :param user_id: The user who asked, whose temp directory gets the archive.
    :param process_id: This task's process id; names its build directory.
    :param parent_id: The parent process, if any.
    :return: The message, ``data`` with the archive's file name and what
        ``write_package`` reports of it but its path on the server, and the
        notification's ``download``.
    :rtype: dict
    :raises NotFoundException: If there is no such dataset.
    """
    dataset = (await get_dataset(dataset_id))["data"]
    run_directory = runtime.env.path(
        "temp", "packages", f"app-{process_id or gen_id(8)}"
    )
    # A directory that exists is one build_package writes the archive into,
    # under the package's name.
    os.makedirs(run_directory, exist_ok=True)
    try:
        summary = await build_package(dataset_id, run_directory)
        file = os.path.basename(summary["archive"])
        await asyncio.to_thread(
            os.replace, summary["archive"], user_temp_path(user_id, file)
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, run_directory, True)

    message = (
        f"The deposit package of dataset '{dataset['dataset_name']}' is ready: "
        f"{summary['files']} files, {_size(summary['bytes'])}."
    )
    not_kept = summary["raw_files"] - summary["raw_files_included"]
    if not_kept:
        message += (
            f" {not_kept} sample file(s) have no raw file kept and are described "
            "without it."
        )
    if summary["raw_files_changed"]:
        message += (
            f" {summary['raw_files_changed']} raw file(s) differ from the file "
            "uploaded; the manifest gives both checksums."
        )
    runtime.logger.info(message)
    return {
        "message": message,
        "data": {
            "file": file,
            **{key: value for key, value in summary.items() if key != "archive"},
        },
        "_notification_data": {"dataset_id": dataset_id, "download": file},
    }


def _size(size: int) -> str:
    """A size in bytes for a person: ``512 B``, ``3.4 MB``, ``1.2 GB``."""
    value = float(size)
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            break
        value /= 1000
    return f"{size} B" if unit == "B" else f"{value:.1f} {unit}"
