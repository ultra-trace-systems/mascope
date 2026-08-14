from fastapi import APIRouter, Body, Depends, Request

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.rate_limit import rate_limit
from mascope_backend.api.new.users.exceptions import InvalidUsernameException
from mascope_backend.api.new.users.first_owner.exceptions import (
    FirstOwnerRegistrationNotAvailableException,
)
from mascope_backend.api.new.users.first_owner.schemas import FirstOwnerCreate
from mascope_backend.api.new.users.first_owner.util import (
    check_first_owner_registration,
)
from mascope_backend.api.new.users.service import register_user
from mascope_backend.api.new.users.user_manager.dependencies import get_user_manager
from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.runtime import runtime


first_owner_router = APIRouter(prefix="/api/users/first-owner", tags=["First Owner"])


@first_owner_router.get("/status")
@api_route(public=True)
async def check_first_owner_sign_up_status_route() -> dict:
    """
    Check if owner signup is available by verifying no users exist in the system.

    Returns 200 with status indicating availability. This endpoint is unprotected.

    :return: Status message indicating if owner signup is possible
    :rtype: dict
    """
    # --- Check if owner signup is available by verifying no users exist ---
    is_available = await check_first_owner_registration()
    message = (
        ("Please sign-up Mascope first owner account")
        if is_available
        else "First owner is already registered"
    )
    runtime.logger.debug(f"First owner signup status check - {message}")

    return {
        "status": "available" if is_available else "unavailable",
        "message": message,
    }


@first_owner_router.post(
    "",
    # Tightly rate-limit: this validates the server owner secret, so cap
    # attempts per IP to prevent brute-forcing it.
    dependencies=[Depends(rate_limit(times=5, seconds=3600, scope="first-owner"))],
)
@api_route(status_code=201, public=True)
async def register_first_owner_route(
    request: Request,
    first_owner_create: FirstOwnerCreate = Body(...),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Register the first owner user if no users exist in the system.
    This endpoint is unprotected but requires a valid server secret.

    :param request: The current HTTP request
    :param first_owner_create: The first owner registration details with server secret
    :param user_manager: User manager instance
    :return: The registered owner details
    """
    # --- Check if this is the first user ---
    if not await check_first_owner_registration():
        raise FirstOwnerRegistrationNotAvailableException()

    # --- Check for explicit null values in the raw request body ---
    body = await request.json()
    if "username" in body and body["username"] is None:
        raise InvalidUsernameException()

    # --- Create owner user (privileges are set in schema validation) ---
    # No forced password change: this form is filled in by the account holder,
    # so the password is already one only they know.
    return await register_user(
        user_create=first_owner_create,
        user_manager=user_manager,
        safe=False,
        require_password_change=False,
    )
