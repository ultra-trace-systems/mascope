"""Tests for reading a batch's merged rows as other merged ions' isotope lines
(``mascope_tools.composition.merged_isotopologues``, step 4.2f)."""

import math

import pytest

from mascope_tools.composition.diagnostic_isotopologues import (
    DIAGNOSTIC_ISOTOPES,
    predict_diagnostic_isotopologues,
)
from mascope_tools.composition.merged_isotopologues import (
    HIGH_FACTOR,
    MAD_TO_SIGMA,
    MEDIAN_HIGH,
    MEDIAN_LOW,
    MERGED_ROW_ISOTOPES,
    MIN_SPECTRA,
    SCATTER_MIN_MEMBERS,
    VERDICT_ISOTOPOLOGUE,
    VERDICT_OFF_RATIO,
    VERDICT_UNTESTABLE,
    WINDOW_FLOOR_PPM,
    WINDOW_SIGMAS,
    at_position,
    brighter,
    mass_scatter_ppm,
    position_window_ppm,
    predict_isotopologue_lines,
    read_share,
    thresholds,
)


C13_SHIFT = 1.0033548


def by_label(ion, resolution=None):
    return {line.label: line for line in predict_isotopologue_lines(ion, resolution)}


class TestTheLines:
    def test_the_13c_line_first_then_the_diagnostic_ones(self):
        assert MERGED_ROW_ISOTOPES == ("13C", *DIAGNOSTIC_ISOTOPES)
        labels = [line.label for line in predict_isotopologue_lines("C10H15O4S-")]
        assert labels == ["13C", "18O", "34S"]

    def test_the_13c_line_at_its_offset_and_share(self):
        line = by_label("C10H15O4-")["13C"]
        assert line.mz_offset == pytest.approx(C13_SHIFT, abs=1e-5)
        # Ten carbons: about ten times 13C's abundance over 12C's.
        assert line.ratio == pytest.approx(10 * 0.0107 / 0.9893, rel=0.02)
        assert line.resolved

    def test_the_offset_is_per_charge(self):
        assert by_label("C10H22N2O4++")["13C"].mz_offset == pytest.approx(
            C13_SHIFT / 2, abs=1e-5
        )

    def test_the_diagnostic_lines_are_step_4_2cs(self):
        """Adding the 13C line moves none of the others."""
        ion = "C8H6Br2Cl+"
        assert {
            line.label: line for line in predict_diagnostic_isotopologues(ion, 60_000)
        } == {
            label: line
            for label, line in by_label(ion, 60_000).items()
            if label != "13C"
        }

    def test_a_tof_merges_the_15n_line_into_the_13c_peak(self):
        """At a TOF's resolving power the 15N line sits inside the 13C peak,
        which the 13C line dominates: the peak is read as the 13C line, at the
        merged share."""
        open_lines = by_label("C10H16N2O4+")
        merged = by_label("C10H16N2O4+", 5_000)
        assert merged["13C"].resolved
        assert not merged["15N"].resolved
        assert merged["13C"].ratio > open_lines["13C"].ratio

    def test_no_charge_no_lines(self):
        assert predict_isotopologue_lines("C10H16O4") == ()


class TestThePosition:
    def test_the_scatter_is_the_scaled_mad_around_each_rows_centre(self):
        centre = 200.0
        ppm = [-2.0, -1.0, 0.0, 1.0, 2.0]
        row = [centre * (1 + p / 1e6) for p in ppm]
        # The deviations' absolute values are 0, 1, 1, 2, 2 ppm: median 1.
        assert mass_scatter_ppm([row]) == pytest.approx(MAD_TO_SIGMA * 1.0, rel=1e-3)

    def test_rows_at_different_mz_pool_member_for_member(self):
        a = [100.0 * (1 + p / 1e6) for p in (-1, 0, 1, 0, 0)]
        b = [500.0 * (1 + p / 1e6) for p in (-3, 0, 3, 3, -3)]
        # Deviations 1,0,1,0,0 and 3,0,3,3,3 ppm: the median of the ten is 1.
        assert mass_scatter_ppm([a, b]) == pytest.approx(MAD_TO_SIGMA, rel=1e-3)

    def test_a_stray_member_moves_no_centre(self):
        """Each row is centred on its median: one member ten ppm off leaves
        the other four on the centre, and the scatter at the median of the
        five deviations."""
        row = [200.0 * (1 + p / 1e6) for p in (0.0, 0.0, 0.0, 0.0, 10.0)]
        assert mass_scatter_ppm([row]) == pytest.approx(0.0, abs=1e-9)

    def test_a_row_of_too_few_members_does_not_count(self):
        few = [300.0, 300.003, 299.997, 300.0][: SCATTER_MIN_MEMBERS - 1]
        assert mass_scatter_ppm([few]) is None
        assert mass_scatter_ppm([]) is None

    def test_the_window_is_four_sigma_floored_at_one_ppm(self):
        assert WINDOW_SIGMAS == 4.0 and WINDOW_FLOOR_PPM == 1.0
        assert position_window_ppm(0.5) == pytest.approx(2.0)
        assert position_window_ppm(0.1) == WINDOW_FLOOR_PPM
        assert position_window_ppm(None) == WINDOW_FLOOR_PPM
        assert position_window_ppm(math.nan) == WINDOW_FLOOR_PPM
        assert position_window_ppm(math.inf) == WINDOW_FLOOR_PPM

    def test_at_position(self):
        line = 201.10876
        assert at_position(line * (1 + 0.9e-6), line, 1.0)
        assert not at_position(line * (1 + 1.1e-6), line, 1.0)
        assert at_position(line * (1 - 1.9e-6), line, 2.0)


class TestBrighter:
    def test_a_dim_line_needs_a_brighter_parent(self):
        assert brighter(100.0, 99.0, 0.11)
        assert not brighter(100.0, 100.0, 0.11)
        assert not brighter(100.0, 150.0, 0.11)

    def test_a_bromine_line_may_outshine_its_parent_by_its_band(self):
        """An 81Br line is 97 % of its parent; read up to the band's top it
        can be 1.36 times as bright, and is still the parent's line."""
        share = 0.973
        assert brighter(100.0, 130.0, share)
        assert not brighter(100.0, 100.0 * share * MEDIAN_HIGH + 1.0, share)

    def test_no_intensity_no_parent(self):
        assert not brighter(None, 1.0, 0.1)
        assert not brighter(1.0, None, 0.1)


class TestTheShare:
    def test_a_line_at_its_share_is_the_parents_isotopologue(self):
        reading = read_share([0.11, 0.10, 0.12, 0.09], 0.11)
        assert reading.verdict == VERDICT_ISOTOPOLOGUE
        assert reading.spectra == 4
        assert reading.median_factor == pytest.approx(0.105 / 0.11)
        assert reading.above == 0

    @pytest.mark.parametrize(
        "factor, verdict",
        [
            (MEDIAN_LOW, VERDICT_ISOTOPOLOGUE),
            (MEDIAN_LOW * 0.99, VERDICT_OFF_RATIO),
            (MEDIAN_HIGH, VERDICT_ISOTOPOLOGUE),
            (MEDIAN_HIGH * 1.01, VERDICT_OFF_RATIO),
        ],
    )
    def test_the_median_band_is_closed(self, factor, verdict):
        assert read_share([0.1 * factor] * 5, 0.1).verdict == verdict

    def test_a_peak_often_twice_its_share_is_another_species(self):
        # One of ten spectra above twice: not fewer than a tenth.
        ten = [0.1] * 9 + [0.1 * HIGH_FACTOR * 1.5]
        assert read_share(ten, 0.1).verdict == VERDICT_OFF_RATIO
        assert read_share(ten, 0.1).above == 1
        # One of eleven is.
        eleven = [0.1] * 10 + [0.1 * HIGH_FACTOR * 1.5]
        assert read_share(eleven, 0.1).verdict == VERDICT_ISOTOPOLOGUE

    def test_exactly_twice_is_not_above(self):
        assert read_share([0.1] * 9 + [0.2], 0.1).above == 0

    def test_too_few_spectra_are_untestable(self):
        reading = read_share([0.1] * (MIN_SPECTRA - 1), 0.1)
        assert reading.verdict == VERDICT_UNTESTABLE
        assert reading.spectra == MIN_SPECTRA - 1
        assert read_share([0.1] * MIN_SPECTRA, 0.1).verdict == VERDICT_ISOTOPOLOGUE

    def test_a_ratio_that_cannot_be_read_is_left_out(self):
        reading = read_share([0.1, 0.1, math.nan, 0.0, -1.0, None, 0.1], 0.1)
        assert reading.spectra == 3
        assert reading.verdict == VERDICT_ISOTOPOLOGUE

    def test_no_share_is_untestable(self):
        assert read_share([0.1] * 5, 0.0).verdict == VERDICT_UNTESTABLE
        assert read_share([0.1] * 5, -0.1).verdict == VERDICT_UNTESTABLE
        assert read_share([0.1] * 5, math.nan).verdict == VERDICT_UNTESTABLE
        assert read_share([], 0.1).verdict == VERDICT_UNTESTABLE

    def test_closeness_ranks_by_the_log_distance_from_one(self):
        near = read_share([0.1] * 4, 0.11)
        far = read_share([0.1] * 4, 0.2)
        assert near.closeness < far.closeness
        assert read_share([], 0.1).closeness == math.inf

    def test_closeness_is_symmetric_in_the_ratio_not_the_difference(self):
        """A factor of 1.35 is nearer one than 0.7 is: 1.35 over is a smaller
        ratio than 0.7 under, though a larger difference."""
        over = read_share([0.135] * 4, 0.1)
        under = read_share([0.07] * 4, 0.1)
        assert over.closeness < under.closeness

    def test_to_json(self):
        assert read_share([0.1, 0.1, 0.1], 0.1).to_json() == {
            "spectra": 3,
            "median_factor": 1.0,
            "above": 0,
            "verdict": VERDICT_ISOTOPOLOGUE,
        }


def test_the_thresholds_name_every_constant():
    assert thresholds() == {
        "isotopes": list(MERGED_ROW_ISOTOPES),
        "min_spectra": 3,
        "median_low": 0.4,
        "median_high": 1.4,
        "high_factor": 2.0,
        "max_high_share": 0.1,
        "window_floor_ppm": 1.0,
        "window_sigmas": 4.0,
    }
