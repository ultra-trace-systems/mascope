"""
The DB-read isotope frame (``_assemble_filtered_isotope_frame``) keeps every
value column the aggregation query fetches from ``match_isotope``.

The frame is reordered with a column list, and selecting by a list drops every
column it does not name, with no error. ``ratio_scan_count`` and
``signal_to_noise`` were both lost that way once, so the list's value part is
``MATCH_ISOTOPE_VALUE_COLUMNS``, the same list the query selects.
"""

import pandas as pd

from mascope_backend.api.controllers.match.aggregate import (
    match_aggregate_controller as controller,
)
from mascope_backend.api.controllers.match.aggregate.match_aggregate_controller import (
    _assemble_filtered_isotope_frame,
)
from mascope_backend.api.controllers.match.lib.match_aggregate import (
    MATCH_ISOTOPE_VALUE_COLUMNS,
)


def _samples() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sample_item_id": ["s1"],
            "filename": ["orbi_2024.01.01_10h00m00s.h5"],
            "instrument": ["orbi"],
            "sample_item_name": ["sample 1"],
            "sample_item_type": ["sample"],
            "polarity": ["+"],
        }
    )


def _targets() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target_collection_id": ["c1"],
            "target_collection_name": ["collection"],
            "target_collection_description": [None],
            "target_collection_type": ["default"],
            "target_compound_id": ["cmp1"],
            "target_compound_formula": ["C6H6"],
            "target_compound_name": ["benzene"],
            "target_ion_id": ["ion1"],
            "target_ion_formula": ["C6H7+"],
            "filter_params": [None],
            "ionization_mechanism": ["H+"],
            "target_isotope_id": ["iA"],
            "target_isotope_formula": ["C6H7+"],
            "mz": [79.0542],
            "relative_abundance": [1.0],
            "resolution": ["HIGH"],
        }
    )


# A matched isotope inside the default match window, with a distinct value in
# every fetched column so a dropped or swapped column shows.
_MATCHED = {
    "match_mz_error": 0.5,
    "match_abundance_error": 0.05,
    "sample_peak_intensity": 100000.0,
    "sample_peak_intensity_relative": 0.8,
    "ratio_scan_count": 7.0,
    "signal_to_noise": 42.0,
    "sample_peak_mz": 79.05424,
    "sample_peak_tof": 12.3,
    "match_score": 0.97,
}


def _stored(value_columns: list[str]) -> pd.DataFrame:
    row = {"sample_item_id": "s1", "target_isotope_id": "iA"}
    row.update({column: _MATCHED.get(column, 3.0) for column in value_columns})
    return pd.DataFrame([row])


def test_every_fetched_value_column_reaches_the_frame():
    frame = _assemble_filtered_isotope_frame(
        _stored(MATCH_ISOTOPE_VALUE_COLUMNS), _samples(), _targets(), None
    )

    assert len(frame) == 1
    assert set(_MATCHED) == set(MATCH_ISOTOPE_VALUE_COLUMNS)
    for column in MATCH_ISOTOPE_VALUE_COLUMNS:
        assert column in frame.columns, column
        assert frame.loc[0, column] == _MATCHED[column], column

    # The values follow the target context, in the list's order.
    start = list(frame.columns).index("resolution") + 1
    assert (
        list(frame.columns)[start : start + len(MATCH_ISOTOPE_VALUE_COLUMNS)]
        == MATCH_ISOTOPE_VALUE_COLUMNS
    )


def test_a_value_column_added_to_the_list_reaches_the_frame(monkeypatch):
    # The next value persisted on match_isotope is added to the one list; the
    # frame carries it without a second edit.
    value_columns = [*MATCH_ISOTOPE_VALUE_COLUMNS, "next_value"]
    monkeypatch.setattr(controller, "MATCH_ISOTOPE_VALUE_COLUMNS", value_columns)

    frame = _assemble_filtered_isotope_frame(
        _stored(value_columns), _samples(), _targets(), None
    )

    assert frame.loc[0, "next_value"] == 3.0
