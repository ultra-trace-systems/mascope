"""``reduce``: a statistic of a Frame's column, the one computation a model
can ask for without Python, in the toolkit and over the MCP server.

Hermetic, on the fake server of ``conftest.py``. The first matrix row showed
why it exists: a model with the MCP server alone declined the top-ions
question for want of any aggregate, and answered another from the first row
of a Frame handle's head.
"""

import json

import pytest
from test_ai_mcp import MAX_RESULT_BYTES, ok, refused

from mascope_ai import InvalidRequest, NotFound
from mascope_ai.mcp import Server
from mascope_ai.toolkit import REDUCE_ROWS, REDUCE_STATS


@pytest.fixture
def server(kit) -> Server:
    return Server(kit.session, make_toolkit=lambda session: kit)


@pytest.fixture
def peaks(kit):
    return kit.load_peaks(batches="Spring 2026")


class TestReduce:
    def test_a_statistic_of_the_whole_frame(self, kit, peaks):
        out = kit.reduce(peaks, value="height", stat="max")

        assert out["stat"] == "max" and out["by"] == []
        assert out["result"] == peaks.df["height"].max()
        assert out["rows"] == [{"height": out["result"], "n": len(peaks.df)}]
        assert out["rows_in"] == out["rows_of_frame"] == len(peaks.df)
        assert out["frame"].startswith("f_")

    def test_by_group_the_largest_first_and_the_top_few(self, kit, peaks):
        out = kit.reduce(
            peaks, value="height", stat="median", by="sample_item_name", top=2
        )

        assert [r["sample_item_name"] for r in out["rows"]] == [
            r["sample_item_name"]
            for r in sorted(out["rows"], key=lambda r: -r["height"])
        ]
        assert len(out["rows"]) == 2
        assert out["n_groups"] == peaks.df["sample_item_name"].nunique()
        assert out["rows_omitted"] == out["n_groups"] - 2
        assert all(r["n"] > 0 for r in out["rows"])
        expected = peaks.df.groupby("sample_item_name")["height"].median()
        for row in out["rows"]:
            assert row["height"] == expected[row["sample_item_name"]]

    def test_argmax_is_the_row_the_extreme_is_in(self, kit):
        spectrum = kit.load_spectrum("s21", (0, 1000))

        out = kit.reduce(spectrum, value="intensity", stat="argmax")

        apex = spectrum.df.loc[spectrum.df["intensity"].idxmax()]
        (row,) = out["rows"]
        assert row["mz"] == apex["mz"] and row["intensity"] == apex["intensity"]
        assert out["result"] == apex["intensity"]

    def test_where_keeps_rows_first(self, kit, peaks):
        threshold = float(peaks.df["height"].median())
        out = kit.reduce(
            peaks,
            value="height",
            stat="count",
            where=[
                ["height", ">", threshold],
                ["sample_item_name", "in", ["Sample 1"]],
            ],
        )

        kept = peaks.df[
            (peaks.df["height"] > threshold)
            & (peaks.df["sample_item_name"] == "Sample 1")
        ]
        assert out["rows_in"] == len(kept) and out["result"] == len(kept)
        assert out["where"] == [
            ["height", ">", threshold],
            ["sample_item_name", "in", ["Sample 1"]],
        ]

    def test_nothing_kept_is_an_empty_answer_not_an_error(self, kit, peaks):
        out = kit.reduce(peaks, value="height", where=[["height", "<", -1]])

        assert out["rows"] == [] and out["result"] is None and out["n_groups"] == 0

    def test_by_its_id_and_the_reduction_is_a_frame_of_the_session(self, kit, peaks):
        out = kit.reduce(peaks.id, value="height", stat="mean", by="sample_item_name")

        derived = kit.session.frame(out["frame"])
        assert derived.query["op"] == "derive"
        assert (
            "reduce mean of 'height' by ['sample_item_name']"
            in derived.query["params"]["code"]
        )
        assert set(derived.source_refs) == set(peaks.source_refs)
        assert set(derived.computed_columns) == {"height", "n"}
        assert len(derived.df) == out["n_groups"]

    def test_what_is_refused(self, kit, peaks):
        with pytest.raises(NotFound):
            kit.reduce("f_000000000000", value="height")
        with pytest.raises(InvalidRequest, match="no column"):
            kit.reduce(peaks, value="nope")
        with pytest.raises(InvalidRequest, match="stat is one of"):
            kit.reduce(peaks, value="height", stat="mode")
        with pytest.raises(InvalidRequest, match="op is one of"):
            kit.reduce(peaks, value="height", where=[["height", "~", 1]])
        with pytest.raises(InvalidRequest, match=f"top is 1 to {REDUCE_ROWS}"):
            kit.reduce(
                peaks, value="height", by="sample_item_name", top=REDUCE_ROWS + 1
            )
        assert "argmax" in REDUCE_STATS

    def test_the_call_is_in_the_manifest(self, kit, peaks):
        kit.reduce(peaks, value="height", stat="sum")

        calls = [c for c in kit.session.manifest.records("call") if c["op"] == "reduce"]
        assert calls and calls[-1]["params"]["stat"] == "sum"


class TestReduceOverMcp:
    def test_the_tool_answers_with_a_few_numbers(self, server, kit):
        handle = ok(server, "load_peaks", batches=["Spring 2026"])

        out = ok(
            server,
            "reduce",
            frame=handle["frame"],
            value="height",
            stat="median",
            by=["sample_item_name"],
            top=3,
        )

        assert out["stat"] == "median" and len(out["rows"]) <= 3
        assert out["n_groups"] == 3
        assert len(json.dumps(out).encode()) <= MAX_RESULT_BYTES
        assert out["frame"].startswith("f_")

    def test_argmax_over_mcp_and_a_where_clause(self, server):
        handle = ok(server, "load_spectrum", sample="s21")

        out = ok(
            server, "reduce", frame=handle["frame"], value="intensity", stat="argmax"
        )

        (row,) = out["rows"]
        assert row["intensity"] == out["result"]
        counted = ok(
            server,
            "reduce",
            frame=handle["frame"],
            value="intensity",
            stat="count",
            where=[["mz", ">", row["mz"]]],
        )
        assert counted["result"] < handle["rows"]

    def test_refusals_come_back_as_errors(self, server):
        handle = ok(server, "load_peaks", batches=["Spring 2026"])

        assert refused(server, "reduce", frame="f_000000000000", value="height")
        assert refused(server, "reduce", frame=handle["frame"], value="nope")
        assert refused(
            server, "reduce", frame=handle["frame"], value="height", stat="mode"
        )

    def test_the_handle_says_where_to_compute(self, server):
        listing = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in listing["result"]["tools"]}

        assert "reduce" in tools
        assert "reduce" in tools["load_peaks"]["description"]
        assert tools["reduce"]["inputSchema"]["required"] == ["frame", "value"]
