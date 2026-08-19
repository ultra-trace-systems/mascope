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

from sqlalchemy import delete, func, select

from mascope_backend.api.lib.api_features import api_controller
from mascope_backend.api.lib.exceptions.api_exceptions import NotFoundException
from mascope_backend.api.new.auth.config import auth_settings
from mascope_backend.api.new.auth.devices.schemas import DeviceRead
from mascope_backend.api.new.auth.exceptions import ForbiddenAccessException
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
