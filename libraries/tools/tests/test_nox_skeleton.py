"""The carbon skeleton of an organonitrate (step 3.6 of the assignment plan).

An -ONO2 or -NO2 group adds a nitrogen, two oxygens and one ring-or-double-bond
to its skeleton and takes a hydrogen's place, so the context's Van Krevelen
windows, written for CHO skeletons, misjudge dinitrates, trinitrates and
nitroaromatics. Where a run reads the skeleton, the windows and the oxygen
lattice judge it beside the formula. Pinned here: which skeletons a formula
has, which reading the windows need, that the reading is off unless switched
on, and when a run switches it on.
"""

import polars as pl
import pytest

from mascope_tools.composition import profiles as P
from mascope_tools.composition.heuristic_filter import (
    NITRO_SKELETON_DBE_PER_C,
    NOX_SKELETON_MAX_GROUPS,
    context_skeleton_groups,
    element_counts,
    nox_skeletons,
    rule_context_ratios,
)
from mascope_tools.composition.implausibility import (
    OXYGEN_LATTICE,
    implausible_signatures,
    skeleton_spared_signatures,
)
from mascope_tools.composition.models import HeuristicFilterConfig


AMBIENT = P.AMBIENT_AIR.ratio_windows()


def skeletons(formula):
    return nox_skeletons(element_counts(formula))


def groups(formula, windows=AMBIENT):
    return context_skeleton_groups(element_counts(formula), windows)


def kept(formulas, skeleton):
    frame = pl.DataFrame({"formula": list(formulas)})
    config = HeuristicFilterConfig(
        context_ratio_windows=AMBIENT,
        context_nox_skeleton=skeleton,
        context_windows_gate=True,
    )
    mask, _ = rule_context_ratios(frame, heuristics_config=config)
    return set(frame.filter(mask).get_column("formula").to_list())


class TestTheSkeletons:
    def test_a_dinitrate_reads_as_its_diol_and_its_mononitrate(self):
        # A C5 dinitrate: two groups off is the diol, one the hydroxy nitrate.
        assert skeletons("C5H8N2O8") == [
            {"C": 5, "H": 10, "O": 4},
            {"C": 5, "H": 9, "N": 1, "O": 6},
        ]

    def test_nitroglycerin_reads_as_glycerol(self):
        assert skeletons("C3H5N3O9")[0] == {"C": 3, "H": 8, "O": 3}

    def test_at_most_three_groups_are_read(self):
        assert NOX_SKELETON_MAX_GROUPS == 3
        # Four nitrate groups on a C4: the first reading takes three.
        assert skeletons("C4H6N4O12")[0] == {"C": 4, "H": 9, "N": 1, "O": 6}

    def test_a_nitroaromatic_reads_as_its_arene(self):
        # Nitrobenzene and dinitrobenzene: nitro groups, two oxygens each and
        # none left on the skeleton, which only an aromatic skeleton carries.
        assert skeletons("C6H5NO2") == [{"C": 6, "H": 6}]
        assert skeletons("C6H4N2O4") == [
            {"C": 6, "H": 6},
            {"C": 6, "H": 5, "N": 1, "O": 2},
        ]
        assert NITRO_SKELETON_DBE_PER_C == 0.5

    def test_a_nitro_group_is_read_only_off_an_aromatic_skeleton(self):
        # Nitromethane-like C3: a skeleton of DBE/C under 0.5 keeps no nitro
        # group, and two oxygens per nitrogen is no nitrate.
        assert skeletons("C3H7NO2") == []

    def test_the_nitro_test_reads_the_skeletons_unsaturation(self):
        # C6H10N2O4 sits at DBE/C 0.5 itself, but two nitro groups would leave
        # C6H12 at 0.17: no aromatic skeleton, so only the nitrate reading of
        # one group stands.
        assert skeletons("C6H10N2O4") == [{"C": 6, "H": 11, "N": 1, "O": 2}]

    def test_a_half_bond_does_not_carry_a_group(self):
        # A radical at DBE 2.5 carries two groups, not three: a third would
        # leave the skeleton C3H9O3 at DBE -0.5.
        assert skeletons("C3H6N3O9") == [
            {"C": 3, "H": 8, "N": 1, "O": 5},
            {"C": 3, "H": 7, "N": 2, "O": 7},
        ]

    def test_a_formula_with_no_nitrogen_has_none(self):
        assert skeletons("C10H16O4") == []

    def test_a_labelled_nitrate_group_is_a_nitrate_group(self):
        assert skeletons("C5H9[15N]O4") == [{"C": 5, "H": 10, "O": 2}]


class TestTheWindowsReadIt:
    def test_a_formula_inside_the_windows_needs_no_skeleton(self):
        assert groups("C10H17NO4") == 0

    def test_a_dinitrate_needs_two_groups_off(self):
        # Raw O/C 1.6 against an ambient window of 1.5.
        assert groups("C5H8N2O8") == 2

    def test_a_nitrophenol_needs_one(self):
        # Raw DBE/C 0.83 against 0.75; phenol reads 0.67.
        assert groups("C6H5NO3") == 1

    def test_a_formula_no_reading_rescues_is_none(self):
        # Hydrogen-poor with no group to take off.
        assert groups("C10H4O3") is None

    def test_a_nitrogen_an_amine_keeps_stays_in_the_window(self):
        # Three nitrogens and three oxygens read as one nitrate group on a
        # skeleton whose other two nitrogens keep their N/C: the windows admit
        # it there, which is why such a reading is a candidate at most, and
        # the odd-nitrogen prior asks it as well.
        assert groups("C6H11N3O3") == 1

    def test_below_the_carbon_floor_nothing_is_asked(self):
        assert groups("C2H2N2O6") == 0

    def test_the_rule_reads_the_skeleton_only_when_switched_on(self):
        formulas = ["C5H8N2O8", "C6H5NO3", "C10H16O7"]
        assert kept(formulas, skeleton=False) == {"C10H16O7"}
        assert kept(formulas, skeleton=True) == set(formulas)


class TestTheOxygenLatticeReadsIt:
    def test_a_dinitrates_lattice_is_spared_on_its_skeleton(self):
        assert implausible_signatures("C5H8N2O8") == (OXYGEN_LATTICE,)
        assert implausible_signatures("C5H8N2O8", nox_skeleton=True) == ()
        assert skeleton_spared_signatures("C5H8N2O8") == (OXYGEN_LATTICE,)

    def test_an_oxygen_lattice_with_no_nitrogen_stays_one(self):
        assert implausible_signatures("C4H4O8", nox_skeleton=True) == (OXYGEN_LATTICE,)
        assert skeleton_spared_signatures("C4H4O8") == ()


class TestWhenARunReadsIt:
    @pytest.mark.parametrize(
        "context", [P.AMBIENT_AIR, P.CHAMBER, P.COMBUSTION, P.WATER]
    )
    def test_the_contexts_that_make_organonitrates_say_so(self, context):
        assert context.organonitrates

    @pytest.mark.parametrize(
        "context",
        [P.INDOOR_AIR, P.OBJECT_HEADSPACE, P.FOOD, P.URONIUM, P.NO_CONTEXT],
    )
    def test_the_others_do_not(self, context):
        assert not context.organonitrates

    @pytest.mark.parametrize("profile", [P.NO3, P.NO3_15N])
    def test_a_nitrate_run_on_an_orbitrap_in_ambient_air_reads_it(self, profile):
        assert P.nox_skeleton_applies(profile, P.AMBIENT_AIR, "orbi")

    def test_not_on_a_tof(self):
        assert not P.nox_skeleton_applies(P.NO3, P.AMBIENT_AIR, "tof")

    def test_not_for_another_reagent(self):
        assert not P.nox_skeleton_applies(P.BR, P.AMBIENT_AIR, "orbi")

    def test_not_in_a_context_without_organonitrates(self):
        assert not P.nox_skeleton_applies(P.NO3, P.INDOOR_AIR, "orbi")
