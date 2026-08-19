from fastapi import APIRouter, Body, Depends, Path

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import admin_user, current_active_user
from mascope_backend.api.new.auth.devices.schemas import DeviceRename
from mascope_backend.api.new.auth.devices.service import (
    list_all_devices,
    list_devices,
    rename_device,
    revoke_device,
)


# Paired-device management, nested under /api/auth (see auth/routes.py).
# Ownership is enforced in the service layer: the self routes act only on
# devices the caller sponsors, so the base authenticated dependency is
# enough (an account without devices simply sees an empty list).
devices_router = APIRouter(prefix="/devices")


@devices_router.get("")
@api_route()
async def list_devices_route(user=Depends(current_active_user)):
    """List the paired devices the caller sponsors."""
    return await list_devices(user=user)


@devices_router.get("/all")
@api_route()
async def list_all_devices_route(user=Depends(admin_user)):
    """List every paired device on the deployment. Requires admin or owner."""
    return await list_all_devices()


@devices_router.patch("/{device_id}")
@api_route()
async def rename_device_route(
    device_id: int = Path(..., description="Device to rename"),
    body: DeviceRename = Body(...),
    user=Depends(current_active_user),
):
    """Rename a paired device the caller sponsors."""
    return await rename_device(user=user, device_id=device_id, name=body.name)


@devices_router.delete("/{device_id}")
@api_route()
async def revoke_device_route(
    device_id: int = Path(..., description="Device to revoke"),
    user=Depends(current_active_user),
):
    """
    Revoke one paired device: its tokens stop authenticating immediately,
    other devices are untouched. Sponsors revoke their own devices; admins
    and owners may revoke others within the user-management role ceiling.
    """
    return await revoke_device(actor=user, device_id=device_id)
