"""
Integration tests for the Dataset API endpoints.
Tests CRUD operations through the API to verify that endpoints work correctly.
"""

import asyncio

import pytest
from fastapi import status


# ============= Role-Based Access Control Tests =============


@pytest.mark.asyncio
async def test_rbac_guest_permissions(
    test_workspace, guest_client, editor_client, dataset_create_data
):
    """Test RBAC for guest users.

    Verifies guests can view datasets but cannot create, update, or delete.
    """
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert create_response.status_code == 201
    dataset_id = create_response.json()["data"]["dataset_id"]

    assert (
        await guest_client.get(f"/api/workspaces/{test_workspace}/datasets")
    ).status_code == 200
    assert (
        await guest_client.get(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
        )
    ).status_code == 200

    assert (
        await guest_client.post(
            f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
        )
    ).status_code == status.HTTP_403_FORBIDDEN

    assert (
        await guest_client.patch(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}",
            json={"dataset_name": "Guest Update Attempt"},
        )
    ).status_code == status.HTTP_403_FORBIDDEN

    assert (
        await guest_client.delete(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
        )
    ).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_rbac_editor_permissions(
    test_workspace, editor_client, dataset_create_data, dataset_update_data
):
    """Test RBAC for editor users.

    Verifies editors can perform all CRUD operations on datasets.
    """
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert create_response.status_code == 201
    dataset_id = create_response.json()["data"]["dataset_id"]

    assert (
        await editor_client.get(f"/api/workspaces/{test_workspace}/datasets")
    ).status_code == 200
    assert (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
        )
    ).status_code == 200

    assert (
        await editor_client.patch(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}",
            json=dataset_update_data,
        )
    ).status_code == 200

    assert (
        await editor_client.delete(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
        )
    ).status_code == 200


# ============= Create Operations =============


@pytest.mark.asyncio
async def test_create_dataset(test_workspace, editor_client, dataset_create_data):
    """Test creating a dataset with valid data."""
    response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )

    assert response.status_code == 201
    data = response.json()
    assert all(k in data for k in ["data", "message"])
    assert "dataset_id" in data["data"]

    dataset = data["data"]
    assert dataset["dataset_name"] == dataset_create_data["dataset_name"]
    assert dataset["dataset_description"] == dataset_create_data["dataset_description"]
    assert dataset["dataset_utc_created"] is not None
    assert dataset["dataset_utc_modified"] is None


@pytest.mark.asyncio
async def test_create_dataset_validation(test_workspace, editor_client):
    """Test validation during dataset creation."""
    assert (
        await editor_client.post(f"/api/workspaces/{test_workspace}/datasets", json={})
    ).status_code == 422
    assert (
        await editor_client.post(
            f"/api/workspaces/{test_workspace}/datasets", json={"dataset_name": 123}
        )
    ).status_code == 422
    assert (
        await editor_client.post(
            f"/api/workspaces/{test_workspace}/datasets", json={"dataset_name": ""}
        )
    ).status_code == 422


# ============= Read Operations =============


@pytest.mark.asyncio
async def test_get_datasets(
    test_workspace, guest_client, editor_client, dataset_create_data
):
    """Test retrieving dataset list."""
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    dataset_id = create_response.json()["data"]["dataset_id"]

    response = await guest_client.get(f"/api/workspaces/{test_workspace}/datasets")
    assert response.status_code == 200

    data = response.json()
    assert all(k in data for k in ["data", "results", "message"])

    dataset_found = any(
        w["dataset_id"] == dataset_id
        and w["dataset_name"] == dataset_create_data["dataset_name"]
        and w["dataset_description"] == dataset_create_data["dataset_description"]
        for w in data["data"]
    )
    assert dataset_found, f"Dataset with ID {dataset_id} not found in results"


@pytest.mark.asyncio
async def test_get_single_dataset(test_workspace, editor_client, dataset_create_data):
    """Test retrieving a single dataset by ID."""
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    dataset_id = create_response.json()["data"]["dataset_id"]

    response = await editor_client.get(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
    )
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["dataset_id"] == dataset_id
    assert data["dataset_name"] == dataset_create_data["dataset_name"]
    assert data["dataset_description"] == dataset_create_data["dataset_description"]
    assert data["dataset_utc_created"] is not None
    assert data["dataset_utc_modified"] is None


@pytest.mark.asyncio
async def test_get_datasets_pagination(
    test_workspace, editor_client, dataset_create_data
):
    """Test pagination for dataset list endpoint."""
    for i in range(5):
        payload = {**dataset_create_data, "dataset_name": f"Dataset {i + 1}"}
        await editor_client.post(
            f"/api/workspaces/{test_workspace}/datasets", json=payload
        )

    first_page = (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets?page=0&limit=2"
        )
    ).json()
    assert len(first_page["data"]) == 2

    second_page = (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets?page=1&limit=2"
        )
    ).json()
    assert len(second_page["data"]) <= 2

    if second_page["data"]:
        first_ids = {w["dataset_id"] for w in first_page["data"]}
        second_ids = {w["dataset_id"] for w in second_page["data"]}
        assert not (first_ids & second_ids), "Pages should not overlap"


@pytest.mark.asyncio
async def test_get_datasets_sorting(test_workspace, editor_client, dataset_create_data):
    """Test sorting for dataset list endpoint."""
    names = ["C Dataset", "A Dataset", "B Dataset"]
    for name in names:
        payload = {**dataset_create_data, "dataset_name": name}
        await editor_client.post(
            f"/api/workspaces/{test_workspace}/datasets", json=payload
        )

    asc_response = (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets?sort=dataset_name&order=asc"
        )
    ).json()
    asc_names = [
        w["dataset_name"] for w in asc_response["data"] if w["dataset_name"] in names
    ]
    for i in range(len(asc_names) - 1):
        assert asc_names[i] <= asc_names[i + 1]

    desc_response = (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets?sort=dataset_name&order=desc"
        )
    ).json()
    desc_names = [
        w["dataset_name"] for w in desc_response["data"] if w["dataset_name"] in names
    ]
    for i in range(len(desc_names) - 1):
        assert desc_names[i] >= desc_names[i + 1]


# ============= Update Operations =============


@pytest.mark.asyncio
async def test_update_dataset(
    test_workspace, editor_client, dataset_create_data, dataset_update_data
):
    """Test updating a dataset."""
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    dataset = create_response.json()["data"]
    dataset_id = dataset["dataset_id"]
    creation_time = dataset["dataset_utc_created"]

    await asyncio.sleep(1)

    update_response = await editor_client.patch(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}",
        json=dataset_update_data,
    )
    assert update_response.status_code == 200

    updated = update_response.json()["data"]
    assert updated["dataset_id"] == dataset_id
    assert updated["dataset_name"] == dataset_update_data["dataset_name"]
    assert updated["dataset_description"] == dataset_update_data["dataset_description"]
    assert updated["dataset_utc_created"] == creation_time
    assert updated["dataset_utc_modified"] is not None

    get_response = await editor_client.get(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
    )
    get_data = get_response.json()["data"]
    assert get_data["dataset_name"] == dataset_update_data["dataset_name"]
    assert get_data["dataset_description"] == dataset_update_data["dataset_description"]


@pytest.mark.asyncio
async def test_update_dataset_partial(
    test_workspace, editor_client, dataset_create_data
):
    """Test partial updates to a dataset."""
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    dataset_id = create_response.json()["data"]["dataset_id"]

    name_response = await editor_client.patch(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}",
        json={"dataset_name": "Updated Name Only"},
    )
    assert name_response.status_code == 200
    name_data = name_response.json()["data"]
    assert name_data["dataset_name"] == "Updated Name Only"
    assert (
        name_data["dataset_description"] == dataset_create_data["dataset_description"]
    )

    desc_response = await editor_client.patch(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}",
        json={"dataset_description": "Updated description only"},
    )
    assert desc_response.status_code == 200
    desc_data = desc_response.json()["data"]
    assert desc_data["dataset_name"] == "Updated Name Only"
    assert desc_data["dataset_description"] == "Updated description only"


# ============= Delete Operations =============


@pytest.mark.asyncio
async def test_delete_dataset(test_workspace, editor_client, dataset_create_data):
    """Test deleting a dataset."""
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    dataset_id = create_response.json()["data"]["dataset_id"]

    delete_response = await editor_client.delete(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
    )
    assert delete_response.status_code == 200
    assert "message" in delete_response.json()

    get_response = await editor_client.get(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
    )
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_dataset_delete_cascades_to_sample_batches(
    test_workspace, editor_client, dataset_create_data, sample_batch_create_data
):
    """Test that deleting a dataset cascades to delete associated sample batches."""
    create_dataset_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert create_dataset_response.status_code == 201
    dataset_id = create_dataset_response.json()["data"]["dataset_id"]

    sample_batch_data = {**sample_batch_create_data, "dataset_id": dataset_id}
    create_batch_response = await editor_client.post(
        "/api/sample/batches", json=sample_batch_data
    )
    assert create_batch_response.status_code == 201
    sample_batch_id = create_batch_response.json()["data"]["sample_batch_id"]

    get_batch_response = await editor_client.get(
        f"/api/sample/batches/{sample_batch_id}"
    )
    assert get_batch_response.status_code == 200
    assert get_batch_response.json()["data"]["dataset_id"] == dataset_id

    delete_dataset_response = await editor_client.delete(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
    )
    assert delete_dataset_response.status_code == 200

    # After cascade delete, the batch no longer exists. The workspace ACL
    # dependency may return 403 (workspace lookup fails) instead of 404.
    batch_resp = await editor_client.get(f"/api/sample/batches/{sample_batch_id}")
    assert batch_resp.status_code in (403, 404)
    assert (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
        )
    ).status_code == 404


# ============= Error Handling Tests =============


@pytest.mark.asyncio
async def test_nonexistent_dataset_operations(
    test_workspace, editor_client, dataset_update_data
):
    """Test operations on non-existent datasets."""
    nonexistent_id = "nonexistent123"

    assert (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets/{nonexistent_id}"
        )
    ).status_code == 404
    assert (
        await editor_client.patch(
            f"/api/workspaces/{test_workspace}/datasets/{nonexistent_id}",
            json=dataset_update_data,
        )
    ).status_code == 404
    assert (
        await editor_client.delete(
            f"/api/workspaces/{test_workspace}/datasets/{nonexistent_id}"
        )
    ).status_code == 404


# ============= End-to-End Workflow Tests =============


@pytest.mark.asyncio
async def test_dataset_lifecycle(
    test_workspace, editor_client, dataset_create_data, dataset_update_data
):
    """Test complete dataset lifecycle from creation to deletion."""
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert create_response.status_code == 201
    dataset = create_response.json()["data"]
    dataset_id = dataset["dataset_id"]

    assert dataset["dataset_name"] == dataset_create_data["dataset_name"]
    assert dataset["dataset_description"] == dataset_create_data["dataset_description"]
    assert dataset["dataset_utc_created"] is not None
    assert dataset["dataset_utc_modified"] is None

    get_response = await editor_client.get(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
    )
    assert get_response.status_code == 200
    creation_time = get_response.json()["data"]["dataset_utc_created"]

    await asyncio.sleep(1)

    update_response = await editor_client.patch(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}",
        json=dataset_update_data,
    )
    assert update_response.status_code == 200
    update_data = update_response.json()["data"]
    assert update_data["dataset_name"] == dataset_update_data["dataset_name"]
    assert (
        update_data["dataset_description"] == dataset_update_data["dataset_description"]
    )
    assert update_data["dataset_utc_created"] == creation_time
    assert update_data["dataset_utc_modified"] is not None

    assert (
        await editor_client.delete(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
        )
    ).status_code == 200
    assert (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
        )
    ).status_code == 404


# ============= Duplicate Name Tests =============


@pytest.mark.asyncio
async def test_create_dataset_duplicate_name_rejected(
    test_workspace, editor_client, dataset_create_data
):
    """A second dataset with the same name in one workspace is refused."""
    first = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert first.status_code == 201

    second = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert second.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in second.json()["error"]
    assert dataset_create_data["dataset_name"] in second.json()["error"]


@pytest.mark.asyncio
async def test_create_dataset_duplicate_name_is_case_insensitive(
    test_workspace, editor_client, dataset_create_data
):
    """Names differing only in case count as the same name."""
    assert (
        await editor_client.post(
            f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
        )
    ).status_code == 201

    upper = {
        **dataset_create_data,
        "dataset_name": dataset_create_data["dataset_name"].upper(),
    }
    response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=upper
    )
    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_create_dataset_duplicate_name_ignores_surrounding_whitespace(
    test_workspace, editor_client, dataset_create_data
):
    """Padding the name does not buy a second dataset with the same name."""
    assert (
        await editor_client.post(
            f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
        )
    ).status_code == 201

    padded = {
        **dataset_create_data,
        "dataset_name": f"  {dataset_create_data['dataset_name']}  ",
    }
    response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=padded
    )
    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_create_dataset_strips_surrounding_whitespace(
    test_workspace, editor_client, dataset_create_data
):
    """A padded name is stored trimmed."""
    padded = {
        **dataset_create_data,
        "dataset_name": f"  {dataset_create_data['dataset_name']}  ",
    }
    response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=padded
    )
    assert response.status_code == 201
    assert (
        response.json()["data"]["dataset_name"] == (dataset_create_data["dataset_name"])
    )


@pytest.mark.asyncio
async def test_create_dataset_same_name_allowed_in_other_workspace(
    test_workspace, second_test_workspace, editor_client, dataset_create_data
):
    """Uniqueness is per workspace, not global."""
    assert (
        await editor_client.post(
            f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
        )
    ).status_code == 201
    assert (
        await editor_client.post(
            f"/api/workspaces/{second_test_workspace}/datasets",
            json=dataset_create_data,
        )
    ).status_code == 201


@pytest.mark.asyncio
async def test_rename_dataset_to_existing_name_rejected(
    test_workspace, editor_client, dataset_create_data
):
    """Renaming onto a sibling's name is refused and leaves the name alone."""
    first = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert first.status_code == 201
    taken_name = first.json()["data"]["dataset_name"]

    second_payload = {
        **dataset_create_data,
        "dataset_name": f"{dataset_create_data['dataset_name']} other",
    }
    second = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=second_payload
    )
    assert second.status_code == 201
    second_id = second.json()["data"]["dataset_id"]

    rename = await editor_client.patch(
        f"/api/workspaces/{test_workspace}/datasets/{second_id}",
        json={"dataset_name": taken_name},
    )
    assert rename.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in rename.json()["error"]

    unchanged = await editor_client.get(
        f"/api/workspaces/{test_workspace}/datasets/{second_id}"
    )
    assert unchanged.json()["data"]["dataset_name"] == second_payload["dataset_name"]


@pytest.mark.asyncio
async def test_update_dataset_keeps_own_name(
    test_workspace, editor_client, dataset_create_data
):
    """Re-sending a dataset's own name is a no-op, not a conflict.

    The edit dialog always submits the name field, so a description-only
    edit would 409 if the dataset were not excluded from the check.
    """
    create_response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert create_response.status_code == 201
    dataset_id = create_response.json()["data"]["dataset_id"]

    response = await editor_client.patch(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}",
        json={
            "dataset_name": dataset_create_data["dataset_name"],
            "dataset_description": "Same name, new description",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["dataset_description"] == (
        "Same name, new description"
    )


@pytest.mark.asyncio
async def test_move_dataset_into_workspace_with_same_name_rejected(
    test_workspace, second_test_workspace, editor_client, dataset_create_data
):
    """A move that would collide in the target workspace is refused."""
    source = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets", json=dataset_create_data
    )
    assert source.status_code == 201
    dataset_id = source.json()["data"]["dataset_id"]

    assert (
        await editor_client.post(
            f"/api/workspaces/{second_test_workspace}/datasets",
            json=dataset_create_data,
        )
    ).status_code == 201

    response = await editor_client.post(
        f"/api/workspaces/{test_workspace}/datasets/{dataset_id}/move",
        json={"target_workspace_id": second_test_workspace},
    )
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in response.json()["error"]

    # The dataset is still where it was
    assert (
        await editor_client.get(
            f"/api/workspaces/{test_workspace}/datasets/{dataset_id}"
        )
    ).status_code == 200
