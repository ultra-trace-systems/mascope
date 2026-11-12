"""Tests for the batch isotope checks' rules
(``mascope_tools.composition.batch_checks``, step 4.2's sixth rider)."""

import math

import pytest

from mascope_tools.composition.batch_checks import (
    ABSENT_SHARE,
    CL37_SPACING,
    LOCK_LOCKED,
    LOCK_NOT_LOCKED,
    LOCK_SILICON,
    LOCK_UNTESTABLE,
    PAIR_ESTABLISHED,
    PAIR_NOT_ESTABLISHED,
    PAIR_UNTESTABLE,
    REASON_ANOTHER_PARENT,
    REASON_BOTH_HALOGENS,
    REASON_NO_OFFSET,
    REASON_NOT_FOUND,
    REASON_NOT_READ,
    REASON_OFFSET,
    REASON_RATIO,
    REASON_SILICON_UNTESTABLE,
    SI29_PER_SI30,
    SI29_SPACING,
    SI30_SPACING,
    TWIN_ABSENT,
    TWIN_PRESENT,
    TWIN_UNTESTABLE,
    SiliconLine,
    SiliconReading,
    TwinSample,
    can_tell,
    core_factor,
    element_count,
    impurity_factor,
    offset_error_ppm,
    pair_partner,
    read_halogen_lock,
    read_neutral_pair,
    read_twin,
    silicon_in_window,
    silicon_line,
    thresholds,
    twin_isotopologue,
    twin_present,
)
from mascope_tools.composition.custom_elements import labelled_purity
from mascope_tools.composition.diagnostic_isotopologues import (
    MIN_JUDGED,
    REASON_FEW_SPECTRA,
    REASON_UNRESOLVED,
    TrackReading,
)
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance


class TestTheSpacings:
    def test_30si_sits_a_fifth_of_a_millidalton_under_37cl(self):
        assert (CL37_SPACING - SI30_SPACING) * 1e3 == pytest.approx(0.206, abs=1e-3)

    def test_a_silicon_reading_puts_its_29si_line_half_again_over_its_30si(self):
        # 4.685 % 29Si against 3.092 % 30Si.
        assert SI29_PER_SI30 == pytest.approx(1.515, abs=1e-3)
        assert SI29_SPACING == pytest.approx(0.99957, abs=1e-5)


class TestElementCount:
    @pytest.mark.parametrize(
        "formula, symbol, count",
        [
            ("C6H9ClO3-", "Cl", 1),
            ("C6H10Cl2O4Br", "Cl", 2),
            ("C6H10Cl2O4Br-", "Br", 1),
            # A labelled atom is the label's, not its element's.
            ("C10H16O4^NO3-", "N", 0),
            ("C10H16NO4^NO3-", "N", 1),
            (None, "Cl", 0),
            ("", "Br", 0),
        ],
    )
    def test_natural_atoms_charge_left_off(self, formula, symbol, count):
        assert element_count(formula, symbol) == count


class TestTheSiliconLine:
    def test_a_one_chlorine_line_read_as_30si_implies_a_29si_line_half_again_as_tall(
        self,
    ):
        line = silicon_line("C6H9ClO3-", 0.32, 120000.0)
        assert line.mz_offset == pytest.approx(SI29_SPACING)
        assert line.own_ratio == pytest.approx(0.32 * SI29_PER_SI30)
        # Resolved from the 13C line, 3.8 mDa above it, on an Orbitrap.
        assert line.ratio == pytest.approx(line.own_ratio)
        assert line.resolved

    def test_on_a_tof_it_merges_with_the_ion_s_13c_line(self):
        line = silicon_line("C6H9ClO3-", 0.32, 5000.0)
        assert line.ratio > line.own_ratio
        # The merged peak sits between the 29Si and the 13C positions.
        assert SI29_SPACING < line.mz_offset < 1.003355
        assert line.resolved

    def test_a_doubly_charged_ion_halves_the_spacing(self):
        line = silicon_line("C12H18Cl2O6--", 0.64, None)
        assert line.mz_offset == pytest.approx(SI29_SPACING / 2)

    @pytest.mark.parametrize(
        "ion, ratio", [("C6H9ClO3", 0.32), ("C6H9ClO3-", 0.0), ("C6H9ClO3-", -1.0)]
    )
    def test_none_without_a_charge_or_a_line(self, ion, ratio):
        assert silicon_line(ion, ratio, None) is None


class TestTheSpacingWindow:
    def test_the_window_cannot_tell_30si_from_37cl_from_m_z_206_up(self):
        assert silicon_in_window(CL37_SPACING, -1, 300.0, 1.0)
        assert not silicon_in_window(CL37_SPACING, -1, 150.0, 1.0)
        # A wider window reaches lower.
        assert silicon_in_window(CL37_SPACING, -1, 150.0, 2.0)

    def test_the_spacing_is_per_charge(self):
        assert silicon_in_window(CL37_SPACING / 2, 2, 300.0, 1.0)
        assert not silicon_in_window(CL37_SPACING, 2, 300.0, 1.0)

    def test_no_charge_or_m_z_is_no_window(self):
        assert not silicon_in_window(CL37_SPACING, None, 300.0, 1.0)
        assert not silicon_in_window(CL37_SPACING, -1, 0.0, 1.0)

    def test_the_offset_error_is_the_median_in_ppm_of_the_parent(self):
        error = offset_error_ppm([2.0001, 2.0, 2.0003, None, math.nan], 2.0, 200.0)
        assert error == pytest.approx(0.5)
        assert offset_error_ppm([], 2.0, 200.0) is None
        assert offset_error_ppm([2.0], 2.0, 0.0) is None


def entry(**kwargs):
    """A 4.2c record entry for a stamped line, overridden by ``kwargs``."""
    return {
        "label": "37Cl",
        "batch_peak_id": "line",
        "resolved": True,
        "judged": 6,
        "holds": 6,
        "share": 1.0,
        "verdict": "stamped",
        "stamped": True,
        **kwargs,
    }


def lock(element="Cl", found=None, **kwargs):
    options = {
        "n_ion": 1,
        "n_neutral": 1,
        "both_halogens": False,
        "offset_error": 0.2,
        "window_ppm": 1.0,
        **kwargs,
    }
    return read_halogen_lock(element, entry(**(found or {})), **options)


def silicon(verdict):
    track = (
        None
        if verdict is None
        else TrackReading(
            6 if verdict != "untestable" else 1,
            6 if verdict == "stamped" else 0,
            0,
            verdict,
        )
    )
    line = SiliconLine(
        mz_offset=SI29_SPACING, ratio=0.48, own_ratio=0.48, resolved=True
    )
    return SiliconReading(line, "si-line" if track else None, track)


class TestTheHalogenLock:
    def test_a_stamped_line_at_the_exact_spacing_locks_its_element(self):
        found = lock("Br", {"label": "81Br"}, n_ion=1, n_neutral=0)
        assert found.verdict == LOCK_LOCKED
        assert found.locked
        assert (found.element, found.label, found.batch_peak_id) == (
            "Br",
            "81Br",
            "line",
        )
        assert found.to_json()["n_neutral"] == 0
        assert found.to_json()["share"] == 1.0

    def test_both_halogens_are_never_locked(self):
        found = lock(both_halogens=True)
        assert (found.verdict, found.reason) == (LOCK_UNTESTABLE, REASON_BOTH_HALOGENS)

    @pytest.mark.parametrize(
        "found, verdict, reason",
        [
            ({"resolved": False}, LOCK_UNTESTABLE, REASON_UNRESOLVED),
            ({"batch_peak_id": None}, LOCK_NOT_LOCKED, REASON_NOT_FOUND),
            (
                {"verdict": "untestable", "reason": REASON_FEW_SPECTRA},
                LOCK_UNTESTABLE,
                REASON_FEW_SPECTRA,
            ),
            ({"verdict": "not_stamped"}, LOCK_NOT_LOCKED, REASON_RATIO),
            ({"stamped": False}, LOCK_NOT_LOCKED, REASON_ANOTHER_PARENT),
        ],
    )
    def test_the_stamping_s_reading_decides_first(self, found, verdict, reason):
        read = lock(found=found)
        assert (read.verdict, read.reason) == (verdict, reason)

    @pytest.mark.parametrize("error", [1.2, -1.2])
    def test_a_line_off_the_exact_spacing_is_not_a_lock(self, error):
        read = lock(offset_error=error)
        assert (read.verdict, read.reason) == (LOCK_NOT_LOCKED, REASON_OFFSET)

    def test_a_line_whose_spacing_was_not_read_is_untestable(self):
        read = lock(offset_error=None)
        assert (read.verdict, read.reason) == (LOCK_UNTESTABLE, REASON_NO_OFFSET)
        assert read.to_json()["offset_error_ppm"] is None

    def test_the_window_is_the_batch_s(self):
        assert lock(offset_error=1.2, window_ppm=1.5).locked

    def test_the_29si_line_decides_a_chlorine_lock_s_silicon(self):
        assert lock(silicon=silicon("stamped")).verdict == LOCK_SILICON
        read = lock(silicon=silicon("untestable"))
        assert (read.verdict, read.reason) == (
            LOCK_UNTESTABLE,
            REASON_SILICON_UNTESTABLE,
        )
        assert lock(silicon=silicon("not_stamped")).locked
        # No batch peak at the 29Si line: the line is not there.
        assert lock(silicon=silicon(None)).locked
        assert lock(silicon=silicon("stamped")).to_json()["silicon"]["verdict"] == (
            "stamped"
        )


class TestTheTwinLine:
    def test_one_labelled_atom_s_twin_is_one_unit_down_at_the_impurity_share(self):
        twin = twin_isotopologue("C10H16O4^NO3-", 120000.0)
        assert twin.label == "14N"
        assert twin.mz_offset == pytest.approx(-0.997035, abs=1e-6)
        assert twin.ratio == pytest.approx(0.02 / 0.98)

    def test_two_labelled_atoms_double_it(self):
        assert twin_isotopologue("H^NO3^NO3-").ratio == pytest.approx(
            2 * 0.02 / 0.98, rel=1e-6
        )

    def test_the_setup_s_purity_is_read(self):
        with labelled_purity(0.9):
            assert twin_isotopologue("^NO3-").ratio == pytest.approx(0.1 / 0.9)

    @pytest.mark.parametrize("ion", ["C10H16NO7-", "C6H9ClO3-", None, ""])
    def test_an_unlabelled_ion_has_none(self, ion):
        assert twin_isotopologue(ion) is None


RATIO = 0.02 / 0.98


def sample(twin=RATIO, snr=1000.0, **kwargs):
    labelled = 1e6
    return TwinSample(
        labelled=labelled,
        twin=None if twin is None else twin * labelled,
        labelled_snr=snr,
        twin_snr=None if twin is None or snr is None else snr * twin,
        **kwargs,
    )


class TestOneSample:
    def test_a_sample_tells_where_the_twin_would_clear_the_detection_threshold(
        self,
    ):
        # 3 / 0.0204 = 147: the labelled ion's S/N the twin needs.
        assert can_tell(sample(snr=150.0), RATIO)
        assert not can_tell(sample(snr=140.0), RATIO)
        assert not can_tell(sample(snr=None), RATIO)

    def test_the_run_s_own_threshold_is_read(self):
        strict = EnvelopeTolerance(detect_snr=10.0)
        assert not can_tell(sample(snr=150.0, tolerance=strict), RATIO)
        assert can_tell(sample(snr=500.0, tolerance=strict), RATIO)

    def test_present_at_the_label_s_share_or_above(self):
        assert twin_present(sample(), RATIO)
        # Ambient 14N adds to the twin: no upper end.
        assert twin_present(sample(twin=RATIO * 20), RATIO)

    def test_absent_or_far_under_the_share(self):
        assert not twin_present(sample(twin=None), RATIO)
        assert not twin_present(sample(twin=RATIO * 0.3), RATIO)

    @pytest.mark.parametrize(
        "widths, present", [(1.5, True), (1.9, True), (2.5, False)]
    )
    def test_present_within_two_widths_under_the_share(self, widths, present):
        # So bright the noise is negligible: the width is the tolerance's 5 %
        # floor of the share, and the band is the share itself.
        floor = EnvelopeTolerance().floor
        assert floor == 0.05
        found = sample(twin=RATIO * (1 - floor * widths), snr=1e7)
        assert twin_present(found, RATIO) is present

    def test_a_dim_twin_s_own_noise_widens_its_band(self):
        # 30 % under the share is six floor widths; the twin's S/N of 3 makes
        # its width a third of its height, so it stays within two.
        dim = TwinSample(labelled=1e6, twin=0.7 * RATIO * 1e6, labelled_snr=1000.0)
        assert not twin_present(dim, RATIO)
        assert twin_present(
            TwinSample(dim.labelled, dim.twin, dim.labelled_snr, twin_snr=3.0), RATIO
        )


class TestTheTwinTrack:
    def test_present_where_the_samples_that_can_tell_show_it(self):
        track = read_twin([sample() for _ in range(4)], RATIO)
        assert (track.verdict, track.judged, track.present) == (TWIN_PRESENT, 4, 4)
        assert track.excess == pytest.approx(1.0)
        assert track.to_json()["share"] == 1.0

    def test_absent_where_missing_from_eight_in_ten(self):
        samples = [sample(twin=None) for _ in range(8)] + [sample(), sample()]
        track = read_twin(samples, RATIO)
        assert ABSENT_SHARE == 0.8
        assert (track.verdict, track.present) == (TWIN_ABSENT, 2)

    def test_present_where_seen_in_more_than_a_fifth(self):
        samples = [sample(twin=None) for _ in range(7)] + [sample() for _ in range(3)]
        assert read_twin(samples, RATIO).verdict == TWIN_PRESENT

    def test_untestable_on_too_few_samples_that_can_tell(self):
        samples = [sample(twin=None) for _ in range(MIN_JUDGED - 1)] + [
            sample(twin=None, snr=10.0) for _ in range(5)
        ]
        track = read_twin(samples, RATIO)
        assert (track.verdict, track.reason) == (TWIN_UNTESTABLE, REASON_FEW_SPECTRA)
        assert track.judged == MIN_JUDGED - 1

    def test_the_excess_reads_every_sample_holding_both(self):
        # The dim samples cannot tell, and still show how the 14N line runs.
        samples = [sample(twin=RATIO * 4, snr=10.0) for _ in range(3)] + [sample()]
        track = read_twin(samples, RATIO)
        assert track.excess == pytest.approx(4.0)
        assert track.judged == 1


class TestTheImpurityFactor:
    def test_a_reagent_ion_reads_how_far_its_twin_runs_from_the_setup_s_share(self):
        pairs = [(1e6, 1e6 * RATIO * f) for f in (0.4, 0.42, 0.45, 1.0)]
        assert core_factor(pairs, RATIO) == pytest.approx(0.435)

    def test_too_few_samples_or_dead_lines_measure_nothing(self):
        assert core_factor([(1e6, 1e4)] * (MIN_JUDGED - 1), RATIO) is None
        dead = [(1e6, 0.0), (0.0, 1e4), (None, 1e4)] + [(1e6, 1e4)] * (MIN_JUDGED - 1)
        assert core_factor(dead, RATIO) is None
        assert core_factor([(1e6, 1e4)] * 3, 0.0) is None

    def test_the_batch_s_factor_is_the_median_over_its_ions(self):
        assert impurity_factor([0.4, None, 0.5, 2.0]) == pytest.approx(0.5)
        assert impurity_factor([None, math.nan, -1.0]) is None
        assert impurity_factor([]) is None


class TestTheNeutralPair:
    @pytest.mark.parametrize(
        "notation, partner",
        [
            ("[M+H]+", "[M+CH4N2O+H]+"),
            ("+H+", "[M+CH4N2O+H]+"),
            ("[M+H+CH4N2O]+", "[M+H]+"),
            ("[M+NH4]+", None),
            ("[M+NO3]-", None),
            (None, None),
        ],
    )
    def test_the_uronium_pair_by_its_notation(self, notation, partner):
        assert pair_partner(notation) == partner

    @pytest.mark.parametrize(
        "reading, verdict, reason",
        [
            ({"verdict": "co_varying", "r": 0.8}, PAIR_ESTABLISHED, None),
            ({"verdict": "not_co_varying", "r": 0.1}, PAIR_NOT_ESTABLISHED, None),
            (
                {"verdict": "untestable", "reason": "few_spectra"},
                PAIR_UNTESTABLE,
                "few_spectra",
            ),
            (None, PAIR_UNTESTABLE, REASON_NOT_READ),
            ({}, PAIR_UNTESTABLE, REASON_NOT_READ),
        ],
    )
    def test_the_co_variation_reading_decides(self, reading, verdict, reason):
        assert read_neutral_pair(reading) == (verdict, reason)


def test_the_thresholds_are_recorded():
    assert thresholds()["absent_share"] == ABSENT_SHARE
    assert thresholds()["si29_per_si30"] == pytest.approx(1.515, abs=1e-3)
