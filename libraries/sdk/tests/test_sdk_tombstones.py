"""
Hermetic tests of ``datasets.tombstones()``: what deleted datasets were.

The method is a thin read over ``GET /api/datasets/tombstones``; pinned here
are the request it sends - the route, with the SDK's token and service name -
and that it hands the records over as the server sent them.
"""

import json
from typing import Any

import pytest
import requests

from mascope_sdk import _http
from mascope_sdk.resources.datasets import DatasetsResource


RECORD = {
    "dataset_id": "ds-1",
    "workspace_id": "ws-gone",
    "dataset_name": "Campaign",
    "descriptive_metadata": {"license": "CC-BY-4.0", "authors": None},
    "n_sample_batches": 2,
    "n_sample_items": 3,
    "n_sample_files": 2,
    "deleted_utc": "2026-10-20T12:00:00+00:00",
    "deleted_by": "someone@example.org",
    "deleted_via": "workspace",
    "provenance": {"provenance_version": 1, "inputs": {"dataset_id": "ds-1"}},
}


class _StubClient:
    url = "http://server"
    access_token = "token"
    workspace_id = "ws-1"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"
    _cache: dict[str, Any] = {}


class _Wire:
    def __init__(self, body: dict[str, Any]):
        self.body = body
        self.sent: list[dict[str, Any]] = []

    def __call__(self, url, **kwargs):
        self.sent.append({"url": url, **kwargs})
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(self.body).encode()
        return response


@pytest.fixture
def wire(monkeypatch):
    def install(body):
        fake = _Wire(body)
        monkeypatch.setattr(_http.requests, "get", fake)
        return fake

    return install


def test_it_reads_the_route_with_the_sdk_token(wire):
    fake = wire({"data": [RECORD], "total": 1})

    records = DatasetsResource(_StubClient()).tombstones()

    assert records == [RECORD]
    [sent] = fake.sent
    assert sent["url"] == "http://server/api/datasets/tombstones"
    assert sent["headers"]["Authorization"] == "Bearer token"
    assert sent["headers"]["X-Service-Name"] == "mascope_sdk"


def test_no_tombstones_is_an_empty_list(wire):
    wire({"data": [], "total": 0})

    assert DatasetsResource(_StubClient()).tombstones() == []
