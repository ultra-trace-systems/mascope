from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.rate_limit import (
    clear_user_rate_limit,
    enforce_user_rate_limit,
    rate_limit,
    refund_ip_rate_limit,
)
from mascope_backend.api.new.auth.dependencies import (
    guest_user,
    password_gate_exempt_active_user,
    password_gate_exempt_guest_user,
)
from mascope_backend.api.new.users.exceptions import InvalidUsernameException
from mascope_backend.api.new.users.me.exceptions import InvalidCurrentPasswordException
from mascope_backend.api.new.users.me.schemas import (
    UserUpdateMe,
    UserUpdateMeCredentials,
)
from mascope_backend.api.new.users.service import get_user, update_user
from mascope_backend.api.new.users.user_manager.dependencies import get_user_manager
from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.db import User


me_router = APIRouter(prefix="/api/users/me", tags=["Current User"])


@me_router.get("")
@api_route()
async def get_me_route(
    user: User = Depends(password_gate_exempt_active_user),
):
    """
    Retrieve the current authenticated user's details.

    :param user: The current authenticated user, injected by dependency.
    :type user: User
    :return: The current user's details.
    :rtype: UserRead
    """
    return await get_user(user_id=user.id)


@me_router.patch("")
@api_route()
async def update_me_route(
    request: Request,
    user_update: UserUpdateMe,
    user: User = Depends(guest_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Update the current authenticated user's details.

    :param user_update: The updates to apply to the current user.
    :type user_update: UserUpdateMe
    :param request: The current HTTP request.
    :type request: Request
    :param user: The current authenticated user, injected by dependency.
    :type user: User
    :param user_manager: The UserManager instance.
    :type user_manager: UserManager
    :return: The updated user details.
    :rtype: UserRead
    """
    # Step 1: Check for explicit null values in the raw request body
    body = await request.json()  # Parse raw JSON body
    if "username" in body and body["username"] is None:
        raise InvalidUsernameException()

    # Step 2: Call the controller to process the update
    return await update_user(
        user_id=user.id,
        user_update=user_update,
        user_manager=user_manager,
    )


@me_router.patch(
    "/creds",
    # Verifies the current password, so it is a password-guessing oracle and
    # needs a limit. The real budget is the per-account one inside the handler:
    # this route is the only way out of a forced password change, and a per-IP
    # budget would let a few colleagues behind one NAT address lock out the rest
    # of the site. Keep a loose per-IP cap as an anti-flood backstop only; the
    # handler refunds it on success, so a whole site complying with a forced
    # change in the same hour cannot exhaust it either.
    dependencies=[Depends(rate_limit(times=100, seconds=3600, scope="creds-change"))],
)
@api_route()
async def update_credentials_route(
    request: Request,
    credentials_update: UserUpdateMeCredentials,
    user: User = Depends(password_gate_exempt_guest_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Update user credentials (password) for the current authenticated user.

    Reachable while a forced password change is pending: it is the way out.

    Steps:
    1. Validates provided credentials update data (handled by schema)
    2. Verifies current password matches user's password
    3. Updates to the new password if all validations pass

    :param request: The incoming request, used to refund the per-IP limit on success
    :type request: Request
    :param credentials_update: Contains current password, new password and verification
    :type credentials_update: UserUpdateMeCredentials
    :param user: The current authenticated user
    :type user: User
    :param user_manager: User manager instance for authentication and updates
    :type user_manager: UserManager
    :return: Updated user details
    :rtype: dict
    :raises CurrentPasswordIncorrectException: If current password is invalid
    """
    # Step 1: Cap consecutive wrong-current-password guesses at this account.
    await enforce_user_rate_limit(
        user.id, times=20, seconds=3600, scope="creds-change-user"
    )

    # Step 2: Verify current password
    credentials = OAuth2PasswordRequestForm(
        username=user.email,
        password=credentials_update.current_password,
    )
    authenticated_user = await user_manager.authenticate(credentials)
    if not authenticated_user:
        raise InvalidCurrentPasswordException()

    # The caller just proved they know the current password, so nothing since
    # the last clear was an oracle guess - reset the budget here, not after the
    # write, so a rejection of the *new* password (e.g. an entry only the
    # server's blocklist knows) cannot strand a user at the mandatory password
    # screen with a burnt budget and a green client-side checklist.
    await clear_user_rate_limit(user.id, scope="creds-change-user")

    # Step 3: Store the new password. set_own_password rather than update_user:
    # this is the only path that clears a pending forced password change, since
    # authenticating above proved the caller knew the old password.
    await user_manager.set_own_password(user, credentials_update.new_password)

    # A completed change is legitimate traffic, and a deployment-wide forced
    # change produces exactly one per account behind the same office address -
    # refund it so the per-IP backstop spends its budget on failures and
    # floods only, instead of locking out the site's stragglers.
    await refund_ip_rate_limit(request, scope="creds-change")

    updated_user = (await get_user(user_id=user.id))["data"]
    return {
        "message": (
            f"User '{updated_user.username}' updated successfully. "
            "Access tokens must be regenerated due to password change."
        ),
        "data": updated_user,
    }
