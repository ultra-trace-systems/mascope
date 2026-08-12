from fastapi import APIRouter, Body, Depends, Path, Request

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import owner_user
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
from mascope_backend.api.new.users.access_token.service import delete_user_access_tokens
from mascope_backend.api.new.users.exceptions import InvalidUsernameException
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
