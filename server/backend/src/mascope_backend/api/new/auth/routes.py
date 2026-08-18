from fastapi import APIRouter, Depends

from mascope_backend.api.lib.rate_limit import (
    rate_limit,
    rate_limit_login_by_account,
)
from mascope_backend.api.new.auth.access_token.routes import access_token_router
from mascope_backend.api.new.auth.login import login_router
from mascope_backend.api.new.auth.mfa.routes import mfa_router
from mascope_backend.api.new.auth.pairing.routes import pairing_router


# main Auth router
auth_router = APIRouter(prefix="/api/auth", tags=["Auth"])

# Sign-in and sign-out. Local rather than fastapi-users' auth router, because an
# account with a second factor must not be handed a session by the password step
# alone - see api/new/auth/login.py.
#
# Two complementary rate limits blunt password brute-forcing / credential
# stuffing against login (both also cover logout in this sub-router, harmlessly):
# - per client IP: caps bursts from a single source.
# - per account: caps attempts against one identifier from many rotating IPs,
#   which the per-IP limit alone cannot stop once the firewall is removed.
auth_router.include_router(
    login_router,
    dependencies=[
        Depends(rate_limit(times=10, seconds=60, scope="auth-login")),
        Depends(rate_limit_login_by_account(times=15, seconds=300)),
    ],
)

# Include the access token router within the main auth router for nested routing
auth_router.include_router(access_token_router)

# Second-factor enrollment and the verify step that completes a sign-in
# (rate limits declared per route)
auth_router.include_router(mfa_router)

# Agent device-pairing routes (rate limits declared per route)
auth_router.include_router(pairing_router)


# add more routes such as password reset and email verification etc, check library code
# auth_router.include_router(
#     fastapi_users.get_reset_password_router(),
# )
#
# auth_router.include_router(
#     fastapi_users.get_verify_router(UserRead),
# )
