"""Tests: per-user scoping of the temp download route ``GET /api/temp/{name}``.

A user may download only files in their own temp directory - never another
user's, even by exact filename - and path traversal is rejected. Regression
cover for the pre-hardening state where any authenticated user could fetch any
temp file by name (cross-tenant export leak).
"""

import os
from urllib.parse import quote

import pytest

from mascope_backend.runtime import runtime


def _plant_temp_file(user_id: int, name: str, content: str) -> str:
    """Write a file directly into ``user_id``'s temp dir (test setup)."""
    directory = runtime.env.path("temp", str(user_id))
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


@pytest.mark.asyncio
async def test_download_own_temp_file(guest_client, test_users):
    """A user can download a file from their own temp dir."""
    path = _plant_temp_file(
        test_users["guest"].id, "guest_export.csv", "mz;intensity\n1.0;2.0\n"
    )
    try:
        resp = await guest_client.get("/api/temp/guest_export.csv")
        assert resp.status_code == 200
        assert "intensity" in resp.text
    finally:
        os.remove(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["20261027_Run_#3.xlsx", "20261027_100%41.xlsx"])
async def test_download_encoded_name(guest_client, test_users, name):
    """A name the app encodes as one path segment is served under that name.

    Export names keep ``#`` and ``%`` (``download_name``), so the frontend puts
    them into the URL with ``encodeURIComponent``; the route decodes the
    segment back to the file's own name, not to another one.
    """
    path = _plant_temp_file(test_users["guest"].id, name, "mz;intensity\n")
    try:
        resp = await guest_client.get(f"/api/temp/{quote(name, safe='')}")
        assert resp.status_code == 200
        assert "intensity" in resp.text
    finally:
        os.remove(path)


@pytest.mark.asyncio
async def test_cannot_download_another_users_temp_file(owner_client, test_users):
    """A file in the guest's temp dir is not reachable by another user by name."""
    path = _plant_temp_file(test_users["guest"].id, "guest_secret.csv", "secret\n")
    try:
        # owner is a different user with an empty temp dir of their own.
        resp = await owner_client.get("/api/temp/guest_secret.csv")
        assert resp.status_code == 404
    finally:
        os.remove(path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack",
    [
        "..",
        "../owner_secret.csv",
        "..%2f..%2fsecrets%2fjwt_secret_key.txt",
    ],
)
async def test_temp_download_rejects_traversal(guest_client, attack):
    """Traversal attempts never resolve to a file outside the user's temp dir."""
    resp = await guest_client.get(f"/api/temp/{attack}")
    assert resp.status_code == 404
