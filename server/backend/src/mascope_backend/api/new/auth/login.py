"""
Sign-in and sign-out for the web session.

These replace ``fastapi_users.get_auth_router``, which mints the session in the
same request that checks the password. An account holding a second factor must
not receive a session cookie until that factor is presented, so login here
either completes exactly as the library's does (no second factor) or returns a
pending token and no session at all.

That ordering is what keeps the rest of the application untouched: every
surface that trusts the session cookie - the role dependencies, Socket.IO -
sees a cookie only after both steps passed, so none of them needs a second
factor check of its own.

Logout is the library's implementation, restated here rather than mounted from
a second router so that both halves of the session's lifecycle read together.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.authentication import Strategy
from fastapi_users.router.common import ErrorCode

from mascope_backend.accounts import ACCOUNT_TYPE_MACHINE
from mascope_backend.api.new.auth import auth_backend_jwt, fastapi_users
from mascope_backend.api.new.auth.mfa.cookie import set_pending_cookie
from mascope_backend.api.new.auth.mfa.pending import create_pending_token
from mascope_backend.api.new.users.user_manager.dependencies import get_user_manager
from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.db import User
from mascope_backend.runtime import runtime


login_router = APIRouter()

_current_user_token = fastapi_users.authenticator.current_user_token(
    active=True, verified=False
)


@login_router.post("/login", name="auth:jwt.login")
async def login_route(
    request: Request,
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager: UserManager = Depends(get_user_manager),
    strategy: Strategy[User, int] = Depends(auth_backend_jwt.get_strategy),
):
    """
    Verify credentials and either open a session or ask for a second factor.

    :param request: The incoming request.
    :param credentials: Submitted username and password.
    :param user_manager: User manager, for authentication and its login hook.
    :param strategy: Session token strategy for the cookie backend.
    :raises HTTPException: 400 when the credentials are wrong or the account is
        inactive - the same response the library gives, so a caller cannot tell
        the two apart.
    :return: The transport's session response, or a 200 marking that a code is
        still owed.
    """
    user = await user_manager.authenticate(credentials)

    # A machine account is refused with the same response as bad credentials: it
    # holds no password anyone knows, so authenticate() already fails, but the
    # explicit check states the rule and keeps it true if that ever changes. A
    # machine account authenticates only through its service bearer token, never
    # an interactive session.
    if user is None or not user.is_active or user.account_type == ACCOUNT_TYPE_MACHINE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorCode.LOGIN_BAD_CREDENTIALS,
        )

    if user.mfa_enabled:
        # No session yet, and no `on_after_login`: the login is not finished, so
        # nothing that hook does (socket authentication, service tokens,
        # clearing the login rate limit) may happen on the strength of a
        # password alone.
        runtime.logger.debug(f"Second factor required for user {user.username}")
        response = JSONResponse(
            status_code=status.HTTP_200_OK, content={"mfa_required": True}
        )
        set_pending_cookie(response, create_pending_token(user.id))
        return response

    response = await auth_backend_jwt.login(strategy, user)
    await user_manager.on_after_login(user, request, response)
    return response


@login_router.post("/logout", name="auth:jwt.logout")
async def logout_route(
    user_token: tuple[User, str] = Depends(_current_user_token),
    strategy: Strategy[User, int] = Depends(auth_backend_jwt.get_strategy),
):
    """
    End the current session.

    :param user_token: The authenticated user and their session token.
    :param strategy: Session token strategy for the cookie backend.
    :return: The transport's logout response, which clears the cookie.
    """
    user, token = user_token
    return await auth_backend_jwt.logout(strategy, user, token)
