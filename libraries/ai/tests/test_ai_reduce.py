"""``reduce``: a statistic of a Frame's column, the one computation a model
can ask for without Python, in the toolkit and over the MCP server.

Hermetic, on the fake server of ``conftest.py``. The first matrix row showed
why it exists: a model with the MCP server alone declined the top-ions
question for want of any aggregate, and answered another from the first row
of a Frame handle's head.
"""

import json

import pandas as pd
import pytest
from test_ai_mcp import MAX_RESULT_BYTES, ok, refused

from mascope_ai import InvalidRequest, NotFound
from mascope_ai.mcp import Server
from mascope_ai.toolkit import REDUCE_COLUMNS, REDUCE_ROWS, REDUCE_STATS


@pytest.fixture
def server(kit) -> Server:
    return Server(kit.session, make_toolkit=lambda session: kit)


@pytest.fixture
def peaks(kit):
    return kit.load_peaks(batches="Spring 2026")


@pytest.fixture
def scored(kit, peaks):
    """The peaks with a score that differs from row to row: the fake's samples
    are alike in every column, so a reduction by sample cannot tell them
    apart on the data alone."""
    df = peaks.df.copy()
    df["score"] = range(len(df))
    return kit.derive(df, peaks, code="a score, one per row", computed=["score"])


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

    def test_argmin_is_the_other_extreme(self, kit):
        spectrum = kit.load_spectrum("s21", (0, 1000))

        low = kit.reduce(spectrum, value="intensity", stat="argmin")
        high = kit.reduce(spectrum, value="intensity", stat="argmax")

        floor = spectrum.df.loc[spectrum.df["intensity"].idxmin()]
        (row,) = low["rows"]
        assert row["mz"] == floor["mz"] and low["result"] == floor["intensity"]
        assert low["result"] == spectrum.df["intensity"].min() < high["result"]

    def test_the_extreme_row_of_each_group_the_largest_first(self, kit, peaks):
        df = peaks.df

        top = kit.reduce(peaks, value="height", stat="argmax", by="sample_item_name")
        bottom = kit.reduce(peaks, value="height", stat="argmin", by="sample_item_name")

        groups = df.groupby("sample_item_name")["height"]
        assert top["n_groups"] == bottom["n_groups"] == groups.ngroups
        heights = [r["height"] for r in top["rows"]]
        assert heights == sorted(heights, reverse=True)
        assert {r["sample_item_name"]: r["height"] for r in top["rows"]} == (
            groups.max().to_dict()
        )
        assert {r["sample_item_name"]: r["height"] for r in bottom["rows"]} == (
            groups.min().to_dict()
        )
        # The row is the whole row the extreme is in, not the number alone
        assert all(r["mz"] for r in top["rows"])

    def test_the_groups_extreme_rows_come_largest_first(self, kit, scored):
        # The order decides "which sample has the brightest peak": argmax by
        # sample with top=1. The fake's heights are alike; the score is not
        by = dict(value="score", by="sample_item_name")
        highs = scored.df.groupby("sample_item_name")["score"].max()
        lows = scored.df.groupby("sample_item_name")["score"].min()

        top = kit.reduce(scored, stat="argmax", **by)
        up = kit.reduce(scored, stat="argmax", ascending=True, **by)
        first = kit.reduce(scored, stat="argmax", top=1, **by)
        bottom = kit.reduce(scored, stat="argmin", **by)

        scores = [r["score"] for r in top["rows"]]
        assert scores == sorted(highs, reverse=True) and scores[0] > scores[-1]
        assert [r["score"] for r in up["rows"]] == sorted(highs)
        assert first["rows"][0]["sample_item_name"] == highs.idxmax()
        assert first["rows_omitted"] == len(highs) - 1
        assert [r["score"] for r in bottom["rows"]] == sorted(lows, reverse=True)

    def test_ascending_puts_the_smallest_first(self, kit, scored):
        # The fake's samples have the same heights; a score tells them apart
        out = kit.reduce(
            scored, value="score", stat="median", by="sample_item_name", ascending=True
        )
        default = kit.reduce(
            scored, value="score", stat="median", by="sample_item_name"
        )

        scores = [r["score"] for r in out["rows"]]
        assert scores == sorted(scores) and scores[0] < scores[-1]
        assert [r["score"] for r in default["rows"]] == scores[::-1]

    def test_the_extreme_row_leads_with_the_columns_asked_about(self, kit, peaks):
        # A peaks Frame has more columns than a row shows: the group key and
        # the reduced column come first, then the Frame's others in its order
        late = [c for c in peaks.df.columns if list(peaks.df.columns).index(c) >= 12]
        value = next(c for c in late if peaks.df[c].notna().any())

        out = kit.reduce(peaks, value=value, stat="argmax", by="sample_item_name")

        (row, *_) = out["rows"]
        assert list(row)[:2] == ["sample_item_name", value]
        assert len(row) == min(REDUCE_COLUMNS, len(peaks.df.columns))
        others = [c for c in peaks.df.columns if c not in ("sample_item_name", value)]
        assert list(row)[2:] == others[: REDUCE_COLUMNS - 2]

    def test_rows_without_a_value_have_no_extreme(self, kit, peaks):
        df = peaks.df.copy()
        df["score"] = float("nan")
        samples = list(df["sample_item_name"].unique())
        valued = df.index[df["sample_item_name"] == samples[0]][:2]
        df.loc[valued, "score"] = [1.0, 2.0]
        frame = kit.derive(df, peaks, code="a score two rows have", computed=["score"])

        by_sample = kit.reduce(
            frame, value="score", stat="argmax", by="sample_item_name"
        )
        whole = kit.reduce(frame, value="score", stat="argmax")
        none = kit.reduce(
            frame,
            value="score",
            stat="argmin",
            where=[["sample_item_name", "==", samples[1]]],
        )

        # The groups with no value are not there, not an error
        assert [r["sample_item_name"] for r in by_sample["rows"]] == [samples[0]]
        assert by_sample["rows"][0]["score"] == 2.0 and by_sample["n_groups"] == 1
        assert whole["result"] == 2.0
        assert whole["rows_in"] == 2 and whole["rows_of_frame"] == len(df)
        assert none["rows"] == [] and none["result"] is None and none["n_groups"] == 0

    def test_a_repeated_index_still_gives_one_row(self, kit, peaks):
        doubled = kit.derive(
            pd.concat([peaks.df, peaks.df]), peaks, code="the peaks twice", computed=[]
        )

        out = kit.reduce(doubled, value="height", stat="argmax")

        assert len(out["rows"]) == 1 and out["result"] == peaks.df["height"].max()

    def test_each_comparison_keeps_what_it_says(self, kit, peaks):
        heights = peaks.df["height"]
        at = float(heights.iloc[0])  # a value the column has: <= and < differ by it

        def count(op):
            return kit.reduce(
                peaks, value="height", stat="count", where=[["height", op, at]]
            )["result"]

        assert count("==") == int((heights == at).sum()) >= 1
        assert count("!=") == int((heights != at).sum()) == len(heights) - count("==")
        assert count(">") == int((heights > at).sum())
        assert count(">=") == int((heights >= at).sum()) == count(">") + count("==")
        assert count("<") == int((heights < at).sum())
        assert count("<=") == int((heights <= at).sum()) == count("<") + count("==")

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
        with pytest.raises(InvalidRequest, match="to filter on"):
            kit.reduce(peaks, value="height", where=[["nope", ">", 1]])
        with pytest.raises(InvalidRequest, match="where clause is"):
            kit.reduce(peaks, value="height", where=[["height", ">"]])
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

    def test_top_and_ascending_reach_the_toolkit(self, server, scored):
        largest = ok(
            server,
            "reduce",
            frame=scored.id,
            value="score",
            stat="median",
            by=["sample_item_name"],
            top=1,
        )
        smallest = ok(
            server,
            "reduce",
            frame=scored.id,
            value="score",
            stat="median",
            by=["sample_item_name"],
            top=1,
            ascending=True,
        )

        assert len(largest["rows"]) == 1 and largest["rows_omitted"] == 2
        assert largest["rows"][0]["score"] > smallest["rows"][0]["score"]
        assert (
            largest["rows"][0]["sample_item_name"]
            != smallest["rows"][0]["sample_item_name"]
        )

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


class TestLinesMixed:
    """An ion's rows in a peak list are its isotope lines: a statistic of an
    intensity by ion, or filtered to one, takes them together, and the result
    says so. The second matrix row named the wrong top ion three times this
    way, from a median height by ion over the whole peak list."""

    @pytest.fixture
    def lines(self, kit, peaks):
        """The matched peaks with a second, ten times weaker line on every
        ion, as a bromine ion or a 13C line has in a real peak list."""
        df = peaks.df[peaks.df["target_isotope_formula"].notna()]
        minor = df.copy()
        minor["target_isotope_formula"] = "[13C]" + minor["target_isotope_formula"]
        minor["target_isotope_id"] = minor["target_isotope_id"].astype(str) + "-13C"
        minor["height"] = minor["height"] / 10
        return kit.derive(
            pd.concat([df, minor], ignore_index=True),
            peaks,
            code="a minor line per ion",
        )

    @pytest.fixture
    def ion(self, lines):
        return lines.df["target_ion_formula"].iloc[0]

    def test_a_height_by_ion_says_the_lines_it_took_together(self, kit, lines):
        out = kit.reduce(lines, value="height", stat="median", by="target_ion_formula")

        # The fake has ions of several lines already; the minor line makes
        # every ion one of at least two
        per_ion = lines.df.groupby("target_ion_formula")[
            "target_isotope_formula"
        ].nunique()
        mixed = out["lines_mixed"]
        assert mixed["lines_per_group_max"] == per_ion.max() >= 2
        assert mixed["groups_with_several"] == int((per_ion > 1).sum()) == len(per_ion)
        assert "isotope lines" in mixed["note"]
        assert "target_isotope_formula" in mixed["note"]

    def test_a_filter_to_an_ion_mixes_its_lines_per_sample_too(self, kit, lines, ion):
        # The shape of A3's background: a mean height per sample of "the ion"
        out = kit.reduce(
            lines,
            value="height",
            stat="mean",
            by="sample_item_name",
            where=[["target_ion_formula", "==", ion]],
        )

        assert out["lines_mixed"]["lines_per_group_max"] >= 2

    def test_one_line_kept_by_a_where_clause_is_not_mixed(self, kit, lines, ion):
        out = kit.reduce(
            lines,
            value="height",
            stat="median",
            by="target_ion_formula",
            where=[["target_isotope_formula", "==", ion]],
        )

        assert "lines_mixed" not in out
        assert (
            out["rows"][0]["height"]
            == lines.df[lines.df["target_isotope_formula"] == ion]["height"].median()
        )

    def test_grouping_by_the_line_is_not_mixed(self, kit, lines):
        out = kit.reduce(
            lines,
            value="height",
            stat="median",
            by=["target_ion_formula", "target_isotope_formula"],
        )

        assert "lines_mixed" not in out

    def test_a_statistic_that_is_not_an_intensity_says_nothing(self, kit, lines):
        out = kit.reduce(lines, value="mz", stat="mean", by="target_ion_formula")

        assert "lines_mixed" not in out

    def test_one_line_per_ion_says_nothing(self, kit, peaks):
        df = peaks.df
        mono = kit.derive(
            df[df["target_isotope_formula"] == df["target_ion_formula"]],
            peaks,
            code="the main line of each ion",
        )

        out = kit.reduce(mono, value="height", stat="median", by="target_ion_formula")

        assert "lines_mixed" not in out

    def test_the_word_reaches_the_model_over_mcp(self, server, kit, lines):
        out = ok(
            server, "reduce", frame=lines.id, value="height", by=["target_ion_formula"]
        )

        assert out["lines_mixed"]["lines_per_group_max"] >= 2

    def test_the_tool_texts_say_so_and_that_session_runs_nothing(self, server):
        listing = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t["description"] for t in listing["result"]["tools"]}

        assert "lines_mixed" in tools["reduce"] and "isotope lines" in tools["reduce"]
        assert "reduce the returned Frame again" in tools["reduce"]
        assert "runs nothing" in tools["session"] and "reduce" in tools["session"]
