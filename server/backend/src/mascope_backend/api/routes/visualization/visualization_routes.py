from fastapi import APIRouter, BackgroundTasks, Depends, Request

from mascope_backend.api.controllers.sample.items.sample_items_controller import (
    get_sample_item,
)
from mascope_backend.api.controllers.target.ions.target_ions_controller import (
    get_target_ion,
)
from mascope_backend.api.controllers.visualization.visualization_controller import (
    visualize_ion_focus,
)
from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.models.visualization.visualization_pydantic_model import (
    GetVisualizationIonFocusQueryParams,
)
from mascope_backend.api.new.auth.dependencies import current_active_user
from mascope_backend.api.new.workspaces.dependencies import check_sample_access
from mascope_backend.db import User
from mascope_backend.db.id import gen_id


visualization_router = APIRouter(prefix="/api/visualization", tags=["Visualization"])


@visualization_router.post("/ion_focus")
@api_route(status_code=202)
async def visualization_ion_focus_route(
    request: Request,
    background_tasks: BackgroundTasks,
    query_params: GetVisualizationIonFocusQueryParams = Depends(),
    user: User = Depends(current_active_user),
):
    """Initiate a visualization task for focusing on a specific ion in a sample.

    :param background_tasks: Background task manager for running visualization tasks.
    :param query_params: Query parameters for the ion focus visualization.
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :return: A dictionary with a message indicating task initiation and a process ID.
    """
    await check_sample_access(query_params.sample_item_id, user, "guest")
    sample_data = await get_sample_item(query_params.sample_item_id)
    sample = sample_data.get("data")
    sample_item_name = sample["sample_item_name"]
    ion_data = await get_target_ion(query_params.target_ion_id)
    ion = ion_data.get("data")
    target_ion_formula = ion["target_ion_formula"]

    # Get data for notifications
    process_id = gen_id(8)
    sid = request.headers.get("x-sid", None)

    background_tasks.add_task(
        visualize_ion_focus,
        **query_params.model_dump(),
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
        sid=sid,
    )

    return {
        "message": f"Visualizing target ion '{target_ion_formula}' in sample '{sample_item_name}', please wait.",
        "process_id": process_id,
    }
