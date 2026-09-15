"""Tests: nullable string columns leave a frame as None, not NaN.

pandas 3 types text columns as its Arrow-backed ``str`` dtype, whose only
missing sentinel is NaN - so a SQL NULL that used to survive as ``None``
through an object column now arrives as a float NaN. starlette renders
responses with ``allow_nan=False``, so a single NULL name in a match response
would 500 the whole request.
"""

import json

import numpy as np
import pandas as pd

from mascope_backend.api.lib.utils import strings_json_safe


def test_missing_string_becomes_none():
    df = pd.DataFrame({"target_compound_name": ["caffeine", None]})

    records = strings_json_safe(df).to_dict(orient="records")

    assert [r["target_compound_name"] for r in records] == ["caffeine", None]


def test_result_is_json_serializable_with_allow_nan_false():
    """This is the property that actually matters - it is what starlette does."""
    df = pd.DataFrame({"name": ["alpha", None], "formula": [None, "CO2"]})

    records = strings_json_safe(df).to_dict(orient="records")

    # Would raise ValueError("Out of range float values...") on a NaN.
    assert json.loads(json.dumps(records, allow_nan=False)) == [
        {"name": "alpha", "formula": None},
        {"name": None, "formula": "CO2"},
    ]


def test_object_columns_holding_lists_are_left_alone():
    """Only genuine string columns are touched.

    ``target_collection_ids`` rides along as a list column; widening the check
    to every object column would rewrite its contents.
    """
    df = pd.DataFrame(
        {
            "name": ["alpha", None],
            "target_collection_ids": pd.Series([[1, 2], [3]], dtype=object),
        }
    )

    records = strings_json_safe(df).to_dict(orient="records")

    assert records[0]["target_collection_ids"] == [1, 2]
    assert records[1]["target_collection_ids"] == [3]


def test_numeric_nan_is_not_touched():
    """Numeric NaN is a different problem, handled by ``snr_columns_json_safe``."""
    df = pd.DataFrame({"name": ["alpha", None], "signal_to_noise": [1.5, np.nan]})

    result = strings_json_safe(df)

    assert result["signal_to_noise"].isna().tolist() == [False, True]


def test_frame_without_missing_values_is_unchanged():
    df = pd.DataFrame({"name": ["alpha", "beta"], "n": [1, 2]})

    pd.testing.assert_frame_equal(strings_json_safe(df), df)


def test_the_input_frame_is_not_mutated():
    df = pd.DataFrame({"name": ["alpha", None]})

    strings_json_safe(df)

    assert df["name"].isna().tolist() == [False, True]
