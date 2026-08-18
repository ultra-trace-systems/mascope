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
from mascope_backend.api.new.auth.mfa import policy
from mascope_backend.api.new.auth.mfa.exceptions import MfaEnrollmentRequiredException
from mascope_backend.api.new.roles.exceptions import InvalidRoleException
from mascope_backend.db import User


# Raw FastAPI Users dependencies. Private on purpose: they resolve an identity
# but enforce neither the forced password change nor the enrolment requirement,
# so a route binding one directly would stay reachable by an account that owes
# either. Everything outside this module goes through the gated wrappers below,
# which is what makes a route added later gated by default.
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


def _enforce_mfa_enrolment(request: Request, user: User) -> User:
    """
    Close the API to an account the deployment requires to hold a second factor
    and which does not hold one yet.

    Scoped to the interactive browser session for the same reason as the
    password gate, and keyed on the same cookie so the two cannot drift apart:
    a bearer-token holder has no way to render an enrolment screen, and its
    credential was already minted under whatever rules applied at the time.

    Applied after the password gate, never before. An account can owe both, and
    an enrolment screen shown to someone holding a password an administrator
    chose would enrol the wrong person's phone against it.

    :param request: The incoming request, inspected for the auth cookie.
    :param user: The authenticated user.
    :raises MfaEnrollmentRequiredException: If the account owes an enrolment.
    :return: The user object when the account may proceed.
    """
    if not request.cookies.get(auth_settings.COOKIE_NAME):
        return user
    if policy.enrollment_required(user.role_id, user.mfa_enabled):
        raise MfaEnrollmentRequiredException()
    return user


def _enforce_gates(request: Request, user: User) -> User:
    """Both interactive gates, in the order they have to be satisfied."""
    return _enforce_mfa_enrolment(request, _enforce_password_change(request, user))


async def current_active_user(
    request: Request, user: User = Depends(_authenticated_active_user)
) -> User:
    """Active user, refused while a password change or enrolment is pending."""
    return _enforce_gates(request, user)


async def current_superuser(
    request: Request, user: User = Depends(_authenticated_superuser)
) -> User:
    """Active superuser, refused while a password change or enrolment is pending."""
    return _enforce_gates(request, user)


# Role-based access dependencies
async def guest_user(user: User = Depends(current_active_user)) -> User:
    return await role_based_access(user, "guest")


async def editor_user(user: User = Depends(current_active_user)) -> User:
    return await role_based_access(user, "editor")


async def admin_user(user: User = Depends(current_active_user)) -> User:
    return await role_based_access(user, "admin")


async def owner_user(user: User = Depends(current_superuser)) -> User:
    return await role_based_access(user, "owner")


# Dependencies that stay reachable while a gate is pending. The set is
# deliberately tiny: an account behind a gate must be able to see that it is
# behind one, and to get out. Anything else waits.
async def password_gate_exempt_active_user(
    user: User = Depends(_authenticated_active_user),
) -> User:
    """
    Identity for ``GET /api/users/me``, which answers while either gate holds.

    The frontend discovers both pending states from this route, and treats any
    response that is neither 200 nor 401 as "not signed in" - so gating it would
    bounce the user to the sign-in screen, where signing in would gate them
    again. Skips ``role_based_access`` deliberately: the route needs an identity,
    not a role level.
    """
    return user


async def password_gate_exempt_guest_user(
    user: User = Depends(_authenticated_active_user),
) -> User:
    """Guest-level access for ``PATCH /api/users/me/creds`` - the way out.

    Exempt from the enrolment gate too, not only the password one: an account
    can owe both, and the password has to be replaced first (see
    ``_enforce_mfa_enrolment``), so this route must answer while an enrolment is
    still outstanding.
    """
    return await role_based_access(user, "guest")


async def mfa_gate_exempt_guest_user(
    request: Request, user: User = Depends(_authenticated_active_user)
) -> User:
    """Guest-level access for the enrolment routes - the way out of that gate.

    Still behind the password gate, which is the ordering the enrolment gate
    itself relies on: someone holding a password an administrator chose must
    replace it before binding an authenticator to the account.
    """
    return await role_based_access(_enforce_password_change(request, user), "guest")


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
