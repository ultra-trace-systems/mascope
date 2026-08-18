from fastapi import APIRouter, Body, Depends

from mascope_backend.api.lib.rate_limit import rate_limit
from mascope_backend.api.new.auth.dependencies import editor_user
from mascope_backend.api.new.auth.mfa.reauth import require_recent_mfa
from mascope_backend.api.new.auth.pairing.schemas import (
    PairingApproveRequest,
    PairingPollRequest,
    PairingStartRequest,
)
from mascope_backend.api.new.auth.pairing.service import (
    approve_pairing,
    poll_pairing,
    start_pairing,
)


# Agent device-pairing routes, nested under /api/auth (see auth/routes.py).
# `start` and `poll` are unauthenticated by design (the agent has no
# credentials yet) and rate-limited per client IP; the token is only ever
# created in `approve`, which requires an authenticated editor.
pairing_router = APIRouter(prefix="/pairing")


@pairing_router.post(
    "/start",
    name="auth:pairing.start",
    dependencies=[Depends(rate_limit(times=10, seconds=60, scope="pairing-start"))],
)
async def pairing_start_route(
    pairing_start_request: PairingStartRequest = Body(...),
):
    """
    Start a pairing: returns a short user code (displayed by the agent)
    and a secret device code (used by the agent to poll).
    """
    return await start_pairing(
        service_name=pairing_start_request.service_name,
        machine_name=pairing_start_request.machine_name,
    )


@pairing_router.post(
    "/poll",
    name="auth:pairing.poll",
    dependencies=[Depends(rate_limit(times=30, seconds=60, scope="pairing-poll"))],
)
async def pairing_poll_route(
    pairing_poll_request: PairingPollRequest = Body(...),
):
    """
    Poll a pairing by device code. Returns pending/approved/expired;
    on approval the access token is returned exactly once.
    """
    return await poll_pairing(device_code=pairing_poll_request.device_code)


@pairing_router.post("/approve", name="auth:pairing.approve")
async def pairing_approve_route(
    pairing_approve_request: PairingApproveRequest = Body(...),
    user=Depends(editor_user),
):
    """
    Approve a pairing code: creates a new access token for the approving
    user and the pairing's service, without revoking existing tokens.
    Requires the editor role or higher.

    Like the regenerate route, this mints a year-long credential, so an account
    with a second factor must have presented a code recently. Approving is the
    one step in pairing that a person performs from a session, which makes it
    the step where that check belongs.
    """
    await require_recent_mfa(user)

    return await approve_pairing(user=user, user_code=pairing_approve_request.user_code)
