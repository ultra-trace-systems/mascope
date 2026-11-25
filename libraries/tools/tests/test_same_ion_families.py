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

from mascope_tools.composition import heuristic_filter, line_model
from mascope_tools.composition.finder import assign_compositions, find_compositions
from mascope_tools.composition.heuristic_filter import (
    SAME_ION_ALTERNATIVES,
    elect_same_ion_families,
    match_isotopic_pattern,
    mechanism_mass_contribution,
    mechanism_multimer,
    neutral_is_closed_shell,
    predict_isotopes,
    propose_same_ion_readings,
)
from mascope_tools.composition.models import (
    CompositionSearchConfig,
    HeuristicFilterConfig,
)
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


class TestTheFormateAdductAndTheAcid:
    """One ion, C11H19O7-: a C11 acid deprotonated, or a C10 product with formate.

    Both neutrals are molecules, so the election falls to the mechanism that
    carries the mass, and the formate adduct is that reading. The acid stays on
    the row as the reading it displaced; which of the two the sample supports
    is the engine's partner gate's question (``engine.apply_partner_gates``),
    with the cross-channel pass reading what the gate leaves.
    """

    def test_the_formate_reading_is_elected_and_the_acid_kept(self):
        acid = _candidate("C11H20O7", "[M-H]-", "C11H19O7-")
        adduct = _candidate("C10H18O5", "[M+HCOO]-", "C11H19O7-")
        assert neutral_is_closed_shell("C11H20O7")
        assert neutral_is_closed_shell("C10H18O5")
        assert mechanism_mass_contribution("[M+HCOO]-") > mechanism_mass_contribution(
            "[M-H]-"
        )
        for order in ((acid, adduct), (adduct, acid)):
            [winner] = elect_same_ion_families(list(order))
            assert winner["formula"] == "C10H18O5"
            assert winner["ionization_mechanism"] == "[M+HCOO]-"
            assert [a["formula"] for a in winner[SAME_ION_ALTERNATIVES]] == ["C11H20O7"]

    def test_the_two_readings_are_one_ion_to_the_finder(self):
        # 263.1136: what the chamber batch's strongest assigned peak reads as.
        target = calculate_mass(formula="C10H18O5") + parse_ionization("[M+HCOO]-").mass
        acid = calculate_mass(formula="C11H20O7") - parse_ionization("[M-H]-").mass
        assert target == pytest.approx(acid, abs=1e-9)
        assert target == pytest.approx(263.1136, abs=5e-4)


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
            ("C6H12O6", "C6H15NO6"),  # [M+NH4]+ against [M+H]+, differing by NH3
            ("C8H16", "C9H20N2O"),  # [M+CH4N2O+H]+ against [M+H]+, by urea
            ("C6H10O5", "C6H11NO8"),  # [M+NO3]- against [M-H]-, by HNO3
            ("C6H12O6", "C6H13BrO6"),  # [M+Br]- against [M-H]-, by HBr
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
        assert mechanism_mass_contribution("[M+NH4]+") == pytest.approx(
            18.034, abs=1e-3
        )
        assert mechanism_mass_contribution("[M-H]-") == pytest.approx(-1.007, abs=1e-3)

    def test_the_reagent_adduct_outranks_the_deprotonation(self):
        # The three readings the nitrate chemistry offers for one anion, in the
        # order the policy puts them: the reagent adduct, the carbonate adduct
        # the source also runs, and last the covalent reading that asks the
        # analyte to have carried the reagent's own atoms all along.
        assert (
            mechanism_mass_contribution("[M+NO3]-")
            > mechanism_mass_contribution("[M+CO3]-")
            > mechanism_mass_contribution("[M-H]-")
        )

    def test_an_unreadable_mechanism_does_not_decide_a_family(self):
        # Fail-open: a notation whose moiety has no mass at all ranks below
        # every addition and above every subtraction, rather than raising in the
        # middle of a ranking or winning a family by accident. "[M+Xx]+" names an
        # element the mass tables do not have.
        assert mechanism_mass_contribution("[M+Xx]+") == 0.0
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
            _candidate("C6H11NO8", "[M-H]-", "C6H10NO8-"),
            _candidate("C5H10NO5", "[M+CO3]-", "C6H10NO8-"),
            _candidate("C6H10O5", "[M+NO3]-", "C6H10NO8-"),
        ]
        (elected,) = elect_same_ion_families(members)

        assert elected["formula"] == "C6H10O5"
        assert elected["ionization_mechanism"] == "[M+NO3]-"
        assert [
            (member["formula"], member["ionization_mechanism"])
            for member in elected[SAME_ION_ALTERNATIVES]
        ] == [("C6H11NO8", "[M-H]-"), ("C5H10NO5", "[M+CO3]-")]

    def test_a_molecule_outranks_a_radical_however_the_mass_falls(self):
        # The measured case: C17H23O4- is a deprotonated acid or a carbonate
        # adduct of a C16H23O radical, and the carbonate mechanism carries 60 Da
        # more. Ranking on mass alone took 159 of these on the gate's nitrate
        # set and read every one as an adduct of something that is not a
        # molecule.
        members = [
            _candidate("C16H23O", "[M+CO3]-", "C17H23O4-"),
            _candidate("C17H24O4", "[M-H]-", "C17H23O4-"),
        ]
        (elected,) = elect_same_ion_families(members)

        assert elected["formula"] == "C17H24O4"
        assert elected["ionization_mechanism"] == "[M-H]-"

    def test_a_radical_still_wins_when_it_is_the_only_reading(self):
        # A tie-break, not a filter. A nitrate source measuring RO2 must keep
        # committing the radical where nothing else explains the ion.
        lone = _candidate("C16H23O", "[M+CO3]-", "C17H23O4-")
        (elected,) = elect_same_ion_families([lone])

        assert elected is lone

    def test_the_election_does_not_depend_on_the_order_they_arrive_in(self):
        members = [
            _candidate("C6H10O5", "[M+NO3]-", "C6H10NO8-"),
            _candidate("C5H10NO5", "[M+CO3]-", "C6H10NO8-"),
            _candidate("C6H11NO8", "[M-H]-", "C6H10NO8-"),
        ]
        for rotation in range(len(members)):
            rotated = members[rotation:] + members[:rotation]
            (elected,) = elect_same_ion_families(rotated)
            assert elected["ionization_mechanism"] == "[M+NO3]-"

    def test_different_ions_are_different_families(self):
        elected = elect_same_ion_families(
            [
                _candidate("C6H12O6", "[M+H]+", "C6H13O6+"),
                _candidate("C6H10O5", "[M+NO3]-", "C6H10NO8-"),
            ]
        )
        assert len(elected) == 2

    def test_a_family_of_one_is_left_exactly_as_it_came(self):
        # The common case must add nothing to the row: no key, no copy of a list
        # nobody will read.
        lone = _candidate("C6H12O6", "[M+H]+", "C6H13O6+")
        (elected,) = elect_same_ion_families([lone])
        assert elected is lone
        assert SAME_ION_ALTERNATIVES not in elected

    def test_the_labelled_reagent_is_not_the_unlabelled_one(self):
        # The 15N reagent makes a different ion, so it is a different hypothesis
        # and must never be collapsed into the unlabelled family.
        elected = elect_same_ion_families(
            [
                _candidate("C6H10O5", "[M+NO3]-", "C6H10NO8-"),
                _candidate("C6H10O5", "[M+^NO3]-", "C6H10^NO8-"),
            ]
        )
        assert len(elected) == 2


class TestTheFewerMolecules:
    """The third key: a monomer and a multimer reading of one ion.

    ``[M+H]+`` on glucose and ``[2M+H]+`` on lactic acid's formula make one ion,
    C6H13O6+, through one moiety, so the mass the mechanism carries ties them.
    The reading of one molecule is elected, whichever formula spells first.
    """

    def test_one_moiety_ties_on_the_mass(self):
        assert mechanism_mass_contribution("[2M+H]+") == pytest.approx(
            mechanism_mass_contribution("[M+H]+")
        )
        assert mechanism_multimer("[M+H]+") == 1
        assert mechanism_multimer("[2M+H]+") == 2

    def test_an_unreadable_mechanism_counts_as_one_molecule(self):
        assert mechanism_multimer(None) == 1
        assert mechanism_multimer("not a mechanism") == 1

    @pytest.mark.parametrize(
        "monomer, multimer, ion",
        [
            # "C3" sorts before "C6": the spelling alone would elect the dimer.
            ("C6H12O6", "C3H6O3", "C6H13O6+"),
            # "C12" sorts before "C6": the spelling alone would get it right.
            ("C12H24O12", "C6H12O6", "C12H25O12+"),
        ],
    )
    def test_the_monomer_reading_is_elected_either_order(self, monomer, multimer, ion):
        one = _candidate(monomer, "[M+H]+", ion)
        two = _candidate(multimer, "[2M+H]+", ion)
        for order in ((one, two), (two, one)):
            [winner] = elect_same_ion_families(list(order))
            assert winner["formula"] == monomer
            assert winner["ionization_mechanism"] == "[M+H]+"
            (displaced,) = winner[SAME_ION_ALTERNATIVES]
            assert displaced["formula"] == multimer
            assert displaced["ionization_mechanism"] == "[2M+H]+"

    @pytest.mark.parametrize(
        "ionizations",
        ["[M+H]+,[2M+H]+", "[2M+H]+,[M+H]+"],
        ids=["monomer first", "dimer first"],
    )
    def test_the_finder_commits_protonated_glucose_as_glucose(self, ionizations):
        mz = parse_ionization("[M+H]+").ion_mz(calculate_mass(formula="C6H12O6"))
        config = CompositionSearchConfig(
            ionizations=ionizations,
            element_count_ranges="C0-20 H0-40 O0-12",
            mass_range_ppm=3.0,
        )
        peaks = pd.DataFrame({"mz": [mz, mz + 1.00336], "intensity": [1.0e6, 6.6e4]})
        matches, _ = assign_compositions(peaks, config)
        m0 = matches[matches["isotope_label"] == "M0"].iloc[0]

        assert m0["formula"] == "C6H12O6"
        assert m0["ionization_mechanism"] == "[M+H]+"
        assert [
            (reading["formula"], reading["ionization_mechanism"])
            for reading in m0[SAME_ION_ALTERNATIVES]
        ] == [("C3H6O3", "[2M+H]+")]


class TestTheFinderCommitsTheElectedReading:
    """End to end: the same ion offered to the search under two mechanisms."""

    #: The ammonium adduct of glucose, which is also the protonated form of the
    #: amide one ammonia heavier.
    MZ = calculate_mass(formula="C6H12O6") + parse_ionization("[M+NH4]+").mass

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
        ["[M+H]+,[M+NH4]+", "[M+NH4]+,[M+H]+"],
        ids=["proton first", "ammonium first"],
    )
    def test_the_ammonium_reading_wins_either_enumeration_order(self, ionizations):
        matches = self._search(ionizations)
        m0 = matches[matches["isotope_label"] == "M0"].iloc[0]

        assert m0["formula"] == "C6H12O6"
        assert m0["ionization_mechanism"] == "[M+NH4]+"

    def test_the_displaced_reading_is_carried_on_the_committed_row(self):
        matches = self._search("[M+H]+,[M+NH4]+")
        m0 = matches[matches["isotope_label"] == "M0"].iloc[0]

        (displaced,) = m0[SAME_ION_ALTERNATIVES]
        assert displaced["formula"] == "C6H15NO6"
        assert displaced["ionization_mechanism"] == "[M+H]+"
        assert displaced["ion"] == m0["ion"]

    def test_the_isotopologues_do_not_restate_it(self):
        # The family is a statement about how the ion was read; the M0 row is
        # where that reading is committed and the isotopologues are owned by it.
        matches = self._search("[M+H]+,[M+NH4]+")
        children = matches[matches["isotope_label"] != "M0"]

        assert not children.empty
        assert children[SAME_ION_ALTERNATIVES].isna().all()


class TestTheFamilyAMatchedReadingWouldHave:
    """A reading the finder did not make, given the family its ion would have had."""

    MECHANISMS = ["[M+H]+", "[M+NH4]+", "[M+CH4N2O+H]+"]
    CONFIG = CompositionSearchConfig(
        ionizations=",".join(MECHANISMS),
        element_count_ranges="C1-20 H0-40 N0-3 O0-10",
        mass_range_ppm=2.0,
    )
    HEURISTICS = HeuristicFilterConfig(use_senior=True)

    def _propose(self, readings, notations=None, config=None, heuristics=None):
        return propose_same_ion_readings(
            readings,
            self.MECHANISMS if notations is None else notations,
            self.CONFIG if config is None else config,
            self.HEURISTICS if heuristics is None else heuristics,
        )

    def test_a_dimer_s_ion_is_a_monomer_s_reading_and_back(self):
        # C12H25O12+ is two glucoses and a proton, or one C12H24O12 and a
        # proton: one ion, two splits.
        notations = ["[2M+H]+", "[M+H]+"]
        config = CompositionSearchConfig(
            ionizations=",".join(notations),
            element_count_ranges="C1-20 H0-40 O0-14",
            mass_range_ppm=2.0,
        )
        (dimer,) = self._propose([("C6H12O6", "[2M+H]+")], notations, config)
        assert dimer == [
            {
                "formula": "C12H24O12",
                "ion": "C12H25O12+",
                "ionization_mechanism": "[M+H]+",
            }
        ]
        (monomer,) = self._propose([("C12H24O12", "[M+H]+")], notations, config)
        assert monomer == [
            {
                "formula": "C6H12O6",
                "ion": "C12H25O12+",
                "ionization_mechanism": "[2M+H]+",
            }
        ]
        # An ion of an odd count of an atom is no two of one molecule.
        assert self._propose([("C13H24O12", "[M+H]+")], notations, config) == [[]]

    def test_an_ion_of_another_charge_is_another_ion(self):
        # Glucose with two protons is not glucose with one, whatever the signs.
        notations = ["[M+2H]2+", "[M+H]+"]
        config = CompositionSearchConfig(
            ionizations=",".join(notations),
            element_count_ranges="C1-20 H0-40 O0-14",
            mass_range_ppm=2.0,
        )
        assert self._propose([("C6H12O6", "[M+2H]2+")], notations, config) == [[]]

    def test_a_loss_ranks_by_what_the_mechanism_adds_less_what_it_loses(self):
        assert mechanism_mass_contribution("[M+H-H2O]+") == pytest.approx(
            parse_ionization("[M+H]+").mass - calculate_mass(formula="H2O")
        )

    @pytest.mark.parametrize(
        "formula, mechanism",
        [("C3H7NO", "[M+H]+"), ("C10H14O7", "[M+CH4N2O+H]+"), ("C6H12O6", "[M+NH4]+")],
    )
    def test_it_is_the_family_the_finder_holds_for_that_ion(self, formula, mechanism):
        # The finder's own path for the ion's mass - the grid, every mechanism,
        # the heuristic rules, the election - and the family it keeps for this
        # ion, against the one proposed for the reading alone.
        ionization = parse_ionization(mechanism)
        mz = calculate_mass(formula=formula) + ionization.mass
        candidates, _ = heuristic_filter.apply_heuristic_rules(
            find_compositions(mz, self.CONFIG), self.HEURISTICS
        )
        ion = next(
            candidate["ion"]
            for candidate in candidates
            if candidate["formula"] == formula
            and candidate["ionization_mechanism"] == mechanism
        )
        (family,) = [
            elected
            for elected in elect_same_ion_families(candidates)
            if elected["ion"] == ion
        ]
        held = {(family["formula"], family["ionization_mechanism"])} | {
            (member["formula"], member["ionization_mechanism"])
            for member in family.get(SAME_ION_ALTERNATIVES, [])
        }

        (proposed,) = self._propose([(formula, mechanism)])

        assert {
            (member["formula"], member["ionization_mechanism"]) for member in proposed
        } == held - {(formula, mechanism)}
        assert {member["ion"] for member in proposed} <= {ion}

    def test_a_protonated_amide_is_an_ammoniated_aldehyde(self):
        # The case the rule is for: a list names dimethylformamide, and its
        # protonated ion is acrolein's ammonium adduct to the last electron.
        (family,) = self._propose([("C3H7N1O1", "[M+H]+")])
        assert family == [
            {"formula": "C3H4O", "ion": "C3H8NO+", "ionization_mechanism": "[M+NH4]+"}
        ]

    def test_each_reading_gets_its_own_family_in_order(self):
        families = self._propose([("C3H7NO", "[M+H]+"), ("CO2", "[M+H]+")])
        assert [len(family) for family in families] == [1, 0]

    def test_a_mechanism_of_the_other_charge_makes_no_reading(self):
        (family,) = self._propose(
            [("C3H7NO", "[M+H]+")], notations=["[M+H]+", "[M-H]-"]
        )
        assert family == []

    def test_a_labelled_mechanism_makes_no_reading_either_way(self):
        # The finder never proposes the labelled neutral, so a family through
        # the 15N reagent is one it never holds.
        notations = ["[M-H]-", "[M+[15N]O3]-", "[M+NO3]-"]
        config = CompositionSearchConfig(
            ionizations=",".join(notations),
            element_count_ranges="C1-20 H0-40 N0-3 O0-15",
        )
        through_label, from_label = self._propose(
            [("C5H9NO7", "[M-H]-"), ("C5H8O4", "[M+[15N]O3]-")],
            notations=notations,
            config=config,
        )
        assert [member["ionization_mechanism"] for member in through_label] == [
            "[M+NO3]-"
        ]
        assert from_label == []

    def test_the_element_box_cuts_a_reading_as_it_cuts_a_candidate(self):
        # Through [M+H]+, the ammonium adduct of C6H12O6 is C6H15NO6 - one nitrogen
        # more than a box that holds none.
        narrow = CompositionSearchConfig(
            ionizations=",".join(self.MECHANISMS),
            element_count_ranges="C1-20 H0-40 O0-10",
        )
        (family,) = self._propose([("C6H12O6", "[M+NH4]+")], config=narrow)
        assert family == []

    def test_the_heuristic_rules_cut_a_reading_as_they_cut_a_candidate(self):
        # Acrolein sits outside a context that asks for at least two hydrogens
        # per carbon.
        strict = HeuristicFilterConfig(
            use_senior=True,
            context_ratio_windows={"H/C": (2.0, 3.0)},
            context_windows_gate=True,
        )
        (family,) = self._propose([("C3H7NO", "[M+H]+")], heuristics=strict)
        assert family == []

    def test_the_reagent_ion_itself_is_no_reading(self):
        # Protonated ammonia is the ammonium ion, which is no analyte at all:
        # the finder calls that an ionization peak rather than a composition,
        # even under a box that floors nothing at one.
        floorless = CompositionSearchConfig(
            ionizations=",".join(self.MECHANISMS),
            element_count_ranges="C0-20 H0-40 N0-3 O0-10",
        )
        (family,) = self._propose([("NH3", "[M+H]+")], config=floorless)
        assert family == []

    def test_an_unreadable_reading_has_no_family(self):
        assert self._propose([("not a formula", "[M+H]+"), ("C3H7NO", "[M+Xx]+")]) == [
            [],
            [],
        ]


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

        monkeypatch.setattr(line_model, "predict_isotopes", counting)
        candidates = [
            _candidate("C6H11NO8", "[M-H]-", "C6H10NO8-"),
            _candidate("C5H10NO5", "[M+CO3]-", "C6H10NO8-"),
            _candidate("C6H10O5", "[M+NO3]-", "C6H10NO8-"),
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
            _candidate("C6H12O6", "[M+H]+", "C6H13O6+", error_ppm=-2.0),
            _candidate("C5H8N2O5", "[M+H]+", "C5H9N2O5+", error_ppm=0.5),
            _candidate("C4H10N4O4", "[M+H]+", "C4H11N4O4+", error_ppm=1.0),
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
            _candidate("C2H7N5O", "[M+H]+", "C2H8N5O+", error_ppm=1.0),
            _candidate("C6H12O6", "[M+H]+", "C6H13O6+", error_ppm=-1.0),
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


class TestOneFamilyEnumerator:
    """One enumerator gives every reading of an ion: the finder's election
    completes a committed winner's family from it, and a list's matches take
    theirs from it, so the three are one family."""

    MECHANISMS = ["[M+H]+", "[M+NH4]+", "[M+CH4N2O+H]+"]
    CONFIG = CompositionSearchConfig(
        ionizations=",".join(MECHANISMS),
        element_count_ranges="C1-20 H0-40 N0-3 O0-10",
        mass_range_ppm=2.0,
    )

    def test_an_ion_s_family_holds_every_reading_through_the_run_s_channels(self):
        family = heuristic_filter.same_ion_family(
            "C10H23N2O3+", self.MECHANISMS, self.CONFIG
        )
        assert [(m["formula"], m["ionization_mechanism"]) for m in family] == [
            ("C10H22N2O3", "[M+H]+"),
            ("C10H19NO3", "[M+NH4]+"),
            ("C9H18O2", "[M+CH4N2O+H]+"),
        ]
        assert {m["ion"] for m in family} == {"C10H23N2O3+"}

    def test_two_spellings_of_one_ion_have_one_family(self):
        one = heuristic_filter.same_ion_family(
            "C10H23N2O3+", self.MECHANISMS, self.CONFIG
        )
        assert one == heuristic_filter.same_ion_family(
            "C10H23O3N2+", self.MECHANISMS, self.CONFIG
        )

    def test_a_mechanism_of_another_charge_makes_no_reading(self):
        assert (
            heuristic_filter.same_ion_family(
                "C10H23N2O3-", self.MECHANISMS, self.CONFIG
            )
            == []
        )

    def test_a_list_s_reading_is_its_ion_s_family_less_itself(self):
        family = heuristic_filter.same_ion_family(
            "C10H23N2O3+", self.MECHANISMS, self.CONFIG
        )
        (proposed,) = propose_same_ion_readings(
            [("C10H22N2O3", "[M+H]+")], self.MECHANISMS, self.CONFIG
        )
        assert proposed == family[1:]

    def test_a_reading_through_a_channel_the_run_did_not_search_has_none(self):
        # Its ion has readings through the searched channels, but a reading the
        # run could not have made is no row of the run's to give a family.
        assert propose_same_ion_readings(
            [("C10H19NO3", "[M+NH4]+")],
            ["[M+H]+", "[M+CH4N2O+H]+"],
            self.CONFIG,
        ) == [[]]


class TestTheElectionReadsOneIonAsOneIon:
    def test_two_spellings_of_one_ion_are_one_family(self):
        # The finder writes its ions in Hill order; a family is grouped by the
        # ion's composition and charge all the same, as every pass tells one
        # ion from another.
        acid = _candidate("C11H20O7", "[M-H]-", "C11H19O7-")
        adduct = _candidate("C10H18O5", "[M+HCOO]-", "C11H19O7-")
        adduct["ion"] = "C11H19O7-".replace("H19O7", "O7H19")
        (elected,) = elect_same_ion_families([acid, adduct])
        assert elected["formula"] == "C10H18O5"
        assert [r["formula"] for r in elected[SAME_ION_ALTERNATIVES]] == ["C11H20O7"]

    def test_the_same_composition_at_another_charge_is_another_ion(self):
        one = _candidate("C6H12O6", "[M+H]+", "C6H13O6+")
        other = _candidate("C6H14O6", "[M-H]-", "C6H13O6-")
        assert len(elect_same_ion_families([one, other])) == 2


class TestAWinnerIsElectedAgainOverItsWholeFamily:
    MECHANISMS = TestOneFamilyEnumerator.MECHANISMS
    CONFIG = TestOneFamilyEnumerator.CONFIG

    def _family(self, ion="C10H23N2O3+"):
        return heuristic_filter.same_ion_family(ion, self.MECHANISMS, self.CONFIG)

    def test_a_reading_the_window_left_out_is_added_and_can_win(self):
        # The window's row cap kept only the protonated reading; the urea
        # adduct carries the most mass and is the family's elected reading.
        protonated = dict(
            _candidate("C10H22N2O3", "[M+H]+", "C10H23N2O3+"),
            isotopic_pattern_score=0.9,
        )
        completed = heuristic_filter.complete_same_ion_family(
            protonated, self._family()
        )
        assert completed["formula"] == "C9H18O2"
        assert completed["ionization_mechanism"] == "[M+CH4N2O+H]+"
        # What was measured is the ion's and stays.
        assert completed["isotopic_pattern_score"] == 0.9
        assert completed["ion"] == "C10H23N2O3+"
        assert [
            (r["formula"], r["ionization_mechanism"])
            for r in completed[SAME_ION_ALTERNATIVES]
        ] == [("C10H19NO3", "[M+NH4]+"), ("C10H22N2O3", "[M+H]+")]
        assert completed[SAME_ION_ALTERNATIVES][0]["neutral_mass"] == pytest.approx(
            calculate_mass(formula="C10H19NO3")
        )

    def test_a_complete_family_is_left_as_it_was(self):
        members = [
            _candidate(formula, notation, "C10H23N2O3+")
            for formula, notation in (
                ("C10H22N2O3", "[M+H]+"),
                ("C10H19NO3", "[M+NH4]+"),
                ("C9H18O2", "[M+CH4N2O+H]+"),
            )
        ]
        (elected,) = elect_same_ion_families(members)
        assert (
            heuristic_filter.complete_same_ion_family(elected, self._family())
            is elected
        )

    def test_another_ion_s_family_adds_nothing(self):
        lone = _candidate("C3H4", None, "C2H3+")
        assert heuristic_filter.complete_same_ion_family(lone, self._family()) is lone
