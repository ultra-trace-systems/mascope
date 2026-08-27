from unittest.mock import AsyncMock

import pytest

from mascope_backend.api.new.workspaces import service as workspaces_service


"""
Tests: Workspace membership management.

Verifies the /api/workspaces/{workspace_id}/members endpoints enforce
correct role requirements and handle CRUD operations properly.
"""


def _members_url(workspace_id, user_id=None):
    base = f"/api/workspaces/{workspace_id}/members"
    if user_id is not None:
        return f"{base}/{user_id}"
    return base


# ============= Read (list members) =============


@pytest.mark.asyncio
async def test_list_members_as_guest(guest_client, ws_alpha):
    """Guest members can list workspace members."""
    resp = await guest_client.get(_members_url(ws_alpha["workspace_id"]))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 4  # guest, editor, admin, owner


@pytest.mark.asyncio
async def test_list_members_as_outsider(outsider_client, ws_alpha):
    """Non-members cannot list workspace members."""
    resp = await outsider_client.get(_members_url(ws_alpha["workspace_id"]))
    assert resp.status_code == 403


# ============= Add member =============


@pytest.mark.asyncio
async def test_add_member_as_admin(admin_client, outsider_user, ws_alpha):
    """Admin can add a new member to the workspace."""
    resp = await admin_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )
    assert resp.status_code == 201
    member_data = resp.json()["data"]
    assert member_data["user_id"] == outsider_user.id
    assert member_data["workspace_role"] == "guest"

    # Clean up: remove the member so other tests aren't affected
    await admin_client.delete(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
    )


@pytest.mark.asyncio
async def test_add_member_as_guest_forbidden(guest_client, outsider_user, ws_alpha):
    """Guest cannot add members."""
    resp = await guest_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_add_member_as_editor_forbidden(editor_client, outsider_user, ws_alpha):
    """Editor cannot add members."""
    resp = await editor_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_add_duplicate_member(admin_client, test_users, ws_alpha):
    """Adding an already-existing member returns a conflict error."""
    resp = await admin_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": test_users["guest"].id, "workspace_role": "guest"},
    )
    assert resp.status_code == 409


# ============= Update member role =============


@pytest.mark.asyncio
async def test_update_member_role_as_admin(
    admin_client,
    outsider_user,
    ws_alpha,
):
    """Admin can change a member's role."""
    # First add the outsider as guest
    await admin_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )

    # Promote to editor
    resp = await admin_client.patch(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
        json={"workspace_role": "editor"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["workspace_role"] == "editor"

    # Clean up
    await admin_client.delete(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
    )


@pytest.mark.asyncio
async def test_update_member_role_as_guest_forbidden(
    guest_client, test_users, ws_alpha
):
    """Guest cannot change member roles."""
    resp = await guest_client.patch(
        _members_url(ws_alpha["workspace_id"], test_users["editor"].id),
        json={"workspace_role": "admin"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_member_role_as_editor_forbidden(
    editor_client, test_users, ws_alpha
):
    """Editor cannot change member roles."""
    resp = await editor_client.patch(
        _members_url(ws_alpha["workspace_id"], test_users["guest"].id),
        json={"workspace_role": "admin"},
    )
    assert resp.status_code == 403


# ============= Remove member =============


@pytest.mark.asyncio
async def test_remove_member_as_admin(admin_client, outsider_user, ws_alpha):
    """Admin can remove a member from the workspace."""
    # Add outsider first
    await admin_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )

    # Remove
    resp = await admin_client.delete(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["removed"] is True


@pytest.mark.asyncio
async def test_remove_last_owner_forbidden(owner_client, ws_alpha):
    """The last owner of a workspace cannot remove themselves (403)."""
    resp = await owner_client.delete(
        _members_url(ws_alpha["workspace_id"], ws_alpha["members"]["owner"].id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_remove_owner(
    admin_client, owner_client, outsider_user, ws_alpha
):
    """Admin cannot remove an owner even when multiple owners exist (role ceiling)."""
    # Add outsider as a second owner
    await owner_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "owner"},
    )

    # Admin tries to remove the second owner — should be blocked by role ceiling
    resp = await admin_client.delete(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
    )
    assert resp.status_code == 403

    # Clean up: owner removes the second owner
    await owner_client.delete(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
    )


@pytest.mark.asyncio
async def test_remove_member_as_guest_forbidden(guest_client, test_users, ws_alpha):
    """Guest cannot remove members."""
    resp = await guest_client.delete(
        _members_url(ws_alpha["workspace_id"], test_users["editor"].id),
    )
    assert resp.status_code == 403


# ============= Role ceiling enforcement =============


@pytest.mark.asyncio
async def test_admin_cannot_add_owner(admin_client, outsider_user, ws_alpha):
    """Admin cannot assign the owner role (exceeds their own level)."""
    resp = await admin_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "owner"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_promote_to_owner(admin_client, outsider_user, ws_alpha):
    """Admin cannot promote a member to owner (exceeds their own level)."""
    # Add outsider as guest first
    await admin_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )

    # Attempt to promote to owner
    resp = await admin_client.patch(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
        json={"workspace_role": "owner"},
    )
    assert resp.status_code == 403

    # Clean up
    await admin_client.delete(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
    )


@pytest.mark.asyncio
async def test_owner_can_assign_owner(owner_client, outsider_user, ws_alpha):
    """Owner can assign the owner role to another member."""
    # Add outsider as guest first
    await owner_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )

    # Promote to owner
    resp = await owner_client.patch(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
        json={"workspace_role": "owner"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["workspace_role"] == "owner"

    # Clean up
    await owner_client.delete(
        _members_url(ws_alpha["workspace_id"], outsider_user.id),
    )


# ============= Self-removal =============


@pytest.mark.asyncio
async def test_member_can_remove_self(guest_client, test_users, ws_alpha, admin_client):
    """Any member can remove themselves from a workspace."""
    # Use admin to add the outsider as a guest we can control
    # Instead, use the guest user who is already a member - but we need to re-add them after
    # So let's use a different approach: add outsider, create a client, self-remove

    # Add outsider as editor
    outsider_user_id = test_users["guest"].id

    # Guest removing themselves
    resp = await guest_client.delete(
        _members_url(ws_alpha["workspace_id"], outsider_user_id),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["removed"] is True

    # Re-add guest for other tests
    await admin_client.post(
        _members_url(ws_alpha["workspace_id"]),
        json={"user_id": outsider_user_id, "workspace_role": "guest"},
    )


@pytest.mark.asyncio
async def test_guest_cannot_remove_others(guest_client, test_users, ws_alpha):
    """Guest cannot remove other members (only self)."""
    resp = await guest_client.delete(
        _members_url(ws_alpha["workspace_id"], test_users["editor"].id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_remove_member_as_editor_forbidden(editor_client, test_users, ws_alpha):
    """Editor cannot remove members."""
    resp = await editor_client.delete(
        _members_url(ws_alpha["workspace_id"], test_users["guest"].id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_remove_nonexistent_member(admin_client, ws_alpha):
    """Removing a non-member returns 404."""
    resp = await admin_client.delete(
        _members_url(ws_alpha["workspace_id"], 999999),
    )
    assert resp.status_code == 404


# ============= Record reload =============
#
# The members dialog refreshes its roster on the workspace record-reload
# broadcast, and only when a single broadcast both reaches a room the tab has
# joined and names the workspace on screen in ``record_id``. Adding a member
# was already pinned by test_registration_membership.py; a role edit and a
# removal are pinned here, because dropping ``record_id`` from either one left
# the whole suite green while silently killing the dialog's live refresh.


def _reload_targets(emit):
    """``(record_id, room)`` of every workspace record-reload the mock recorded.

    Pairing the two per call is the point: read off the list of calls
    separately, an implementation emitting the room on one call and the
    ``record_id`` on another would satisfy both checks and refresh nothing.
    """
    return [
        (call.kwargs.get("record_id"), call.kwargs.get("room"))
        for call in emit.await_args_list
        if call.kwargs.get("record_type") == "workspace"
    ]


@pytest.mark.asyncio
async def test_update_member_announces_the_workspace_it_changed(
    admin_client, outsider_user, ws_alpha, monkeypatch
):
    """A role edit broadcasts to the workspace room, naming that workspace.

    Without the ``record_id`` the dialog drops the broadcast, and an
    administrator watching the roster keeps seeing the member's old role.
    """
    workspace_id = ws_alpha["workspace_id"]
    await admin_client.post(
        _members_url(workspace_id),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )

    # Recording starts only after the setup addition, which emits a broadcast
    # of its own - otherwise the assertion below could be satisfied by the
    # addition and could never fail on the update.
    emit = AsyncMock()
    monkeypatch.setattr(workspaces_service, "emit_record_reload", emit)

    resp = await admin_client.patch(
        _members_url(workspace_id, outsider_user.id),
        json={"workspace_role": "editor"},
    )
    assert resp.status_code == 200

    assert (workspace_id, workspace_id) in _reload_targets(emit)

    # Clean up
    await admin_client.delete(_members_url(workspace_id, outsider_user.id))


@pytest.mark.asyncio
async def test_remove_member_announces_the_workspace_it_changed(
    admin_client, outsider_user, ws_alpha, monkeypatch
):
    """A removal broadcasts to the workspace and the removed user, naming it.

    Without the ``record_id`` the dialog drops the broadcast and goes on
    listing the member who has just been removed.
    """
    workspace_id = ws_alpha["workspace_id"]
    await admin_client.post(
        _members_url(workspace_id),
        json={"user_id": outsider_user.id, "workspace_role": "guest"},
    )

    # The addition emits the very same room list this removal does, so the
    # recording has to start after it or the assertion could never fail.
    emit = AsyncMock()
    monkeypatch.setattr(workspaces_service, "emit_record_reload", emit)

    resp = await admin_client.delete(_members_url(workspace_id, outsider_user.id))
    assert resp.status_code == 200

    assert (
        workspace_id,
        [workspace_id, f"user-{outsider_user.id}"],
    ) in _reload_targets(emit)
