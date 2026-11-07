"""Tests: the collection update schema says its compound and batch fields replace.

``update_target_collection`` removes every compound that neither
``target_compound_ids`` nor ``target_compounds_create`` names, so a client that
reads "added" in the schema and sends one new compound empties the rest of the
collection. ``sample_batch_ids`` is the same: a batch it does not name is
detached. The create model's fields do add, and keep their wording.
"""

import pytest

from mascope_backend.api.models.target.collections.target_collection_pydantic_model import (
    TargetCollectionCreate,
    TargetCollectionUpdate,
)
from mascope_backend.app.fast import fast


_COMPOUND_FIELDS = ("target_compounds_create", "target_compound_ids")
_REPLACE_FIELDS = (*_COMPOUND_FIELDS, "sample_batch_ids")


@pytest.mark.parametrize("field", _REPLACE_FIELDS)
def test_update_list_fields_say_they_replace(field):
    description = TargetCollectionUpdate.model_json_schema()["properties"][field][
        "description"
    ]
    assert "replace" in description


@pytest.mark.parametrize("field", _REPLACE_FIELDS)
def test_create_list_fields_do_not_say_replace(field):
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
    assert "replace the collection's batch list" in description


def test_update_batch_field_says_unnamed_batches_are_detached():
    description = TargetCollectionUpdate.model_json_schema()["properties"][
        "sample_batch_ids"
    ]["description"]
    assert "detached" in description
    assert "send the current ids too" in description


# The guard against changing compounds and batches in one request reads each
# compound field as the controller does: an empty `target_compound_ids` empties
# the collection, so beside `sample_batch_ids` it is refused; an empty
# `target_compounds_create` creates nothing and changes no compound.


@pytest.mark.parametrize("compound_ids", [[], ["c1"]])
def test_update_refuses_compound_ids_beside_batches(compound_ids):
    with pytest.raises(ValueError, match="simultaneously"):
        TargetCollectionUpdate(
            sample_batch_ids=["b1"], target_compound_ids=compound_ids
        )


def test_update_refuses_created_compounds_beside_batches():
    with pytest.raises(ValueError, match="simultaneously"):
        TargetCollectionUpdate(
            sample_batch_ids=["b1"],
            target_compounds_create=[
                {"target_compound_name": "x", "target_compound_formula": "H2O"}
            ],
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"sample_batch_ids": ["b1"]},
        {"sample_batch_ids": [], "target_compound_ids": None},
        {"sample_batch_ids": ["b1"], "target_compounds_create": []},
        {"target_compound_ids": []},
    ],
)
def test_update_allows_one_side_alone(fields):
    TargetCollectionUpdate(**fields)
