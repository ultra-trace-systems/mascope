"""Tests: the collection update schema says its compound fields replace the list.

``update_target_collection`` removes every compound that neither
``target_compound_ids`` nor ``target_compounds_create`` names, so a client that
reads "added" in the schema and sends one new compound empties the rest of the
collection. The create model's fields do add, and keep their wording.
"""

import pytest

from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    TargetCollectionCreate,
    TargetCollectionUpdate,
)
from mascope_backend.app.fast import fast


_COMPOUND_FIELDS = ("target_compounds_create", "target_compound_ids")


@pytest.mark.parametrize("field", _COMPOUND_FIELDS)
def test_update_compound_fields_say_they_replace(field):
    description = TargetCollectionUpdate.model_json_schema()["properties"][field][
        "description"
    ]
    assert "replace" in description


@pytest.mark.parametrize("field", _COMPOUND_FIELDS)
def test_create_compound_fields_do_not_say_replace(field):
    description = TargetCollectionCreate.model_json_schema()["properties"][field][
        "description"
    ]
    assert "replace" not in description


def test_update_operation_description_says_it_replaces():
    operation = fast.openapi()["paths"][
        "/api/target/collections/{target_collection_id}"
    ]["patch"]
    description = " ".join(operation["description"].split())
    assert "replace the collection's compound list" in description
