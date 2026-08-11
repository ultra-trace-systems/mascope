"""
The serialized isotope records must stay valid JSON.

``signal_to_noise`` rides along on every computed isotope frame for the v2
score and stays NaN whenever the sample file carries no signal-to-noise
data. Starlette serializes responses with ``allow_nan=False``, so a NaN
that survives ``to_dict("records")`` turns the whole match response into a
500. ``_snr_columns_json_safe`` is the boundary that prevents that.
"""

import json

import numpy as np
import pandas as pd

from mascope_backend.api.controllers.match.aggregate.sample.match_aggregate_sample_controller import (  # noqa: E501
    _snr_columns_json_safe,
)


def test_nan_snr_serializes_as_null():
    df = pd.DataFrame(
        {
            "target_ion_id": ["ion-1", "ion-1"],
            "sample_peak_mz": [181.07, 182.07],
            "signal_to_noise": [np.nan, 12.5],
            "is_satellite": [False, False],
        }
    )

    records = _snr_columns_json_safe(df).to_dict("records")

    # The point of the exercise: starlette's allow_nan=False must accept it.
    payload = json.dumps(records, allow_nan=False)
    parsed = json.loads(payload)
    assert parsed[0]["signal_to_noise"] is None
    assert parsed[1]["signal_to_noise"] == 12.5


def test_columns_absent_is_a_no_op():
    df = pd.DataFrame({"target_ion_id": ["ion-1"], "sample_peak_mz": [181.07]})
    records = _snr_columns_json_safe(df).to_dict("records")
    assert records == df.to_dict("records")


def test_other_columns_are_untouched():
    """Only the SNR carrier columns are rewritten; the frame is not mutated."""
    df = pd.DataFrame(
        {
            "target_ion_id": ["ion-1"],
            "signal_to_noise": [np.nan],
            "match_score": [0.9],
        }
    )
    result = _snr_columns_json_safe(df)
    assert result["match_score"].tolist() == [0.9]
    # The input frame keeps its NaN; persistence still sees the raw values.
    assert np.isnan(df["signal_to_noise"].iloc[0])
