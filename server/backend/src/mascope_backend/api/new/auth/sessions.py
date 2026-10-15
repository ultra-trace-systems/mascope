"""
Ending web sessions before they expire.

A web session is a signed token the server keeps no record of, so it cannot be
deleted the way a bearer token row can. It carries the moment it was issued
(``iat``) instead, and the user row carries a cutoff: a session issued before
the cutoff is refused. The cutoff is the later of two stamps:

- ``password_changed_at``, written by every password write, and
- ``sessions_revoked_at``, written by an account disable, the forced-password-
  change sweep and the sign-out-everywhere action (:func:`revoke_user_sessions`).

The comparison lives here, in one place, because two boundaries make it: the
cookie strategy on every request and the socket on every subscribe. Bearer
tokens never come here - they are database rows and revoked by deleting them.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import update

from mascope_backend.db import User, async_session


#: How far before the cutoff a session may have been issued and still be kept.
#: Tokens are minted on whichever worker served the sign-in, and the cutoff is
#: stamped by whichever served the revocation, so the two clocks are not the
#: same clock: without slack, a sign-in moments after a password change could be
#: refused because its worker's clock runs behind. The cost is that a session
#: issued in the minute before a revocation survives it.
SESSION_CUTOFF_TOLERANCE = timedelta(minutes=1)


def session_cutoff(user: User) -> Optional[datetime]:
    """
    The moment before which this account's web sessions are refused.

    :param user: The account.
    :return: The later of the password change and the revocation stamp, or
        ``None`` when neither has happened.
    """
    stamps = [
        stamp
        for stamp in (user.password_changed_at, user.sessions_revoked_at)
        if stamp is not None
    ]
    return max(stamps) if stamps else None


def session_revoked(user: User, issued_at: Optional[int]) -> bool:
    """
    Whether a web session issued at ``issued_at`` has been ended.

    A session without an issue time - minted before tokens carried one - cannot
    show that it postdates the cutoff, so it is refused as soon as the account
    has one. That signs such sessions out once rather than leaving them
    unrevocable for the rest of their lifetime.

    :param user: The account the session belongs to.
    :param issued_at: The session's ``iat`` claim, seconds since the epoch.
    :return: True when the session must be refused.
    """
    cutoff = session_cutoff(user)
    if cutoff is None:
        return False
    if issued_at is None:
        return True
    issued = datetime.fromtimestamp(issued_at, tz=timezone.utc)
    return issued < cutoff - SESSION_CUTOFF_TOLERANCE


async def revoke_user_sessions(user_id: int) -> Optional[datetime]:
    """
    End every web session of an account that exists now.

    Moves the account's cutoff to the present. Sessions issued from here on are
    unaffected, so the account can sign in again straight away - this ends
    sessions, it does not lock anyone out.

    :param user_id: The account.
    :return: The new cutoff, or ``None`` when no such account exists.
    """
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        result = await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(sessions_revoked_at=now)
            .returning(User.id)
            .execution_options(synchronize_session=False)
        )
        revoked = result.scalar_one_or_none()
        await session.commit()
    return now if revoked is not None else None
