"""A nitrate cluster needs a neutral with oxygen.

Nitrate holds on to an analyte by hydrogen bonds from the analyte's
oxygen-bearing groups, so a reading that clusters nitrate with a neutral that
has no oxygen names an ion the source is unlikely to make. These pin which
channels count as such a cluster, and which readings the test reaches.
"""

import pytest

from mascope_tools.composition.heuristic_filter import (
    clusters_on_oxygen,
    oxygen_free_cluster,
)


class TestWhichChannelsHoldOnToOxygen:
    @pytest.mark.parametrize(
        "notation",
        [
            "[M+NO3]-",
            "[M+[15N]O3]-",
            "[M+^NO3]-",
            "[M+HNO3+NO3]-",
            "[M+(HNO3)2NO3]-",
            "[M+H[15N]O3+[15N]O3]-",
        ],
    )
    def test_every_spelling_of_a_nitrate_cluster(self, notation):
        assert clusters_on_oxygen(notation)

    @pytest.mark.parametrize(
        "notation",
        [
            # Not a cluster at all.
            "[M-H]-",
            "[M+H]+",
            # Clusters of anions that hold on to anything they can.
            "[M+Br]-",
            "[M+I]-",
            # Carbonate, left out on the gate's measurement.
            "[M+CO3]-",
            # Nitrite is not nitrate, and a hydrate is not the acid cluster.
            "[M+NO2]-",
            "+H2O+NO3-",
            # Nitrate's composition taken away rather than added, which leaves
            # an anion too, and added as a cation.
            "[M-NO3]-",
            "[M+NO3]+",
        ],
    )
    def test_no_other_channel(self, notation):
        assert not clusters_on_oxygen(notation)

    @pytest.mark.parametrize("notation", [None, "", "not a mechanism", "[M+Xx]-"])
    def test_a_notation_nobody_can_read_holds_on_to_nothing(self, notation):
        assert not clusters_on_oxygen(notation)


class TestWhichReadingsItReaches:
    @pytest.mark.parametrize(
        "formula, notation",
        [("C10H16", "[M+NO3]-"), ("C9H13N2", "[M+[15N]O3]-"), ("HBr", "[M+HNO3+NO3]-")],
    )
    def test_a_neutral_with_no_oxygen_in_a_nitrate_cluster(self, formula, notation):
        assert oxygen_free_cluster(formula, notation)

    def test_one_oxygen_is_enough_to_hold_on_to(self):
        assert not oxygen_free_cluster("C10H16O", "[M+NO3]-")

    @pytest.mark.parametrize("notation", ["[M-H]-", "[M+Br]-", None])
    def test_a_reading_through_another_channel_is_not_judged(self, notation):
        assert not oxygen_free_cluster("C10H16", notation)

    @pytest.mark.parametrize("formula", [None, "", "not a formula"])
    def test_a_formula_nobody_can_read_is_not_judged(self, formula):
        assert not oxygen_free_cluster(formula, "[M+NO3]-")
