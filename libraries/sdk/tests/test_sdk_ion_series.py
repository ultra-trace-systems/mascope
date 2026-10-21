"""
Hermetic unit tests for ``BatchesResource.ion_series``: the request it makes
of ``POST /api/match/records/ion/series`` and the long frame it makes of the
columnar answer. They stub the HTTP layer and need no running stack.

The route answers one record per target ion, the ion's metadata once beside a
``match_series`` of parallel arrays; the frame is one row per (sample, ion).
What a store leans on: no pair lost or repeated, each row's metadata its own
ion's, the batch's own collections read when none is named, and the
provenance stamp the loaders' frames carry.
"""

from typing import Any

import pandas as pd
import pytest

from mascope_sdk.resources.batches import ION_SERIES_COLUMNS, BatchesResource


BATCH_ID = "batch-1"

#: The route's answer for collection ``tc-1``: two ions, one matched in two
#: samples, one in one sample.
ION_SERIES_TC1 = [
    {
        "target_compound_id": "cmp-1",
        "target_compound_name": "Sulfuric acid",
        "target_compound_formula": "H2SO4",
        "target_ion_id": "ion-1",
        "target_ion_formula": "HSO4-",
        "ionization_mechanism_id": "mech-1",
        "ionization_mechanism": "-H",
        "match_series": {
            "sample_item_ids": ["s1", "s2"],
            "sample_peak_intensity_sums": [1000.0, 2000.0],
            "match_categories": [2, 1],
        },
    },
    {
        "target_compound_id": "cmp-2",
        "target_compound_name": "Nitric acid",
        "target_compound_formula": "HNO3",
        "target_ion_id": "ion-2",
        "target_ion_formula": "NO3-",
        "ionization_mechanism_id": "mech-1",
        "ionization_mechanism": "-H",
        "match_series": {
            "sample_item_ids": ["s2"],
            "sample_peak_intensity_sums": [30],
            "match_categories": [0],
        },
    },
]

#: Collection ``tc-2`` shares ``ion-2`` with ``tc-1`` and adds ``ion-3``, which
#: no sample matched.
ION_SERIES_TC2 = [
    ION_SERIES_TC1[1],
    {
        "target_compound_id": "cmp-3",
        "target_compound_name": "Iodine",
        "target_compound_formula": "I2",
        "target_ion_id": "ion-3",
        "target_ion_formula": "I3-",
        "ionization_mechanism_id": "mech-2",
        "ionization_mechanism": "+I-",
        "match_series": {
            "sample_item_ids": [],
            "sample_peak_intensity_sums": [],
            "match_categories": [],
        },
    },
]

ANSWERS = {"tc-1": ION_SERIES_TC1, "tc-2": ION_SERIES_TC2}


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeServer:
    """The series route and the collections listing behind the HTTP seams."""

    def __init__(self):
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.batch_collections = ["tc-1", "tc-2"]

    def http_post(self, url, path, access_token, data=None, **kwargs):
        self.posts.append((path, dict(data or {})))
        if "target_ion_ids" in data:
            wanted = set(data["target_ion_ids"])
            ions = {
                ion["target_ion_id"]: ion
                for answer in ANSWERS.values()
                for ion in answer
                if ion["target_ion_id"] in wanted
            }
            rows = list(ions.values())
        else:
            rows = ANSWERS.get(data["target_collection_id"], [])
        return _Response({"status": "success", "results": len(rows), "data": rows})

    def http_get(self, url, path, access_token, params=None, **kwargs):
        self.gets.append((path, dict(params or {})))
        assert path == "target/collections"
        rows = [
            {"target_collection_id": c, "target_collection_name": c.upper()}
            for c in self.batch_collections
        ]
        return _Response({"status": "success", "results": len(rows), "data": rows})


class _StubClient:
    url = "http://testserver"
    access_token = "token"
    workspace_id = "ws-1"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}
        self.stamped = 0

    @property
    def collections(self):
        from mascope_sdk.resources.collections import CollectionsResource

        return CollectionsResource(self)

    def _with_provenance(self, frame):
        self.stamped += 1
        frame.attrs["provenance"] = {"deployment_id": "example-lab"}
        return frame


@pytest.fixture()
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr("mascope_sdk.resources._base.http_post", fake.http_post)
    monkeypatch.setattr("mascope_sdk.resources._base.http_get", fake.http_get)
    return fake


@pytest.fixture()
def client(server):
    return _StubClient()


@pytest.fixture()
def batches(client):
    return BatchesResource(client)


def test_one_row_per_sample_and_ion_with_its_own_ions_metadata(batches):
    series = batches.ion_series(BATCH_ID, collection="tc-1")

    assert list(series.columns) == ION_SERIES_COLUMNS
    assert list(zip(series["sample_item_id"], series["target_ion_id"])) == [
        ("s1", "ion-1"),
        ("s2", "ion-1"),
        ("s2", "ion-2"),
    ]
    assert series["intensity"].tolist() == [1000.0, 2000.0, 30.0]
    assert series["intensity"].dtype == "float64"
    assert series["match_category"].tolist() == [2, 1, 0]
    nitric = series[series["target_ion_id"] == "ion-2"].iloc[0]
    assert nitric["target_compound_name"] == "Nitric acid"
    assert nitric["target_compound_formula"] == "HNO3"
    assert nitric["ion_formula"] == "NO3-"
    assert nitric["ionization_mechanism"] == "-H"


def test_a_collection_is_asked_for_by_the_batch(server, batches):
    batches.ion_series(BATCH_ID, collection="tc-1")

    assert server.posts == [
        (
            "match/records/ion/series",
            {"sample_batch_id": BATCH_ID, "target_collection_id": "tc-1"},
        )
    ]
    assert server.gets == []


def test_ions_are_asked_for_by_id(server, batches):
    series = batches.ion_series(BATCH_ID, ions=["ion-1"])

    assert server.posts == [
        (
            "match/records/ion/series",
            {"sample_batch_id": BATCH_ID, "target_ion_ids": ["ion-1"]},
        )
    ]
    assert set(series["target_ion_id"]) == {"ion-1"}


def test_a_single_ion_id_is_one_ion(server, batches):
    series = batches.ion_series(BATCH_ID, ions="ion-2")

    assert server.posts[0][1]["target_ion_ids"] == ["ion-2"]
    # Its one intensity came as an integer, and is a float like any other.
    assert series["intensity"].dtype == "float64"


def test_no_scope_reads_every_collection_of_the_batch(server, batches):
    series = batches.ion_series(BATCH_ID)

    assert server.gets == [("target/collections", {"sample_batch_id": BATCH_ID})]
    assert [body["target_collection_id"] for _, body in server.posts] == [
        "tc-1",
        "tc-2",
    ]
    # ion-2 is in both collections and is still one row per sample; ion-3,
    # matched nowhere, is no row.
    assert list(zip(series["sample_item_id"], series["target_ion_id"])) == [
        ("s1", "ion-1"),
        ("s2", "ion-1"),
        ("s2", "ion-2"),
    ]


def test_a_batch_without_collections_is_an_empty_frame(server, batches):
    server.batch_collections = []

    series = batches.ion_series(BATCH_ID)

    assert server.posts == []
    assert series.empty
    assert list(series.columns) == ION_SERIES_COLUMNS
    assert series["intensity"].dtype == "float64"


def test_no_ions_asks_nothing(server, batches):
    series = batches.ion_series(BATCH_ID, ions=[])

    assert server.posts == []
    assert series.empty


def test_collection_and_ions_together_are_refused(server, batches):
    with pytest.raises(ValueError, match="not both"):
        batches.ion_series(BATCH_ID, collection="tc-1", ions=["ion-1"])
    assert server.posts == []


def test_the_frame_is_stamped_like_a_loaders(client, batches):
    series = batches.ion_series(BATCH_ID, collection="tc-1")

    assert client.stamped == 1
    assert series.attrs["provenance"] == {"deployment_id": "example-lab"}


def test_an_empty_frame_is_stamped_too(server, client, batches):
    server.batch_collections = []

    series = batches.ion_series(BATCH_ID)

    assert series.attrs["provenance"] == {"deployment_id": "example-lab"}
