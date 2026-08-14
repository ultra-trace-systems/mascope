"""
Route dependencies
"""

from fastapi import Depends, Request

from mascope_backend.api.new.auth import fastapi_users, get_enabled_backends
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import (
    ForbiddenAccessException,
    PasswordChangeRequiredException,
)
from mascope_backend.api.new.roles.exceptions import InvalidRoleException
from mascope_backend.db import User


# Raw FastAPI Users dependencies. Private on purpose: they resolve an identity
# but do not enforce the forced password change, so a route binding one directly
# would stay reachable by an account that owes a new password. Everything
# outside this module goes through the gated wrappers below, which is what makes
# a route added later gated by default.
_authenticated_active_user = fastapi_users.current_user(
    active=True, get_enabled_backends=get_enabled_backends
)
_authenticated_superuser = fastapi_users.current_user(
    active=True, superuser=True, get_enabled_backends=get_enabled_backends
)


def _enforce_password_change(request: Request, user: User) -> User:
    """
    Close the API to an account that owes a forced password change.

    Only the interactive browser session is gated. ``get_enabled_backends``
    selects the cookie/JWT backend exactly when the auth cookie is present, so
    keying on the same cookie keeps the gate and the backend selection from
    drifting apart. Service bearer tokens are server-generated random secrets
    whose strength does not depend on the account's password, and their holders
    (the SDK, notebooks, the file converter, instrument agents) have no way to
    render a password screen.

    :param request: The incoming request, inspected for the auth cookie.
    :param user: The authenticated user.
    :raises PasswordChangeRequiredException: If the account owes a change.
    :return: The user object when the account may proceed.
    """
    if not request.cookies.get(auth_settings.COOKIE_NAME):
        return user
    if user.must_change_password:
        raise PasswordChangeRequiredException()
    return user


async def current_active_user(
    request: Request, user: User = Depends(_authenticated_active_user)
) -> User:
    """Active user, refused while a forced password change is pending."""
    return _enforce_password_change(request, user)


async def current_superuser(
    request: Request, user: User = Depends(_authenticated_superuser)
) -> User:
    """Active superuser, refused while a forced password change is pending."""
    return _enforce_password_change(request, user)


# Role-based access dependencies
async def guest_user(user: User = Depends(current_active_user)) -> User:
    return await role_based_access(user, "guest")


async def editor_user(user: User = Depends(current_active_user)) -> User:
    return await role_based_access(user, "editor")


async def admin_user(user: User = Depends(current_active_user)) -> User:
    return await role_based_access(user, "admin")


async def owner_user(user: User = Depends(current_superuser)) -> User:
    return await role_based_access(user, "owner")


# Dependencies that stay reachable while a forced password change is pending.
# The set is deliberately tiny: an account behind the gate must be able to see
# that it is behind the gate, and to get out. Anything else waits.
async def password_gate_exempt_active_user(
    user: User = Depends(_authenticated_active_user),
) -> User:
    """
    Identity for ``GET /api/users/me``, which answers while a change is pending.

    The frontend discovers the pending change from this route, and treats any
    response that is neither 200 nor 401 as "not signed in" - so gating it would
    bounce the user to the sign-in screen, where signing in would gate them
    again. Skips ``role_based_access`` deliberately: the route needs an identity,
    not a role level.
    """
    return user


async def password_gate_exempt_guest_user(
    user: User = Depends(_authenticated_active_user),
) -> User:
    """Guest-level access for ``PATCH /api/users/me/creds`` - the way out."""
    return await role_based_access(user, "guest")


async def role_based_access(user: User, access: str) -> User:
    """
    Enforces role-based access control by comparing the user's role_id with the required access level.

    :param user: The current active user.
    :param access: Name of the required role (e.g., "admin", "editor").
    :raises HTTPException: If the user's role does not meet the required level.
    :return: The user object if the role requirement is met.
    """
    role_access_levels = auth_settings.ROLE_ACCESS_LEVELS
    # Get the required role level
    required_role_id = role_access_levels.get(access, None)
    if required_role_id is None:
        raise InvalidRoleException(
            detail=f"The required role '{access}' is not defined in the configuration."
        )

    # Validate user's role_id
    if user.role_id is None or user.role_id not in role_access_levels.values():
        raise InvalidRoleException(
            detail=f"The user's role ID '{user.role_id}' is not defined in the configuration. Please check for configuration issues."
        )

    # Enforce role-based access
    if user.role_id < required_role_id:
        raise ForbiddenAccessException()

    return user
