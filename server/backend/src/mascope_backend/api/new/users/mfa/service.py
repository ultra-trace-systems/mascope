"""
Administrative reset of another account's second factor.

The recovery path of last resort inside the application. An authenticator lost
together with its recovery codes leaves no self-service way back, and these
deployments have no support desk to call; the only step beyond this one is the
CLI escape hatch on the host, for when nobody who could perform this reset can
sign in either.
"""

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.new.auth.mfa.reauth import clear_recent_verification
from mascope_backend.api.new.auth.mfa.service import disable_mfa
from mascope_backend.api.new.users.service import get_user
from mascope_backend.runtime import runtime
from mascope_backend.socket.records.service import emit_record_updated


@api_controller()
async def reset_user_mfa(user_id: int) -> dict:
    """
    Clear an account's second factor so its holder can enrol again.

    Clearing is all it does: the account signs in with its password alone until
    it enrols again, which the deployment policy requires immediately if it
    covers that role. It does not hand anyone a way in - the password is still
    needed, and is untouched.

    :param user_id: The account to clear.
    :raises NotFoundException: If the account does not exist.
    :return: A message and the updated user.
    """
    # Resolved before the write so a bad id fails without side effects.
    await get_user(user_id=user_id)

    await disable_mfa(user_id)
    # Any step-up window this account had open was opened by the factor just
    # removed, so it must not outlive it.
    await clear_recent_verification(user_id)

    updated = (await get_user(user_id=user_id))["data"]
    runtime.logger.info(f"MFA reset for user {updated.username}")

    await emit_record_updated(
        record_type="user",
        record_id=str(user_id),
        record=updated.model_dump(),
    )
    return {
        "message": (
            f"Two-factor authentication reset for '{updated.username}'. "
            "They can set it up again from their account settings."
        ),
        "data": updated,
    }
