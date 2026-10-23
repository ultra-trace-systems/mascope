"""
Hermetic tests of ``compounds.find_structures()``: the reference rows that
could give a compound its structure.

The method reads ``GET /api/target/compounds/structures``, a page at a time;
pinned here are the request it sends, that it pages until it has every row or
the rows asked for, and that it hands the rows over as the server sent them.
"""

import json
from typing import Any
from urllib.parse import urlparse

import pytest
import requests

from mascope_sdk import _http
from mascope_sdk.resources.compounds import STRUCTURES_PAGE_LIMIT, CompoundsResource


def _row(index: int) -> dict[str, Any]:
    return {
        "name": f"Isomer {index}",
        "inchikey": f"{'A' * 14}-{'B' * 10}-N",
        "inchi": "InChI=1S/C6H12O6/c7-1",
        "smiles": "OCC1OC(O)C(O)C(O)C1O",
        "cas_number": "50-99-7" if index == 0 else None,
        "source": "comptox",
        "source_native_id": f"DTXSID{index}",
        "license": "CC-BY-4.0",
        "cas_match": index == 0,
        "name_match": False,
    }


class _StubClient:
    url = "http://server"
    access_token = "token"
    workspace_id = "ws-1"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"
    _cache: dict[str, Any] = {}


class _Server:
    """Answers each page of ``total`` rows as the route does."""

    def __init__(self, total: int):
        self.rows = [_row(index) for index in range(total)]
        self.sent: list[dict[str, Any]] = []

    def __call__(self, url, **kwargs):
        self.sent.append({"url": url, **kwargs})
        params = kwargs["params"]
        page, limit = int(params["page"]), int(params["limit"])
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(
            {
                "message": "found",
                "results": len(self.rows),
                "formula": "C6H12O6",
                "data": self.rows[page * limit : (page + 1) * limit],
            }
        ).encode()
        return response


@pytest.fixture
def server(monkeypatch):
    def install(total: int) -> _Server:
        fake = _Server(total)
        monkeypatch.setattr(_http.requests, "get", fake)
        return fake

    return install


def test_it_reads_the_route_with_the_sdk_token(server):
    fake = server(1)

    rows = CompoundsResource(_StubClient()).find_structures(
        "C6H12O6", cas_number="50-99-7", name="Glucose"
    )

    assert rows == [_row(0)]
    [sent] = fake.sent
    assert urlparse(sent["url"]).path == "/api/target/compounds/structures"
    assert sent["params"] == {
        "formula": "C6H12O6",
        "cas_number": "50-99-7",
        "name": "Glucose",
        "limit": STRUCTURES_PAGE_LIMIT,
        "page": 0,
    }
    assert sent["headers"]["Authorization"] == "Bearer token"
    assert sent["headers"]["X-Service-Name"] == "mascope_sdk"


def test_it_sends_no_cas_number_or_name_it_was_not_given(server):
    fake = server(0)

    CompoundsResource(_StubClient()).find_structures("C6H12O6")

    assert set(fake.sent[0]["params"]) == {"formula", "limit", "page"}


def test_it_pages_until_it_has_every_row(server):
    fake = server(STRUCTURES_PAGE_LIMIT * 2 + 5)

    rows = CompoundsResource(_StubClient()).find_structures("C6H12O6")

    assert [row["source_native_id"] for row in rows] == [
        f"DTXSID{index}" for index in range(STRUCTURES_PAGE_LIMIT * 2 + 5)
    ]
    assert [sent["params"]["page"] for sent in fake.sent] == [0, 1, 2]


def test_a_limit_reads_only_the_first_rows(server):
    fake = server(STRUCTURES_PAGE_LIMIT * 3)

    rows = CompoundsResource(_StubClient()).find_structures(
        "C6H12O6", limit=STRUCTURES_PAGE_LIMIT + 1
    )

    assert len(rows) == STRUCTURES_PAGE_LIMIT + 1
    assert rows[-1]["source_native_id"] == f"DTXSID{STRUCTURES_PAGE_LIMIT}"
    assert len(fake.sent) == 2


def test_a_small_limit_is_one_small_page(server):
    fake = server(50)

    rows = CompoundsResource(_StubClient()).find_structures("C6H12O6", limit=3)

    assert len(rows) == 3
    [sent] = fake.sent
    assert sent["params"]["limit"] == 3


def test_no_structure_is_an_empty_list(server):
    server(0)

    assert CompoundsResource(_StubClient()).find_structures("C7H3Cl9") == []


def test_a_limit_below_one_is_refused(server):
    fake = server(1)

    with pytest.raises(ValueError, match="limit"):
        CompoundsResource(_StubClient()).find_structures("C6H12O6", limit=0)
    assert fake.sent == []
