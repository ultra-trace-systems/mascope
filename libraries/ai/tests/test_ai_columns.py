"""What the columns of a loaded table mean: the meanings a Frame and its
handle carry, and that no column a loader makes goes without one.
"""

import pandas as pd

from mascope_ai import Frame, Ref
from mascope_ai.columns import BY_OP, COMMON, COMPUTED, meaning, meanings
from mascope_ai.manifest import CLIENT_COMPUTED


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

    def test_the_three_heights_are_told_apart(self):
        peaks = meaning("load_peaks", "height")
        series = meaning("load_timeseries", "height")
        spectrum = meaning("load_spectrum", "intensity")
        assert "mean" in peaks and "scans" in peaks
        assert "one scan" in series
        assert "counts/s" in spectrum and "apex" in spectrum
        assert len({peaks, series, spectrum}) == 3

    def test_a_stage_is_averaged_over_its_own_scans(self):
        assert "stage's scans" in meaning("load_peaks_by_stage", "height")

    def test_the_trees_scores_are_mascopes_where_a_peak_lists_are_the_sdks(self):
        assert (
            meaning("load_peaks", "match_score_ion")
            == CLIENT_COMPUTED["match_score_ion"]
        )
        assert meaning("match_compounds", "match_score_ion").startswith("Mascope's")

    def test_an_unknown_column_has_none(self):
        assert meaning("load_peaks", "no such column") is None

    def test_every_meaning_fits_a_handle(self):
        texts = [*COMMON.values(), *(t for op in BY_OP.values() for t in op.values())]
        assert all(len(text) <= 160 for text in texts)


class TestFrameMeanings:
    def test_a_loaded_frame_knows_its_columns(self):
        frame = _frame("load_peaks", ["sample_item_id", "mz", "height", "oddity"])
        out = frame.column_meanings()
        assert list(out) == ["sample_item_id", "mz", "height"]
        assert out["height"] == COMMON["height"]

    def test_a_derived_frames_computed_column_says_so_whatever_its_name(self):
        frame = _frame(
            "derive", ["sample_item_id", "height"], computed_columns=["height"]
        )
        out = frame.column_meanings()
        assert out["sample_item_id"] == COMMON["sample_item_id"]
        assert out["height"] == COMPUTED

    def test_meanings_follow_the_frames_op(self):
        frame = _frame("load_timeseries", ["time", "height"])
        assert frame.column_meanings()["height"] == BY_OP["load_timeseries"]["height"]
        assert meanings("load_timeseries", ["time"]) == {"time": COMMON["time"]}


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
