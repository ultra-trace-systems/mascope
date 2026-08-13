"""
Integration tests for the admin/owner password-reset routes.

Resetting another user's password changes state, so the route must not be
reachable via GET: the SameSite=lax auth cookie rides on cross-site
top-level GET navigations, which would let a crafted link reset a
password with an admin's ambient credentials. These tests pin the POST
verb (on both the admin and owner routes) and the role rules around the
reset - who may call it, and which targets are refused.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from mascope_backend.app.fast import fast


@pytest.mark.asyncio
async def test_admin_resets_editor_password_via_post(admin_client, test_users):
    resp = await admin_client.post(
        f"/api/users/admin/{test_users['editor'].id}/reset-password"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["new_password"]


@pytest.mark.asyncio
async def test_admin_reset_password_is_not_reachable_via_get(admin_client, test_users):
    resp = await admin_client.get(
        f"/api/users/admin/{test_users['editor'].id}/reset-password"
    )
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_owner_reset_password_is_not_reachable_via_get(owner_client, test_users):
    resp = await owner_client.get(
        f"/api/users/owner/{test_users['admin'].id}/reset-password"
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
async def test_owner_cannot_reset_own_password(owner_client, test_users):
    # The owner route refuses a self-reset (use the self-service creds route
    # instead); owner_client is authenticated as the owner user.
    resp = await owner_client.post(
        f"/api/users/owner/{test_users['owner'].id}/reset-password"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_reset_an_admin_role_target(admin_client, test_users):
    # admin_client is authenticated as the admin user, so this targets its own
    # (admin-role) id. The guard keys on the target's role level, not identity:
    # an admin may reset only guest/editor users, never an admin-or-higher one.
    resp = await admin_client.post(
        f"/api/users/admin/{test_users['admin'].id}/reset-password"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_editor_cannot_reset_a_password(editor_client, test_users):
    # Caller authorization: the admin route requires the admin role, so an
    # editor caller is refused before any target-role check.
    resp = await editor_client.post(
        f"/api/users/admin/{test_users['guest'].id}/reset-password"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_anonymous_cannot_reset_a_password(test_users):
    # No auth cookie: the reset routes must require authentication (401), not
    # be reachable anonymously.
    async with AsyncClient(
        transport=ASGITransport(app=fast), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/users/admin/{test_users['editor'].id}/reset-password"
        )
    assert resp.status_code == 401
