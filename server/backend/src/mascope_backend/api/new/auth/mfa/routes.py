from fastapi import APIRouter, Body, Depends, Request
from fastapi_users.authentication import Strategy

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.lib.rate_limit import enforce_user_rate_limit, rate_limit
from mascope_backend.api.new.auth import auth_backend_jwt
from mascope_backend.api.new.auth.dependencies import (
    guest_user,
    mfa_gate_exempt_guest_user,
)
from mascope_backend.api.new.auth.mfa import policy, service
from mascope_backend.api.new.auth.mfa.cookie import (
    clear_pending_cookie,
    read_pending_cookie,
)
from mascope_backend.api.new.auth.mfa.exceptions import (
    InvalidMfaCodeException,
    InvalidPendingTokenException,
    MfaRequiredByPolicyException,
)
from mascope_backend.api.new.auth.mfa.pending import (
    burn_pending_token,
    register_failed_attempt,
    resolve_pending_token,
)
from mascope_backend.api.new.auth.mfa.reauth import (
    clear_recent_verification,
    mark_recently_verified,
)
from mascope_backend.api.new.auth.mfa.schemas import (
    MfaCodeRequest,
    MfaConfirmResponse,
    MfaEnrollResponse,
    MfaStatusResponse,
)
from mascope_backend.api.new.users.user_manager.dependencies import get_user_manager
from mascope_backend.api.new.users.user_manager.service import UserManager
from mascope_backend.db import User
from mascope_backend.runtime import runtime


mfa_router = APIRouter(prefix="/mfa")


@mfa_router.post(
    "/verify",
    name="auth:mfa.verify",
    # Per-IP backstop against a flood. The budget that actually bounds guessing
    # is the per-account one inside the handler and the attempt counter on the
    # pending token itself; this one only has to stop a firehose, and is loose
    # enough that a shared office address cannot exhaust it in normal use.
    dependencies=[Depends(rate_limit(times=60, seconds=300, scope="mfa-verify"))],
)
async def verify_route(
    request: Request,
    body: MfaCodeRequest = Body(...),
    user_manager: UserManager = Depends(get_user_manager),
    strategy: Strategy[User, int] = Depends(auth_backend_jwt.get_strategy),
):
    """
    Complete a sign-in by presenting the second factor.

    Accepts either a code from the authenticator app or one of the account's
    recovery codes; which one was submitted is decided by what it matches.

    Deliberately not decorated with ``api_route``: this route mints the session
    cookie through the auth backend's transport, so it must return that
    response object rather than a re-encoded body. It is public in the same
    sense the login route is - the pending token is the credential.

    :param request: The incoming request, carrying the pending cookie.
    :param body: The submitted code.
    :param user_manager: User manager, for the post-login hook.
    :param strategy: Session token strategy for the cookie backend.
    :raises InvalidPendingTokenException: If the half-finished sign-in is gone.
    :raises InvalidMfaCodeException: If the code does not verify.
    :return: The transport's session response.
    """
    user_id, jti = await resolve_pending_token(read_pending_cookie(request))

    # Cap guessing per account as well as per token: burning one pending token
    # and starting again from the password step must not reset the budget.
    await enforce_user_rate_limit(
        user_id, times=20, seconds=900, scope="mfa-verify-user"
    )

    user = await service.load_user(user_id)
    if user is None or not user.is_active or not user.mfa_enabled:
        # The account changed under the half-finished sign-in (disabled,
        # deleted, factor removed). Nothing to complete.
        raise InvalidPendingTokenException()

    verified = await service.verify_totp_for_user(user, body.code)
    if not verified:
        verified = await service.redeem_recovery_code(user, body.code)

    if not verified:
        await register_failed_attempt(jti)
        raise InvalidMfaCodeException()

    # Spend the token before issuing the session so a captured cookie cannot be
    # replayed against a second code.
    await burn_pending_token(jti)

    # The user just presented a code, so the step-up window opens here. Without
    # this, minting a token immediately after signing in would ask for a second
    # code seconds after the first.
    await mark_recently_verified(user.id)

    response = await auth_backend_jwt.login(strategy, user)
    clear_pending_cookie(response)
    # Only now is the login complete, so this is where the hook belongs: it
    # authenticates the socket and clears the login rate limit.
    await user_manager.on_after_login(user, request, response)
    return response


@mfa_router.get("/status", name="auth:mfa.status")
@api_route()
async def status_route(user: User = Depends(mfa_gate_exempt_guest_user)):
    """
    The calling account's second-factor state.

    Reachable from behind the enrolment gate, which is where the enrolment
    screen reads it. Still behind the password gate: an account owing a password
    change is shown the password screen and nothing else.

    :param user: The current authenticated user.
    :return: Enrollment state, remaining recovery codes, and the deployment
        policy the screen explains itself with.
    """
    return {
        "data": MfaStatusResponse(
            enabled=user.mfa_enabled,
            available=service.mfa_available(),
            required=policy.required_for_role(user.role_id),
            policy_min_role=policy.required_min_role_name(),
            confirmed_at=(
                user.mfa_confirmed_at.isoformat() if user.mfa_confirmed_at else None
            ),
            unused_recovery_codes=(
                await service.unused_recovery_code_count(user)
                if user.mfa_enabled
                else 0
            ),
        ).model_dump()
    }


@mfa_router.post("/reauth", name="auth:mfa.reauth")
@api_route()
async def reauth_route(
    body: MfaCodeRequest = Body(...),
    user: User = Depends(guest_user),
):
    """
    Present a code to authorize the actions that need a recent one.

    Separate from the login verify route: this caller already holds a session,
    and is proving they still hold the factor before being handed a credential
    that outlives it.

    :param body: A current code from the authenticator app, or a recovery code.
    :param user: The current authenticated user.
    :raises InvalidMfaCodeException: If the code does not verify.
    :return: Confirmation message.
    """
    await enforce_user_rate_limit(user.id, times=20, seconds=900, scope="mfa-reauth")

    if not user.mfa_enabled:
        # Nothing to present. Reported as success so a client that prompts
        # defensively is not stuck in a loop it cannot satisfy.
        return {
            "message": "No second factor is required for this account.",
            "data": None,
        }

    verified = await service.verify_totp_for_user(user, body.code)
    if not verified:
        verified = await service.redeem_recovery_code(user, body.code)
    if not verified:
        raise InvalidMfaCodeException()

    await mark_recently_verified(user.id)
    return {"message": "Verified.", "data": None}


@mfa_router.post("/enroll", name="auth:mfa.enroll")
@api_route()
async def enroll_route(user: User = Depends(mfa_gate_exempt_guest_user)):
    """
    Begin enrollment and hand back the seed to put into an authenticator app.

    Arms nothing on its own: the factor only starts gating logins once a code
    generated from this seed is confirmed.

    :param user: The current authenticated user.
    :return: The seed and its provisioning URI.
    """
    secret, uri = await service.begin_enrollment(user)
    return {
        "message": "Scan the code with your authenticator app.",
        "data": MfaEnrollResponse(secret=secret, provisioning_uri=uri).model_dump(),
    }


@mfa_router.post("/enroll/confirm", name="auth:mfa.enroll-confirm")
@api_route()
async def enroll_confirm_route(
    body: MfaCodeRequest = Body(...),
    user: User = Depends(mfa_gate_exempt_guest_user),
):
    """
    Confirm enrollment with the first code and return the recovery codes.

    The recovery codes are shown exactly once; nothing can recover them
    afterwards, only replace them by enrolling again.

    :param body: The code from the authenticator app.
    :param user: The current authenticated user.
    :raises InvalidMfaCodeException: If the code does not verify.
    :return: The recovery codes.
    """
    # Bound guessing here too: without it this route is an oracle for a seed the
    # caller may have only partially observed.
    await enforce_user_rate_limit(
        user.id, times=10, seconds=900, scope="mfa-enroll-confirm"
    )

    codes = await service.confirm_enrollment(user, body.code)
    if codes is None:
        raise InvalidMfaCodeException()

    # A code was just presented, so the step-up window opens here too - enrolling
    # must not immediately ask for a second code to finish what the user was
    # doing.
    await mark_recently_verified(user.id)

    return {
        "message": "Two-factor authentication is on. Save your recovery codes.",
        "data": MfaConfirmResponse(recovery_codes=codes).model_dump(),
    }


@mfa_router.delete("", name="auth:mfa.disable")
@api_route()
async def disable_route(
    body: MfaCodeRequest = Body(...),
    user: User = Depends(guest_user),
):
    """
    Turn the second factor off for the calling account.

    Requires a current code rather than only a session: a stolen session must
    not be able to strip the factor that would have stopped it. Refused outright
    when the deployment requires a factor at this account's role - there the way
    to replace a lost authenticator is an administrative reset, not self-service
    removal.

    :param body: A current code from the authenticator app, or a recovery code.
    :param user: The current authenticated user.
    :raises InvalidMfaCodeException: If the code does not verify.
    :return: Confirmation message.
    """
    await enforce_user_rate_limit(user.id, times=10, seconds=900, scope="mfa-disable")

    if not user.mfa_enabled:
        return {"message": "Two-factor authentication is already off.", "data": None}

    # Refused before the code is checked, so a correct code cannot be spent on
    # an action that was never going to be allowed.
    if policy.required_for_role(user.role_id):
        raise MfaRequiredByPolicyException()

    verified = await service.verify_totp_for_user(user, body.code)
    if not verified:
        verified = await service.redeem_recovery_code(user, body.code)
    if not verified:
        raise InvalidMfaCodeException()

    await service.disable_mfa(user.id)
    # The window is keyed on the account, not on the enrollment, so a marker
    # left over from the code just presented would still be valid if the account
    # re-enrolled within it.
    await clear_recent_verification(user.id)
    runtime.logger.info(f"MFA disabled by user {user.username}")
    return {"message": "Two-factor authentication is off.", "data": None}
