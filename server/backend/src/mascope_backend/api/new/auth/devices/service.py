"""
Paired-device management.

Devices are created by pairing approval (see auth/pairing) and managed here:
a sponsor lists, renames and revokes their own machines one at a time -
unlike the per-service "Regenerate" flow, which replaces every token of a
service at once. Revocation deletes the device's tokens and stamps
``revoked_at``; the row is kept so uploads attributed to the device stay
explainable.
"""

from datetime import datetime as dt
from datetime import timezone

from sqlalchemy import delete, func, select, update

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.auth.access_token.service import create_access_token
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.devices.schemas import DeviceRead
from mascope_backend.api.new.auth.exceptions import (
    ForbiddenAccessException,
    InvalidTokenException,
)
from mascope_backend.db import AccessToken, AgentDevice, User, async_session
from mascope_backend.runtime import runtime


def _device_rows_stmt():
    """Devices with sponsor username and live-token count, newest first."""
    return (
        select(AgentDevice, User.username, func.count(AccessToken.token))
        .outerjoin(User, AgentDevice.sponsor_user_id == User.id)
        .outerjoin(AccessToken, AccessToken.device_id == AgentDevice.device_id)
        .group_by(AgentDevice.device_id, User.username)
        .order_by(AgentDevice.created_at.desc())
    )


def _to_device_read(device: AgentDevice, username: str | None, tokens: int) -> dict:
    return DeviceRead(
        device_id=device.device_id,
        name=device.name,
        service_name=device.service_name,
        sponsor_username=username,
        created_at=device.created_at,
        last_seen_at=device.last_seen_at,
        revoked_at=device.revoked_at,
        token_count=tokens,
    ).model_dump()


@api_controller()
async def list_devices(user: User) -> dict:
    """
    List the devices the user sponsors.

    :param user: The authenticated user.
    :type user: User
    :return: The user's devices, newest first.
    :rtype: dict
    """
    async with async_session() as session:
        result = await session.execute(
            _device_rows_stmt().where(AgentDevice.sponsor_user_id == user.id)
        )
        rows = result.all()

    data = [
        _to_device_read(device, username, tokens) for device, username, tokens in rows
    ]
    return {
        "message": f"{len(data)} paired devices.",
        "results": len(data),
        "data": data,
    }


@api_controller()
async def list_all_devices() -> dict:
    """
    List every paired device on the deployment (admin view).

    :return: All devices with their sponsors, newest first.
    :rtype: dict
    """
    async with async_session() as session:
        result = await session.execute(_device_rows_stmt())
        rows = result.all()

    data = [
        _to_device_read(device, username, tokens) for device, username, tokens in rows
    ]
    return {
        "message": f"{len(data)} paired devices on this deployment.",
        "results": len(data),
        "data": data,
    }


@api_controller()
async def rename_device(user: User, device_id: int, name: str) -> dict:
    """
    Rename a device the user sponsors.

    :param user: The authenticated user.
    :type user: User
    :param device_id: The device to rename.
    :type device_id: int
    :param name: The new display name.
    :type name: str
    :raises NotFoundException: Unknown device.
    :raises ForbiddenAccessException: The user does not sponsor this device.
    :return: The renamed device.
    :rtype: dict
    """
    async with async_session() as session:
        device = await session.get(AgentDevice, device_id)
        if device is None:
            raise NotFoundException(f"Device {device_id} not found")
        if device.sponsor_user_id != user.id:
            raise ForbiddenAccessException("You can only rename devices you paired.")

        device.name = name
        await session.commit()
        await session.refresh(device)
        tokens = (
            await session.execute(
                select(func.count(AccessToken.token)).where(
                    AccessToken.device_id == device_id
                )
            )
        ).scalar_one()
        return {
            "message": f"Device renamed to '{device.name}'.",
            "data": _to_device_read(device, user.username, tokens),
        }


@api_controller()
async def revoke_device(actor: User, device_id: int) -> dict:
    """
    Revoke one device: delete its tokens and stamp ``revoked_at``.

    A sponsor may revoke their own devices. Admins may additionally revoke
    devices sponsored by guest or editor accounts, and owners any device -
    the same ceiling as the per-user token deletion routes, because in both
    cases what is removed is a token belonging to the sponsor.

    Idempotent: revoking an already-revoked device reports success without
    moving ``revoked_at``, so the timestamp keeps recording the first
    revocation.

    :param actor: The authenticated user performing the revocation.
    :type actor: User
    :param device_id: The device to revoke.
    :type device_id: int
    :raises NotFoundException: Unknown device.
    :raises ForbiddenAccessException: Not the sponsor, and outside the
        actor's role ceiling.
    :return: The revoked device.
    :rtype: dict
    """
    role_levels = auth_settings.ROLE_ACCESS_LEVELS
    async with async_session() as session:
        device = await session.get(AgentDevice, device_id)
        if device is None:
            raise NotFoundException(f"Device {device_id} not found")

        if device.sponsor_user_id != actor.id:
            if actor.role_id < role_levels["admin"]:
                raise ForbiddenAccessException(
                    "You can only revoke devices you paired."
                )
            sponsor_role_id = None
            if device.sponsor_user_id is not None:
                sponsor_role_id = (
                    await session.execute(
                        select(User.role_id).where(User.id == device.sponsor_user_id)
                    )
                ).scalar_one_or_none()
            # Ceiling as in user management: an admin must not strip an
            # admin's or owner's credentials. A sponsorless device (its
            # sponsor account was removed) is admin-revocable cleanup.
            if (
                sponsor_role_id is not None
                and sponsor_role_id >= role_levels["admin"]
                and actor.role_id < role_levels["owner"]
            ):
                raise ForbiddenAccessException()

        # Delete every token the machine account holds, not only the
        # device-bound one: the file-converter token it uploads with is not
        # bound to the device, and must stop working too.
        if device.machine_user_id is not None:
            deleted = await session.execute(
                delete(AccessToken).where(AccessToken.user_id == device.machine_user_id)
            )
            # Deactivate the machine account so any credential is refused at the
            # active-user gate, and so it cannot be mistaken for a live account.
            await session.execute(
                update(User)
                .where(User.id == device.machine_user_id)
                .values(is_active=False)
            )
        else:
            deleted = await session.execute(
                delete(AccessToken).where(AccessToken.device_id == device_id)
            )
        if device.revoked_at is None:
            device.revoked_at = dt.now(timezone.utc)
        await session.commit()
        await session.refresh(device)

    runtime.logger.warning(
        f"Device '{device.name}' (id {device.device_id}, {device.service_name}) "
        f"revoked by {actor.username}; {deleted.rowcount} token(s) removed"
    )
    return {
        "message": f"Device '{device.name}' revoked; its tokens no longer authenticate.",
        "data": _to_device_read(device, None, 0),
    }


@api_controller()
async def renew_device_token(machine_user: User) -> dict:
    """
    Issue a fresh token for the calling agent's device and reap old ones.

    Called by the agent with its current (still-valid) device token; the token
    it presents identifies its machine account, which maps one-to-one to a
    device. A new device-bound token is created and all but the newest two
    tokens for the device are removed - the just-superseded one is kept so an
    upload in flight during the switch finishes on it, and it lapses on its own
    lifetime rather than being extended.

    :param machine_user: The authenticated machine account (the token's subject).
    :type machine_user: User
    :return: The new token and its lifetime in seconds.
    :rtype: dict
    :raises InvalidTokenException: The caller is not an agent machine account
        with a device (e.g. a personal token, or a revoked device).
    """
    async with async_session() as session:
        device = (
            await session.execute(
                select(AgentDevice).where(
                    AgentDevice.machine_user_id == machine_user.id,
                    AgentDevice.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    if device is None:
        # Only a live paired device renews; a personal token or a revoked
        # device has nothing to rotate.
        raise InvalidTokenException(
            "This credential is not a live paired-device token, so it cannot "
            "be renewed. Re-pair the machine from the agent's setup wizard."
        )

    new_token = await create_access_token(
        user=machine_user,
        service_name=device.service_name,
        description=f"Paired: {device.name}",
        device_id=device.device_id,
    )

    # Keep the newest N tokens for the device (the fresh one and the token it
    # supersedes); remove the rest.
    keep = auth_settings.access_token.DEVICE_TOKENS_KEPT_PER_DEVICE
    async with async_session() as session:
        keep_tokens = (
            (
                await session.execute(
                    select(AccessToken.token)
                    .where(AccessToken.device_id == device.device_id)
                    .order_by(AccessToken.created_at.desc())
                    .limit(keep)
                )
            )
            .scalars()
            .all()
        )
        reaped = await session.execute(
            delete(AccessToken)
            .where(AccessToken.device_id == device.device_id)
            .where(AccessToken.token.notin_(keep_tokens))
        )
        await session.commit()

    runtime.logger.info(
        f"Renewed token for device '{device.name}' (id {device.device_id}); "
        f"reaped {reaped.rowcount} superseded token(s)"
    )
    return {
        "message": "Access token renewed.",
        "data": {
            "access_token": new_token,
            "expires_in": auth_settings.access_token.DEVICE_TOKEN_LIFETIME_SECONDS,
        },
    }
