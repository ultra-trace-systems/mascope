import numpy as np
import pandas as pd

from mascope_match.compute.isotopes import (
    assign_defaults_to_unmatched,
    calculate_match_stats,
)
from mascope_match.params import unmatched_isotope_params


class TestCalculateMatchStats:
    def test_non_finite_ratios_are_replaced_with_zero(self):
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_1", "ion_1"],
                "relative_abundance": [1.0, 0.5, 0.1],
                "sample_peak_intensity": [0.0, 10.0, -10.0],
                "mz": [100.0, 101.0, 102.0],
                "sample_peak_mz": [100.0, 101.0, 102.0],
            }
        )

        result = calculate_match_stats(match_isotope_df)

        np.testing.assert_allclose(
            result["sample_peak_intensity_relative"].to_numpy(),
            np.array([0.0, 0.0, 0.0]),
        )
        assert np.isfinite(result["sample_peak_intensity_relative"]).all()

    def test_ratios_are_preserved(self):
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_1"],
                "relative_abundance": [1.0, 0.5],
                "sample_peak_intensity": [20.0, 10.0],
                "mz": [100.0, 101.0],
                "sample_peak_mz": [100.0, 101.0],
            }
        )

        result = calculate_match_stats(match_isotope_df)

        np.testing.assert_allclose(
            result["sample_peak_intensity_relative"].to_numpy(),
            np.array([1.0, 0.5]),
        )

    def test_existing_reference_overrides_matching_ions_only(self):
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_1", "ion_2", "ion_2"],
                "relative_abundance": [1.0, 0.5, 1.0, 0.5],
                "sample_peak_intensity": [100.0, 50.0, 20.0, 10.0],
                "mz": [100.0, 101.0, 200.0, 201.0],
                "sample_peak_mz": [100.0, 101.0, 200.0, 201.0],
            }
        )
        existing_reference_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1"],
                "sample_peak_intensity": [0.0],
                "relative_abundance": [1.0],
            }
        )

        result = calculate_match_stats(match_isotope_df, existing_reference_df)

        np.testing.assert_allclose(
            result["sample_peak_intensity_relative"].to_numpy(),
            np.array([0.0, 0.0, 1.0, 0.5]),
        )

    def test_perfect_match_scores_one(self):
        # Intensities exactly proportional to relative abundances and exact
        # m/z agreement: zero errors, match score 1 for every isotope.
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_1"],
                "relative_abundance": [1.0, 0.25],
                "sample_peak_intensity": [200.0, 50.0],
                "mz": [100.0, 101.0],
                "sample_peak_mz": [100.0, 101.0],
            }
        )

        result = calculate_match_stats(match_isotope_df)

        np.testing.assert_allclose(result["match_abundance_error"], [0.0, 0.0])
        np.testing.assert_allclose(result["match_mz_error"], [0.0, 0.0])
        np.testing.assert_allclose(result["match_score"], [1.0, 1.0])

    def test_abundance_error_known_answer(self):
        # Main isotope: intensity 100 at rel_ab 1.0. Second isotope measured
        # at 30 but expected at 0.5 relative -> relative intensity 0.3,
        # abundance error 0.3/0.5 - 1 = -0.4, score 1 - |-0.4| = 0.6.
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_1"],
                "relative_abundance": [1.0, 0.5],
                "sample_peak_intensity": [100.0, 30.0],
                "mz": [100.0, 101.0],
                "sample_peak_mz": [100.0, 101.0],
            }
        )

        result = calculate_match_stats(match_isotope_df)

        np.testing.assert_allclose(result["match_abundance_error"], [0.0, -0.4])
        np.testing.assert_allclose(result["match_score"], [1.0, 0.6])

    def test_mz_error_ppm_and_score_term(self):
        # 50 ppm m/z error: mz_term = 1 - 0.01 * 50 = 0.5. Abundance is
        # perfect (single-isotope ion), so the score equals the mz term.
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1"],
                "relative_abundance": [1.0],
                "sample_peak_intensity": [50.0],
                "mz": [200.0],
                "sample_peak_mz": [200.0 * (1 + 50e-6)],
            }
        )

        result = calculate_match_stats(match_isotope_df)

        np.testing.assert_allclose(result["match_mz_error"], [50.0])
        np.testing.assert_allclose(result["match_score"], [0.5])

    def test_mz_error_beyond_100_ppm_scores_zero(self):
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1"],
                "relative_abundance": [1.0],
                "sample_peak_intensity": [50.0],
                "mz": [100.0],
                "sample_peak_mz": [100.0 * (1 + 200e-6)],
            }
        )

        result = calculate_match_stats(match_isotope_df)

        np.testing.assert_allclose(result["match_mz_error"], [200.0])
        np.testing.assert_allclose(result["match_score"], [0.0])

    def test_rows_meet_their_own_ion_on_a_filtered_frame(self):
        # The matched rows of a longer frame, still carrying its labels: the
        # second isotope of the first ion found no peak and was filtered out.
        # Every peak holds its expected share of its own ion's main isotope.
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_2", "ion_2"],
                "relative_abundance": [0.9, 0.8, 0.2],
                "sample_peak_intensity": [900.0, 800.0, 200.0],
                "mz": [100.0, 200.0, 201.0],
                "sample_peak_mz": [100.0, 200.0, 201.0],
            },
            index=[0, 2, 3],
        )

        result = calculate_match_stats(match_isotope_df)

        assert result.index.tolist() == [0, 2, 3]
        np.testing.assert_allclose(
            result["sample_peak_intensity_relative"], [1.0, 1.0, 0.25]
        )
        np.testing.assert_allclose(
            result["match_abundance_error"], [0.0, 0.0, 0.0], atol=1e-12
        )
        np.testing.assert_allclose(result["match_score"], [1.0, 1.0, 1.0])

    def test_an_ion_with_two_references_takes_the_first_and_shifts_no_row(self):
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_1", "ion_2", "ion_2"],
                "relative_abundance": [1.0, 0.5, 1.0, 0.5],
                "sample_peak_intensity": [100.0, 50.0, 20.0, 10.0],
                "mz": [100.0, 101.0, 200.0, 201.0],
                "sample_peak_mz": [100.0, 101.0, 200.0, 201.0],
            }
        )
        existing_reference_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_1"],
                "sample_peak_intensity": [200.0, 50.0],
                "relative_abundance": [1.0, 1.0],
            }
        )

        result = calculate_match_stats(match_isotope_df, existing_reference_df)

        assert len(result) == 4
        np.testing.assert_allclose(
            result["sample_peak_intensity_relative"], [0.5, 0.25, 1.0, 0.5]
        )


class TestAssignDefaultsToUnmatched:
    def test_unmatched_rows_get_defaults_matched_rows_untouched(self):
        match_isotope_df = pd.DataFrame(
            {
                "target_ion_id": ["ion_1", "ion_1"],
                "relative_abundance": [1.0, 0.5],
                "mz": [100.0, 101.0],
                "sample_peak_id": ["peak_7", np.nan],
                "sample_peak_mz": [100.0, np.nan],
                "sample_peak_intensity": [42.0, np.nan],
                "sample_peak_intensity_relative": [1.0, np.nan],
                "match_abundance_error": [0.0, np.nan],
                "match_mz_error": [0.0, np.nan],
                "match_score": [1.0, np.nan],
                "sample_peak_tof": [3.0, np.nan],
            }
        )
        unmatched_mask = match_isotope_df["sample_peak_mz"].isna()

        result = assign_defaults_to_unmatched(match_isotope_df, unmatched_mask)

        # The unmatched isotope reports its own target m/z as the peak m/z
        # and the documented defaults everywhere else.
        assert result.loc[1, "sample_peak_mz"] == 101.0
        assert (
            result.loc[1, "sample_peak_id"] == unmatched_isotope_params.sample_peak_id
        )
        assert result.loc[1, "match_score"] == unmatched_isotope_params.match_score
        assert (
            result.loc[1, "match_abundance_error"]
            == unmatched_isotope_params.match_abundance_error
        )
        # The matched isotope is untouched.
        assert result.loc[0, "sample_peak_id"] == "peak_7"
        assert result.loc[0, "sample_peak_mz"] == 100.0
        assert result.loc[0, "match_score"] == 1.0
