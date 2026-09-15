"""
Hermetic unit tests for ``Ms2Resource.get_averaged_centroids``: what it asks
the server for. They stub the resource's GET seam and need no running stack.

The server answers per parent peak unless ``by_activation`` is sent, so the
SDK's default has to match it - a script upgrading the SDK keeps the response
shape it was written against - and the option has to reach the request.
"""

from typing import Any

import pytest

from mascope_sdk.resources.ms2 import Ms2Resource


@pytest.fixture
def ms2(monkeypatch) -> Ms2Resource:
    """An MS2 resource that records its GETs in ``requests`` instead of sending them."""
    resource = Ms2Resource(client=None, sample_item_id="sample-1")
    resource.requests = []

    def fake_get(path: str, params: dict[str, Any] | None = None, **kwargs):
        resource.requests.append((path, params or {}))
        return {}

    monkeypatch.setattr(resource, "_get", fake_get)
    return resource


def test_averaged_centroids_ask_for_one_spectrum_per_parent_peak_by_default(ms2):
    ms2.get_averaged_centroids()

    [(path, params)] = ms2.requests
    assert path == "samples/sample-1/ms2/centroids"
    assert params["by_activation"] is False


def test_by_activation_reaches_the_request(ms2):
    ms2.get_averaged_centroids(by_activation=True, noise_threshold=5.0)

    [(_, params)] = ms2.requests
    assert params == {
        "noise_threshold": 5.0,
        "parent_peak_tolerance": 0.001,
        "by_activation": True,
    }
