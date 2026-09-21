"""Lookups on paired agent devices that more than one layer needs.

Kept in the database package, beside the models it reads, so that the socket
layer and the API controllers can share it without importing each other.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mascope_backend.db.models import AgentDevice


async def device_sponsor_id(session: AsyncSession, user_id: int | None) -> int | None:
    """The sponsor of the device that authenticates as this account, if any.

    Only a machine account (an instrument agent's credential) has a device, so
    a person's id finds none. Neither does a machine whose device lost its
    sponsor when the sponsor's account was removed (``ON DELETE SET NULL``).

    :param session: An open async session.
    :type session: AsyncSession
    :param user_id: The account to look up; ``None`` finds nothing.
    :type user_id: int | None
    :return: The sponsor's user id, or ``None``.
    :rtype: int | None
    """
    if user_id is None:
        return None
    return await session.scalar(
        select(AgentDevice.sponsor_user_id)
        .where(
            AgentDevice.machine_user_id == user_id,
            AgentDevice.sponsor_user_id.is_not(None),
        )
        .limit(1)
    )
