"""
"Prepare for publication": a dataset's deposit package from the app.

Nested under the dataset's workspace like the dataset routes, and open to an
editor of it, as the batch spreadsheet export is to an editor of the batch's:
the package holds those spreadsheets, and every raw file of the dataset.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Path

from mascope_backend.api.controllers.dataset.dataset_controller import get_dataset
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.dataset_package.service import export_dataset_package
from mascope_backend.api.new.workspaces.dependencies import require_workspace_role
from mascope_backend.db.id import gen_id
from mascope_backend.deposit import preview_package


dataset_package_router = APIRouter(
    prefix="/api/workspaces/{workspace_id}/datasets", tags=["Dataset"]
)


@dataset_package_router.get("/{dataset_id}/export/package/preview")
@api_route()
async def get_dataset_package_preview_route(
    dataset_id: str,
    workspace_id: str = Path(...),
    user=Depends(current_active_user),
    membership=Depends(require_workspace_role("editor")),
):
    """What the dataset's deposit package would hold, and what it lacks.

    Reads the package the archive would be written from, without writing
    anything: the archive's name, the dataset's metadata record, the batches
    and their samples, the raw files and their size, the target collections
    and ionization modes, and the gaps a deposit would have: no licence, no
    authors, no description, no samples.

    :param dataset_id: The dataset.
    :param workspace_id: The workspace the dataset belongs to.
    :param membership: Editor membership of the workspace.
    :return: ``{"data": ...}``, as ``preview_package`` gives it.
    :rtype: dict
    :raises NotFoundException: If the workspace holds no such dataset.
    """
    await get_dataset(dataset_id, workspace_id=workspace_id)
    return {"data": await preview_package(dataset_id)}


@dataset_package_router.post("/{dataset_id}/export/package")
@api_route(status_code=202)
async def export_dataset_package_route(
    dataset_id: str,
    background_tasks: BackgroundTasks,
    workspace_id: str = Path(...),
    user=Depends(current_active_user),
    membership=Depends(require_workspace_role("editor")),
):
    """Build the dataset's deposit package, for the user to download.

    The archive is built in the background, by the builder the operator
    command ``mascope export package`` runs, and the notification that says
    it is ready names the download.

    :param dataset_id: The dataset.
    :param background_tasks: Background task handler.
    :param workspace_id: The workspace the dataset belongs to.
    :param user: The current user, who gets the download.
    :param membership: Editor membership of the workspace.
    :return: A message and the process id.
    :rtype: dict
    :raises NotFoundException: If the workspace holds no such dataset.
    """
    dataset = (await get_dataset(dataset_id, workspace_id=workspace_id))["data"]
    process_id = gen_id(8)
    background_tasks.add_task(
        export_dataset_package,
        dataset_id=dataset_id,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )
    return {
        "message": f"Building the deposit package of dataset "
        f"'{dataset['dataset_name']}', please wait.",
        "process_id": process_id,
    }
