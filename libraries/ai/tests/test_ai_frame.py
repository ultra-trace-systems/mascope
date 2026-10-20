"""
The repr invariant: a Frame is a handle whose printed form is a bounded
summary - schema, row count, completeness and five rows - however large or
wide the table, and whose rows come out only through ``.df``.

Hermetic: no server, no SDK client.
"""

import pandas as pd
import pytest

from mascope_ai import FailureNote, Frame, Ref
from mascope_ai.frame import CELL_CHARS, HEAD_COLUMNS, SCHEMA_COLUMNS


BATCH = Ref("b1", "batch", "Batch 1", parent="d1")
QUERY = {"op": "load_peaks", "params": {}}


def _frame(df: pd.DataFrame, **kwargs) -> Frame:
    return Frame.from_df(df, source_refs=[BATCH], query=QUERY, **kwargs)


def _peaks(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "peak_id": [f"p{i}" for i in range(n)],
            "mz": [100.0 + i / 1000 for i in range(n)],
            "height": [float(i) for i in range(n)],
        }
    )


class TestRepr:
    def test_it_prints_the_schema_the_size_completeness_and_five_rows(self):
        frame = _frame(_peaks(400_000))

        text = repr(frame)

        assert "400,000 rows x 3 columns, complete" in text
        assert "peak_id: " in text and "mz: float64" in text
        assert "head (5 of 400,000 rows)" in text
        for i in range(5):
            assert f"p{i} " in text or text.rstrip().endswith(f"p{i}")
        assert "p5 " not in text and "p399999" not in text
        assert "batch 'Batch 1'" in text

    def test_it_stays_short_whatever_the_size(self):
        small, large = repr(_frame(_peaks(10))), repr(_frame(_peaks(400_000)))

        # The row count's digits are the only difference
        assert abs(len(large) - len(small)) < 40

    def test_it_stays_short_however_wide_and_long_the_cells(self):
        wide = pd.DataFrame(
            {
                f"a_very_long_column_name_number_{i:03d}": ["x" * 5000] * 50
                for i in range(300)
            }
        )

        text = repr(_frame(wide))

        assert len(text) < 4000
        assert f"and {300 - SCHEMA_COLUMNS} more" in text
        assert f"first {HEAD_COLUMNS} columns" in text
        assert "x" * CELL_CHARS not in text

    def test_an_incomplete_frame_says_so_and_names_a_failure(self):
        df = _peaks(3)
        df.attrs["complete"] = False
        df.attrs["failures"] = [
            {
                "sample_item_id": "s2",
                "sample_item_name": "Sample 2",
                "error_type": "ServerError",
                "error": "[HTTP 500] boom",
            }
        ]

        frame = _frame(df)

        assert frame.complete is False
        assert frame.failures == (
            FailureNote(
                {"sample_item_id": "s2", "sample_item_name": "Sample 2"},
                "ServerError",
                "[HTTP 500] boom",
            ),
        )
        assert "INCOMPLETE" in repr(frame)
        assert "1 read(s) failed" in repr(frame) and "s2" in repr(frame)

    def test_warnings_are_counted_not_dumped(self):
        df = _peaks(3)
        df.attrs["warnings"] = ["w" * 1000, "second"]

        text = repr(_frame(df))

        assert "2 server warning(s)" in text
        assert "w" * 200 not in text

    def test_str_and_html_are_the_summary(self):
        frame = _frame(_peaks(3))

        assert str(frame) == repr(frame)
        assert frame._repr_html_().startswith("<pre>")
        assert (
            "<script>"
            not in _frame(
                pd.DataFrame({"name": ["<script>alert(1)</script>"]})
            )._repr_html_()
        )

    def test_an_empty_frame(self):
        text = repr(_frame(pd.DataFrame()))

        assert "0 rows x 0 columns, complete" in text


class TestHandle:
    def test_it_is_no_dataframe(self):
        frame = _frame(_peaks(3))

        assert not isinstance(frame, pd.DataFrame)
        with pytest.raises(TypeError):
            frame["mz"]  # noqa: B018
        with pytest.raises(TypeError):
            iter(frame)

    def test_it_is_immutable(self):
        frame = _frame(_peaks(3))

        with pytest.raises(AttributeError):
            frame.n_rows = 1  # type: ignore[misc]

    def test_df_is_a_copy_of_the_rows(self):
        frame = _frame(_peaks(3))

        df = frame.df
        df.loc[0, "mz"] = -1.0

        assert frame.df.loc[0, "mz"] == 100.0
        assert len(frame) == 3

    def test_df_without_rows_or_session_says_so(self):
        frame = _frame(_peaks(3))
        frame.evict()

        with pytest.raises(RuntimeError, match="no session"):
            frame.df  # noqa: B018

    def test_to_dict_carries_five_rows_at_most(self):
        frame = _frame(_peaks(1000))

        data = frame.to_dict()

        assert len(data["head5"]) == 5
        assert data["n_rows"] == 1000
        assert data["columns"] == ["peak_id", "mz", "height"]
        assert data["source_refs"] == [BATCH.to_dict()]
        assert Frame.from_dict(data).to_dict() == data

    def test_head_values_are_json(self):
        df = pd.DataFrame(
            {
                "when": [pd.Timestamp("2026-01-01", tz="UTC")],
                "ids": [["c1", "c2"]],
                "missing": [float("nan")],
            }
        )

        (row,) = _frame(df).head5

        assert row == {
            "when": "2026-01-01T00:00:00+00:00",
            "ids": "['c1', 'c2']",
            "missing": None,
        }

    def test_provenance_is_read_off_the_attrs(self):
        df = _peaks(1)
        df.attrs["provenance"] = {"deployment_id": "dep", "produced_with": {}}

        assert _frame(df).provenance == {"deployment_id": "dep", "produced_with": {}}
        assert _frame(_peaks(1)).provenance is None


class TestRef:
    def test_round_trip_and_quoting(self):
        ref = Ref("s1", "sample", "Sample 'x'", parent="b1")

        assert Ref.from_dict(ref.to_dict()) == ref
        assert str(ref) == "sample \"Sample 'x'\" (s1)"

    def test_unknown_kind(self):
        with pytest.raises(ValueError, match="kind"):
            Ref("x", "instrument", "X")  # type: ignore[arg-type]
