"""
The provenance manifest: every call of a session and every answer, with the
objects touched, the versions, and the split between values Mascope
returned, values the SDK computed client-side and statements the model
inferred. And ``derive``, through which a Frame's sources accumulate.

Hermetic, over conftest.py's recorded server.
"""

import json
import re

import pandas as pd
import pytest

from mascope_ai import (
    AmbiguousName,
    Frame,
    InvalidRequest,
    Manifest,
    Session,
    Toolkit,
)
from mascope_ai.manifest import CLIENT_COMPUTED, COMPUTED


def _calls(kit):
    return kit.session.manifest.records("call")


class TestTheFile:
    def test_it_lives_in_the_session(self, kit):
        kit.find("spring")

        path = kit.session.path / "manifest.jsonl"
        assert kit.session.manifest.path == path
        lines = path.read_text().splitlines()
        assert [json.loads(line)["type"] for line in lines] == ["toolkit", "call"]
        assert [json.loads(line)["seq"] for line in lines] == [0, 1]

    def test_the_toolkit_record_names_the_versions(self, kit):
        from importlib.metadata import version

        kit.find("spring")

        (record,) = kit.session.manifest.records("toolkit")
        assert record["mascope_ai"] == version("mascope_ai")
        assert record["mascope_sdk"] == version("mascope_sdk")
        assert record["python"]
        assert record["server"] == "http://mascope.test"
        assert record["workspace"] == {"id": "ws1", "name": "Workspace A"}
        assert record["session"] == kit.session.id

    def test_a_toolkit_writes_its_record_once(self, kit):
        kit.find("spring")
        kit.find("blank")

        assert len(kit.session.manifest.records("toolkit")) == 1

    def test_a_session_reopened_numbers_on(self, kit, tmp_path):
        kit.find("spring")
        again = Toolkit(
            kit.client, session=Session(kit.session.id, root=tmp_path / "sessions")
        )

        again.find("blank")

        records = again.session.manifest.records()
        assert [r["type"] for r in records] == ["toolkit", "call", "toolkit", "call"]
        assert [r["seq"] for r in records] == [0, 1, 2, 3]

    def test_an_empty_manifest(self, tmp_path):
        manifest = Manifest(tmp_path / "none" / "manifest.jsonl")

        assert manifest.records() == []
        assert manifest.touched() == []
        assert manifest.since_last_answer() == []


class TestCalls:
    def test_each_call_is_written_with_its_parameters(self, kit):
        kit.find(re.compile("^Spring"), kind="batch", limit=5)

        (call,) = _calls(kit)
        assert call["op"] == "find"
        assert call["params"] == {
            "query": {"$regex": "^Spring"},
            "kind": "batch",
            "limit": 5,
            "within": None,
        }
        assert call["outcome"] == "ok"
        assert {ref["name"] for ref in call["refs"]} == {
            "Spring 2026",
            "Spring 2026 repeat",
        }

    def test_a_loads_own_lookups_are_part_of_it(self, kit):
        frame = kit.load_peaks(batches="Spring 2026")

        (call,) = _calls(kit)  # the resolve inside is not a call of its own
        assert call["op"] == "load_peaks"
        assert call["params"]["batches"] == "Spring 2026"
        assert call["frame"] == frame.id
        assert call["refs"] == [ref.to_dict() for ref in frame.source_refs]
        assert call["provenance"] == frame.provenance
        assert call["provenance"]["produced_with"]["mascope_version"]

    def test_a_peak_load_says_which_values_the_sdk_computed(self, kit):
        frame = kit.load_peaks(samples="s21")

        assert frame.client_columns == ("match_score_ion", "match_score_compound")
        (call,) = _calls(kit)
        assert call["values"]["client"] == CLIENT_COMPUTED
        assert "match_score_isotope" in call["values"]["mascope"]
        assert "match_score_ion" not in call["values"]["mascope"]

    def test_so_does_a_stage_load(self, kit):
        frame = kit.load_peaks_by_stage("s21", [(0, 10)])

        assert set(frame.client_columns) == set(CLIENT_COMPUTED)

    def test_a_load_without_matches_has_none_of_them(self, kit):
        frame = kit.load_peaks(samples="s21", matches=False)

        assert frame.client_columns == ()
        assert _calls(kit)[0]["values"]["client"] == {}

    def test_the_trees_scores_are_mascopes(self, kit):
        kit.match_compounds("s21", ["CH2O2"], suggested=["Qq2"])

        (call,) = _calls(kit)
        assert call["values"]["client"] == {}
        assert "match_score_ion" in call["values"]["mascope"]
        assert call["values"]["unscored"][0]["formula"] == "Qq2"
        assert call["refs"][0]["id"] == "s21"

    def test_a_failed_call_is_written_with_its_error(self, kit):
        with pytest.raises(AmbiguousName):
            kit.load_peaks(batches="Spring")

        (call,) = _calls(kit)
        assert call["outcome"]["error"] == "ambiguous"
        assert "refs" not in call and "frame" not in call

    def test_a_describe_touches_its_object(self, kit):
        kit.describe(kit.resolve("Spring 2026", "batch"))

        resolve, describe = _calls(kit)
        assert resolve["op"] == "resolve"
        assert describe["op"] == "describe"
        assert describe["refs"][0]["name"] == "Spring 2026"
        assert "n_samples" in describe["values"]["mascope"]

    def test_touched_lists_every_object_of_the_session_once(self, kit):
        kit.find("Calibration", kind="dataset")
        kit.load_peaks(batches="Spring 2026")
        kit.match_compounds("s11", ["CH2O2"])
        kit.query_by_mz(179.0561, "[M-H]-")
        kit.load_peaks(batches="Spring 2026")

        touched = kit.session.manifest.touched()
        assert [(ref.kind, ref.name) for ref in touched] == [
            ("dataset", "Calibration runs"),
            ("batch", "Spring 2026"),
            ("sample", "Blank 1"),
        ]


class TestDerive:
    def test_sources_accumulate(self, kit):
        spring = kit.load_peaks(batches="Spring 2026")
        blank = kit.load_peaks(batches=["Blank (A)", "Spring 2026"])

        df = (
            pd.concat([spring.df, blank.df])
            .groupby("peak_id", as_index=False)["height"]
            .max()
        )
        derived = kit.derive(df, [spring, blank], code="pd.concat(...)")

        assert isinstance(derived, Frame)
        assert [ref.name for ref in derived.source_refs] == ["Spring 2026", "Blank (A)"]
        assert derived.query == {
            "op": "derive",
            "params": {"from": [spring.id, blank.id], "code": "pd.concat(...)"},
        }
        assert derived.provenance == spring.provenance
        assert kit.session.frame(derived.id).n_rows == len(df)

    def test_a_derived_frame_of_a_derived_frame(self, kit):
        spring = kit.load_peaks(batches="Spring 2026")
        blank = kit.load_peaks(batches="Blank (A)")
        first = kit.derive(spring.df.head(3), spring)

        second = kit.derive(pd.concat([first.df, blank.df]), [first, blank])

        assert [ref.name for ref in second.source_refs] == ["Spring 2026", "Blank (A)"]

    def test_client_side_columns_carry_over(self, kit):
        peaks = kit.load_peaks(samples="s21")

        kept = kit.derive(peaks.df[["peak_id", "match_score_compound"]], peaks)
        dropped = kit.derive(peaks.df[["peak_id", "height"]], peaks)

        assert kept.client_columns == ("match_score_compound",)
        assert dropped.client_columns == ()

    def test_columns_computed_in_python_are_the_codes(self, kit):
        peaks = kit.load_peaks(samples="s21")
        df = (
            peaks.df.groupby("target_compound_formula")["height"]
            .mean()
            .rename("mean_height")
            .reset_index()
        )

        derived = kit.derive(df, peaks, code="groupby(...).mean()")

        assert derived.computed_columns == ("mean_height",)
        values = _calls(kit)[-1]["values"]
        assert values["mascope"] == ["target_compound_formula"]
        assert values["computed"] == {"mean_height": COMPUTED}
        assert values["client"] == {}

    def test_a_column_that_kept_its_name_is_the_codes_when_named(self, kit):
        peaks = kit.load_peaks(samples="s21")
        df = peaks.df.groupby("peak_id", as_index=False)[
            ["height", "match_score_ion"]
        ].mean()

        derived = kit.derive(df, peaks, computed=["height", "match_score_ion"])

        assert derived.computed_columns == ("height", "match_score_ion")
        # Recomputed, the SDK's score is the code's now
        assert derived.client_columns == ()
        assert _calls(kit)[-1]["values"]["mascope"] == ["peak_id"]

    def test_computed_columns_carry_over(self, kit):
        peaks = kit.load_peaks(samples="s21")
        first = kit.derive(peaks.df[["peak_id"]].assign(twice=2.0), peaks)

        second = kit.derive(first.df.head(2), first)
        without = kit.derive(first.df[["peak_id"]], first)

        assert second.computed_columns == ("twice",)
        assert kit.session.frame(second.id).computed_columns == ("twice",)
        assert without.computed_columns == ()

    def test_completeness_and_failures_carry_over(self, kit, api):
        api.fail("GET", r"samples/s22/peaks", 500)
        partial = kit.load_peaks(batches="Spring 2026")
        assert not partial.complete

        derived = kit.derive(partial.df.head(2), partial)

        assert not derived.complete
        assert derived.failures == partial.failures

    def test_its_rows_cannot_be_loaded_again(self, kit):
        peaks = kit.load_peaks(samples="s21")
        derived = kit.derive(peaks.df.head(2), peaks)

        kit.session.evict(derived.id)

        with pytest.raises(InvalidRequest, match="cannot be loaded again"):
            kit.session.frame(derived.id).df

    def test_the_call_is_written_without_the_rows(self, kit):
        peaks = kit.load_peaks(samples="s21")
        kit.derive(peaks.df.head(2), peaks, code="head(2)")

        call = _calls(kit)[-1]
        assert call["op"] == "derive"
        assert call["params"]["df"] == {
            "$dataframe": {"rows": 2, "columns": len(peaks.columns)}
        }
        assert call["params"]["inputs"] == {"$frame": peaks.id}

    @pytest.mark.parametrize("args", [("rows", None), (pd.DataFrame(), [])])
    def test_bad_arguments(self, kit, args):
        with pytest.raises(InvalidRequest):
            kit.derive(*args)

    @pytest.mark.parametrize("computed", ["nope", [1]])
    def test_computed_names_columns_of_the_table(self, kit, computed):
        peaks = kit.load_peaks(samples="s21")

        with pytest.raises(InvalidRequest, match="computed names columns"):
            kit.derive(peaks.df.head(2), peaks, computed=computed)


class TestAnswers:
    def test_an_answer_rests_on_the_calls_before_it(self, kit):
        peaks = kit.load_peaks(samples="s21")
        result = kit.match_compounds("s21", ["CH2O2"], suggested=["Qq2"])

        record = kit.answer(
            "CH2O2 is probable in Sample 1",
            code="kit.match_compounds('s21', ['CH2O2'])",
            inferred="It is formic acid, from the reagent.",
        )

        assert record["type"] == "answer"
        assert record["code"] == "kit.match_compounds('s21', ['CH2O2'])"
        assert record["calls"] == [c["seq"] for c in _calls(kit)]
        assert record["frames"] == [peaks.id, result.tree.id]
        assert [ref["id"] for ref in record["refs"]] == ["s21"]
        values = record["values"]
        assert [v["source"] for v in values["mascope"]] == [peaks.id, result.tree.id]
        assert values["client"] == [
            {"source": peaks.id, "op": "load_peaks", "fields": CLIENT_COMPUTED}
        ]
        assert [u["formula"] for u in values["unscored"]] == ["Qq2"]
        assert values["inferred"] == ["It is formic acid, from the reagent."]
        assert kit.session.manifest.records("answer") == [record]

    def test_the_next_answer_rests_on_the_calls_since(self, kit):
        kit.load_peaks(samples="s21")
        kit.answer("first")
        kit.query_by_mz(179.0561, "[M-H]-")

        record = kit.answer("second", inferred=["a", "b"])

        (call,) = [c for c in _calls(kit) if c["op"] == "query_by_mz"]
        assert record["calls"] == [call["seq"]]
        assert record["frames"] == [] and record["refs"] == []
        assert record["values"]["mascope"][0]["source"] == f"call {call['seq']}"
        assert record["values"]["client"] == []
        assert record["values"]["inferred"] == ["a", "b"]

    def test_a_frame_from_before_counts_when_named(self, kit):
        peaks = kit.load_peaks(batches="Spring 2026")
        kit.answer("first")

        record = kit.answer("second", frames=[peaks.id])

        assert record["frames"] == [peaks.id]
        assert record["refs"][0]["name"] == "Spring 2026"
        assert record["values"]["client"][0]["source"] == peaks.id

    def test_a_frame_made_since_is_listed_once(self, kit):
        peaks = kit.load_peaks(samples="s21")

        record = kit.answer("one", frames=[peaks, peaks.id])

        assert record["frames"] == [peaks.id]
        assert [v["source"] for v in record["values"]["mascope"]] == [peaks.id]

    def test_an_answer_on_a_derived_frame_says_what_the_code_computed(self, kit):
        peaks = kit.load_peaks(samples="s21")
        kit.answer("first")
        derived = kit.derive(
            peaks.df.groupby("peak_id", as_index=False)["height"].max(),
            peaks,
            computed="height",
        )
        kit.answer("second")
        named = kit.derive(peaks.df[["peak_id"]].assign(n=1), peaks)

        record = kit.answer("third", frames=[derived])

        assert record["frames"] == [named.id, derived.id]
        assert record["values"]["computed"] == [
            {"source": named.id, "op": "derive", "fields": {"n": COMPUTED}},
            {"source": derived.id, "op": "derive", "fields": {"height": COMPUTED}},
        ]
        assert [v["fields"] for v in record["values"]["mascope"]] == [
            ["peak_id"],
            ["peak_id"],
        ]

    def test_a_failed_call_counts_but_adds_no_values(self, kit):
        with pytest.raises(AmbiguousName):
            kit.load_peaks(batches="Spring")

        record = kit.answer("nothing")

        assert len(record["calls"]) == 1
        assert record["values"]["mascope"] == []

    @pytest.mark.parametrize(
        "args, kwargs", [((None,), {}), (("text",), {"inferred": [1]})]
    )
    def test_bad_arguments(self, kit, args, kwargs):
        with pytest.raises(InvalidRequest):
            kit.answer(*args, **kwargs)
