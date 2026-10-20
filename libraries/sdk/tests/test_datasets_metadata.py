"""
Hermetic test: a dataset listing carries the dataset's descriptive metadata.

The server reports a dataset's licence, authors, keywords, related identifiers
and funding with the rest of its fields, and ``datasets.list()`` builds its
frame from the server's dicts as they come, so the metadata arrives as columns
without the SDK naming them. This pins that it stays so: a listing that
started picking its columns would drop them.
"""

from types import SimpleNamespace

import pandas as pd

from mascope_sdk.resources.datasets import DatasetsResource


AUTHORS = [
    {"name": "Author One", "orcid": "0000-0002-1825-0097", "affiliation": "Lab A"},
    {"name": "Author Two", "orcid": None, "affiliation": None},
]
RELATED = [
    {
        "identifier": "10.1234/example",
        "relation": "IsSupplementTo",
        "identifier_type": "DOI",
    }
]


def test_a_dataset_listing_passes_the_descriptive_metadata_through(monkeypatch):
    listing = [
        {
            "dataset_id": "ds-1",
            "dataset_name": "Described",
            "license": "CC-BY-4.0",
            "authors": AUTHORS,
            "keywords": ["aerosol", "CI-MS"],
            "related_identifiers": RELATED,
            "funding": [{"funder": "Funder A", "award": "123"}],
        },
        {
            "dataset_id": "ds-2",
            "dataset_name": "Undescribed",
            "license": None,
            "authors": None,
            "keywords": None,
            "related_identifiers": None,
            "funding": None,
        },
    ]
    resource = DatasetsResource(SimpleNamespace(workspace_id="ws-1", _cache={}))
    monkeypatch.setattr(resource, "_get", lambda path, **kwargs: listing)

    rows = resource.list().set_index("dataset_id")

    described = rows.loc["ds-1"]
    assert described["license"] == "CC-BY-4.0"
    assert described["authors"] == AUTHORS
    assert described["keywords"] == ["aerosol", "CI-MS"]
    assert described["related_identifiers"] == RELATED
    assert described["funding"] == [{"funder": "Funder A", "award": "123"}]
    assert pd.isna(rows.loc["ds-2", "license"])
