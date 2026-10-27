"""
Tests: the Batch sheet's descriptive-metadata rows keep their shape.

The rows are written whether or not the dataset carries the metadata, so a
parser finds the same labels in the same places in every export. The export
with metadata filled in is covered end to end by
``tests/integration/api/export/test_batch_spreadsheet_traceability.py``.
"""

import pytest

from mascope_backend.api.controllers.sample.batches.export.service import (
    _dataset_metadata_rows,
)


LABELS = ["License", "Authors", "Keywords", "Related identifiers", "Funding"]


@pytest.mark.parametrize(
    "dataset",
    [
        None,
        {"dataset_name": "No metadata"},
        {
            "license": None,
            "authors": None,
            "keywords": [],
            "related_identifiers": [],
            "funding": None,
        },
    ],
    ids=["no dataset", "fields absent", "fields empty"],
)
def test_the_rows_are_written_empty_when_the_dataset_has_no_metadata(dataset):
    assert _dataset_metadata_rows(dataset) == [[label, None] for label in LABELS]
