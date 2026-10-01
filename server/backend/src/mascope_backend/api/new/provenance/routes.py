from fastapi import APIRouter, Depends

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import guest_user
from mascope_backend.provenance import build_provenance


provenance_router = APIRouter(prefix="/api/provenance", tags=["Provenance"])


@provenance_router.get("")
@api_route(token_access=True)
async def get_provenance_route(user=Depends(guest_user)):
    """
    Report which software, on which deployment, is answering.

    The provenance block (``mascope_backend.provenance``) without ``inputs``:
    this deployment's id, the Mascope version it runs, and the match-score and
    peak-assignment engine versions in force. The SDK stamps it onto the frames
    its loaders return, so a notebook - and whatever is published from it -
    keeps the name of the build its numbers came from.

    Open to every signed-in user, and to an API token with its service name,
    because the SDK reads it. Not to anonymous callers: nothing an anonymous
    visitor does needs the deployment id, so it is not handed to one.

    :param user: The currently authenticated user.
    :type user: User
    :return: A message and the provenance block.
    :rtype: dict
    """
    return {
        "message": "Provenance of this deployment.",
        "data": build_provenance(),
    }
