"""
Hermetic unit tests for ``Ms2Resource.get_averaged_centroids``,
``get_window`` and ``get_readings``: what they ask the server for and what
they hand back. They stub the resource's GET seams and need no running
stack.

The server answers per parent peak unless ``by_activation`` is sent, so the
SDK's default has to match it - a script upgrading the SDK keeps the response
shape it was written against - and the option has to reach the request.
"""

import json
from typing import Any

import pytest

from mascope_sdk.resources.ms2 import _READINGS_ATTRS, READINGS_COLUMNS, Ms2Resource


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


#: A readings response as the server sends it: one window of two spectra, the
#: second with no peak above the floor.
READINGS = {
    "message": "Readings of the MS2 group '195.0877@hcd20.00' ...",
    "source": "store",
    "parent_peak_tolerance": 0.001,
    "cached": True,
    "core_version": 1,
    "reason": None,
    "data": {
        "group": "195.0877@hcd20.00",
        "polarity": "+",
        "ions_source": "named",
        "ions": [{"ion": "C8H11N4O2+", "mz": 195.087652, "role": "target"}],
        "left_out": [],
        "recalibration": {"offset_ppm": 0.2},
        "spectra": [
            {
                "group": "195.0877@hcd10.00",
                "tic": 3.0,
                "n_peaks": 2,
                "readings": [
                    {"peak": 0, "mz": 83.06, "role": "fragment", "rank": 1},
                    {"peak": 1, "mz": 195.09, "role": "precursor", "rank": 1},
                ],
            },
            {"group": "195.0877@hcd20.00", "tic": 0.0, "n_peaks": 0, "readings": []},
        ],
    },
}


@pytest.fixture
def answering(monkeypatch) -> Ms2Resource:
    """An MS2 resource whose envelope GETs answer :data:`READINGS` and are
    recorded in ``requests``."""
    resource = Ms2Resource(client=None, sample_item_id="sample-1")
    resource.requests = []

    def fake_get_envelope(path: str, params: dict[str, Any] | None = None):
        resource.requests.append((path, params or {}))
        return json.loads(json.dumps(READINGS))

    monkeypatch.setattr(resource, "_get_envelope", fake_get_envelope)
    return resource


def test_readings_ask_for_the_group_and_its_ions(answering):
    answering.get_readings("195.0877@hcd20.00", ["C8H11N4O2+", "C10H11O4+"])
    answering.get_readings("195.0877@hcd20.00", "C8H11N4O2+", noise_threshold=3.0)

    [(path, params), (_, as_text)] = answering.requests
    assert path == "samples/sample-1/ms2/readings"
    assert params == {
        "group": "195.0877@hcd20.00",
        "ions": "C8H11N4O2+,C10H11O4+",
        "noise_threshold": 10.0,
        "parent_peak_tolerance": 0.001,
    }
    assert as_text["ions"] == "C8H11N4O2+" and as_text["noise_threshold"] == 3.0


def test_readings_are_one_row_per_reading_with_the_window_in_attrs(answering):
    frame = answering.get_readings("195.0877@hcd20.00", ["C8H11N4O2+"])

    assert list(frame.columns) == list(READINGS_COLUMNS)
    assert frame["group"].tolist() == ["195.0877@hcd10.00"] * 2
    assert frame["role"].tolist() == ["fragment", "precursor"]
    assert frame["formula"].isna().all()
    assert {name: frame.attrs[name] for name in _READINGS_ATTRS} == {
        "source": "store",
        "cached": True,
        "core_version": 1,
        "parent_peak_tolerance": 0.001,
        "reason": None,
    }
    window = frame.attrs["window"]
    assert window["group"] == "195.0877@hcd20.00"
    assert window["ions"] == [{"ion": "C8H11N4O2+", "mz": 195.087652, "role": "target"}]
    assert window["recalibration"] == {"offset_ppm": 0.2}
    assert window["spectra"] == [
        {"group": "195.0877@hcd10.00", "tic": 3.0, "n_peaks": 2},
        {"group": "195.0877@hcd20.00", "tic": 0.0, "n_peaks": 0},
    ]


def test_readings_of_no_spectrum_are_an_empty_frame_with_the_columns(
    answering, monkeypatch
):
    unsupported = {
        **READINGS,
        "source": None,
        "cached": False,
        "reason": "unsupported",
        "data": {**READINGS["data"], "spectra": []},
    }
    monkeypatch.setattr(answering, "_get_envelope", lambda path, params: unsupported)

    frame = answering.get_readings("195.0877@hcd20.00", ["C8H11N4O2+"])

    assert frame.empty and list(frame.columns) == list(READINGS_COLUMNS)
    assert frame.attrs["reason"] == "unsupported"
    assert frame.attrs["window"]["spectra"] == []
