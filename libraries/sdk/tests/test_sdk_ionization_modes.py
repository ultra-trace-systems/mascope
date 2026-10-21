"""
Hermetic tests of ``ionization.modes()``: the modes a sample's
``ionization_mode_id`` names.

The method is a cached read over ``GET /api/ionization/modes``; pinned here
are the request it sends - the route, with the SDK's token and service name,
and ``include_system`` only when asked for - and that it hands the modes over
as a DataFrame, or None when there are none.
"""

import json
from typing import Any

import pytest
import requests

from mascope_sdk import _http
from mascope_sdk.resources.ionization import IonizationResource


MODE = {
    "ionization_mode_id": "mode-1",
    "ionization_mode_name": "Bromide",
    "ionization_mode_token": "br",
    "instrument": None,
    "ionization_mode_polarity": "-",
    "ionization_mechanism_ids": ["mech-1"],
    "system_key": None,
    "calibration_collection_id": None,
    "diagnostic_collection_id": None,
}


class _StubClient:
    url = "http://server"
    access_token = "token"
    workspace_id = "ws-1"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, Any] = {}


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
    fake = wire({"results": 1, "data": [MODE]})

    modes = IonizationResource(_StubClient()).modes()

    assert modes.to_dict("records") == [MODE]
    [sent] = fake.sent
    assert sent["url"] == "http://server/api/ionization/modes"
    assert not sent.get("params")
    assert sent["headers"]["Authorization"] == "Bearer token"
    assert sent["headers"]["X-Service-Name"] == "mascope_sdk"


def test_the_unadopted_shipped_modes_only_when_asked_for(wire):
    fake = wire({"results": 1, "data": [MODE]})
    resource = IonizationResource(_StubClient())

    resource.modes()
    resource.modes(include_system=True)

    # Two listings, each cached under its own key.
    assert [sent.get("params") for sent in fake.sent] == [
        None,
        {"include_system": "true"},
    ]


def test_a_second_call_is_served_from_the_cache(wire):
    fake = wire({"results": 1, "data": [MODE]})
    resource = IonizationResource(_StubClient())

    resource.modes()
    again = resource.modes()

    assert len(fake.sent) == 1
    assert again["ionization_mode_name"].tolist() == ["Bromide"]


def test_no_modes_is_none(wire):
    wire({"results": 0, "data": []})

    assert IonizationResource(_StubClient()).modes() is None
