from fastapi import APIRouter, BackgroundTasks, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.auth.dependencies import current_active_user, guest_user
from mascope_backend.api.new.cheminfo.match_results import load_match_result
from mascope_backend.api.new.cheminfo.schema import (
    CheminfoMatchedQueryBody,
    CheminfoQueryBody,
)
from mascope_backend.api.new.cheminfo.service import (
    match_compositions_by_mz,
    retrieve_compositions_by_mz,
)
from mascope_backend.api.new.workspaces.dependencies import require_sample_role
from mascope_backend.db import User
from mascope_backend.db.id import gen_id


cheminfo_router = APIRouter(prefix="/api/cheminfo", tags=["cheminfo"])


@cheminfo_router.post("/mz/query")
@api_route(token_access=True)
async def retrieve_compositions_by_mz_route(
    body: CheminfoQueryBody, user=Depends(guest_user)
) -> dict:
    """
    Find molecular compositions matching a given m/z value.

    This endpoint uses Mascope Tools to find potential molecular formulas
    that match the provided m/z value within the specified precision. Results can be
    filtered by formula ranges and ionization mechanisms.

    :param body: Query parameters including m/z value, precision, formula ranges, and
        ionization mechanisms
    :type body: CheminfoQueryBody
    :return: List of potential molecular formulas matching the m/z value
    :rtype: dict
    """
    return await retrieve_compositions_by_mz(**body.model_dump())


@cheminfo_router.post("/mz/match/sample/{sample_item_id}")
@api_route(status_code=202)
async def match_compositions_by_mz_route(
    sample_item_id: str,
    body: CheminfoMatchedQueryBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_active_user),
    membership=Depends(require_sample_role("guest")),
) -> dict:
    """
    Find and match molecular compositions for a given m/z value.

    This endpoint finds potential molecular formulas matching the given m/z
    using Mascope Tools, then matches these formulas against a specific sample.

    The search runs as a background task. Its completion notification
    (``match_compositions_by_mz``) carries the counts, not the candidates: those
    are fetched from ``GET /mz/match/result/{process_id}`` with the process id
    this response returns in its ``Process-ID`` header and the notification
    repeats.

    :param sample_item_id: The unique identifier of the sample to match against.
    :param body: request query options; the only required field is `mz`
    :type body: CheminfoMatchedQueryBody
    :param user: The current authenticated user. Requires workspace guest role.
    :type user: User
    :param membership: Workspace membership with guest role on the sample.
    :type membership: WorkspaceMember
    :rtype: dict
    """
    # Get the socket ID from request headers for notifications
    process_id = gen_id(8)

    # Add background task for processing
    background_tasks.add_task(
        match_compositions_by_mz,
        sample_item_id=sample_item_id,
        mz=body.mz,
        mz_precision=body.mz_precision,
        formula_ranges=body.formula_ranges,
        ionization_mechanism_ids=body.ionization_mechanism_ids,
        match_params=body.match_params,
        isotopologues=body.isotopologues,
        independent_transaction=True,
        user_id=user.id,
        process_id=process_id,
    )

    return {
        "message": f"Matching potential formulae for m/z {body.mz}, please wait",
        "process_id": process_id,
    }


@cheminfo_router.get("/mz/match/result/{process_id}")
@api_route()
async def match_compositions_result_route(
    process_id: str,
    user: User = Depends(current_active_user),
) -> dict:
    """
    Fetch the result of a composition match search.

    A match search (``POST /mz/match/sample/{sample_item_id}``) keeps its result
    for the user who ran it, for ``MATCH_RESULT_TTL_SECONDS``, and its completion
    notification names the process. This hands the result to that user: the
    candidates as ``data``, with the m/z, the sample and the counts the
    notification carried.

    No sample role is checked here: the search checked it when it ran, and a
    result can only be read by the account that ran it.

    :param process_id: The search task's process id.
    :type process_id: str
    :param user: The current authenticated user.
    :type user: User
    :raises NotFoundException: (404) This user has no result under this process
        id: it expired, another account ran the search, or no search did.
    :return: The search's result.
    :rtype: dict
    """
    result = await load_match_result(user.id, process_id)
    if result is None:
        raise NotFoundException(
            "No such composition search result; it may have expired"
        )
    return {
        "message": f"Retrieved {result['results']} matched compositions "
        f"for m/z {result['mz']}",
        **result,
    }
