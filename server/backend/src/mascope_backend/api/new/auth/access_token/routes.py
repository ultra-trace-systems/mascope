from fastapi import APIRouter, Body, Depends

from mascope_backend.api.new.auth.access_token.exceptions import (
    InternalServiceAccessException,
)
from mascope_backend.api.new.auth.access_token.schemas import AccessTokenRequest
from mascope_backend.api.new.auth.access_token.service import regenerate_access_token
from mascope_backend.api.new.auth.dependencies import (
    guest_user,
    role_based_access,
)
from mascope_backend.api.new.auth.mfa.reauth import require_recent_mfa


# Access token-based routes for Jupyter server or external API access
access_token_router = APIRouter(prefix="/access_token")


@access_token_router.post("/regenerate", name="auth:access-token.regenerate")
async def access_token_regenerate_route(
    access_token_request: AccessTokenRequest = Body(...),
    user=Depends(guest_user),
):
    """
    API route to regenerate an access token.
    Removes existing tokens for the service and generates a new one.

    Different services require different minimum roles:
    - mascope_sdk: guest or higher - for Jupyter access
    - tof-agent: editor or higher - for TOF agent access
    - file-agent: editor or higher - for File agent access
    - export-agent: editor or higher - for Export agent access
    - file-converter: internal service, managed automatically

    An account with a second factor must have presented a code recently. The
    token this returns is valid for a year and is not bound to the session that
    asked for it, so a session alone must not be enough to obtain one - that
    would let a single stolen session outlast the factor permanently.
    """
    await require_recent_mfa(user)

    service_name = access_token_request.service_name

    match service_name:
        case "mascope_sdk":
            await role_based_access(user, "guest")
        case "tof-agent" | "file-agent" | "export-agent":
            await role_based_access(user, "editor")
        case "file-converter":
            raise InternalServiceAccessException()

    return await regenerate_access_token(user=user, service_name=service_name)
