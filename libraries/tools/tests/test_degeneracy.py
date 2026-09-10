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
    _plausible_rival,
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
    @pytest.mark.parametrize("element", ["H", "N", "O", "S", "F", "Cl", "Br", "Si"])
    def test_the_relaxed_box_admits_every_family_it_claims_to(self, element):
        # The point of the wider box is the families a run's own box leaves out,
        # so an edit that quietly dropped one would leave the measurement
        # answering the narrow question it exists to widen.
        assert f"{element}0-" in RELAXED_ELEMENT_RANGES

    def test_carbon_is_floored_at_one(self):
        # Every resolved Mascope grid floors carbon at one, so a carbon-free
        # composition is not an answer this engine could have committed, and
        # counting one as a rival to a committed reading inflates the density
        # with hypotheses that were never available.
        assert RELAXED_ELEMENT_RANGES.startswith("C1-")

    def test_oxygen_stays_capped(self):
        # An open oxygen range grids an O-monster mass fit onto every heavy
        # peak, and the count stops meaning anything.
        assert "O0-12 " in RELAXED_ELEMENT_RANGES + " "


class TestTheCompetitorGate:
    """peaky admits a competitor only when it carries at most a few heteroatom
    TYPES. Mascope's heuristic rules cannot stand in: they grade rather than
    gate, so a formula mixing four heteroatoms scores 1.0 and would be counted
    as a rival."""

    def test_a_formula_mixing_too_many_heteroatoms_is_not_a_rival(self):
        assert not _plausible_rival({"formula": "C4H2BrClFNSi"})

    @pytest.mark.parametrize(
        "formula", ["C6H12O6", "C5H8N2O4", "C10H15NO4S", "C8H9BrO2", "C6H18OSi2"]
    )
    def test_the_species_a_source_presents_are(self, formula):
        assert _plausible_rival({"formula": formula})

    def test_the_gate_is_on_types_not_on_atoms(self):
        # Three bromines is one heteroatom type; a real polybrominated species
        # must not be gated out for being heavy in one element.
        assert _plausible_rival({"formula": "C2H3Br3"})

    @pytest.mark.parametrize("formula", ["O3^N", ""])
    def test_an_unreadable_formula_is_not_gated_out(self, formula):
        # Fails open like every other chemistry rule here.
        assert _plausible_rival({"formula": formula})

    def test_the_gate_reaches_the_count(self):
        wide = measure_degeneracy(
            [GLUCOSE_H],
            config=config(
                mass_range_ppm=300.0,
                max_result_rows=200,
                element_count_ranges="C1-8 H0-18 N0-3 O0-8 S0-1 F0-6 Cl0-2",
            ),
        )
        assert wide[GLUCOSE_H].competitors
        assert all(
            _plausible_rival({"formula": name.split(" ")[0]})
            for name in wide[GLUCOSE_H].competitors
        )


class TestWhenTheBoxWillNotFit:
    """A density of 0 says the window was searched and held nothing. A box too
    wide to enumerate over a peak's window is a different answer, and reporting
    it as 0 says "nothing could be here" about a peak nothing looked at."""

    @staticmethod
    def refusing_to_band(monkeypatch):
        """A walker that can build no grid, whatever it is asked for.

        Searching one channel at a time makes a real overflow very hard to
        reach - a band halves down to a dalton, and a dalton of any box holds
        thousands of compositions rather than millions - so the branch is
        pinned by injecting the failure rather than by finding a box that
        still causes it. What it must never do is answer 0.
        """
        from mascope_tools.composition import degeneracy as module

        monkeypatch.setattr(
            module,
            "grids_for_targets",
            lambda targets, config, mechanisms, **kw: (
                (float(t), None) for t in targets
            ),
        )

    def test_a_box_that_cannot_be_enumerated_is_not_a_density_of_zero(
        self, monkeypatch
    ):
        self.refusing_to_band(monkeypatch)
        readings = measure_degeneracy([GLUCOSE_H], config=config())
        reading = readings[GLUCOSE_H]
        assert reading.measured is False
        assert reading.competitors == ()

    def test_a_window_that_was_searched_and_held_nothing_is(self):
        readings = measure_degeneracy([3.5], config=config())
        assert readings[3.5].measured is True
        assert readings[3.5].density == 0

    def test_one_channel_that_cannot_be_enumerated_taints_the_count(self, monkeypatch):
        # Whatever the other channels found is a lower bound, not a count.
        self.refusing_to_band(monkeypatch)
        readings = measure_degeneracy(
            [GLUCOSE_H], config=config(ionizations="+H+, +NH4+")
        )
        assert readings[GLUCOSE_H].measured is False

    def test_a_channel_too_heavy_for_the_peak_is_not_a_gap(self, monkeypatch):
        # A dibromide adduct weighs more than a peak at m/z 100, so that channel
        # cannot explain it - which is an answer, not a hole. Reporting it as
        # unmeasured would mark every light peak of a bromide run unanswered.
        self.refusing_to_band(monkeypatch)
        readings = measure_degeneracy([100.0], config=config(ionizations="+Br2-"))
        assert readings[100.0].measured is True
        assert readings[100.0].density == 0

    @staticmethod
    def a_row_bound_the_union_overflows(monkeypatch, max_rows: int = 30_000):
        """Shrink the enumerator's row bound so the defect is reachable.

        The bound is two million rows, and a band halves down to a dalton, so
        with the real bound the union of two channels only overflows on a
        spectrum and a box wider than a test should build. Shrunk, a band over
        the union of two channels' windows overflows where a band over ONE
        channel's window still fits - which is exactly the geometry the fix is
        about, at a size a test can hold.
        """
        from mascope_tools.composition import degeneracy as module

        real = module.grids_for_targets
        monkeypatch.setattr(
            module,
            "grids_for_targets",
            lambda targets, config, mechanisms, **kw: real(
                targets, config, mechanisms, max_rows=max_rows
            ),
        )

    def test_a_peak_s_answer_does_not_depend_on_who_it_was_asked_with(
        self, monkeypatch
    ):
        # The banded walk sizes its bands from the whole list. Sized once from
        # every mechanism at once, the first overflow stops it banding for good,
        # and every heavier peak of a long list then gets a different answer
        # from the one it gets alone.
        self.a_row_bound_the_union_overflows(monkeypatch)
        wide = config(
            ionizations="+H+, +NH4+",
            element_count_ranges="C1-20 H0-36 N0-3 O0-12 S0-1 F0-17",
            mass_range_ppm=2.0,
            max_result_rows=200,
        )
        crowd = sorted([120.05, 181.0707, 240.1, 300.15, 360.2, 420.25, 480.3])
        together = measure_degeneracy(crowd, config=wide)
        for mz in crowd:
            alone = measure_degeneracy([mz], config=wide)
            assert alone[mz].density == together[mz].density
            assert alone[mz].measured == together[mz].measured

    def test_the_bound_this_uses_is_one_the_union_really_overflows(self):
        # The guard that makes the two tests above mean something. Under this
        # row bound a band sized from BOTH channels gives up part-way through
        # the list - which is the defect - while a band sized from either
        # channel alone covers every target, which is the fix. Without this the
        # tests pass on an implementation that never reaches the case.
        from dataclasses import replace as replace_config

        import numpy as np

        from mascope_tools.composition import utils
        from mascope_tools.composition.finder import (
            get_ionization_mech_string_list,
            grids_for_targets,
        )

        wide = config(
            ionizations="+H+, +NH4+",
            element_count_ranges="C1-20 H0-36 N0-3 O0-12 S0-1 F0-17",
            mass_range_ppm=2.0,
            max_result_rows=200,
        )
        crowd = np.array(
            sorted([120.05, 181.0707, 240.1, 300.15, 360.2, 420.25, 480.3])
        )
        notations = get_ionization_mech_string_list(wide.ionizations)
        both = [utils.parse_ionization(name) for name in notations]

        union = [
            grid for _mz, grid in grids_for_targets(crowd, wide, both, max_rows=30_000)
        ]
        assert any(grid is None for grid in union), "the union has to overflow"

        for name in notations:
            one = replace_config(wide, ionizations=name)
            per_channel = [
                grid
                for _mz, grid in grids_for_targets(
                    crowd, one, [utils.parse_ionization(name)], max_rows=30_000
                )
            ]
            assert all(grid is not None for grid in per_channel)

    def test_and_the_peaks_are_answered_rather_than_abandoned(self, monkeypatch):
        # The half of the fix the equality above cannot see: under the same
        # shrunk bound a band over ONE channel still fits, so the peaks get
        # counts instead of the silent nothing the union walk left them.
        self.a_row_bound_the_union_overflows(monkeypatch)
        wide = config(
            ionizations="+H+, +NH4+",
            element_count_ranges="C1-20 H0-36 N0-3 O0-12 S0-1 F0-17",
            mass_range_ppm=2.0,
            max_result_rows=200,
        )
        readings = measure_degeneracy([300.15, 360.2, 420.25, 480.3], config=wide)
        assert all(r.measured for r in readings.values())
        assert any(r.density > 0 for r in readings.values())
