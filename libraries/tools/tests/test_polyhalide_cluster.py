"""A halide attached to a neutral made of halogens only is a polyhalide anion.

IBr read through bromide is IBr2-, which a halide source makes from the halogens
of the air it samples and from its own. These pin which channels attach halogens
only, and which readings the test reaches.
"""

import pytest

from mascope_tools.composition.heuristic_filter import (
    attaches_halogens_only,
    polyhalide_cluster,
)


class TestWhichChannelsAttachHalogensOnly:
    @pytest.mark.parametrize("notation", ["+Br-", "+I-", "+Cl-", "+Br2-", "+I2-"])
    def test_a_halide_or_a_dihalide(self, notation):
        assert attaches_halogens_only(notation)

    @pytest.mark.parametrize(
        "notation",
        [
            # Not an attachment.
            "-H+",
            "+H+",
            # Anions that are not halogens only.
            "+NO3-",
            "+CO3-",
            "+BrO-",
            # A hydrate and an acid cluster of a halide carry hydrogen.
            "+H2O+Br-",
            "+(HBr)Br-",
            # A halogen taken away, and one added as a cation.
            "-Br+",
            "+Br+",
        ],
    )
    def test_no_other_channel(self, notation):
        assert not attaches_halogens_only(notation)

    @pytest.mark.parametrize("notation", [None, "", "not a mechanism", "+Xx-"])
    def test_a_notation_nobody_can_read_attaches_nothing(self, notation):
        assert not attaches_halogens_only(notation)


class TestWhichReadingsItReaches:
    @pytest.mark.parametrize(
        "formula, notation",
        [
            ("BrI", "+Br-"),
            ("IBr", "+Br-"),
            ("I2", "+Br-"),
            ("ClI", "+I-"),
            ("Br2", "+Br2-"),
        ],
    )
    def test_a_neutral_of_halogens_through_a_halide(self, formula, notation):
        assert polyhalide_cluster(formula, notation)

    @pytest.mark.parametrize("formula", ["HBr", "HOI", "C2H5I", "CH2Br2", "IO3"])
    def test_one_other_element_is_enough_to_leave_it(self, formula):
        assert not polyhalide_cluster(formula, "+Br-")

    @pytest.mark.parametrize("notation", ["+NO3-", "-H+", "+H2O+Br-", None])
    def test_a_reading_through_another_channel_is_not_judged(self, notation):
        assert not polyhalide_cluster("BrI", notation)

    @pytest.mark.parametrize("formula", [None, "", "()", "not a formula"])
    def test_a_formula_nobody_can_read_is_not_judged(self, formula):
        assert not polyhalide_cluster(formula, "+Br-")
