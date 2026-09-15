from fastapi import APIRouter, Body, Depends, Path, Request

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.dependencies import admin_user
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
from mascope_backend.api.new.auth.mfa.reauth import require_recent_mfa
from mascope_backend.api.new.users.access_token.service import delete_user_access_tokens
from mascope_backend.api.new.users.exceptions import InvalidUsernameException
from mascope_backend.api.new.users.mfa.service import reset_user_mfa
from mascope_backend.api.new.users.password.service import reset_user_password
from mascope_backend.api.new.users.schemas import (
    UserCreate,
    UserUpdate,
)
from mascope_backend.api.new.users.service import (
    delete_user,
    get_user,
    register_user,
    update_user,
)
from mascope_backend.api.new.users.user_manager.dependencies import get_user_manager
from mascope_backend.api.new.users.user_manager.service import UserManager


admin_router = APIRouter(prefix="/api/users/admin", tags=["User Management Admin"])


@admin_router.post("/register")
@api_route(status_code=201)
async def admin_register_user_route(
    request: Request,
    user_create: UserCreate = Body(...),
    user=Depends(admin_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Register a new user. Admins can register users with roles up to `editor`.

    :param request: The current HTTP request.
    :type request: Request
    :param user_create: The details of the user to be registered.
    :type user_create: UserCreate
    :param user: The currently authenticated admin user.
    :type user: User
    :param user_manager: The UserManager instance for user operations.
    :type user_manager: UserManager
    :raises ForbiddenAccessException: If the admin tries to register a user with a higher role.
    :return: A success message and the registered user's details.
    :rtype: dict
    """
    # Step 1: Check for explicit null values in the raw request body
    body = await request.json()
    if "username" in body and body["username"] is None:
        raise InvalidUsernameException()

    # Step 2: Restrict role changes, admin can assign up to editor
    if user_create.role_id is not None:
        max_role_id = auth_settings.ROLE_ACCESS_LEVELS["editor"]
        if user_create.role_id > max_role_id:
            raise ForbiddenAccessException(
                detail="You can only assign roles up to 'editor'."
            )
    # Step 3: Create new user
    return await register_user(user_create=user_create, user_manager=user_manager)


@admin_router.patch("/{user_id}")
@api_route()
async def admin_update_user_route(
    request: Request,
    user_id: int = Path(..., description="ID of the user to update"),
    user_update: UserUpdate = Body(...),
    user=Depends(admin_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Update a user by ID.. Admins can update users with `editor` or `guest` roles only.
    Admins can assign roles only up to `editor`.

    :param request: The current HTTP request.
    :type request: Request
    :param user_id: The unique ID of the user to be updated.
    :type user_id: int
    :param user_update: The update payload containing the fields to be updated.
    :type user_update: UserUpdate
    :param user: The currently authenticated admin user, validated via dependency injection.
    :type user: User
    :param user_manager: The user manager instance used to interact with the user database.
    :type user_manager: UserManager
    :raises ForbiddenAccessException: If the admin attempts to update a user with a role
        that is higher than or equal to their own role.
    :return: A dictionary containing a success message and the updated user details.
    :rtype: dict
    """
    # Step 1: Check for explicit null values in the raw request body
    body = await request.json()
    if "username" in body and body["username"] is None:
        raise InvalidUsernameException()

    # Step 2: Compare roles, admin cannot update users with admin/owner roles
    target_user_role_id = (await get_user(user_id=user_id)).get("data").role_id
    if target_user_role_id >= user.role_id:
        raise ForbiddenAccessException()

    # Step 3: Restrict role changes, admin can assign up to editor
    if user_update.role_id is not None:
        max_role_id = auth_settings.ROLE_ACCESS_LEVELS["editor"]
        if user_update.role_id > max_role_id:
            raise ForbiddenAccessException(
                detail="You can only assign roles up to 'editor'."
            )

    # Step 3: Perform the update
    return await update_user(
        user_id=user_id,
        user_update=user_update,
        user_manager=user_manager,
    )


# POST, not GET: resetting a password changes state, and the SameSite=lax
# auth cookie rides on cross-site top-level GET navigations - as a GET, a
# crafted link could reset a user's password with the admin's ambient
# credentials.
@admin_router.post("/{user_id}/reset-password")
@api_route()
async def admin_reset_user_password(
    user_id: int = Path(..., description="ID of the user to reset password"),
    user=Depends(admin_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Reset the password for a user. Admins can reset passwords for users with `guest` or `editor` roles only.

    :param user_id: The ID of the user to reset the password.
    :param user: The currently authenticated admin user.
    :param user_manager: The user manager instance.
    :return: The new password for the user.
    """
    # Step 1: Ensure target user role is within allowed limits
    target_user_role_id = (await get_user(user_id=user_id)).get("data").role_id
    if target_user_role_id >= auth_settings.ROLE_ACCESS_LEVELS["admin"]:
        raise ForbiddenAccessException()

    # Step 2: Reset the user's password
    return await reset_user_password(user_id=user_id, user_manager=user_manager)


# POST for the same reason as the password reset above: it changes state, and
# the SameSite=lax auth cookie rides on cross-site top-level GET navigations.
@admin_router.post("/{user_id}/mfa/reset")
@api_route()
async def admin_reset_user_mfa(
    user_id: int = Path(..., description="ID of the user whose MFA to reset"),
    user=Depends(admin_user),
):
    """
    Clear a user's second factor. Admins can do this for `guest` or `editor`
    accounts only.

    For an authenticator lost together with its recovery codes. The account
    keeps its password and enrols again; if the deployment requires a factor at
    that role, it is held at the enrolment screen until it does.

    :param user_id: The account to clear.
    :param user: The currently authenticated admin user.
    :raises MfaReauthRequiredException: If the admin holds a factor but has not
        presented a code recently.
    :return: A success message and the updated user.
    """
    # Clearing another account's factor is a security downgrade, so an admin who
    # holds a factor of their own must have presented a code recently - a stolen
    # session alone must not be able to strip a covered account's second factor,
    # the same reason the token and pairing routes ask for one. No-op for an
    # admin without a factor, so a deployment that does not use MFA is unchanged.
    await require_recent_mfa(user)

    # Step 1: Ensure target user role is within allowed limits
    target_user_role_id = (await get_user(user_id=user_id)).get("data").role_id
    if target_user_role_id >= auth_settings.ROLE_ACCESS_LEVELS["admin"]:
        raise ForbiddenAccessException()

    # Step 2: Clear the factor
    return await reset_user_mfa(user_id=user_id)


@admin_router.delete("/{user_id}/access-tokens")
@api_route()
async def admin_delete_user_access_tokens(
    user_id: int = Path(
        ..., description="ID of the user whose access tokens to delete"
    ),
    user=Depends(admin_user),
):
    """
    Deletes all access tokens for a user. Admins can delete access tokens for users with `guest` or `editor` roles only.

    :param user_id: The ID of the user whose access tokens should be deleted.
    :param user: The currently authenticated admin user.
    :return: A success message.
    """
    # Step 1: Ensure target user role is within allowed limits
    target_user_role_id = (await get_user(user_id=user_id)).get("data").role_id
    if target_user_role_id >= auth_settings.ROLE_ACCESS_LEVELS["admin"]:
        raise ForbiddenAccessException()

    # Step 2: Delete the user's access tokens
    return await delete_user_access_tokens(user_id=user_id)


@admin_router.delete("/{user_id}")
@api_route()
async def admin_delete_user_route(
    user_id: int = Path(..., description="ID of the user to delete"),
    user=Depends(admin_user),
    user_manager: UserManager = Depends(get_user_manager),
):
    """
    Deletes a user by ID. Admins can delete users with `guest` or `editor` roles only.

    :param user_id: The unique ID of the user to delete.
    :type user_id: int
    :param user: The current authenticated admin user.
    :type user: User
    :param user_manager: The UserManager instance.
    :type user_manager: UserManager
    :raises ForbiddenAccessException: If the admin attempts to delete a user with a higher role.
    :return: A success message.
    :rtype: dict
    """
    # Step 1: Restrict deletion to roles `guest` and `editor`
    target_user_role_id = (await get_user(user_id=user_id)).get("data").role_id
    if target_user_role_id >= auth_settings.ROLE_ACCESS_LEVELS["admin"]:
        raise ForbiddenAccessException()

    # Step 2: Delete the user
    return await delete_user(user_id=user_id, user_manager=user_manager)
