"""The bound on the formulas a known source contributes, and the contexts' ceiling.

A source's window and the context's ceiling are intersected axis by axis, so the
narrower of the two holds on every axis and an unbounded axis bounds nothing.
What a window admits is read off the formula: its elements, its carbon count,
and its mass, where a mass cap also turns away a formula whose mass is not
known.
"""

import pytest

from mascope_tools.composition import profiles as P
from mascope_tools.composition.known_window import KnownWindow, is_element


CHNOS = frozenset({"C", "H", "N", "O", "S"})
ATMOSPHERIC = KnownWindow(CHNOS, max_carbon=40, max_mass=700.0)
D4 = ("C8H24O4Si4", 296.0752)
TEP = ("C6H15O4P", 182.0708)


class TestAdmits:
    def test_an_element_outside_the_window_is_turned_away(self):
        assert not ATMOSPHERIC.admits(*D4)
        assert not ATMOSPHERIC.admits(*TEP)
        assert ATMOSPHERIC.admits("C10H16O3", 184.1099)

    def test_an_unbounded_window_admits_anything(self):
        assert KnownWindow().admits(*D4)
        # An unbounded window does not even need the formula to be readable.
        assert KnownWindow().admits("not a formula", None)

    def test_the_carbon_cap_is_inclusive(self):
        window = KnownWindow(max_carbon=10)
        assert window.admits("C10H16O3", 184.1099)
        assert not window.admits("C11H18O3", 198.1256)

    def test_a_mass_cap_turns_away_an_unknown_mass(self):
        window = KnownWindow(max_mass=300.0)
        assert window.admits("C10H16O3", 184.1099)
        assert not window.admits("C10H16O3", None)
        assert window.admits("C20H30O13", 299.9)
        assert not window.admits("C20H30O13", 478.17)

    def test_an_unreadable_formula_is_outside_a_bounded_window(self):
        assert not ATMOSPHERIC.admits("Xx12", 100.0)


class TestIntersect:
    def test_the_narrower_bound_holds_on_every_axis(self):
        source = KnownWindow(
            frozenset({"C", "H", "O", "Si"}), max_carbon=50, max_mass=500.0
        )
        ceiling = KnownWindow(
            frozenset({"C", "H", "N", "O", "Si", "P"}), max_carbon=40, max_mass=700.0
        )
        assert source.intersect(ceiling) == KnownWindow(
            frozenset({"C", "H", "O", "Si"}), max_carbon=40, max_mass=500.0
        )

    def test_an_unbounded_axis_takes_the_others_bound(self):
        assert KnownWindow().intersect(ATMOSPHERIC) == ATMOSPHERIC
        assert ATMOSPHERIC.intersect(KnownWindow()) == ATMOSPHERIC

    def test_no_ceiling_leaves_the_window_as_it_is(self):
        assert ATMOSPHERIC.intersect(None) == ATMOSPHERIC
        assert KnownWindow().intersect(None).is_unbounded


class TestStoredForm:
    @pytest.mark.parametrize(
        "window", [ATMOSPHERIC, KnownWindow(), KnownWindow(max_mass=450.5)]
    )
    def test_a_window_round_trips_through_json(self, window):
        assert KnownWindow.from_json(window.to_json()) == window

    def test_symbols_are_checked_as_written(self):
        assert is_element("Si") and is_element("Cl") and is_element("I")
        assert not is_element("CL")
        assert not is_element("Xx")
        with pytest.raises(ValueError, match="Xx"):
            KnownWindow(frozenset({"C", "Xx"}))


class TestTheContextCeiling:
    """Every shipped context sets the same ceiling; the identity context sets none."""

    @pytest.mark.parametrize(
        "context",
        [c for c in P.CHEMISTRY_CONTEXTS.values() if c.name != P.NO_CONTEXT.name],
        ids=lambda c: c.name,
    )
    def test_a_shipped_context_opens_the_listed_families_at_the_atmospheric_caps(
        self, context
    ):
        assert context.known_window == KnownWindow(
            frozenset({"C", "H", "N", "O", "S", "Si", "P", "F", "Cl", "Br", "I"}),
            max_carbon=40,
            max_mass=700.0,
        )

    def test_the_identity_context_sets_no_ceiling(self):
        assert P.NO_CONTEXT.known_window is None

    def test_the_ceiling_admits_what_the_shipped_lists_hold(self):
        ceiling = P.KNOWN_WINDOW_CEILING
        for formula, mass in [
            ("C12H36O6Si6", 444.1127),  # D6
            ("C12HF23O2", 613.9607),  # perfluorododecanoic acid
            ("C9H11Cl3NO3PS", 348.9263),  # chlorpyrifos
            ("HIO3", 175.8970),
            ("IBr", 205.8228),
        ]:
            assert ceiling.admits(formula, mass), formula
        # ...and still holds a mirror to the carbon and mass the lists stay under.
        assert not ceiling.admits("C41H84", 576.66)
        assert not ceiling.admits("C30H50O20", 730.3)
