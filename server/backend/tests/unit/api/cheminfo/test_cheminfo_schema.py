"""
Unit tests for ChemInfo Pydantic models.
"""

import pytest
from pydantic import ValidationError

from mascope_backend.api.new.cheminfo.schema import (
    CheminfoMatchedQueryBody,
    CheminfoQueryBody,
)


def assert_cheminfo_query_model(cheminfo_query_data: dict):
    """Assert the cheminfo query model"""
    cheminfo_query_model = CheminfoQueryBody(**cheminfo_query_data)
    # Test with fixture data
    assert cheminfo_query_model.mz == cheminfo_query_data["mz"]
    # Test all default values
    for key, value in cheminfo_query_data.items():
        # Check if the model value is equal to the query data value
        assert key in cheminfo_query_model.__dict__
        assert getattr(cheminfo_query_model, key) == value


def test_cheminfo_query_valid(cheminfo_query_data):
    """Test making a cheminfo query with valid data."""
    assert_cheminfo_query_model(cheminfo_query_data)


def test_cheminfo_query_invalid():
    """Test validation errors for query with invalid data."""
    # Test missing required field
    with pytest.raises(ValidationError):
        CheminfoQueryBody()
    with pytest.raises(ValidationError):
        CheminfoQueryBody(mz=123.456)

    # Test invalid m/z type
    with pytest.raises(ValidationError):
        CheminfoQueryBody(mz="hundred")


def test_cheminfo_matched_query_valid(
    cheminfo_matched_query_data, cheminfo_matched_query_model
):
    """Test making a cheminfo matched query with valid data."""
    # Test with fixture data
    assert cheminfo_matched_query_model.mz == cheminfo_matched_query_data["mz"]
    # Test all default values
    for key, value in cheminfo_matched_query_data.items():
        # Check if the model value is equal to the query data value
        assert key in cheminfo_matched_query_model.__dict__
        assert getattr(cheminfo_matched_query_model, key) == value


def test_cheminfo_matched_query_invalid():
    """Test validation errors for query with invalid data."""
    # Test missing required field
    with pytest.raises(ValidationError):
        CheminfoMatchedQueryBody()

    # Test invalid m/z type
    with pytest.raises(ValidationError):
        CheminfoMatchedQueryBody(mz="hundred")


def test_isotopologues_defaults_to_the_monoisotopic_line(cheminfo_query_data):
    """A caller that says nothing gets the search it always had."""
    assert CheminfoQueryBody(**cheminfo_query_data).isotopologues is False


def test_isotopologues_can_be_asked_for(cheminfo_matched_query_data):
    body = CheminfoMatchedQueryBody(**cheminfo_matched_query_data, isotopologues=True)
    assert body.isotopologues is True


@pytest.mark.parametrize("value", ["all", "brightest", 0.01])
def test_isotopologues_is_a_yes_or_no(cheminfo_query_data, value):
    with pytest.raises(ValidationError):
        CheminfoQueryBody(**cheminfo_query_data, isotopologues=value)
