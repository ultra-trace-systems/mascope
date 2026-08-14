from fastapi import APIRouter, Body, Depends, Path, Request

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.rate_limit import rate_limit
from mascope_backend.api.new.auth.dependencies import owner_user
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
from mascope_backend.api.new.users.access_token.service import delete_user_access_tokens
from mascope_backend.api.new.users.exceptions import InvalidUsernameException
from mascope_backend.api.new.users.owner.schemas import RequirePasswordChange
from mascope_backend.api.new.users.password.service import reset_user_password
from mascope_backend.api.new.users.schemas import (
    UserCreate,
    UserUpdate,
)
from mascope_backend.api.new.users.service import (
    delete_user,
    register_user,
    update_user,
)
from mascope_backend.api.new.users.user_manager.dependencies import get_user_manager
from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.db.admin.user.require_password_change import (
    require_password_change_for_all_users,
)
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import (
    emit_record_reload,
    emit_record_updated,
)


owner_router = APIRouter(prefix="/api/users/owner", tags=["User Management Owner"])


@owner_router.post("/register")
@api_route(status_code=201)
async def owner_register_user_route(
    request: Request,
    user_create: UserCreate = Body(...),
    user=Depends(owner_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Register a new user. Owners can register users with any role.

    :param request: The current HTTP request.
    :type request: Request
    :param user_create: The details of the user to be registered.
    :type user_create: UserCreate
    :param user: The currently authenticated owner user.
    :type user: User
    :param user_manager: The UserManager instance for user operations.
    :type user_manager: UserManager
    :return: A success message and the registered user's details.
    :rtype: dict
    """
    # Step 1: Check for explicit null values in the raw request body
    body = await request.json()
    if "username" in body and body["username"] is None:
        raise InvalidUsernameException()

    # Step 2: Create new user
    return await register_user(
        user_create=user_create, user_manager=user_manager, safe=False
    )


# POST, not GET: this changes state for every account, and the SameSite=lax
# auth cookie rides on cross-site top-level GET navigations - as a GET, a
# crafted link could fire it with the owner's ambient credentials.
# Declared before the "/{user_id}" routes so a literal path cannot be shadowed.
@owner_router.post(
    "/require-password-change",
    # Idempotent and deployment-wide, so a handful of attempts per hour is
    # ample; failing open on a Redis outage is harmless for the same reason.
    dependencies=[
        Depends(rate_limit(times=3, seconds=3600, scope="require-password-change"))
    ],
)
@api_route()
async def owner_require_password_change_route(
    body: RequirePasswordChange = Body(...),
    user=Depends(owner_user),
):
    """
    Require every user account to set a new password before continuing.

    Puts every account through the current password policy, which is how a
    deployment brings passwords created under an older policy up to it. Nobody
    is excluded, the calling owner included. Everyone keeps signing in with
    their existing password and is held at a mandatory password screen until
    they replace it.

    There is no counterpart to undo this over HTTP - withdrawing the
    requirement is a maintenance script run on the server.

    :param body: Acknowledgement, which must set ``confirm`` to true.
    :param user: The currently authenticated owner user.
    :return: A success message and the counts of affected accounts.
    :rtype: dict
    """
    # Step 1: Require the change
    result = await require_password_change_for_all_users()
    data = result["data"]

    # Step 2: Record who did it. WARNING so a deployment-wide security action
    # reaches error tracking and outlives the request log.
    runtime.logger.warning(
        f"Password change required for {data['flagged_count']} account(s) "
        f"by owner '{user.username}' (id {user.id})"
    )

    # Step 3: Notify open sessions. The bulk update fires no ORM events, so
    # without this nothing reaches a browser until its next request is refused.
    # The auth store re-fetches the current user on this event and ignores the
    # payload, so a minimal record is enough.
    for user_id in data["user_ids"]:
        await emit_record_updated(
            record_type="user_me",
            record_id=str(user_id),
            record={"id": user_id, "must_change_password": True},
            room=f"user-{user_id}",
        )
    # Open user-management tables would otherwise show stale flags.
    await emit_record_reload(record_type="user")

    return {
        "message": (
            f"{data['flagged_count']} of {data['total_users']} user accounts must "
            "now set a new password - including your own. Everyone keeps signing "
            "in with their current password until they change it. Changing it "
            "revokes that user's API access tokens (SDK, notebooks, instrument "
            "agents), which must be regenerated or re-paired."
        ),
        "data": data,
    }


@owner_router.patch("/{user_id}")
@api_route()
async def owner_update_user_route(
    request: Request,
    user_id: int = Path(..., description="ID of the user to update"),
    user_update: UserUpdate = Body(...),
    user=Depends(owner_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Update a user by ID. Owners can update any user's details, including Admins.
    Owners can assign roles up to `owner`.

    :param user_id: The unique ID of the user to be updated.
    :type user_id: int
    :param user_update: The update payload containing the fields to be updated.
    :type user_update: UserUpdate
    :param user: The currently authenticated owner user, validated via dependency injection.
    :type user: User
    :param user_manager: The UserManager instance used to interact with the user database.
    :type user_manager: UserManager
    :param request: The current HTTP request.
    :type request: Request
    :raises ForbiddenAccessException: If the owner attempts to update his own account. Use update self endpoint.
    :return: A dictionary containing a success message and the updated user details.
    :rtype: dict
    """
    # Step 1: Check owner is not updating themselves
    if user.id == user_id:
        raise ForbiddenAccessException(
            detail="You can not update your own account by this endpoint."
        )

    # Step 2: Check for explicit null values in the raw request body
    body = await request.json()
    if "username" in body and body["username"] is None:
        raise InvalidUsernameException()

    # Step 3: Perform the update
    return await update_user(
        user_id=user_id,
        user_update=user_update,
        user_manager=user_manager,
    )


# POST, not GET: resetting a password changes state, and the SameSite=lax
# auth cookie rides on cross-site top-level GET navigations - as a GET, a
# crafted link could reset a user's password with the owner's ambient
# credentials.
@owner_router.post("/{user_id}/reset-password")
@api_route()
async def owner_reset_user_password(
    user_id: int = Path(..., description="ID of the user to reset password"),
    user=Depends(owner_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Reset the password for a user. Owners can reset passwords for all users except themselves.

    :param user_id: The ID of the user to reset the password.
    :param user: The currently authenticated owner user.
    :param user_manager: The user manager instance.
    :return: The new password for the user.
    """
    # Step 1: Prevent owners from resetting their own password
    if user_id == user.id:
        raise ForbiddenAccessException(detail="You cannot reset your own password.")

    # Step 2: Reset the user's password
    return await reset_user_password(user_id=user_id, user_manager=user_manager)


@owner_router.delete("/{user_id}/access-tokens")
@api_route()
async def owner_delete_user_access_tokens(
    user_id: int = Path(
        ..., description="ID of the user whose access tokens to delete"
    ),
    user=Depends(owner_user),
):
    """
    Deletes all access tokens for a user. Owners can delete access tokens for any user except themselves.

    :param user_id: The ID of the user whose access tokens should be deleted.
    :param user: The currently authenticated owner user.
    :return: A success message.
    """
    # Step 1: Ensure owner is not deleting their own access tokens
    if user_id == user.id:
        raise ForbiddenAccessException(
            detail="You cannot delete your own access tokens."
        )

    # Step 2: Delete the user's access tokens
    return await delete_user_access_tokens(user_id=user_id)


@owner_router.delete("/{user_id}")
@api_route()
async def owner_delete_user_route(
    user_id: int = Path(..., description="ID of the user to delete"),
    user=Depends(owner_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Deletes a user by ID. Owners can delete any user except themselves.

    :param user_id: The unique ID of the user to delete.
    :type user_id: int
    :param user: The currently authenticated owner user.
    :type user: User
    :param user_manager: The UserManager instance.
    :type user_manager: UserManager
    :return: A success message.
    :rtype: dict
    """
    # Step 1: Check owner is not deleting themselves
    if user.id == user_id:
        raise ForbiddenAccessException(detail="You can not delete your own account.")

    # Step 2: Delete the user
    return await delete_user(user_id=user_id, user_manager=user_manager)
