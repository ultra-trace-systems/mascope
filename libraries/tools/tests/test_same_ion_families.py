"""One ion, read two ways.

Candidates that combine into the same ion formula are not rival hypotheses about
a peak: they sit at one mass, predict one envelope and score identically, so the
measurement cannot separate them. What separates them is a policy - the reading
whose mechanism carries the mass is the reading - and what used to separate them
was the order the finder enumerated its mechanisms in.

The other half of this module is the ordering between hypotheses the measurement
CAN separate, which must fall to the data at every step and never to row order.
"""

import numpy as np
import pandas as pd
import polars as pl
import pytest

from mascope_tools.composition import heuristic_filter
from mascope_tools.composition.finder import assign_compositions
from mascope_tools.composition.heuristic_filter import (
    SAME_ION_ALTERNATIVES,
    elect_same_ion_families,
    match_isotopic_pattern,
    mechanism_mass_contribution,
    neutral_is_closed_shell,
    predict_isotopes,
)
from mascope_tools.composition.models import CompositionSearchConfig
from mascope_tools.composition.utils import calculate_mass, parse_ionization


def _candidate(formula, mechanism, ion, error_ppm=0.0):
    return {
        "formula": formula,
        "ion": ion,
        "ionization_mechanism": mechanism,
        "composition_error_ppm": error_ppm,
        "neutral_mass": calculate_mass(formula=formula),
        "unsaturation": None,
    }


class TestTheNeutralIsAMolecule:
    """The first key: between two readings of one ion, prefer the molecule."""

    def test_an_integer_dbe_is_closed_shell_and_a_half_integer_one_is_not(self):
        assert neutral_is_closed_shell("C17H24O4")
        assert not neutral_is_closed_shell("C16H23O")

    def test_the_two_readings_differ_when_the_fragment_between_them_does(self):
        # Adding a fragment F to a neutral moves its DBE by DBE(F) - 1, so two
        # readings of an ion differ here exactly when F's own DBE is a
        # half-integer. HCO3 is such a fragment: [M-H]- against [M'+CO3]- puts
        # a molecule against a radical, and this key decides.
        assert neutral_is_closed_shell("C17H24O4") != neutral_is_closed_shell("C16H23O")

    def test_it_is_silent_where_the_fragment_has_a_whole_dbe(self):
        # NH3, urea, HNO3 and HBr all do, so on an ammonium, a urea-cluster or a
        # nitrate-against-deprotonation family both neutrals are molecules, this
        # key says nothing and the mechanism's mass decides alone. That is most
        # of the gate.
        for molecule, heavier in (
            ("C6H12O6", "C6H15NO6"),  # +NH4+ against +H+, differing by NH3
            ("C8H16", "C9H20N2O"),  # +(CH4N2O)H+ against +H+, by urea
            ("C6H10O5", "C6H11NO8"),  # +NO3- against -H+, by HNO3
            ("C6H12O6", "C6H13BrO6"),  # +Br- against -H+, by HBr
        ):
            assert neutral_is_closed_shell(molecule)
            assert neutral_is_closed_shell(heavier)

    def test_an_unreadable_formula_is_not_demoted_on_a_test_that_could_not_run(
        self,
    ):
        assert neutral_is_closed_shell("not a formula")


class TestTheMechanismCarryingTheMass:
    """The second key, on the mechanisms the profiles actually declare."""

    def test_an_addition_contributes_positive_mass_and_a_subtraction_negative(self):
        assert mechanism_mass_contribution("+NH4+") == pytest.approx(18.034, abs=1e-3)
        assert mechanism_mass_contribution("-H+") == pytest.approx(-1.007, abs=1e-3)

    def test_the_reagent_adduct_outranks_the_deprotonation(self):
        # The three readings the nitrate chemistry offers for one anion, in the
        # order the policy puts them: the reagent adduct, the carbonate adduct
        # the source also runs, and last the covalent reading that asks the
        # analyte to have carried the reagent's own atoms all along.
        assert (
            mechanism_mass_contribution("+NO3-")
            > mechanism_mass_contribution("+CO3-")
            > mechanism_mass_contribution("-H+")
        )

    def test_an_unreadable_mechanism_does_not_decide_a_family(self):
        # Fail-open: a notation whose moiety has no mass at all ranks below
        # every addition and above every subtraction, rather than raising in the
        # middle of a ranking or winning a family by accident. "+Xx+" names an
        # element the mass tables do not have.
        assert mechanism_mass_contribution("+Xx+") == 0.0
        assert mechanism_mass_contribution(None) == 0.0


class TestTheFamilyElection:
    """Grouping by the ion, and electing one reading of it."""

    def test_the_nitrate_reading_wins_and_the_others_ride_along(self):
        # C6H10NO8- is all three of these at once: the nitrate adduct of a sugar
        # acid, the carbonate adduct of the same mass with one carbon read as
        # nitrogen, and the deprotonated nitric-acid adduct. Two of the three
        # neutrals are molecules; among those the nitrate reading carries the
        # most mass. The carbonate reading is last because its neutral is a
        # radical, not because its mechanism is the lightest.
        members = [
            _candidate("C6H11NO8", "-H+", "C6H10NO8-"),
            _candidate("C5H10NO5", "+CO3-", "C6H10NO8-"),
            _candidate("C6H10O5", "+NO3-", "C6H10NO8-"),
        ]
        (elected,) = elect_same_ion_families(members)

        assert elected["formula"] == "C6H10O5"
        assert elected["ionization_mechanism"] == "+NO3-"
        assert [
            (member["formula"], member["ionization_mechanism"])
            for member in elected[SAME_ION_ALTERNATIVES]
        ] == [("C6H11NO8", "-H+"), ("C5H10NO5", "+CO3-")]

    def test_a_molecule_outranks_a_radical_however_the_mass_falls(self):
        # The measured case: C17H23O4- is a deprotonated acid or a carbonate
        # adduct of a C16H23O radical, and the carbonate mechanism carries 60 Da
        # more. Ranking on mass alone took 159 of these on the gate's nitrate
        # set and read every one as an adduct of something that is not a
        # molecule.
        members = [
            _candidate("C16H23O", "+CO3-", "C17H23O4-"),
            _candidate("C17H24O4", "-H+", "C17H23O4-"),
        ]
        (elected,) = elect_same_ion_families(members)

        assert elected["formula"] == "C17H24O4"
        assert elected["ionization_mechanism"] == "-H+"

    def test_a_radical_still_wins_when_it_is_the_only_reading(self):
        # A tie-break, not a filter. A nitrate source measuring RO2 must keep
        # committing the radical where nothing else explains the ion.
        lone = _candidate("C16H23O", "+CO3-", "C17H23O4-")
        (elected,) = elect_same_ion_families([lone])

        assert elected is lone

    def test_the_election_does_not_depend_on_the_order_they_arrive_in(self):
        members = [
            _candidate("C6H10O5", "+NO3-", "C6H10NO8-"),
            _candidate("C5H10NO5", "+CO3-", "C6H10NO8-"),
            _candidate("C6H11NO8", "-H+", "C6H10NO8-"),
        ]
        for rotation in range(len(members)):
            rotated = members[rotation:] + members[:rotation]
            (elected,) = elect_same_ion_families(rotated)
            assert elected["ionization_mechanism"] == "+NO3-"

    def test_different_ions_are_different_families(self):
        elected = elect_same_ion_families(
            [
                _candidate("C6H12O6", "+H+", "C6H13O6+"),
                _candidate("C6H10O5", "+NO3-", "C6H10NO8-"),
            ]
        )
        assert len(elected) == 2

    def test_a_family_of_one_is_left_exactly_as_it_came(self):
        # The common case must add nothing to the row: no key, no copy of a list
        # nobody will read.
        lone = _candidate("C6H12O6", "+H+", "C6H13O6+")
        (elected,) = elect_same_ion_families([lone])
        assert elected is lone
        assert SAME_ION_ALTERNATIVES not in elected

    def test_the_labelled_reagent_is_not_the_unlabelled_one(self):
        # The 15N reagent makes a different ion, so it is a different hypothesis
        # and must never be collapsed into the unlabelled family.
        elected = elect_same_ion_families(
            [
                _candidate("C6H10O5", "+NO3-", "C6H10NO8-"),
                _candidate("C6H10O5", "+^NO3-", "C6H10^NO8-"),
            ]
        )
        assert len(elected) == 2


class TestTheFinderCommitsTheElectedReading:
    """End to end: the same ion offered to the search under two mechanisms."""

    #: The ammonium adduct of glucose, which is also the protonated form of the
    #: amide one ammonia heavier.
    MZ = calculate_mass(formula="C6H12O6") + parse_ionization("+NH4+").mass

    def _search(self, ionizations):
        config = CompositionSearchConfig(
            ionizations=ionizations,
            element_count_ranges="C0-10 H0-20 N0-2 O0-8",
            mass_range_ppm=5.0,
        )
        peaks = pd.DataFrame(
            {"mz": [self.MZ, self.MZ + 1.00336], "intensity": [1.0e6, 6.6e4]}
        )
        matches, _ = assign_compositions(peaks, config)
        return matches

    @pytest.mark.parametrize(
        "ionizations",
        ["+H+,+NH4+", "+NH4+,+H+"],
        ids=["proton first", "ammonium first"],
    )
    def test_the_ammonium_reading_wins_either_enumeration_order(self, ionizations):
        matches = self._search(ionizations)
        m0 = matches[matches["isotope_label"] == "M0"].iloc[0]

        assert m0["formula"] == "C6H12O6"
        assert m0["ionization_mechanism"] == "+NH4+"

    def test_the_displaced_reading_is_carried_on_the_committed_row(self):
        matches = self._search("+H+,+NH4+")
        m0 = matches[matches["isotope_label"] == "M0"].iloc[0]

        (displaced,) = m0[SAME_ION_ALTERNATIVES]
        assert displaced["formula"] == "C6H15NO6"
        assert displaced["ionization_mechanism"] == "+H+"
        assert displaced["ion"] == m0["ion"]

    def test_the_isotopologues_do_not_restate_it(self):
        # The family is a statement about how the ion was read; the M0 row is
        # where that reading is committed and the isotopologues are owned by it.
        matches = self._search("+H+,+NH4+")
        children = matches[matches["isotope_label"] != "M0"]

        assert not children.empty
        assert children[SAME_ION_ALTERNATIVES].isna().all()


class TestTheFamilyIsOneScoringUnit:
    def test_an_ion_is_scored_once_however_many_ways_it_splits(self, monkeypatch):
        # Not an optimisation: the envelope belongs to the ion, so scoring a
        # family member twice would be asking the spectrum a question it has
        # already answered and pretending the second answer is new evidence.
        seen = []
        real = heuristic_filter.predict_isotopes

        def counting(ion_formula, ion_charge, *args, **kwargs):
            seen.append(ion_formula)
            return real(ion_formula, ion_charge, *args, **kwargs)

        monkeypatch.setattr(heuristic_filter, "predict_isotopes", counting)
        candidates = [
            _candidate("C6H11NO8", "-H+", "C6H10NO8-"),
            _candidate("C5H10NO5", "+CO3-", "C6H10NO8-"),
            _candidate("C6H10O5", "+NO3-", "C6H10NO8-"),
        ]
        peaks = pl.DataFrame({"mz": [1.0], "intensity": [1.0]})
        match_isotopic_pattern(candidates, peaks)

        assert seen == ["C6H10NO8"]


class TestTheRankingBetweenDifferentIons:
    """What the measurement cannot separate must still come out in one order."""

    @staticmethod
    def _tied_candidates():
        # Three ions the spectrum below says nothing about at all: every
        # envelope misses, so every score is exactly 0.0 and only the tie-break
        # remains. Distinct masses, so the |ppm| key bites before the rest.
        return [
            _candidate("C6H12O6", "+H+", "C6H13O6+", error_ppm=-2.0),
            _candidate("C5H8N2O5", "+H+", "C5H9N2O5+", error_ppm=0.5),
            _candidate("C4H10N4O4", "+H+", "C4H11N4O4+", error_ppm=1.0),
        ]

    @staticmethod
    def _empty_spectrum():
        return pl.DataFrame({"mz": [10.0], "intensity": [1.0]})

    def test_the_closest_mass_wins_a_tie_on_score(self):
        ranked, _ = match_isotopic_pattern(
            self._tied_candidates(), self._empty_spectrum()
        )
        assert [candidate["isotopic_pattern_score"] for candidate in ranked] == [
            0.0
        ] * 3
        assert [abs(candidate["composition_error_ppm"]) for candidate in ranked] == [
            0.5,
            1.0,
            2.0,
        ]

    def test_the_order_does_not_depend_on_the_order_they_arrive_in(self):
        candidates = self._tied_candidates()
        first, _ = match_isotopic_pattern(candidates, self._empty_spectrum())
        second, _ = match_isotopic_pattern(
            list(reversed(candidates)), self._empty_spectrum()
        )
        assert [c["formula"] for c in first] == [c["formula"] for c in second]

    def test_plausibility_breaks_a_tie_the_masses_cannot(self):
        # Same score and the same distance from the peak: what remains is which
        # formula is the more ordinary chemistry.
        candidates = [
            _candidate("C2H7N5O", "+H+", "C2H8N5O+", error_ppm=1.0),
            _candidate("C6H12O6", "+H+", "C6H13O6+", error_ppm=-1.0),
        ]
        ranked, _ = match_isotopic_pattern(candidates, self._empty_spectrum())
        assert [c["formula"] for c in ranked] == ["C6H12O6", "C2H7N5O"]

    def test_each_candidate_keeps_its_own_isotope_pattern(self):
        # The two lists are consumed by index - the winner's pattern is
        # `isotope_data[0]` - so a ranking that sorts them separately can hand a
        # composition another composition's envelope the moment two scores tie.
        ranked, isotope_data = match_isotopic_pattern(
            self._tied_candidates(), self._empty_spectrum()
        )
        assert len(ranked) == len(isotope_data)
        for candidate, data in zip(ranked, isotope_data):
            expected, _, _ = predict_isotopes(candidate["ion"][:-1], 1)
            assert np.allclose(data["predicted_masses"], expected)
