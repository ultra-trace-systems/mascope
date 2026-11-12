"""Tests for an ion's diagnostic isotopologues and how a batch's lines hold
them (``mascope_tools.composition.diagnostic_isotopologues``, step 4.2's
second rider)."""

import math

import pytest

from mascope_tools.composition import diagnostic_isotopologues
from mascope_tools.composition.diagnostic_isotopologues import (
    DIAGNOSTIC_ISOTOPES,
    HOLD_WIDTHS,
    MAX_SPAN,
    MIN_JUDGED,
    REASON_FEW_SPECTRA,
    SHARE_WIDTH_FLOOR,
    TRACK_SHARE,
    VERDICT_NOT_STAMPED,
    VERDICT_STAMPED,
    VERDICT_UNTESTABLE,
    RatioReading,
    ion_charge,
    judge_ratio,
    predict_diagnostic_isotopologues,
    read_track,
    stamps_in,
)
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance


#: One heavy atom's ratio to its light one, as the isotope tables give them.
BR81 = 0.4931 / 0.5069
CL37 = 0.2424 / 0.7576
S34 = 0.0425 / 0.9499
N15 = 0.00364 / 0.99636


def by_label(ion, resolution=None):
    return {s.label: s for s in predict_diagnostic_isotopologues(ion, resolution)}


class TestTheCharge:
    @pytest.mark.parametrize(
        "ion, charge",
        [
            ("C6H13O6+", 1),
            ("C2HO4-", -1),
            ("C10H22N2O4++", 2),
            ("C5H9N2O7^N-", -1),
            # A count before the sign is the last element's, not the charge's.
            ("C5H8NO4^NO3-", -1),
            ("C6H12O6", None),
            (None, None),
        ],
    )
    def test_the_sign_once_per_charge(self, ion, charge):
        assert ion_charge(ion) == charge


class TestTheDiagnosticIsotopologues:
    def test_each_element_the_ion_holds_a_natural_atom_of(self):
        found = by_label("C5H11NO2S+")
        assert list(found) == ["15N", "18O", "34S"]
        assert found["34S"].ratio == pytest.approx(S34, rel=1e-2)
        assert found["15N"].ratio == pytest.approx(N15, rel=1e-2)

    def test_the_ratio_grows_with_the_element_count(self):
        one = by_label("C6H5BrO-")["81Br"]
        two = by_label("C6H4Br2O-")["81Br"]
        assert one.ratio == pytest.approx(BR81, rel=1e-3)
        assert two.ratio == pytest.approx(2 * BR81, rel=1e-3)
        assert by_label("C6H5ClO-")["37Cl"].ratio == pytest.approx(CL37, rel=1e-3)

    def test_the_offset_is_the_heavy_atoms_mass_over_the_charge(self):
        assert by_label("C6H5BrO-")["81Br"].mz_offset == pytest.approx(
            1.997953, abs=2e-5
        )
        assert by_label("C10H22N2O4++")["15N"].mz_offset == pytest.approx(
            0.997035 / 2, abs=2e-5
        )

    def test_a_labelled_atom_is_the_labels_not_a_natural_one(self):
        # One natural nitrogen beside the label: the 15N line is that one's,
        # read from the labelled line, and the label adds none.
        found = by_label("C10H18NO7^N-")
        assert found["15N"].ratio == pytest.approx(N15, rel=1e-2)
        assert found["15N"].mz_offset == pytest.approx(0.997035, abs=2e-5)
        assert by_label("C5H9N2O7^N-")["15N"].ratio == pytest.approx(2 * N15, rel=1e-2)
        # No natural nitrogen, no 15N isotope line.
        assert "15N" not in by_label("C10H17O7^N-")

    def test_an_ion_without_a_charge_or_a_diagnostic_element_has_none(self):
        assert predict_diagnostic_isotopologues("C6H12O6") == ()
        assert predict_diagnostic_isotopologues("C16H33+") == ()
        assert predict_diagnostic_isotopologues("") == ()

    def test_every_label_is_one_the_predictor_writes(self):
        found = by_label("C2H5NO3SSi2ClBr+")
        assert set(found) == set(DIAGNOSTIC_ISOTOPES)


class TestResolution:
    def test_a_line_one_peak_of_the_files_width_merges_is_unresolved(self):
        # At a TOF's width the 15N line sits inside the 13C peak.
        tof = by_label("C10H19N2O5+", 3000.0)["15N"]
        assert not tof.resolved
        assert tof.ratio > 10 * tof.own_ratio
        orbitrap = by_label("C10H19N2O5+", 120000.0)["15N"]
        assert orbitrap.resolved
        assert orbitrap.ratio == pytest.approx(orbitrap.own_ratio, rel=1e-3)

    def test_a_resolved_line_is_read_at_the_peak_it_falls_in(self):
        # Two silicon lines a peak holds together with the chlorine one: the
        # chlorine line carries the peak, the 30Si line does not.
        found = by_label("C2H6O3Si2Cl+", 120000.0)
        assert found["37Cl"].resolved
        assert not found["30Si"].resolved
        assert found["37Cl"].ratio > found["37Cl"].own_ratio
        assert found["37Cl"].mz_offset == found["30Si"].mz_offset

    def test_no_resolution_merges_nothing(self):
        found = by_label("C10H19N2O5+")["15N"]
        assert found.resolved
        assert found.ratio == found.own_ratio


class TestOneSpectrum:
    def test_a_ratio_on_its_prediction_holds(self):
        reading = judge_ratio(BR81, BR81)
        assert reading.holds and reading.informative
        assert reading.deviation == 0.0

    def test_a_ratio_outside_two_widths_does_not(self):
        # Today's tolerance: a 5% floor of the share, so two widths is 10%.
        assert judge_ratio(BR81 * 1.09, BR81).holds
        assert not judge_ratio(BR81 * 1.11, BR81).holds
        assert not judge_ratio(BR81 * 0.89, BR81).holds
        assert judge_ratio(BR81 * 0.89, BR81).deviation < 0

    def test_the_noise_of_both_peaks_widens_it(self):
        assert not judge_ratio(BR81 * 1.3, BR81).holds
        assert judge_ratio(BR81 * 1.3, BR81, parent_snr=10.0, line_snr=10.0).holds

    def test_an_unusable_noise_widens_nothing(self):
        for snr in (0.0, -1.0, math.nan, math.inf):
            reading = judge_ratio(BR81 * 1.3, BR81, parent_snr=snr, line_snr=snr)
            assert not reading.holds

    def test_the_runs_fitted_curve_admits_a_diluted_line(self):
        # An Orbitrap file reads a dim line low; its curve opens the band down
        # to 0.6 of the share at an expected S/N under 10.
        curve = EnvelopeTolerance(snr_edges=(3.0, 10.0), lower=(0.6, 0.9))
        observed = 0.7 * S34
        assert not judge_ratio(observed, S34, parent_snr=1000.0, line_snr=1000.0).holds
        assert judge_ratio(
            observed, S34, tolerance=curve, parent_snr=100.0, line_snr=1000.0
        ).holds
        # The curve is read at the line's expected S/N: 0.9 from 10 up.
        assert not judge_ratio(
            observed, S34, tolerance=curve, parent_snr=1000.0, line_snr=1000.0
        ).holds

    def test_a_tofs_centre_moves_the_band(self):
        centre = EnvelopeTolerance(centre=0.2)
        assert not judge_ratio(BR81 * 1.2, BR81).holds
        assert judge_ratio(BR81 * 1.2, BR81, tolerance=centre).holds

    def test_a_line_under_the_absolute_floor_cannot_tell(self):
        # A 15N line of 0.4% read at the score's absolute floor: two widths
        # reach past half of it whatever the noise.
        dim = judge_ratio(N15, N15, parent_snr=1e6, line_snr=1e6)
        assert dim.holds and not dim.informative
        assert 2 * SHARE_WIDTH_FLOOR > MAX_SPAN * N15
        # A bright line can, until its noise reaches half its share.
        assert judge_ratio(BR81, BR81).informative
        assert not judge_ratio(BR81, BR81, parent_snr=3.0, line_snr=3.0).informative
        assert judge_ratio(BR81, BR81, parent_snr=20.0, line_snr=20.0).informative

    def test_informative_is_read_at_the_prediction_not_the_reading(self):
        # A peak far brighter than the line would be has a wide width of its
        # own, which must not make the spectrum uninformative.
        reading = judge_ratio(20 * S34, S34, parent_snr=50.0, line_snr=50.0)
        assert reading.informative and not reading.holds

    def test_a_stamp_needs_both(self):
        assert stamps_in(judge_ratio(BR81, BR81))
        assert not stamps_in(judge_ratio(N15, N15))
        assert not stamps_in(judge_ratio(2 * BR81, BR81))


def reading(holds=True, informative=True):
    return RatioReading(1.0, 1.0, 0.0, informative, holds)


class TestTheTrack:
    def test_too_few_informative_spectra_are_untestable(self):
        track = read_track(
            [reading()] * (MIN_JUDGED - 1) + [reading(informative=False)] * 5
        )
        assert track.verdict == VERDICT_UNTESTABLE
        assert track.reason == REASON_FEW_SPECTRA
        assert track.judged == MIN_JUDGED - 1
        assert track.uninformative == 5

    def test_a_share_at_the_bar_is_stamped(self):
        held = int(TRACK_SHARE * 10)
        track = read_track([reading()] * held + [reading(holds=False)] * (10 - held))
        assert track.verdict == VERDICT_STAMPED
        assert track.share == pytest.approx(held / 10)

    def test_a_share_under_it_is_not(self):
        held = int(TRACK_SHARE * 10) - 1
        track = read_track([reading()] * held + [reading(holds=False)] * (10 - held))
        assert track.verdict == VERDICT_NOT_STAMPED
        assert track.reason is None

    def test_an_uninformative_spectrum_counts_for_neither(self):
        track = read_track(
            [reading()] * MIN_JUDGED + [reading(holds=False, informative=False)] * 50
        )
        assert track.verdict == VERDICT_STAMPED
        assert track.judged == MIN_JUDGED
        assert track.holds == MIN_JUDGED
        assert track.uninformative == 50

    def test_the_record(self):
        track = read_track([reading()] * 4 + [reading(holds=False)])
        assert track.to_json() == {
            "judged": 5,
            "holds": 4,
            "uninformative": 0,
            "share": 0.8,
            "verdict": VERDICT_STAMPED,
            "reason": None,
        }
        assert read_track([]).share is None

    def test_the_hold_width_is_the_constant(self, monkeypatch):
        monkeypatch.setattr(diagnostic_isotopologues, "HOLD_WIDTHS", 3.0)
        assert judge_ratio(BR81 * 1.14, BR81).holds
        assert HOLD_WIDTHS == 2.0
