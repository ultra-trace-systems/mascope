"""
Unit tests for the dataset service functions.
Tests the logic in the dataset controllers.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from test_utils import gen_test_id

import mascope_backend.api.controllers.dataset.dataset_controller as dataset_service
from mascope_backend.api.lib.exceptions.api_exceptions import ApiException
from mascope_backend.api.models.dataset.dataset_pydantic_model import (
    DatasetCreate,
    DatasetUpdate,
)
from mascope_backend.db import Dataset, Workspace


@pytest.mark.asyncio
async def test_get_datasets(test_datasets: list):
    """Test retrieving all datasets with default parameters.

    This test verifies:
    1. The basic response structure (message, results, data)
    2. That the correct number of datasets are returned
    3. That the returned data includes our test datasets
    4. The default sorting order (by dataset_utc_created, ascending)

    :param test_datasets: Pre-populated dataset fixtures
    :type test_datasets: list
    """
    # Execute the controller function with default parameters
    result = await dataset_service.get_datasets()

    # 1. Verify response structure
    assert isinstance(result, dict)
    assert "message" in result
    assert "results" in result
    assert "data" in result
    assert result["message"] == "Datasets retrieved successfully"

    # 2. Verify dataset count
    assert result["results"] == len(test_datasets)
    assert len(result["data"]) == len(test_datasets)

    # 3. Verify our test datasets are included in results
    dataset_ids = {w["dataset_id"] for w in result["data"]}
    for dataset in test_datasets:
        assert dataset.dataset_id in dataset_ids

    # 4. Verify response data structure matches DatasetRead model
    first_dataset = result["data"][0]
    expected_fields = {
        "dataset_id",
        "dataset_name",
        "dataset_description",
        "dataset_utc_created",
        "dataset_utc_modified",
    }
    assert all(field in first_dataset for field in expected_fields)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sort_field,order,expected_reversed",
    [
        ("dataset_name", "asc", False),
        ("dataset_name", "desc", True),
        ("dataset_utc_created", "asc", False),
        ("dataset_utc_created", "desc", True),
    ],
)
async def test_get_datasets_sorting(
    test_datasets, sort_field, order, expected_reversed
):
    """Test dataset retrieval with different sorting parameters.

    :param test_datasets: Pre-populated dataset fixtures
    :param sort_field: Field to sort by ('dataset_name' or 'dataset_utc_created')
    :param order: Sort direction ('asc' or 'desc')
    :param expected_reversed: Whether to expect reversed order
    """
    # Execute the controller function with the specified sort parameters
    result = await dataset_service.get_datasets(sort=sort_field, order=order)

    # Extract the field we're sorting by from the results
    if sort_field == "dataset_name":
        actual_values = [w["dataset_name"] for w in result["data"]]
        expected_values = sorted(actual_values.copy(), reverse=expected_reversed)
        assert actual_values == expected_values
    elif sort_field == "dataset_utc_created":
        # For datetime fields, verify they're in the right order
        dates = [w["dataset_utc_created"] for w in result["data"]]

        # Convert string dates to datetime objects if needed
        if dates and isinstance(dates[0], str):
            dates = [datetime.fromisoformat(d.replace("Z", "+00:00")) for d in dates]

        # Verify the dates are in the expected order
        assert dates == sorted(dates, reverse=expected_reversed)

    # Additional verification for the response
    assert result["message"] == "Datasets retrieved successfully"
    assert len(result["data"]) == len(test_datasets)


def _calculate_expected_count(total_count, limit, page):
    """Helper function to calculate expected count for a given page and limit."""
    if page * limit >= total_count:
        return 0  # No items on this page
    return min(limit, total_count - (page * limit))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page,limit",
    [
        (0, 10),  # Default case, should return all datasets if <= 10
        (0, 1),  # First page with limit 1
        (1, 1),  # Second page with limit 1
        (0, 5),  # First page with limit 5
        (100, 10),  # Page beyond available data
    ],
)
async def test_get_datasets_pagination(test_datasets, page, limit):
    """Test dataset retrieval with different pagination parameters.

    :param test_datasets: Pre-populated dataset fixtures
    :param page: Page number (0-indexed)
    :param limit: Number of items per page
    """
    # Get total count first for verification
    total_result = await dataset_service.get_datasets()
    total_count = total_result["results"]

    # Calculate expected count
    expected_items = _calculate_expected_count(total_count, limit, page)

    # Execute with pagination parameters
    result = await dataset_service.get_datasets(page=page, limit=limit)

    # Verify result count
    assert len(result["data"]) == expected_items

    # Verify total is always accurate regardless of pagination
    assert result["results"] == total_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dataset_id,should_exist",
    [
        ("unit-test-1", True),  # exist in test_datasets
        ("unit-test-2", True),  # exist in test_datasets
        ("nonexistent-id", False),  # not exist
        (f"{'a' * 100}", False),  # long id
        ("", False),  # empty string
    ],
)
async def test_get_dataset_existence(test_datasets, dataset_id, should_exist):
    """Test retrieving datasets that do and don't exist.

    :param test_datasets: Pre-populated dataset fixtures
    :param dataset_id: ID to test with
    :param should_exist: Whether the dataset should exist or not
    """
    if should_exist:
        # Positive case - dataset should exist
        result = await dataset_service.get_dataset(dataset_id)

        # Verify response structure
        assert isinstance(result, dict)
        assert "message" in result
        assert "data" in result

        # Verify dataset data
        dataset_data = result["data"]
        assert dataset_data["dataset_id"] == dataset_id
        assert (
            f"Dataset '{dataset_data['dataset_name']}' retrieved successfully"
            in result["message"]
        )

        # Verify all expected fields are present
        expected_fields = {
            "dataset_id",
            "dataset_name",
            "dataset_description",
            "dataset_utc_created",
            "dataset_utc_modified",
        }
        assert all(field in dataset_data for field in expected_fields)
    else:
        # Negative case - dataset should not exist
        with pytest.raises(ApiException) as exc_info:
            await dataset_service.get_dataset(dataset_id)
        assert (
            f"Dataset with ID '{dataset_id}' not found" in exc_info.value.user_message
        )
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_dataset(
    dataset_create_model, mock_emit_dataset, async_session_factory, unit_test_workspace
):
    """Test creating a new dataset.

    This test verifies:
    1. A new dataset can be created with valid data
    2. The response structure is correct
    3. The dataset is actually saved to the database
    4. Socket.IO events are emitted properly

    :param dataset_create_model: Sample dataset creation data
    :param mock_emit_dataset: Mocked Socket.IO for event verification
    :param async_session_factory: Factory for creating database sessions
    :param unit_test_workspace: Workspace fixture
    """
    # Execute the controller function
    result = await dataset_service.create_dataset(
        workspace_id=unit_test_workspace.workspace_id,
        dataset=dataset_create_model,
        independent_transaction=True,
    )

    # Verify response structure
    assert isinstance(result, dict)
    assert "message" in result
    assert "data" in result
    assert (
        f"Dataset '{dataset_create_model.dataset_name}' created successfully"
        in result["message"]
    )

    # Verify dataset data in response
    dataset_data = result["data"]
    assert dataset_data["dataset_name"] == dataset_create_model.dataset_name
    assert (
        dataset_data["dataset_description"] == dataset_create_model.dataset_description
    )
    assert "dataset_id" in dataset_data
    assert "dataset_utc_created" in dataset_data

    # Verify dataset exists in database
    async with async_session_factory() as session:
        db_dataset = await session.get(Dataset, dataset_data["dataset_id"])
        assert db_dataset is not None
        assert db_dataset.dataset_name == dataset_create_model.dataset_name
        assert (
            db_dataset.dataset_description == dataset_create_model.dataset_description
        )

    # Verify emit_record_created was called
    mock_emit_dataset.created.assert_called_once()
    call_args = mock_emit_dataset.created.call_args
    assert call_args.kwargs["record_type"] == "dataset"
    assert call_args.kwargs["record_id"] == dataset_data["dataset_id"]
    assert call_args.kwargs.get("room") == unit_test_workspace.workspace_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dataset_id,should_exist",
    [
        ("unit-test-1", True),  # existing dataset
        ("nonexistent-id", False),  # non-existent dataset
    ],
)
async def test_update_dataset(
    test_datasets,
    dataset_update_model,
    dataset_id,
    should_exist,
    mock_emit_dataset,
    async_session_factory,
    unit_test_workspace,
):
    """Test updating an existing dataset.

    This test verifies:
    1. An existing dataset can be updated with valid data
    2. Appropriate error is raised for non-existent datasets
    3. The response structure is correct
    4. Socket.IO events are emitted properly
    5. Database is updated correctly

    :param test_datasets: Pre-populated dataset fixtures
    :param dataset_update_model: Sample dataset update data
    :param dataset_id: ID of the dataset to update
    :param should_exist: Whether the dataset should exist
    :param mock_emit_dataset: Mocked Socket.IO for event verification
    :param async_session_factory: Factory for creating database sessions
    """
    if should_exist:
        # Positive case - dataset should exist and be updated
        result = await dataset_service.update_dataset(
            dataset_id=dataset_id,
            dataset_update=dataset_update_model,
            independent_transaction=True,
        )

        # Verify response structure
        assert isinstance(result, dict)
        assert "message" in result
        assert "data" in result
        assert "updated successfully" in result["message"]

        # Verify dataset data in response
        dataset_data = result["data"]
        assert dataset_data["dataset_id"] == dataset_id
        assert dataset_data["dataset_name"] == dataset_update_model.dataset_name
        assert (
            dataset_data["dataset_description"]
            == dataset_update_model.dataset_description
        )
        assert "dataset_utc_modified" in dataset_data

        # Verify emit_record_updated was called
        mock_emit_dataset.updated.assert_called_once()
        call_args = mock_emit_dataset.updated.call_args
        assert call_args.kwargs["record_type"] == "dataset"
        assert call_args.kwargs["record_id"] == dataset_id
        assert call_args.kwargs.get("room") == unit_test_workspace.workspace_id

        # Verify dataset was actually updated in the database
        async with async_session_factory() as session:
            updated_dataset = await session.get(Dataset, dataset_id)
            assert updated_dataset is not None
            assert updated_dataset.dataset_name == dataset_update_model.dataset_name
            assert (
                updated_dataset.dataset_description
                == dataset_update_model.dataset_description
            )
    else:
        # Negative case - dataset should not exist
        with pytest.raises(ApiException) as exc_info:
            await dataset_service.update_dataset(
                dataset_id, dataset_update_model, independent_transaction=True
            )

        assert (
            f"Dataset with ID '{dataset_id}' not found" in exc_info.value.user_message
        )
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "update_field,update_value",
    [
        ("dataset_name", "Only Name Updated"),
        ("dataset_description", "Only Description Updated"),
    ],
)
async def test_partial_update_dataset(
    test_datasets, update_field, update_value, async_session_factory
):
    """Test partial updates to a dataset.

    This test verifies:
    1. Partial updates (only name or only description) work correctly
    2. Original values are preserved for fields not included in the update
    3. Database state is correctly updated

    :param test_datasets: Pre-populated dataset fixtures
    :param update_field: Field to update ('dataset_name' or 'dataset_description')
    :param update_value: Value to set for the updated field
    :param async_session_factory: Factory for creating database sessions
    """
    # Get a fresh copy of the dataset to avoid state conflicts between test runs
    dataset_id = test_datasets[0].dataset_id

    async with async_session_factory() as session:
        dataset = await session.get(Dataset, dataset_id)
        original_name = dataset.dataset_name
        original_description = dataset.dataset_description

    # Create a partial update model with just one field
    partial_update = {update_field: update_value}
    update_model = DatasetUpdate(**partial_update)

    # Execute update
    result = await dataset_service.update_dataset(
        dataset_id, update_model, independent_transaction=True
    )

    # Verify response
    assert "updated successfully" in result["message"]

    # Check database state after update
    async with async_session_factory() as session:
        updated_dataset = await session.get(Dataset, dataset_id)

        if update_field == "dataset_name":
            # Name should be updated, description preserved
            assert updated_dataset.dataset_name == update_value
            assert updated_dataset.dataset_description == original_description
            # Verify response matches database
            assert result["data"]["dataset_name"] == update_value
            assert result["data"]["dataset_description"] == original_description
        else:
            # Description should be updated, name preserved
            assert updated_dataset.dataset_description == update_value
            assert updated_dataset.dataset_name == original_name
            # Verify response matches database
            assert result["data"]["dataset_description"] == update_value
            assert result["data"]["dataset_name"] == original_name


@pytest.mark.asyncio
async def test_update_both_fields_dataset(
    test_datasets, mock_emit_dataset, async_session_factory
):
    """Test updating both name and description simultaneously.

    This test verifies:
    1. Multiple fields can be updated in a single operation
    2. All specified fields are updated correctly
    3. Database state reflects all changes

    :param test_datasets: Pre-populated dataset fixtures
    :param mock_emit_dataset: Mocked Socket.IO for event verification
    :param async_session_factory: Factory for creating database sessions
    """
    dataset_id = test_datasets[0].dataset_id
    update_data = {
        "dataset_name": "Both Fields Updated",
        "dataset_description": "Updated Together",
    }

    update_model = DatasetUpdate(**update_data)
    result = await dataset_service.update_dataset(
        dataset_id, update_model, independent_transaction=True
    )

    # Verify response
    assert "updated successfully" in result["message"]
    assert result["data"]["dataset_name"] == update_data["dataset_name"]
    assert result["data"]["dataset_description"] == update_data["dataset_description"]

    # Verify emit_record_updated was called
    mock_emit_dataset.updated.assert_called_once()

    # Verify database state
    async with async_session_factory() as session:
        updated_dataset = await session.get(Dataset, dataset_id)
        assert updated_dataset.dataset_name == update_data["dataset_name"]
        assert updated_dataset.dataset_description == update_data["dataset_description"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dataset_id,should_exist",
    [
        ("unit-test-2", True),  # existing dataset
        ("nonexistent-id", False),  # non-existent dataset
    ],
)
async def test_delete_dataset(
    test_datasets,
    dataset_id,
    should_exist,
    mock_emit_dataset,
    async_session_factory,
    unit_test_workspace,
):
    """Test deleting a dataset.

    This test verifies:
    1. An existing dataset can be deleted
    2. Appropriate error is raised for non-existent datasets
    3. The response structure is correct
    4. Socket.IO events are emitted properly
    5. The dataset is actually removed from the database

    :param test_datasets: Pre-populated dataset fixtures
    :param dataset_id: ID of the dataset to delete
    :param should_exist: Whether the dataset should exist
    :param mock_emit_dataset: Mocked Socket.IO for event verification
    :param async_session_factory: Factory for creating database sessions
    """
    if should_exist:
        # Get the dataset name before deletion for verification
        async with async_session_factory() as session:
            dataset = await session.get(Dataset, dataset_id)
            dataset_name = dataset.dataset_name

        # Positive case - dataset should exist and be deleted
        result = await dataset_service.delete_dataset(
            dataset_id, independent_transaction=True
        )

        # Verify response structure
        assert isinstance(result, dict)
        assert "message" in result
        assert f"Dataset '{dataset_name}' deleted successfully" in result["message"]

        # Verify emit_record_deleted was called
        mock_emit_dataset.deleted.assert_called_once()
        call_args = mock_emit_dataset.deleted.call_args
        assert call_args.kwargs["record_type"] == "dataset"
        assert call_args.kwargs["record_id"] == dataset_id
        assert call_args.kwargs.get("room") == unit_test_workspace.workspace_id

        # Verify dataset was actually deleted from the database
        async with async_session_factory() as session:
            deleted_dataset = await session.get(Dataset, dataset_id)
            assert deleted_dataset is None
    else:
        # Negative case - dataset should not exist
        with pytest.raises(ApiException) as exc_info:
            await dataset_service.delete_dataset(
                dataset_id, independent_transaction=True
            )

        assert (
            f"Dataset with ID '{dataset_id}' not found" in exc_info.value.user_message
        )
        assert exc_info.value.status_code == 404


# ============= Duplicate name refusal =============
#
# These live at the end of the module on purpose: `test_get_datasets` and
# `test_get_datasets_sorting` above assert a global dataset count, so any
# test that adds rows has to run after them.


@pytest_asyncio.fixture
async def second_unit_test_workspace(async_session_factory):
    """A second workspace, so per-workspace scoping can be exercised.

    Function-scoped and randomly named: workspace names are themselves
    case-insensitively unique (`ix_workspace_name_ci`).

    :param async_session_factory: Factory for creating database sessions
    :return: The created workspace
    :rtype: Workspace
    """
    async with async_session_factory() as session:
        workspace = Workspace(
            workspace_id=gen_test_id(),
            workspace_name=f"Second Unit Test Workspace {gen_test_id(8)}",
            # Spelled out rather than left to the server default: move_dataset
            # refuses a non-active or system target before it gets as far as
            # the name check.
            workspace_status="active",
            is_system=False,
            workspace_utc_created=datetime.now(timezone.utc),
        )
        session.add(workspace)
        await session.commit()
        await session.refresh(workspace)
        return workspace


@pytest.mark.asyncio
async def test_create_dataset_rejects_duplicate_name(
    mock_emit_dataset, unit_test_workspace
):
    """Creating a second dataset with a taken name raises a 409."""
    name = f"dup-{gen_test_id(8)}"

    await dataset_service.create_dataset(
        workspace_id=unit_test_workspace.workspace_id,
        dataset=DatasetCreate(dataset_name=name),
        independent_transaction=True,
    )

    with pytest.raises(ApiException) as exc_info:
        await dataset_service.create_dataset(
            workspace_id=unit_test_workspace.workspace_id,
            dataset=DatasetCreate(dataset_name=name),
            independent_transaction=True,
        )

    assert exc_info.value.status_code == 409
    assert "already exists" in exc_info.value.user_message
    assert name in exc_info.value.user_message


@pytest.mark.asyncio
async def test_create_dataset_duplicate_name_is_case_insensitive(
    mock_emit_dataset, unit_test_workspace
):
    """Two names differing only in case are the same name."""
    name = f"dup-case-{gen_test_id(8)}"

    await dataset_service.create_dataset(
        workspace_id=unit_test_workspace.workspace_id,
        dataset=DatasetCreate(dataset_name=name),
        independent_transaction=True,
    )

    with pytest.raises(ApiException) as exc_info:
        await dataset_service.create_dataset(
            workspace_id=unit_test_workspace.workspace_id,
            dataset=DatasetCreate(dataset_name=name.upper()),
            independent_transaction=True,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_dataset_allows_same_name_in_another_workspace(
    mock_emit_dataset, unit_test_workspace, second_unit_test_workspace
):
    """The name only has to be unique within one workspace."""
    name = f"shared-{gen_test_id(8)}"

    for workspace in (unit_test_workspace, second_unit_test_workspace):
        result = await dataset_service.create_dataset(
            workspace_id=workspace.workspace_id,
            dataset=DatasetCreate(dataset_name=name),
            independent_transaction=True,
        )
        assert result["data"]["dataset_name"] == name


@pytest.mark.asyncio
async def test_update_dataset_rejects_duplicate_name(
    mock_emit_dataset, unit_test_workspace
):
    """Renaming a dataset onto a sibling's name raises a 409."""
    taken_name = f"taken-{gen_test_id(8)}"
    await dataset_service.create_dataset(
        workspace_id=unit_test_workspace.workspace_id,
        dataset=DatasetCreate(dataset_name=taken_name),
        independent_transaction=True,
    )
    other = await dataset_service.create_dataset(
        workspace_id=unit_test_workspace.workspace_id,
        dataset=DatasetCreate(dataset_name=f"other-{gen_test_id(8)}"),
        independent_transaction=True,
    )

    with pytest.raises(ApiException) as exc_info:
        await dataset_service.update_dataset(
            other["data"]["dataset_id"],
            DatasetUpdate(dataset_name=taken_name),
            independent_transaction=True,
        )

    assert exc_info.value.status_code == 409
    assert "already exists" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_update_dataset_accepts_its_own_name(
    mock_emit_dataset, unit_test_workspace
):
    """Re-sending the dataset's own name is not a conflict."""
    name = f"self-{gen_test_id(8)}"
    created = await dataset_service.create_dataset(
        workspace_id=unit_test_workspace.workspace_id,
        dataset=DatasetCreate(dataset_name=name),
        independent_transaction=True,
    )

    result = await dataset_service.update_dataset(
        created["data"]["dataset_id"],
        DatasetUpdate(dataset_name=name, dataset_description="Renamed to itself"),
        independent_transaction=True,
    )

    assert result["data"]["dataset_name"] == name
    assert result["data"]["dataset_description"] == "Renamed to itself"


@pytest.mark.asyncio
async def test_move_dataset_rejects_duplicate_name_in_target(
    mock_emit_dataset, unit_test_workspace, second_unit_test_workspace
):
    """Moving into a workspace that already has the name raises a 409.

    `independent_transaction=False` keeps the move off the socket layer:
    `emit_record_reload` is not one of the functions `mock_emit_dataset`
    patches.
    """
    name = f"move-{gen_test_id(8)}"
    source = await dataset_service.create_dataset(
        workspace_id=unit_test_workspace.workspace_id,
        dataset=DatasetCreate(dataset_name=name),
        independent_transaction=True,
    )
    await dataset_service.create_dataset(
        workspace_id=second_unit_test_workspace.workspace_id,
        dataset=DatasetCreate(dataset_name=name),
        independent_transaction=True,
    )

    with pytest.raises(ApiException) as exc_info:
        await dataset_service.move_dataset(
            dataset_id=source["data"]["dataset_id"],
            source_workspace_id=unit_test_workspace.workspace_id,
            target_workspace_id=second_unit_test_workspace.workspace_id,
        )

    assert exc_info.value.status_code == 409
    assert "already exists" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_update_dataset_description_when_the_name_is_already_shared(
    mock_emit_dataset, unit_test_workspace, async_session_factory
):
    """A description-only edit saves even where two datasets share a name.

    Nothing has ever stopped a workspace from holding a duplicate pair -
    there is no unique index, and ACQUISITION rows are inserted without the
    check - so the pair is written straight to the database here. The edit
    dialog always submits the name field, so an unchanged name has to skip
    the check rather than refuse the edit over a field nobody touched.

    :param mock_emit_dataset: Patches the controller's emit_record_* calls
    :param unit_test_workspace: The workspace the pair is created in
    :param async_session_factory: Factory for creating database sessions
    """
    name = f"twin-{gen_test_id(8)}"
    dataset_ids = [gen_test_id(), gen_test_id()]
    async with async_session_factory() as session:
        for dataset_id in dataset_ids:
            session.add(
                Dataset(
                    dataset_id=dataset_id,
                    workspace_id=unit_test_workspace.workspace_id,
                    dataset_name=name,
                    dataset_type="ANALYSIS",
                    dataset_utc_created=datetime.now(timezone.utc),
                )
            )
        await session.commit()

    result = await dataset_service.update_dataset(
        dataset_ids[0],
        DatasetUpdate(dataset_name=name, dataset_description="Only the description"),
        independent_transaction=True,
    )

    assert result["data"]["dataset_name"] == name
    assert result["data"]["dataset_description"] == "Only the description"


@pytest.mark.asyncio
async def test_create_dataset_rejects_a_name_a_padded_row_already_holds(
    mock_emit_dataset, unit_test_workspace, async_session_factory
):
    """A stored name with surrounding padding still counts as taken.

    Names are only stripped on the way in from this change onwards, so an
    older row can keep its padding while `DatasetRead` renders it trimmed.
    Accepting the trimmed name would put two entries in the workspace list
    that read identically - the very thing the check exists to prevent.

    :param mock_emit_dataset: Patches the controller's emit_record_* calls
    :param unit_test_workspace: The workspace the padded row is created in
    :param async_session_factory: Factory for creating database sessions
    """
    name = f"padded-{gen_test_id(8)}"
    async with async_session_factory() as session:
        session.add(
            Dataset(
                dataset_id=gen_test_id(),
                workspace_id=unit_test_workspace.workspace_id,
                dataset_name=f"  {name}  ",
                dataset_type="ANALYSIS",
                dataset_utc_created=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    with pytest.raises(ApiException) as exc_info:
        await dataset_service.create_dataset(
            workspace_id=unit_test_workspace.workspace_id,
            dataset=DatasetCreate(dataset_name=name),
            independent_transaction=True,
        )

    assert exc_info.value.status_code == 409
    assert "already exists" in exc_info.value.user_message
