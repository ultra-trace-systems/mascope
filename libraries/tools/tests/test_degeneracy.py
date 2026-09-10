"""Unit tests for the cross-family degeneracy measurement (step 2.4).

Contract: count the distinct plausible IONS that fall inside a calibrated mass
window around a peak, over whatever element box the caller states - and say when
the count is a lower bound rather than the count. It measures; it never ranks,
and it never knows what a run committed.

Every box here is deliberately tiny. The measurement's real box enumerates
millions of compositions, and a test that paid for that would be testing the
grid builder rather than this.
"""

import pytest

from mascope_tools.composition.degeneracy import (
    RELAXED_ELEMENT_RANGES,
    SATURATION_DENSITY,
    measure_degeneracy,
)
from mascope_tools.composition.models import CompositionSearchConfig


def config(**overrides) -> CompositionSearchConfig:
    base = {
        "ionizations": "+H+",
        "element_count_ranges": "C0-6 H0-14 N0-2 O0-6",
        "mass_range_ppm": 5.0,
        "max_result_rows": 50,
    }
    base.update(overrides)
    return CompositionSearchConfig(**base)


#: Protonated glucose. A real ion at a mass with real competitors nearby.
GLUCOSE_H = 181.0707

#: The same ion at its exact theoretical mass, and as an axis running 3 ppm
#: high would report it.
GLUCOSE_H_EXACT = 181.070665
GLUCOSE_H_ON_A_HIGH_AXIS = GLUCOSE_H_EXACT * (1 + 3e-6)


class TestWhatIsCounted:
    def test_no_peaks_is_no_reading(self):
        assert measure_degeneracy([], config=config()) == {}

    def test_a_peak_no_composition_explains_reads_zero(self):
        # Below the lightest ion the box can make, so nothing is in the window.
        [reading] = measure_degeneracy([3.5], config=config()).values()
        assert reading.density == 0
        assert reading.competitors == ()

    def test_every_peak_given_gets_a_reading(self):
        readings = measure_degeneracy([GLUCOSE_H, 200.1], config=config())
        assert set(readings) == {GLUCOSE_H, 200.1}

    def test_a_repeated_peak_is_measured_once(self):
        readings = measure_degeneracy([GLUCOSE_H, GLUCOSE_H], config=config())
        assert list(readings) == [GLUCOSE_H]

    def test_a_wider_window_admits_more(self):
        tight = measure_degeneracy([GLUCOSE_H], config=config(mass_range_ppm=0.5))
        wide = measure_degeneracy([GLUCOSE_H], config=config(mass_range_ppm=200.0))
        assert wide[GLUCOSE_H].density >= tight[GLUCOSE_H].density

    def test_a_wider_element_box_admits_more(self):
        # The whole point of the measurement: "unique in the window" is a claim
        # about the box. At 200 ppm the CHO box sees glucose alone; opening
        # nitrogen and sulfur puts real competitors on the same mass.
        narrow = measure_degeneracy(
            [GLUCOSE_H],
            config=config(element_count_ranges="C0-6 H0-14 O0-6", mass_range_ppm=200.0),
        )
        wide = measure_degeneracy(
            [GLUCOSE_H],
            config=config(
                element_count_ranges="C0-6 H0-14 N0-4 O0-6 S0-1",
                mass_range_ppm=200.0,
            ),
        )
        assert wide[GLUCOSE_H].density > narrow[GLUCOSE_H].density


class TestTheBoxIsWhatUniqueMeans:
    def test_a_tight_window_over_a_narrow_box_reads_unique(self):
        readings = measure_degeneracy([GLUCOSE_H], config=config())
        assert readings[GLUCOSE_H].density == 1
        assert readings[GLUCOSE_H].competitors == ("C6H12O6 +H+",)


class TestOneIonIsOneCandidate:
    def test_two_readings_of_the_same_ion_count_once(self):
        # m/z 198.0972 is C6H16NO6+ read two ways: ammonium on glucose, and a
        # proton on the amino-hexitol C6H15NO6. One ion, one candidate - a
        # count of two would say the mass is ambiguous when what is ambiguous
        # is only how one ion was written down.
        ammoniated_glucose = 198.0972
        readings = measure_degeneracy(
            [ammoniated_glucose],
            config=config(
                ionizations="+H+, +NH4+",
                element_count_ranges="C0-6 H0-16 N0-1 O0-6",
            ),
        )
        assert readings[ammoniated_glucose].density == 1
        assert len(readings[ammoniated_glucose].competitors) == 1

    def test_a_second_channel_still_adds_the_ions_only_it_can_make(self):
        # The collapse is on the ION, not on the channel: a channel that reaches
        # a genuinely different ion still contributes it.
        box = "C0-6 H0-16 N0-2 O0-6"
        one = measure_degeneracy(
            [GLUCOSE_H], config=config(mass_range_ppm=30.0, element_count_ranges=box)
        )
        both = measure_degeneracy(
            [GLUCOSE_H],
            config=config(
                ionizations="+H+, +NH4+", mass_range_ppm=30.0, element_count_ranges=box
            ),
        )
        assert both[GLUCOSE_H].density > one[GLUCOSE_H].density


class TestTheCalibratedWindow:
    def test_an_offset_axis_needs_the_offset_to_find_anything(self):
        # Glucose as an axis running 3 ppm high reports it, against a 1 ppm
        # window: without the offset the formula is three widths away and the
        # window is empty. Told what the axis does, the window lands on it.
        peak = GLUCOSE_H_ON_A_HIGH_AXIS
        assert (
            measure_degeneracy([peak], config=config(mass_range_ppm=1.0))[peak].density
            == 0
        )
        assert (
            measure_degeneracy([peak], config=config(mass_range_ppm=1.0), mu_ppm=3.0)[
                peak
            ].density
            == 1
        )

    def test_the_offset_has_a_direction(self):
        # The same magnitude the other way moves the window further off, which
        # is what distinguishes correcting for the axis from doubling its error.
        peak = GLUCOSE_H_ON_A_HIGH_AXIS
        assert (
            measure_degeneracy([peak], config=config(mass_range_ppm=1.0), mu_ppm=-3.0)[
                peak
            ].density
            == 0
        )

    def test_the_peak_is_still_reported_at_its_own_mz(self):
        # The offset moves what is searched, never what is answered about.
        shifted = measure_degeneracy(
            [GLUCOSE_H], config=config(mass_range_ppm=1.0), mu_ppm=3.0
        )
        assert list(shifted) == [GLUCOSE_H]


class TestSayingWhenItDoesNotKnow:
    def test_a_full_result_cap_is_reported_as_a_lower_bound(self):
        readings = measure_degeneracy(
            [GLUCOSE_H],
            config=config(
                mass_range_ppm=50_000.0,
                max_result_rows=2,
                element_count_ranges="C0-9 H0-20 N0-3 O0-6",
            ),
        )
        assert readings[GLUCOSE_H].censored is True

    def test_a_complete_answer_is_not_censored(self):
        readings = measure_degeneracy(
            [GLUCOSE_H], config=config(mass_range_ppm=0.5, max_result_rows=50)
        )
        assert readings[GLUCOSE_H].censored is False

    def test_the_cap_is_per_channel_so_two_channels_are_not_a_lower_bound(self):
        # `find_compositions` keeps the closest `max_result_rows` PER MECHANISM,
        # so a two-channel peak can return twice the cap with nothing lost.
        # Testing the total against the cap calls that complete answer censored:
        # here two channels return one composition each, which is two of a cap
        # of two and nothing missing.
        readings = measure_degeneracy(
            [GLUCOSE_H],
            config=config(
                ionizations="+H+, +NH4+",
                mass_range_ppm=30.0,
                max_result_rows=2,
                element_count_ranges="C0-6 H0-16 N0-2 O0-6",
            ),
        )
        reading = readings[GLUCOSE_H]
        assert reading.density == 2
        assert reading.censored is False


class TestSaturation:
    def test_a_crowded_window_says_it_is_saturated(self):
        # A 300 ppm window over a CHNO box puts eighteen distinct ions on this
        # mass - a mass nothing could be identified on from accuracy alone.
        readings = measure_degeneracy(
            [GLUCOSE_H],
            config=config(
                mass_range_ppm=300.0,
                max_result_rows=200,
                element_count_ranges="C0-8 H0-18 N0-3 O0-8",
            ),
        )
        reading = readings[GLUCOSE_H]
        assert reading.density > SATURATION_DENSITY
        assert reading.saturated is True

    def test_a_resolved_window_is_not_saturated(self):
        readings = measure_degeneracy([GLUCOSE_H], config=config())
        assert readings[GLUCOSE_H].density == 1
        assert readings[GLUCOSE_H].saturated is False

    def test_an_empty_window_is_not_saturated(self):
        [reading] = measure_degeneracy([3.5], config=config()).values()
        assert reading.saturated is False


class TestTheCompetitorsNamed:
    def test_they_are_named_and_bounded(self):
        readings = measure_degeneracy(
            [GLUCOSE_H],
            config=config(mass_range_ppm=200.0, max_result_rows=200),
            max_competitors=3,
        )
        reading = readings[GLUCOSE_H]
        assert len(reading.competitors) == min(3, reading.density)
        for competitor in reading.competitors:
            assert "+H+" in competitor

    @pytest.mark.parametrize("max_competitors", [0, 1])
    def test_naming_none_still_counts(self, max_competitors):
        readings = measure_degeneracy(
            [GLUCOSE_H],
            config=config(mass_range_ppm=200.0, max_result_rows=200),
            max_competitors=max_competitors,
        )
        assert readings[GLUCOSE_H].density >= 1
        assert len(readings[GLUCOSE_H].competitors) == max_competitors


class TestTheBoxTheMeasurementWasFittedOn:
    @pytest.mark.parametrize(
        "element", ["C", "H", "N", "O", "S", "F", "Cl", "Br", "Si"]
    )
    def test_the_relaxed_box_admits_every_family_it_claims_to(self, element):
        # The point of the wider box is the families a run's own box leaves out,
        # so an edit that quietly dropped one would leave the measurement
        # answering the narrow question it exists to widen.
        assert f"{element}0-" in RELAXED_ELEMENT_RANGES

    def test_oxygen_stays_capped(self):
        # An open oxygen range grids an O-monster mass fit onto every heavy
        # peak, and the count stops meaning anything.
        assert "O0-12 " in RELAXED_ELEMENT_RANGES + " "
