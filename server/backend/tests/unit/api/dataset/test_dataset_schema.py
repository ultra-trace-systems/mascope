"""
Unit tests for Dataset Pydantic models.
Tests schema validation for dataset-related models.
"""

import pytest
from pydantic import ValidationError

from mascope_backend.api.models.dataset.dataset_pydantic_model import (
    DatasetCreate,
    DatasetRead,
    DatasetUpdate,
    GetDatasetsQueryParams,
)


def test_dataset_create_valid(dataset_data, dataset_create_model):
    """Test creating a dataset with valid data."""
    # Test with fixture data
    assert dataset_create_model.dataset_name == dataset_data["dataset_name"]
    assert (
        dataset_create_model.dataset_description == dataset_data["dataset_description"]
    )

    # Test with minimal required data
    dataset = DatasetCreate(dataset_name="Test Dataset")
    assert dataset.dataset_name == "Test Dataset"
    assert dataset.dataset_description is None

    # Test with all fields
    dataset = DatasetCreate(
        dataset_name="Test Dataset",
        dataset_description="This is a test dataset",
    )
    assert dataset.dataset_name == "Test Dataset"
    assert dataset.dataset_description == "This is a test dataset"


def test_dataset_create_invalid():
    """Test validation errors when creating a dataset with invalid data."""
    # Test missing required field
    with pytest.raises(ValidationError):
        DatasetCreate()

    # Test empty name
    with pytest.raises(ValidationError):
        DatasetCreate(dataset_name="")


def test_dataset_update_valid():
    """Test updating a dataset with valid data."""
    # Test empty update (no fields required)
    dataset = DatasetUpdate()
    assert dataset.dataset_name is None
    assert dataset.dataset_description is None

    # Test updating only name
    dataset = DatasetUpdate(dataset_name="Updated Dataset")
    assert dataset.dataset_name == "Updated Dataset"
    assert dataset.dataset_description is None

    # Test updating only description
    dataset = DatasetUpdate(dataset_description="Updated description")
    assert dataset.dataset_name is None
    assert dataset.dataset_description == "Updated description"


def test_query_params_defaults():
    """Test default values for query parameters."""
    params = GetDatasetsQueryParams()
    assert params.sort == "dataset_utc_created"
    assert params.order == "asc"
    assert params.page is None
    assert params.limit is None


def test_query_params_custom():
    """Test setting custom values for query parameters."""
    params = GetDatasetsQueryParams(sort="dataset_name", order="desc", page=2, limit=50)
    assert params.sort == "dataset_name"
    assert params.order == "desc"
    assert params.page == 2
    assert params.limit == 50


def test_descriptive_metadata_is_optional_on_create_and_update():
    """Metadata a user does not have never blocks a create."""
    for model in (DatasetCreate(dataset_name="Bare"), DatasetUpdate()):
        assert model.license is None
        assert model.authors is None
        assert model.keywords is None
        assert model.related_identifiers is None
        assert model.funding is None
    # An update sends only what it sets, so a field left out keeps its value
    assert DatasetUpdate(license=None).model_dump(exclude_unset=True) == {
        "license": None
    }


def test_descriptive_metadata_is_stripped_and_blank_optional_parts_are_absent():
    dataset = DatasetUpdate(
        license=" CC-BY-4.0 ",
        authors=[
            {"name": " Ann ", "orcid": " 0000-0002-1825-009X ", "affiliation": " "}
        ],
        keywords=[" aerosol "],
        related_identifiers=[
            {"identifier": " 10.1234/x ", "relation": " Cites ", "identifier_type": ""}
        ],
        funding=[{"funder": " Funder A ", "award": "  "}],
    )
    assert dataset.model_dump(exclude_unset=True) == {
        "license": "CC-BY-4.0",
        "authors": [
            {"name": "Ann", "orcid": "0000-0002-1825-009X", "affiliation": None}
        ],
        "keywords": ["aerosol"],
        "related_identifiers": [
            {"identifier": "10.1234/x", "relation": "Cites", "identifier_type": None}
        ],
        "funding": [{"funder": "Funder A", "award": None}],
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"license": " "},
        {"license": "x" * 65},
        {"authors": [{"name": " "}]},
        {"authors": [{"name": "Ann", "orcid": "0000-0002-1825-009"}]},
        {"authors": [{"name": "Ann", "orcid": "0000-0002-1825-009x"}]},
        {
            "authors": [
                {"name": "Ann", "orcid": "https://orcid.org/0000-0002-1825-0097"}
            ]
        },
        {"keywords": [""]},
        {"related_identifiers": [{"identifier": " ", "relation": "Cites"}]},
        {"related_identifiers": [{"identifier": "10.1234/x", "relation": " "}]},
        {"funding": [{"funder": " "}]},
    ],
)
def test_malformed_descriptive_metadata_is_refused(metadata):
    with pytest.raises(ValidationError):
        DatasetCreate(dataset_name="Described", **metadata)
    with pytest.raises(ValidationError):
        DatasetUpdate(**metadata)


def test_a_stored_row_reads_back_as_stored_even_if_the_rules_refuse_it():
    """The read model spells the fields only: a row written outside the API,
    or under rules that have since tightened, is listed rather than failing
    the listing."""
    stored = {
        "dataset_id": "ds-1",
        "workspace_id": "ws-1",
        "dataset_name": "Stored",
        "locked": 0,
        "dataset_utc_created": "2026-01-01T00:00:00Z",
        "license": " ",
        "authors": [{"name": "", "orcid": "not-an-orcid", "affiliation": " "}],
        "keywords": [""],
        "related_identifiers": [{"identifier": "x", "relation": ""}],
        "funding": [{"funder": "", "award": None}],
    }
    dataset = DatasetRead.model_validate(stored).model_dump()
    assert dataset["license"] == " "
    assert dataset["authors"] == stored["authors"]
    assert dataset["keywords"] == [""]
    assert dataset["related_identifiers"] == [
        {"identifier": "x", "relation": "", "identifier_type": None}
    ]
