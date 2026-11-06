"""What the columns of a loaded table mean: the meanings a Frame and its
handle carry, and that no column a loader makes goes without one.
"""

import pandas as pd

from mascope_ai import Frame, Ref
from mascope_ai.columns import BY_OP, COMMON, meaning, meanings
from mascope_ai.manifest import CLIENT_COMPUTED, COMPUTED


def _frame(op: str, columns: list[str], **kwargs) -> Frame:
    df = pd.DataFrame({c: [1.0] for c in columns})
    return Frame.from_df(
        df,
        source_refs=[Ref("s1", "sample", "Sample 1", parent="b1")],
        query={"op": op, "params": {}},
        **kwargs,
    )


class TestMeaning:
    def test_a_column_means_the_same_everywhere_unless_the_load_says_otherwise(
        self,
    ):
        assert meaning("load_peaks", "height") == COMMON["height"]
        assert (
            meaning("load_timeseries", "height") == BY_OP["load_timeseries"]["height"]
        )
        assert meaning(None, "height") == COMMON["height"]

    def test_the_three_heights_are_told_apart_and_share_a_unit(self):
        peaks = meaning("load_peaks", "height")
        series = meaning("load_timeseries", "height")
        spectrum = meaning("load_spectrum", "intensity")
        assert "mean" in peaks and "scans" in peaks
        assert "one scan" in series
        assert "apex" in spectrum
        assert len({peaks, series, spectrum}) == 3
        # One unit per instrument, the averaged spectrum's: never a rate on
        # an Orbitrap, whatever the spectrum route labels it
        assert all(
            "counts (Orbitrap) or ions/s (TOF)" in text
            for text in (peaks, series, spectrum)
        )
        assert "counts/s" not in peaks

    def test_times_are_on_the_files_clock(self):
        assert "file's clock" in meaning("load_timeseries", "time")
        assert "not 0" in meaning("load_timeseries", "time")
        assert "file's acquisition start" in meaning("load_peaks", "datetime_utc")
        assert "moved to t0" in meaning("load_peaks_by_stage", "t_min")

    def test_the_trees_placeholders_are_named(self):
        assert "placeholder" in meaning("match_compounds", "sample_peak_mz")
        assert "ppm" in meaning("match_compounds", "match_mz_error")
        assert "HIGH" in meaning("match_compounds", "resolution")
        assert "sum to about 1" in meaning("load_peaks", "relative_abundance")
        assert "batch point" in meaning("match_compounds", "intensity_ion")

    def test_a_mixed_column_has_no_meaning(self):
        from mascope_ai.columns import MIXED

        assert meaning(MIXED, "height") is None
        inherited = {"height": MIXED, "time": "load_timeseries"}
        assert meanings("derive", ["height", "time"], inherited=inherited) == {
            "time": COMMON["time"]
        }

    def test_a_stage_is_averaged_over_its_own_scans_but_keeps_the_samples_rest(
        self,
    ):
        assert "stage's scans" in meaning("load_peaks_by_stage", "height")
        assert "whole-window" in meaning("load_peaks_by_stage", "sparsity")

    def test_the_trees_scores_and_mz_are_mascopes_where_a_peak_lists_are_not(self):
        assert (
            meaning("load_peaks", "match_score_ion")
            == CLIENT_COMPUTED["match_score_ion"]
        )
        assert meaning("match_compounds", "match_score_ion").startswith("Mascope's")
        assert "measured" in meaning("load_peaks", "mz")
        assert "theoretical" in meaning("match_compounds", "mz")

    def test_a_zero_sparsity_may_be_an_uncomputed_series(self):
        assert "not yet" in meaning("load_peaks", "sparsity")

    def test_the_gates_are_named_on_the_trees_intensities(self):
        # Each fact of what a line keeps under the match tolerances, pinned
        # one by one so a reverted text fails
        assert "outside the m/z tolerance" in meaning(
            "match_compounds", "sample_peak_intensity"
        )
        assert "ratio miss keeps it" in meaning(
            "match_compounds", "sample_peak_intensity"
        )
        assert "m/z-gated lines 0" in meaning("match_compounds", "intensity_ion")
        assert "m/z-gated lines count 0" in meaning(
            "match_compounds", "intensity_compound"
        )
        relative = meaning("match_compounds", "sample_peak_intensity_relative")
        assert "before the m/z gate" in relative and "may exceed 1" in relative
        # Also 0 with a peak: when the main line has none, or no shared scan
        assert "either line has no peak or no shared scan" in relative
        assert "not 0" not in relative

    def test_the_tree_has_no_compound_names_and_a_formula_on_every_row(self):
        assert "always empty" in meaning("match_compounds", "target_compound_name")
        assert "NaN when unmatched" in meaning("load_peaks", "target_compound_name")
        formula = meaning("match_compounds", "target_compound_formula")
        assert "on every row" in formula and "NaN" not in formula
        assert "NaN when unmatched" in meaning("load_peaks", "target_compound_formula")

    def test_peak_id_follows_each_tables_row_rule(self):
        # Every peak has a row; a matched one repeats per line, so rows do
        # not count matched lines
        peaks = meaning("load_peaks", "peak_id")
        assert (
            "when the peak matched several" in peaks and "per matched line" not in peaks
        )
        assert "every point of its series" in meaning("load_timeseries", "peak_id")
        stage = meaning("load_peaks_by_stage", "peak_id")
        assert "per stage" in stage and "matched several" in stage

    def test_a_derived_column_with_no_recorded_source_has_no_meaning(self):
        assert meaning("derive", "height") is None
        assert meanings("derive", ["height", "time"]) == {}

    def test_an_unknown_column_has_none(self):
        assert meaning("load_peaks", "no such column") is None

    def test_every_meaning_is_short_enough_for_a_handle(self):
        # A peak list's two dozen meanings ride on its handle inside the
        # result's byte budget beside the head and the schema
        texts = [*COMMON.values(), *(t for op in BY_OP.values() for t in op.values())]
        assert all(len(text) <= 120 for text in texts), [
            t for t in texts if len(t) > 120
        ]


class TestFrameMeanings:
    def test_a_loaded_frame_knows_its_columns(self):
        frame = _frame("load_peaks", ["sample_item_id", "mz", "height", "oddity"])
        out = frame.column_meanings()
        assert list(out) == ["sample_item_id", "mz", "height"]
        assert out["height"] == COMMON["height"]

    def test_a_derived_frames_computed_column_says_so_whatever_its_name(self):
        frame = _frame(
            "derive",
            ["sample_item_id", "height"],
            computed_columns=["height"],
            inherited={"sample_item_id": "load_peaks", "height": "load_peaks"},
        )
        out = frame.column_meanings()
        assert out["sample_item_id"] == COMMON["sample_item_id"]
        assert out["height"] == COMPUTED

    def test_a_derived_frames_carried_over_column_means_what_its_source_meant(
        self,
    ):
        frame = _frame(
            "derive",
            ["time", "height", "mz"],
            inherited={
                "height": "load_timeseries",
                "mz": "load_spectrum",
                "time": "load_timeseries",
            },
        )
        out = frame.column_meanings()
        assert out["height"] == BY_OP["load_timeseries"]["height"]
        assert out["mz"] == BY_OP["load_spectrum"]["mz"]
        assert out["time"] == COMMON["time"]

    def test_inherited_survives_the_sessions_record(self):
        frame = _frame("derive", ["height"], inherited={"height": "load_timeseries"})
        again = Frame.from_dict(frame.to_dict())
        assert again.inherited == {"height": "load_timeseries"}
        assert again.column_meanings() == frame.column_meanings()

    def test_a_derived_frame_saved_before_inherited_gives_no_meaning(self):
        # A session written before columns recorded their source has derived
        # Frames with no inherited: their columns are not the peak list's
        frame = _frame("derive", ["time", "height", "rank"], computed_columns=["rank"])
        record = frame.to_dict()
        del record["inherited"]
        older = Frame.from_dict(record)
        assert older.inherited == {}
        assert older.column_meanings() == {"rank": COMPUTED}

    def test_meanings_follow_the_frames_op(self):
        frame = _frame("load_timeseries", ["time", "height"])
        assert frame.column_meanings()["height"] == BY_OP["load_timeseries"]["height"]
        assert meanings("load_timeseries", ["time"]) == {"time": COMMON["time"]}


class TestDerive:
    def test_a_time_series_filtered_by_time_keeps_one_scans_height(self, kit):
        formula = kit.load_peaks(samples="s21").df["target_compound_formula"].dropna()
        series = kit.load_timeseries(compound=formula.iloc[0], batches="Spring 2026")
        early = kit.derive(
            series.df[series.df["time"] < 5], series, code="series.df[time < 5]"
        )
        assert early.inherited["height"] == "load_timeseries"
        meanings_ = early.column_meanings()
        assert meanings_["height"] == BY_OP["load_timeseries"]["height"]
        assert meanings_["datetime_utc"] == BY_OP["load_timeseries"]["datetime_utc"]

    def test_a_derived_frames_source_carries_on_to_the_next_derive(self, kit):
        spectrum = kit.load_spectrum("s21", mz_range=(100, 101))
        window = kit.derive(spectrum.df.head(3), spectrum, code="head")
        top = kit.derive(
            window.df.nlargest(1, "intensity").assign(rank=1),
            window,
            code="nlargest",
        )
        assert top.inherited == {"mz": "load_spectrum", "intensity": "load_spectrum"}
        out = top.column_meanings()
        assert out["intensity"] == BY_OP["load_spectrum"]["intensity"]
        assert out["rank"] == COMPUTED

    def test_a_column_two_inputs_mean_differently_has_no_meaning(self, kit):
        from mascope_ai.columns import MIXED

        peaks = kit.load_peaks(samples="s21")
        formula = peaks.df["target_compound_formula"].dropna().iloc[0]
        series = kit.load_timeseries(compound=formula, samples="s21")
        areas = peaks.df[["peak_id", "area"]].drop_duplicates()
        joined = series.df.merge(areas, on="peak_id")
        # Whichever input comes first: both inputs have a height and an mz,
        # and mean them differently, so the joined table's have no meaning
        for inputs in ([peaks, series], [series, peaks]):
            frame = kit.derive(joined, inputs, code="merge")
            assert frame.inherited["height"] == MIXED
            assert frame.inherited["mz"] == MIXED
            out = frame.column_meanings()
            assert "height" not in out and "mz" not in out
            # Columns the inputs agree on, or only one has, keep theirs
            assert out["time"] == COMMON["time"]
            assert out["area"] == COMMON["area"]
            assert frame.inherited["area"] == "load_peaks"
            # peak_id follows each table's row rule, so it is mixed too; a
            # column both inputs mean alike takes the first input's op
            assert frame.inherited["peak_id"] == MIXED
            assert frame.inherited["sample_item_id"] == inputs[0].query["op"]
            assert out["sample_item_id"] == COMMON["sample_item_id"]

    def test_two_inputs_that_agree_keep_the_meaning(self, kit):
        a = kit.load_peaks(samples="s21")
        b = kit.load_peaks(samples="s22")
        both = kit.derive(pd.concat([a.df, b.df]), [a, b], code="concat")
        assert both.inherited["height"] == "load_peaks"
        assert both.column_meanings()["height"] == COMMON["height"]

    def test_a_column_recomputed_under_its_old_name_is_the_codes(self, kit):
        peaks = kit.load_peaks(samples="s21")
        means = kit.derive(
            peaks.df.groupby("sample_item_id", as_index=False)["height"].mean(),
            peaks,
            code="mean",
            computed="height",
        )
        assert "height" not in means.inherited
        assert means.column_meanings()["height"] == COMPUTED


class TestEveryLoadedColumnHasAMeaning:
    """A loader's table has no column the toolkit cannot explain: a new
    column from the SDK or the server lands here first."""

    def _unexplained(self, frame: Frame) -> set[str]:
        return set(frame.columns) - set(frame.column_meanings())

    def test_peaks(self, kit):
        assert self._unexplained(kit.load_peaks(batches="Spring 2026")) == set()

    def test_timeseries(self, kit):
        formula = kit.load_peaks(samples="s21").df["target_compound_formula"].dropna()
        frame = kit.load_timeseries(compound=formula.iloc[0], batches="Spring 2026")
        assert frame.n_rows > 0
        assert self._unexplained(frame) == set()

    def test_spectrum(self, kit):
        assert self._unexplained(kit.load_spectrum("s21")) == set()

    def test_peaks_by_stage(self, kit):
        frame = kit.load_peaks_by_stage("s21", [(0, 5, "first"), (5, 10)])
        assert self._unexplained(frame) == set()

    def test_match_tree(self, kit):
        tree = kit.match_compounds("s21", ["CH2O2"]).tree
        assert tree.n_rows > 0
        assert self._unexplained(tree) == set()
        assert "theoretical" in tree.column_meanings()["mz"]
