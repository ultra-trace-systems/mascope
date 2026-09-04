"""Access token validation."""

import time
from datetime import datetime as dt
from datetime import timedelta, timezone

from sqlalchemy import or_, update

from mascope_backend.api.new.auth.access_token import cache as token_cache
from mascope_backend.api.new.auth.access_token.util import resolve_token_context
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.exceptions import (
    AgentCredentialRefusedException,
    InvalidTokenException,
)
from mascope_backend.api.new.auth.pairing.config import pairing_settings
from mascope_backend.api.new.auth.reported import (
    AGENT_VERSION_MAX_LENGTH,
    clean_reported_text,
)
from mascope_backend.api.new.auth.strategies.database import (
    get_database_strategy_context,
)
from mascope_backend.db import AgentDevice, async_session
from mascope_backend.runtime import runtime


# Seconds between last_seen_at writes for one device: "last seen" needs
# minute precision, so a busy agent (a resumable upload is one request per
# chunk) costs one write per window instead of one per request.
DEVICE_LAST_SEEN_THROTTLE_S = 60


def agent_version_from_header(value: str | None) -> str | None:
    """
    The agent release a request reports, fit for storing and showing.

    The header is free text from a client, so it goes through the same
    cleaning as the version in a pairing request: control characters
    stripped, cut to the column's width. An absent or blank header is None,
    which records that this request reported no version.

    :param value: The raw ``X-Agent-Version`` header, if the request sent one.
    :type value: str | None
    :return: The cleaned version, or None.
    :rtype: str | None
    """
    return clean_reported_text(value, AGENT_VERSION_MAX_LENGTH)


def ensure_device_bound(service_name: str, device_id: int | None) -> None:
    """
    Refuse an unbound agent token when the deployment requires paired devices.

    Applies only to the pairable agent services; personal (mascope_sdk) and
    internal (file-converter) tokens have no device concept and pass through.

    :param service_name: The token's service scope.
    :type service_name: str
    :param device_id: The token's device binding, None when unbound.
    :type device_id: int | None
    :raises AgentCredentialRefusedException: The deployment requires paired
        devices and this agent token has none (issued before the device
        registry, or its device was removed).
    """
    if service_name not in pairing_settings.ALLOWED_SERVICES:
        return
    if device_id is None and runtime.config.require_device_tokens:
        raise AgentCredentialRefusedException(
            "This deployment accepts only paired agent credentials. "
            "Re-pair this machine from the agent's setup wizard to get a new token."
        )


def ensure_device_token_fresh(
    service_name: str, device_id: int | None, created_at: dt | None
) -> None:
    """
    Refuse a device-bound agent token that is older than its short lifetime.

    Device tokens live in plaintext on shared instrument PCs, so they expire
    far sooner than the 360-day database-strategy cap and the agent renews them
    automatically. Enforced only for device-bound tokens; unbound personal or
    internal tokens keep the default lifetime. A token past the limit is
    refused, not deleted - renewal (a fresh token) or re-pairing is the way
    back.

    :param service_name: The token's service scope.
    :param device_id: The token's device binding; None means not a device token.
    :param created_at: When the token was issued.
    :raises AgentCredentialRefusedException: The device token has outlived its
        lifetime.
    """
    if device_id is None or created_at is None:
        return
    lifetime = timedelta(
        seconds=auth_settings.access_token.DEVICE_TOKEN_LIFETIME_SECONDS
    )
    if dt.now(timezone.utc) - created_at > lifetime:
        raise AgentCredentialRefusedException(
            "This agent credential has expired. The agent renews its token "
            "automatically; if it has been offline past the token's lifetime, "
            "re-pair the machine from the agent's setup wizard."
        )


#: Monotonic time of this worker's claim on the last_seen write per device.
#: Bounded by the number of paired devices a deployment has, which is small and
#: finite. An entry means "this worker has a write for this device in flight,
#: or landed one within the window" - taken by _mark_last_seen_written, handed
#: back by _release_last_seen_mark when the write does not land.
_last_seen_written_at: dict[int, float] = {}


def _is_due_for_last_seen_write(device_id: int) -> bool:
    """
    Whether this worker should issue a last_seen write for ``device_id``.

    Pure: asking does not claim the window. :func:`_mark_last_seen_written`
    does that, and the caller claims before issuing the write rather than
    after, so concurrent requests for one device do not each check out a
    connection for a row only one of them can change.

    :param device_id: The authenticated device.
    :return: True when no write has been claimed within the throttle window.
    :rtype: bool
    """
    last = _last_seen_written_at.get(device_id)
    if last is None:
        return True
    return time.monotonic() - last >= DEVICE_LAST_SEEN_THROTTLE_S


def _mark_last_seen_written(device_id: int) -> float:
    """
    Claim the throttle window for ``device_id``.

    :param device_id: The authenticated device.
    :return: The stamp written, for :func:`_release_last_seen_mark`.
    :rtype: float
    """
    stamp = time.monotonic()
    _last_seen_written_at[device_id] = stamp
    return stamp


def _release_last_seen_mark(device_id: int, stamp: float) -> None:
    """
    Give a claimed window back, so the next request retries the write.

    Only when the claim is still the one ``stamp`` made: a write can outlive
    its own window (the pool's timeout is twice the throttle), by which time a
    later request may legitimately hold the claim, and clearing that one would
    let the herd back in.

    The identity check is only as fine-grained as the platform clock. Linux
    gives nanoseconds, so it is exact where this runs; on a coarse clock two
    claims taken inside one tick compare equal and a failure could release the
    newer one. The cost of that is one extra UPDATE, never a lost write.

    :param device_id: The authenticated device.
    :param stamp: The value :func:`_mark_last_seen_written` returned.
    """
    if _last_seen_written_at.get(device_id) == stamp:
        del _last_seen_written_at[device_id]


async def touch_device_last_seen(
    device_id: int, agent_version: str | None = None
) -> None:
    """
    Record that a device authenticated, at most once per throttle window.

    The agent's reported version rides on the same write, so recording it
    costs no extra round trip; it is therefore also refreshed at most once
    per window, which is plenty for a value that changes only at an upgrade.
    It is written whether or not the request carried one, so the column says
    what the machine last reported rather than the highest it ever reported:
    a site rolled back to an agent that sends no version would otherwise go
    on showing the newer release indefinitely, and following an upgrade
    across instrument PCs is what the column is for.

    Two gates, doing different jobs. The in-process claim keeps this worker to
    one write per device per window and, because it is taken before the await,
    to one write *in flight* per device: this path takes no admission-control
    permit (see :mod:`mascope_backend.db`), so N concurrent agent requests
    would otherwise be N concurrent checkouts for a row only one of them can
    change. The WHERE clause is the cross-worker backstop - several workers may
    each claim once per window, and it keeps that to one actual update, exactly
    as before.

    A write that does not land gives its claim back, so the next request
    retries rather than the device going unreported for the rest of the
    window. That matters most under pool exhaustion, which is both when the
    write fails and when an operator most wants to know what a device is doing.
    It also means the caller must keep letting this raise: swallowing the error
    here would turn every request into another parked pool waiter.

    :param device_id: The authenticated device.
    :type device_id: int
    :param agent_version: The release the agent reported, already cleaned,
        or None when this request reported none.
    :type agent_version: str | None
    """
    now = dt.now(timezone.utc)
    if not _is_due_for_last_seen_write(device_id):
        # Skips the round trip entirely. Without it this costs a session, a
        # connection and a commit on every request - once per chunk for an
        # upload, to change nothing 59 times out of 60.
        return
    stamp = _mark_last_seen_written(device_id)
    committed = False
    cutoff = now - timedelta(seconds=DEVICE_LAST_SEEN_THROTTLE_S)
    try:
        async with async_session() as session:
            await session.execute(
                update(AgentDevice)
                .where(AgentDevice.device_id == device_id)
                .where(
                    or_(
                        AgentDevice.last_seen_at.is_(None),
                        AgentDevice.last_seen_at < cutoff,
                    )
                )
                .values(last_seen_at=now, last_seen_version=agent_version)
            )
            await session.commit()
            # Re-stamp from the commit rather than the attempt, and here rather
            # than after the block: AsyncSession.__aexit__ awaits a shielded
            # close(), which is a cancellation point, so a client that hangs up
            # while the connection is released would skip a mark placed after
            # the `async with` and leave every later request in the window
            # re-issuing an UPDATE that already committed. Nothing is awaited
            # between commit() returning and these two lines.
            stamp = _mark_last_seen_written(device_id)
            committed = True
    except BaseException:
        # BaseException, not Exception: a cancelled write (the client hung up)
        # raises CancelledError, which is not an Exception, and is exactly the
        # write the next request should retry. `committed` is what stops a
        # failure while the session is being released from un-marking a write
        # that is already durable.
        if not committed:
            _release_last_seen_mark(device_id, stamp)
        raise


async def validate_service_access_token(access_token: str, service_name: str):
    """
    Validate service access token and return associated user.

    :param access_token: Access token string for service
    :type access_token: str
    :param service_name: Expected service name
    :type service_name: str
    :return: User instance if token is valid
    :raises InvalidTokenException: If token is invalid or service mismatch
    """
    try:
        # Step 1. Basic token validation
        if not isinstance(access_token, str):
            raise InvalidTokenException("Invalid token format: not a string")

        # Step 1b. A validation this recent is reused rather than repeated.
        # An upload revalidates the same token once per chunk; see
        # access_token.cache for what that costs and what caching it trades
        # away (revocation becomes effective within the window, not at once).
        cached_user = token_cache.get(
            access_token, service_name, auth_settings.access_token
        )
        if cached_user is not None:
            return cached_user

        # Step 2. Token validation using access token strategy
        from mascope_backend.api.new.users.user_manager.util import (
            get_user_manager_context,
        )

        # One session for both adapters. They are used sequentially, never
        # concurrently, so a session each bought nothing and cost a second
        # connection held for the whole call. This path takes no admission
        # permit (see mascope_backend.db), so its concurrency is bounded only
        # by how many bearer requests are in flight - which is what exhausted
        # the pool under a bulk upload: every waiter then blocked for
        # pool_timeout and the worker stopped serving anything at all.
        async with async_session() as session:
            async with get_database_strategy_context(session) as database_strategy:
                async with get_user_manager_context(session) as user_manager:
                    user = await database_strategy.read_token(
                        access_token, user_manager
                    )
                    if not user:
                        raise InvalidTokenException(
                            "Token validation failed, no associated user found"
                        )

                    # Service scope, device binding and token age in one
                    # lookup, run on the session already open above. This used
                    # to call get_token_service as well, which re-read the same
                    # row twice more for values this query already returns; and
                    # it used to open its own session, which meant holding one
                    # connection while needing a second - the pattern
                    # mascope_backend.db documents as the worker deadlock, on a
                    # path that takes no permit. Sharing the session means this
                    # function never needs a connection it does not already
                    # hold. The exceptions raised are unchanged.
                    #
                    # Stays here, after read_token: hoisting it above the
                    # session would report a wrong-service token before a
                    # missing user, and an agent operator is told to do
                    # different things about those two.
                    token_service, device_id, created_at = await resolve_token_context(
                        access_token, auth_settings.access_token, session
                    )
                    if token_service != service_name:
                        raise InvalidTokenException(
                            f"The provided token is not authorized for {service_name}. Please try to refresh the token."
                        )

                    # Device policy for agent tokens (no-op for other services):
                    # the deployment's paired-device requirement and the short
                    # device-token lifetime.
                    ensure_device_bound(service_name, device_id)
                    ensure_device_token_fresh(service_name, device_id, created_at)

                    # Only successes are cached: tokens are minted on demand,
                    # so a cached rejection would refuse one that had just
                    # been created.
                    token_cache.put(
                        access_token, service_name, user, auth_settings.access_token
                    )
                    return user

    except InvalidTokenException as e:
        # Routine 401-class condition (expired/mismatched service token, cured
        # by a token refresh); the raised exception reports it upstream
        runtime.logger.info(f"User's service token validation failed: {str(e)}")
        raise
    except Exception as e:
        runtime.logger.error(f"Service token validation failed: {str(e)}")
        raise InvalidTokenException("Token validation failed") from e
