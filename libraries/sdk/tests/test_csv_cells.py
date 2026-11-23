"""Text cells of the CSV exports: the apostrophe on the way out, and off again."""

import numpy as np
import pandas as pd
import pytest

from mascope_sdk import (
    escape_cell,
    escape_frame,
    read_export_csv,
    unescape_cell,
    unescape_frame,
)
from mascope_sdk.csv_cells import FORMULA_STARTS


# The six starts the convention names, spelled out rather than read from the
# constant, so dropping one from FORMULA_STARTS turns these tests red.
SIX_STARTS = ["=", "+", "-", "@", "\t", "\r"]


def test_the_formula_starts_are_the_six_the_convention_names():
    assert FORMULA_STARTS == tuple(SIX_STARTS)


@pytest.mark.parametrize("start", SIX_STARTS)
def test_a_text_that_starts_a_formula_gets_one_apostrophe(start):
    assert escape_cell(f"{start}1+1") == f"'{start}1+1"


@pytest.mark.parametrize("value", ["Spring", "", "1=1", "'quoted", "C6H12O6"])
def test_any_other_text_is_left_alone(value):
    assert escape_cell(value) == value


@pytest.mark.parametrize("value", [-1, -2.5, None, True, np.float64(-3.0)])
def test_a_number_or_none_is_left_alone(value):
    assert escape_cell(value) is value


@pytest.mark.parametrize("start", SIX_STARTS)
def test_the_apostrophe_comes_off_again(start):
    assert unescape_cell(escape_cell(f"{start}x")) == f"{start}x"


@pytest.mark.parametrize("value", ["'quoted", "'", "''", "Spring", "'1"])
def test_an_apostrophe_before_anything_else_stays(value):
    assert unescape_cell(value) == value


def test_an_apostrophe_typed_before_a_formula_does_not_round_trip():
    """The one case the docs name: a name that really begins with ' and =."""
    assert unescape_cell(escape_cell("'=x")) == "=x"


def test_a_frame_escapes_its_text_columns_only():
    frame = pd.DataFrame(
        {
            "name": ["=1+1", "Spring", None],
            "offset": [-1.0, 2.0, np.nan],
            "count": [-1, 2, 3],
            "label": pd.array(["-x", "y", None], dtype="string"),
        }
    )

    out = escape_frame(frame)

    assert out["name"].tolist()[:2] == ["'=1+1", "Spring"]
    assert out["label"].tolist()[:2] == ["'-x", "y"]
    assert out["name"].isna().iloc[2] and out["label"].isna().iloc[2]
    pd.testing.assert_series_equal(out["offset"], frame["offset"])
    pd.testing.assert_series_equal(out["count"], frame["count"])
    # The frame it was given is left as it was.
    assert frame["name"].iloc[0] == "=1+1"
    pd.testing.assert_frame_equal(unescape_frame(out), frame)


@pytest.mark.parametrize("sep", [";", ","])
def test_a_written_export_reads_back_as_typed(tmp_path, sep):
    frame = pd.DataFrame(
        {
            "sample_item_name": [
                "=1+1",
                "@SUM(A1)",
                "Spring",
                "+cmd",
                "\tTabbed",
                "\rReturned",
            ],
            "engine_claim": ["-x", None, "'=lost", "plain", "a", "b"],
            "mz": [-1.5, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
    path = tmp_path / "export.csv"
    escape_frame(frame).to_csv(path, index=False, sep=sep)

    # The file holds the apostrophes a spreadsheet shows as text.
    raw = pd.read_csv(path, sep=sep)
    assert raw["sample_item_name"].tolist() == [
        "'=1+1",
        "'@SUM(A1)",
        "Spring",
        "'+cmd",
        "'\tTabbed",
        "'\rReturned",
    ]

    back = read_export_csv(path, sep=sep)

    assert back["sample_item_name"].tolist() == frame["sample_item_name"].tolist()
    # "'=lost" was typed with its apostrophe, which the reader takes for the
    # export's: the one text that does not round-trip.
    assert back["engine_claim"].fillna("").tolist() == [
        "-x",
        "",
        "=lost",
        "plain",
        "a",
        "b",
    ]
    assert back["mz"].tolist() == [-1.5, 2.0, 3.0, 4.0, 5.0, 6.0]
