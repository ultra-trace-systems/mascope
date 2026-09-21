"""Unit tests for calibration matching and summary statistics methods."""

import numpy as np
import pandas as pd
from calibration_test_support import get_test_calibration_handler


def _make_isotope_row(mz):
    return pd.Series(
        {
            "mz": mz,
            "sample_peak_mz": np.nan,
            "sample_peak_tof": np.nan,
            "sample_peak_intensity": np.nan,
            "matched_peak_idx": np.nan,
        }
    )


class TestMatchMaxInRange:
    """Tests for _match_max_in_range.

    Handler refine_window (Orbi default after with_defaults) = 10 ppm.
    At target_mz = 100, tolerance = 0.001.

    Expected behaviors:
    - If a single peak is within the refine window, it should be matched.
    - If multiple peaks are within the refine window, the one with the highest intensity wins.
    - If no peaks are within the refine window, the row should be unchanged (NaN values remain).
    - If a peak is exactly on the boundary of the refine window, it should be matched.
    """

    def setup_method(self):
        self.orbi_pos_handler = get_test_calibration_handler("orbitrap", "+")
        self.isotope_row = _make_isotope_row(100.0)

    def test_single_peak_in_range_matched(self):
        peaks = {
            "mz": np.array([100.0005]),
            "tof": np.array([5000.0]),
            "intensity": np.array([42.0]),
        }
        isotope_row = self.orbi_pos_handler._match_max_in_range(self.isotope_row, peaks)

        assert isotope_row["sample_peak_mz"] == 100.0005
        assert isotope_row["sample_peak_tof"] == 5000.0
        assert isotope_row["sample_peak_intensity"] == 42.0

    def test_highest_intensity_wins(self):
        peaks = {
            "mz": np.array([100.0002, 100.0005, 100.0008]),
            "tof": np.array([4998.0, 5000.0, 5002.0]),
            "intensity": np.array([10.0, 99.0, 50.0]),
        }
        isotope_row = self.orbi_pos_handler._match_max_in_range(self.isotope_row, peaks)

        assert isotope_row["sample_peak_mz"] == 100.0005
        assert isotope_row["sample_peak_intensity"] == 99.0

    def test_no_peaks_in_range_row_unchanged(self):
        peaks = {
            "mz": np.array([200.0]),
            "tof": np.array([8000.0]),
            "intensity": np.array([50.0]),
        }
        isotope_row = self.orbi_pos_handler._match_max_in_range(self.isotope_row, peaks)

        assert np.isnan(isotope_row["sample_peak_mz"])
        assert np.isnan(isotope_row["sample_peak_intensity"])

    def test_peak_at_exact_boundary(self):
        target_mz = 100.0
        boundary_mz = (
            target_mz + target_mz * self.orbi_pos_handler.params.refine_window * 1e-6
        )
        peaks = {
            "mz": np.array([boundary_mz]),
            "tof": np.array([5000.0]),
            "intensity": np.array([7.0]),
        }
        isotope_row = self.orbi_pos_handler._match_max_in_range(self.isotope_row, peaks)

        np.testing.assert_allclose(isotope_row["sample_peak_mz"], boundary_mz)


class TestSelectRetainedMatches:
    """Tests for residual-based retained peak selection.

    Expected behaviors:
    - Small samples only drop clearly inconsistent peaks.
    - Ambiguous small samples keep all peaks.
    - Larger samples retain only peaks within the post-fit residual tolerance.
    """

    def setup_method(self):
        self.orbi_pos_handler = get_test_calibration_handler("orbitrap", "+")
        self.orbi_pos_handler._get_old_factor = lambda: 1.0
        self.base_df = pd.DataFrame(
            {
                "mz": [100.0, 200.0, 300.0],
                "sample_peak_mz": [100.0, 200.0, 300.0],
                "sample_peak_tof": [1000.0, 2000.0, 3000.0],
                "match_mz_error": [0.0, 0.0, 0.0],
                "calibrant_to_tic": [0.1, 0.2, 0.3],
                "target_ion_id": ["ion-1", "ion-2", "ion-3"],
            }
        )

    def test_small_sample_consensus_removes_clear_outlier(self):
        df = self.base_df.copy()
        df.loc[2, "sample_peak_mz"] = 330.0

        result = self.orbi_pos_handler._select_retained_matches(df)

        assert len(result) == 2
        assert 330.0 not in result["sample_peak_mz"].values

    def test_small_sample_consensus_keeps_ambiguous_points(self):
        df = self.base_df.copy()
        df["sample_peak_mz"] = [100.00005, 200.00010, 300.00015]

        result = self.orbi_pos_handler._select_retained_matches(df)

        assert len(result) == 3

    def test_large_sample_residual_filter_removes_bad_peak(self):
        df = pd.DataFrame(
            {
                "mz": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0],
                "sample_peak_mz": [100.0, 200.0, 300.0, 400.0, 500.0, 660.0],
                "sample_peak_tof": [1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0],
                "match_mz_error": [0.0] * 6,
                "calibrant_to_tic": [0.1] * 6,
                "target_ion_id": [f"ion-{idx}" for idx in range(6)],
            }
        )

        result = self.orbi_pos_handler._select_retained_matches(df)

        assert len(result) == 5
        assert 660.0 not in result["sample_peak_mz"].values


class TestFitRetainedMatches:
    def setup_method(self):
        self.orbi_pos_handler = get_test_calibration_handler("orbitrap", "+")
        self.orbi_pos_handler._get_old_factor = lambda: 1.0
        self.base_df = pd.DataFrame(
            {
                "mz": [100.0, 200.0, 300.0],
                "sample_peak_mz": [100.0, 200.0, 300.0],
                "sample_peak_tof": [1000.0, 2000.0, 3000.0],
                "match_mz_error": [0.0, 0.0, 0.0],
                "calibrant_to_tic": [0.1, 0.2, 0.3],
                "target_ion_id": ["ion-1", "ion-2", "ion-3"],
            }
        )

    def test_refits_on_retained_matches(self):
        df = self.base_df.copy()
        fit_calls = []

        self.orbi_pos_handler._select_retained_matches = lambda matches_df: (
            matches_df.iloc[:2].copy()
        )

        def fake_fit_matches(matches_df):
            fit_calls.append(len(matches_df))
            scale = float(len(matches_df))
            return {
                "mode": "one-point",
                "par": {
                    "old_factor": 1.0,
                    "old_factor_scaling": scale,
                    "calibration_factor": scale,
                },
            }, {
                "mz": matches_df["mz"].to_numpy(),
                "new_mz": matches_df["sample_peak_mz"].to_numpy() * scale,
                "pre_dmz": np.zeros(len(matches_df)),
                "post_dmz": np.zeros(len(matches_df)),
            }

        def fake_evaluate_fit(matches_df, fit_result):
            scale = fit_result["par"]["old_factor_scaling"]
            return {
                "mz": matches_df["mz"].to_numpy(),
                "new_mz": matches_df["sample_peak_mz"].to_numpy() * scale,
                "pre_dmz": np.zeros(len(matches_df)),
                "post_dmz": np.zeros(len(matches_df)),
            }

        self.orbi_pos_handler._fit_matches = fake_fit_matches
        self.orbi_pos_handler._evaluate_fit = fake_evaluate_fit

        fit_result, calibration_df = self.orbi_pos_handler._fit_retained_matches(df)

        assert fit_calls == [2]
        assert fit_result["par"]["old_factor_scaling"] == 2.0
        assert len(calibration_df) == 2

    def test_empty_retained_matches_returns_none_without_refit(self):
        df = self.base_df.copy()

        self.orbi_pos_handler._select_retained_matches = lambda matches_df: (
            matches_df.iloc[0:0].copy()
        )

        def fail_if_fit_called(matches_df):
            raise AssertionError(
                "_fit_matches should not be called for empty selections"
            )

        self.orbi_pos_handler._fit_matches = fail_if_fit_called
        self.orbi_pos_handler.warning = None

        fit_result, calibration_df = self.orbi_pos_handler._fit_retained_matches(df)

        assert fit_result is None
        assert calibration_df.empty
        assert list(calibration_df.columns) == list(df.columns)
        assert (
            self.orbi_pos_handler.warning
            == "No calibration peaks found after filtering."
        )

    def test_empty_retained_matches_preserves_existing_warning(self):
        df = self.base_df.copy()

        self.orbi_pos_handler._select_retained_matches = lambda matches_df: (
            matches_df.iloc[0:0].copy()
        )

        def fail_if_fit_called(matches_df):
            raise AssertionError(
                "_fit_matches should not be called for empty selections"
            )

        self.orbi_pos_handler._fit_matches = fail_if_fit_called
        self.orbi_pos_handler.warning = "Existing warning"

        fit_result, calibration_df = self.orbi_pos_handler._fit_retained_matches(df)

        assert fit_result is None
        assert calibration_df.empty
        assert self.orbi_pos_handler.warning == "Existing warning"


class TestGetSummaryRow:
    """Tests for _get_summary_row.

    Expected behaviors:
    - The summary row contains the mean absolute match_mz_error and calibration_mz_error.
    - The summary row contains the absolute difference between these two means as mz_error_diff.
    - The summary row contains the sum of calibrant_to_tic.
    - The summary row is correctly calculated even if only one row is present.
    """

    def setup_method(self):
        self.orbi_pos_handler = get_test_calibration_handler("orbitrap", "+")
        self.isotope_row = _make_isotope_row(100.0)

    def test_known_values(self):
        df = pd.DataFrame(
            {
                "match_mz_error": [-2.0, 4.0],
                "calibration_mz_error": [1.0, -3.0],
                "calibrant_to_tic": [0.1, 0.2],
            }
        )
        result = self.orbi_pos_handler._get_summary_row(df)

        # abs(match_mz_error).mean() = (2+4)/2 = 3.0
        assert result["match_mz_error"] == 3.0
        # abs(calibration_mz_error).mean() = (1+3)/2 = 2.0
        assert result["calibration_mz_error"] == 2.0
        # |3.0 - 2.0| = 1.0
        assert result["mz_error_diff"] == 1.0
        # 0.1 + 0.2 = 0.3
        np.testing.assert_almost_equal(result["calibrant_to_tic"], 0.3)

    def test_single_row(self):
        df = pd.DataFrame(
            {
                "match_mz_error": [5.0],
                "calibration_mz_error": [-3.0],
                "calibrant_to_tic": [0.05],
            }
        )
        result = self.orbi_pos_handler._get_summary_row(df)

        assert result["match_mz_error"] == 5.0
        assert result["calibration_mz_error"] == 3.0
        assert result["calibrant_to_tic"] == 0.05
