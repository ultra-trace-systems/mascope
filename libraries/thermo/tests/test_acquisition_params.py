"""Acquisition-parameter capture (``ReaderBackend.acquisition_parameters``).

The capture exists to record what acquisitions actually carry, so a structured
acquisition-method schema can later be designed from evidence. That makes two
properties load-bearing, and both are pinned here:

* the constant/varying split must not report a per-scan measurement as a
  method-level setting, and
* the result must survive ``json.dump``, because it is written verbatim into
  ``.props``.
"""

import json
import math

import numpy as np
import pytest
from conftest import POS_ORBI_FILE_PATH

from mascope_thermo import backend as m_backend
from mascope_thermo.backend import open_backend


# -- pure helpers (no file, no backend) -------------------------------------


class _Exotic:
    """Stands in for a boxed .NET value with no JSON representation."""

    def __repr__(self):
        return "<exotic>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("text", "text"),
        (True, True),
        (7, 7),
        (1.5, 1.5),
        (b"bytes", "bytes"),
        (bytearray(b"buf"), "buf"),
        (np.int64(42), 42),
        (np.float64(2.5), 2.5),
        (np.bool_(True), True),
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (_Exotic(), "<exotic>"),
    ],
)
def test_json_safe_coerces_to_serializable(value, expected):
    coerced = m_backend._json_safe(value)
    assert coerced == expected
    json.dumps(coerced)  # must not raise


def test_json_safe_unwraps_numpy_without_stringifying_numbers():
    """np.int64/np.bool_ do not subclass int/bool, so a naive isinstance chain
    would stringify them and silently turn numbers into text."""
    assert isinstance(m_backend._json_safe(np.int64(42)), int)
    assert isinstance(m_backend._json_safe(np.float64(2.5)), float)
    assert isinstance(m_backend._json_safe(np.bool_(True)), bool)


def test_json_safe_rejects_non_finite_floats():
    """NaN/Infinity are accepted by Python's json but are not valid JSON, so
    they must not reach .props as bare literals."""
    for value in (float("nan"), float("inf"), float("-inf")):
        coerced = m_backend._json_safe(value)
        assert isinstance(coerced, str)
        assert not isinstance(coerced, float) or not math.isfinite(coerced)


@pytest.mark.parametrize(
    ("items", "count", "expected"),
    [
        ([], 5, []),
        ([1, 2, 3], 0, []),
        ([1, 2, 3], 5, [1, 2, 3]),
        ([1, 2, 3], 3, [1, 2, 3]),
        ([1, 2, 3, 4, 5], 1, [1]),
        ([1, 2, 3, 4, 5], 2, [1, 5]),
        ([1, 2, 3, 4, 5], 3, [1, 3, 5]),
        (list(range(100)), 5, [0, 25, 50, 74, 99]),
    ],
)
def test_sample_evenly(items, count, expected):
    assert m_backend._sample_evenly(items, count) == expected


def test_sample_evenly_includes_both_endpoints():
    """Spread matters: a value that drifts over the run is only caught as
    'varying' if the sample reaches the end of the run."""
    sampled = m_backend._sample_evenly(list(range(1000)), 5)
    assert sampled[0] == 0
    assert sampled[-1] == 999


def test_summarize_splits_constant_from_varying():
    summary = m_backend._summarize_acquisition_parameters(
        "test",
        [
            {"FT Resolution:": 120000, "Injection Time:": 10.0},
            {"FT Resolution:": 120000, "Injection Time:": 12.5},
        ],
    )
    assert summary["source"] == "test"
    assert summary["scans_sampled"] == 2
    assert summary["constant"] == {"FT Resolution:": 120000}
    assert summary["varying"] == ["Injection Time:"]


def test_summarize_does_not_leak_varying_values():
    """A single scan's measurement is not method metadata; only the key name is
    kept, so it cannot later be mistaken for a method-level setting."""
    summary = m_backend._summarize_acquisition_parameters(
        "test", [{"AGC:": 1}, {"AGC:": 2}]
    )
    assert summary["constant"] == {}
    assert summary["varying"] == ["AGC:"]
    assert "1" not in json.dumps(summary["varying"])


def test_summarize_handles_keys_missing_from_some_scans():
    summary = m_backend._summarize_acquisition_parameters(
        "test",
        [
            {"only_first": 1, "shared": "s"},
            {"only_second": 2, "shared": "s"},
        ],
    )
    assert summary["constant"] == {"shared": "s"}
    assert summary["varying"] == ["only_first", "only_second"]


def test_summarize_single_scan_reports_everything_constant():
    summary = m_backend._summarize_acquisition_parameters("test", [{"a": 1, "b": 2}])
    assert summary["scans_sampled"] == 1
    assert summary["constant"] == {"a": 1, "b": 2}
    assert summary["varying"] == []


@pytest.mark.parametrize("per_scan", [[], [{}], [{}, {}, None]])
def test_summarize_tolerates_empty_input(per_scan):
    summary = m_backend._summarize_acquisition_parameters("test", per_scan)
    assert summary == {
        "source": "test",
        "scans_sampled": 0,
        "constant": {},
        "varying": [],
    }


def test_summarize_output_is_json_serializable():
    summary = m_backend._summarize_acquisition_parameters(
        "test", [{"np": np.int64(5), "raw": b"x", "bad": float("nan")}]
    )
    json.dumps(summary)  # must not raise


def test_summarize_compares_coerced_values_across_scans():
    """Constancy must be judged on the *coerced* values, not the raw ones.

    Values that are equal only after coercion -- bytes, boxed .NET objects, NaN
    -- are the case _json_safe exists for. Comparing raw values instead would
    demote every one of them from ``constant`` to ``varying``, silently
    discarding exactly the method-level evidence this capture is for. bytes and
    NaN both fail a raw ``!=`` (``b"v1" != "v1"``; ``nan != nan``), so this
    fails if the coercion is dropped from the comparison.
    """
    scan = {"Mode:": _Exotic(), "Raw:": b"v1", "Missing:": float("nan")}
    summary = m_backend._summarize_acquisition_parameters(
        "test",
        [dict(scan), {"Mode:": _Exotic(), "Raw:": b"v1", "Missing:": float("nan")}],
    )
    assert summary["varying"] == []
    assert summary["constant"] == {
        "Mode:": "<exotic>",
        "Raw:": "v1",
        "Missing:": "nan",
    }


# -- backend contract (reads the committed sample file) ---------------------


@pytest.fixture
def acquisition_params(backend, monkeypatch):
    """acquisition_parameters() for the committed POS sample, per backend."""
    monkeypatch.setenv(m_backend.ENV_BACKEND, backend)
    with open_backend(POS_ORBI_FILE_PATH) as be:
        return be.acquisition_parameters()


def test_acquisition_parameters_shape(acquisition_params):
    assert set(acquisition_params) == {
        "source",
        "scans_sampled",
        "constant",
        "varying",
    }
    assert acquisition_params["scans_sampled"] > 0
    assert isinstance(acquisition_params["constant"], dict)
    assert isinstance(acquisition_params["varying"], list)


def test_acquisition_parameters_are_written_to_props_unchanged(acquisition_params):
    """.props is written with json.dump, so an unserializable trailer value
    would fail the whole file, not just this field."""
    json.dumps(acquisition_params)


def test_acquisition_parameters_capture_the_method_settings(acquisition_params):
    """The point of the capture: the trailer carries acquisition-method
    settings well beyond the five fields _OTF_TRAILER_FIELDS surfaces.

    Labels are the instrument's own trailer labels, so both backends agree on
    them. Values are compared as text because their *type* is backend-dependent
    -- see test_value_encoding_is_backend_dependent.
    """
    constant = acquisition_params["constant"]
    assert len(constant) > 20, "expected a rich trailer, not a handful of fields"
    assert constant["Application Mode:"] == "Small Molecule"
    assert str(constant["FT Resolution:"]) == "120000"
    assert str(constant["AGC Target:"]) == "10000000"


def test_value_encoding_is_backend_dependent(acquisition_params):
    """Pins a known, deliberate asymmetry rather than leaving it latent.

    Thermo's ``GetTrailerExtraInformation().Values`` is a .NET ``string[]``, so
    every value arrives as text; OpenTFRaw parses the same trailer into typed
    scalars. Values are stored verbatim -- normalising them would be a typing
    decision about 70+ heterogeneous fields, which is exactly the schema call
    this capture exists to defer. Anything analysing .props across a mixed
    corpus must therefore normalise per ``source``.
    """
    resolution = acquisition_params["constant"]["FT Resolution:"]
    if acquisition_params["source"] == "thermo":
        assert isinstance(resolution, str)
    else:
        assert isinstance(resolution, int)


def test_acquisition_parameters_respect_max_scans(backend, monkeypatch):
    monkeypatch.setenv(m_backend.ENV_BACKEND, backend)
    with open_backend(POS_ORBI_FILE_PATH) as be:
        assert be.acquisition_parameters(max_scans=1)["scans_sampled"] == 1
