"""
User management CRUD service module.

This module provides core CRUD operations for user management in the Mascope server.
It extends the FastAPI Users library by combining its user management functionality
with custom operations and integrations, including role filtering, validation, etc.
"""

from typing import Optional, Union

from sqlalchemy import asc, desc, func, select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.roles.exceptions import InvalidRoleException
from mascope_backend.api.new.users.first_owner.util import (
    check_last_owner_deletion,
    check_owner_role_change,
)
from mascope_backend.api.new.users.me.schemas import (
    UserUpdateMe,
    UserUpdateMeCredentials,
)
from mascope_backend.api.new.users.password.generate import (
    generate_random_password,
)
from mascope_backend.api.new.users.schemas import (
    UserCreate,
    UserPublic,
    UserRead,
    UserUpdate,
)
from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.api.new.users.util import check_username_exists
from mascope_backend.db import Role, User, Workspace, WorkspaceMember, async_session
from mascope_backend.db.id import gen_id


@api_controller()
async def get_users(
    role_name_min: Optional[str] = None,
    role_name_max: Optional[str] = None,
    page: int | None = None,
    limit: int | None = None,
    sort: str = "registered_at",
    order: str = "desc",
    caller: User | None = None,
) -> dict:
    """
    Retrieves a paginated, sorted, and optionally filtered list of users.

    :param role_name_min: Minimum role name for filtering (inclusive), defaults to None.
    :type role_name_min: Optional[str]
    :param role_name_max: Maximum role name for filtering (inclusive), defaults to None.
    :type role_name_max: Optional[str]
    :param page: Page number for pagination, defaults to None (no pagination).
    :type page: int | None
    :param limit: Number of results per page, defaults to None (no pagination).
    :type limit: int | None
    :param sort: Column name to sort by, defaults to "registered_at".
    :type sort: str
    :param order: Sort order, either 'asc' or 'desc', defaults to "desc".
    :type order: str
    :param caller: The user making the request, used for access control. Admin and
                   higher roles see full user details, defaults to None (public view).
    :type caller: User | None
    :raises NotFoundException: If no users are found in the database.
    :return: A dictionary containing the user list and metadata.
    :rtype: dict
    """
    # Validate pagination parameters
    if (page is None) != (limit is None):
        raise ValueError(
            "Both 'page' and 'limit' must be provided together or both omitted."
        )
    async with async_session() as session:
        # Step 1: Construct the base query with join to Role
        query = select(User, Role.role_name).join(Role, Role.role_id == User.role_id)

        # Step 2: Apply filtering if specified
        if role_name_min or role_name_max:
            # Retrieve role levels from the configuration
            role_access_levels = auth_settings.ROLE_ACCESS_LEVELS

            if role_name_min:
                min_access_level = role_access_levels.get(role_name_min, None)
                if min_access_level is not None:
                    query = query.filter(Role.role_id >= min_access_level)

            if role_name_max:
                max_access_level = role_access_levels.get(role_name_max, None)
                if max_access_level is not None:
                    query = query.filter(Role.role_id <= max_access_level)

        # Step 2: Apply sorting
        if sort:
            query = query.order_by(
                desc(getattr(User, sort))
                if order == "desc"
                else asc(getattr(User, sort))
            )

        # Step 3: Get total count for pagination
        count_query = select(func.count()).select_from(query.subquery())
        total = await session.scalar(count_query)

        # Step 4: Apply pagination and execute the query
        if page is not None and limit is not None:
            query = query.offset(page * limit).limit(limit)
        result = await session.execute(query)

        # Step 5: Construct the response data
        # Admins/owners see full user details; others get public-only fields
        admin_level = auth_settings.ROLE_ACCESS_LEVELS["admin"]
        schema = (
            UserRead
            if caller
            and (
                caller.is_superuser
                or (caller.role_id is not None and caller.role_id >= admin_level)
            )
            else UserPublic
        )
        users = []
        for user, role_name in result.all():
            user_data = user.to_dict()
            user_data["role_name"] = role_name
            users.append(schema.model_validate(user_data))

    return {
        "message": f"Retrieved {len(users)} user records.",
        "results": total,
        "data": users,
    }


@api_controller()
async def get_user(user_id: int) -> dict:
    """
    Retrieves a user by their ID.

    :param user_id: The ID of the user to retrieve.
    :type user_id: int
    :raises NotFoundException: If the user does not exist.
    :return: The user object.
    """
    async with async_session() as session:
        # Step 1: Retrieve the user by ID
        user = await session.get(User, user_id)
        if not user:
            raise NotFoundException(f"User with ID '{user_id}' not found.")

        # Step 2: Validate the user's role ID
        role_access_levels = auth_settings.ROLE_ACCESS_LEVELS
        if user.role_id is None or user.role_id not in role_access_levels.values():
            raise InvalidRoleException(
                detail=f"The user's role ID '{user.role_id}' is not defined in the configuration. Please check for configuration issues"
            )

        # Step 3: Fetch role name by joining with the Role table
        query = select(Role.role_name).filter(Role.role_id == user.role_id)
        result = await session.execute(query)
        role_name = result.scalar_one_or_none()

        if not role_name:
            raise InvalidRoleException(
                detail=f"The role ID '{user.role_id}' is not defined in the configuration. Please check for configuration issues"
            )

        # Step 4: Prepare and return the validated user data
        user_data = user.to_dict()
        user_data["role_name"] = role_name

        # Step 5: Validate and return the user read data
        validated_user = UserRead.model_validate(user_data)
        return {
            "message": f"User '{validated_user.username}' retrieved.",
            "data": validated_user,
        }


@api_controller()
async def register_user(
    user_create: UserCreate,
    user_manager: UserManager,
    safe: bool = True,
    require_password_change: bool = True,
) -> dict:
    """
    Registers a new user in Mascope.

    :param user_create: The details of the user to be registered.
    :type user_create: UserCreate
    :param user_manager: The UserManager instance for user operations.
    :type user_manager: UserManager
    :param safe: If True, sensitive fields (is_superuser, is_active, is_verified)
                will be restricted during creation, defaults to True
    :type safe: bool
    :param require_password_change: Whether the account must replace its password
                before it can use the application, defaults to True because the
                password is one someone else knows.
    :type require_password_change: bool
    :raises UsernameAlreadyExistsException: If the username already exists.
    :raises UserAlreadyExists: If a user with the same email already exists.
    :return: The registered user's details, and the generated temporary password
             when the caller supplied none.
    :rtype: dict
    """
    # --- Check if the username already exists ---
    await check_username_exists(user_create.username)

    # --- Generate the hand-over password when the caller supplied none ---
    # Asking an administrator to invent one buys nothing: the holder must
    # replace it at first sign-in regardless, so its only job is to survive
    # being read out once. A generated one does that better than an invented
    # one, and cannot be a password the administrator uses elsewhere. Returned
    # exactly once, like a password reset; nothing can recover it afterwards.
    generated_password = None
    if not user_create.password:
        generated_password = generate_random_password()
        user_create.password = generated_password

    # --- Sync is_superuser with role_id (owner role requires superuser) ---
    user_create.is_superuser = (
        user_create.role_id == auth_settings.ROLE_ACCESS_LEVELS.get("owner")
    )

    # --- Create the user ---
    created_user = await user_manager.create(
        user_create=user_create,
        safe=safe,
        require_password_change=require_password_change,
    )

    # --- Auto-add to system workspaces with matching global role ---
    role_name = next(
        (
            name
            for name, level in auth_settings.ROLE_ACCESS_LEVELS.items()
            if level == user_create.role_id
        ),
        "guest",
    )
    async with async_session() as session:
        result = await session.execute(
            select(Workspace).where(Workspace.is_system.is_(True))
        )
        for ws in result.scalars().all():
            member = WorkspaceMember(
                workspace_member_id=gen_id(),
                workspace_id=ws.workspace_id,
                user_id=created_user.id,
                workspace_role=role_name,
                granted_by=None,
            )
            session.add(member)
        await session.commit()

    # --- Validate and return the registered user's details ---
    user = (await get_user(user_id=created_user.id))["data"]
    if generated_password is None:
        return {
            "message": f"User '{user.username}' registered successfully.",
            "data": user,
        }
    return {
        "message": (
            f"User '{user.username}' registered. Share the temporary password "
            "with them - it is shown only once, and they must replace it at "
            "first sign-in."
        ),
        "data": user,
        "temporary_password": generated_password,
    }


@api_controller()
async def update_user(
    user_id: int,
    user_update: Union[UserUpdate, UserUpdateMe, UserUpdateMeCredentials],
    user_manager: UserManager,
) -> dict:
    """
    Updates a user's details.

    :param user_id: The ID of the user to update.
    :type user_id: int
    :param user_update: The updates to apply to the user.
    :type user_update: UserUpdate
    :param user_manager: The UserManager instance.
    :type user_manager: UserManager
    :raises NotFoundException: If the user does not exist.
    :raises LastOwnerDowngradeException: If attempting to downgrade the last owner
    :return: The updated user details.
    """
    # --- Retrieve the user ---
    user = await user_manager.get(user_id)

    # --- Check owner role downgrade only for full UserUpdate schema ---
    if (
        isinstance(user_update, UserUpdate)
        and hasattr(user_update, "role_id")
        and user_update.role_id is not None
    ):
        await check_owner_role_change(user_id, user_update.role_id)

        # --- Sync is_superuser with role_id (owner role requires superuser) ---
        user_update.is_superuser = (
            user_update.role_id == auth_settings.ROLE_ACCESS_LEVELS.get("owner")
        )

    # --- Check username new username already exists if it's being updated ---
    if (
        hasattr(user_update, "username")
        and user_update.username
        and user_update.username != user.username
    ):
        await check_username_exists(user_update.username)

    # --- Update the user ---
    await user_manager.update(user_update, user)

    # --- Validate and return the updated user details ---
    user = (await get_user(user_id=user_id))["data"]
    message = f"User '{user.username}' updated successfully."
    if user_update.password is not None:
        message += " Access tokens must be regenerated due to password change."
    return {
        "message": message,
        "data": user,
    }


@api_controller()
async def delete_user(user_id: int, user_manager: UserManager) -> dict:
    """
    Deletes a user by their ID.

    :param user_id: The ID of the user to delete.
    :type user_id: int
    :param user_manager: The UserManager instance.
    :type user_manager: UserManager
    :raises NotFoundException: If the user does not exist.
    :raises LastOwnerDeletionException: If attempting to delete the last owner
    :return: A success message.
    :rtype: dict
    """
    # Step 1: Fetch the user
    user = await user_manager.get(user_id)

    # Step 2: Check if this would remove last owner
    await check_last_owner_deletion(user_id)

    # Step 3: Perform the delete operation
    await user_manager.delete(user)

    return {"message": f"User '{user.username}' deleted successfully."}
