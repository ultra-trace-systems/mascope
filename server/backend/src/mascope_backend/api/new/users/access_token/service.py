"""
Access token management for other users.

It allows admins or owners to delete access tokens for other users.
"""

from sqlalchemy import delete

from mascope_backend.accounts import refuse_machine_account
from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.db import AccessToken, User, async_session
from mascope_backend.runtime import runtime


@api_controller()
async def delete_user_access_tokens(
    user_id: int, service_name: str | None = None
) -> dict:
    """
    Deletes access tokens for the specified user.

    :param user_id: The ID of the user whose tokens should be deleted.
    :param service_name: Optional service name to delete only specific tokens.
                        If None, deletes all tokens for the user.
    :return: A success message with count of deleted tokens.
    """
    async with async_session() as session:
        user = await session.get(User, user_id)
        if not user:
            raise NotFoundException(f"User with ID '{user_id}' not found")

        # A machine account's credentials are revoked with its device, which
        # applies the sponsor ceiling. Reached from here they would not: every
        # machine account sits at editor, so the admin routes' "not an admin or
        # above" check would let an admin silently kill an owner's agent.
        refuse_machine_account(user)

        delete_query = delete(AccessToken).where(AccessToken.user_id == user_id)

        # Filter by service if specified
        if service_name:
            delete_query = delete_query.where(AccessToken.service_name == service_name)

        result = await session.execute(delete_query)
        await session.commit()

        if result.rowcount == 0:
            service_msg = f"for service '{service_name}'" if service_name else ""
            message = (
                f"No access tokens {service_msg} found for user `{user.username}`."
            )
        else:
            service_msg = f"'{service_name}' " if service_name else ""
            message = f"Deleted {result.rowcount} {service_msg}access token(s) for user `{user.username}`."

        runtime.logger.info(message)
        return {"message": message}
