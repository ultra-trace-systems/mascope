"""
Hermetic unit tests for ``Ms2Resource.get_averaged_centroids`` and
``get_window``: what they ask the server for and what they hand back. They
stub the resource's GET seams and need no running stack.

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


@pytest.fixture
def windowed(monkeypatch) -> Ms2Resource:
    """An MS2 resource whose envelope GETs answer ``resource.answer``."""
    resource = Ms2Resource(client=None, sample_item_id="sample-1")
    resource.requests = []
    resource.answer = {}

    def fake_get_envelope(path: str, params: dict[str, Any] | None = None):
        resource.requests.append((path, params or {}))
        return resource.answer

    monkeypatch.setattr(resource, "_get_envelope", fake_get_envelope)
    return resource


def test_window_asks_for_each_groups_own_width_by_default(windowed):
    windowed.get_window()

    [(path, params)] = windowed.requests
    assert path == "samples/sample-1/ms2/window"
    # No width sent: the server draws each group at its own.
    assert params == {"parent_peak_tolerance": 0.001}


def test_window_width_reaches_the_request(windowed):
    windowed.get_window(width=1.5, parent_peak_tolerance=0.002)

    [(_, params)] = windowed.requests
    assert params == {"width": 1.5, "parent_peak_tolerance": 0.002}


def test_window_hands_back_the_records_and_why_they_are_empty(windowed):
    record = {
        "group": "137.0960@hcd25.00",
        "located_precursor_mz": 137.0961,
        "centroids": {"mz": [137.0961], "is_parent": [True]},
    }
    windowed.answer = {
        "message": "The isolation windows of 1 MS2 groups.",
        "source": "store",
        "parent_peak_tolerance": 0.001,
        "window_pad": 0.005,
        "reason": None,
        "data": [record],
    }

    assert windowed.get_window() == {
        "data": [record],
        "reason": None,
        "source": "store",
        "parent_peak_tolerance": 0.001,
        "window_pad": 0.005,
    }

    windowed.answer = {"source": None, "reason": "unsupported", "data": []}
    window = windowed.get_window()
    assert window["data"] == []
    assert window["reason"] == "unsupported"


def test_window_logs_the_servers_warnings(windowed):
    from loguru import logger

    windowed.answer = {"data": [], "warnings": ["the store is being rewritten"]}
    caught: list[str] = []
    handle = logger.add(lambda m: caught.append(str(m)), level="WARNING")
    try:
        windowed.get_window()
    finally:
        logger.remove(handle)

    assert any("the store is being rewritten" in line for line in caught)
