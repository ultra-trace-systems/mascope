"""
Integration tests for the admin/owner password-reset routes.

Resetting another user's password changes state, so the route must not be
reachable via GET: the SameSite=lax auth cookie rides on cross-site
top-level GET navigations, which would let a crafted link reset a
password with an admin's ambient credentials. These tests pin the POST
verb and the role rules around the reset.
"""

import pytest


@pytest.mark.asyncio
async def test_admin_resets_editor_password_via_post(admin_client, test_users):
    resp = await admin_client.post(
        f"/api/users/admin/{test_users['editor'].id}/reset-password"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["new_password"]


@pytest.mark.asyncio
async def test_reset_password_is_not_reachable_via_get(admin_client, test_users):
    resp = await admin_client.get(
        f"/api/users/admin/{test_users['editor'].id}/reset-password"
    )
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_owner_resets_admin_password_via_post(owner_client, test_users):
    resp = await owner_client.post(
        f"/api/users/owner/{test_users['admin'].id}/reset-password"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["new_password"]


@pytest.mark.asyncio
async def test_admin_cannot_reset_a_peer_admin_password(admin_client, test_users):
    resp = await admin_client.post(
        f"/api/users/admin/{test_users['admin'].id}/reset-password"
    )
    assert resp.status_code == 403
