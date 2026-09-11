"""Assignment profile presets: the data, the fingerprint, and the grid they imply.

These are the pure half of step 1.1 of ``docs/dev/assignment_quality_plan.md``.
The properties pinned here are the ones the engine leans on and that a later
edit to the presets could quietly break: that a context can only narrow a grid,
that the resolved grid never outgrows what the run config is allowed to carry,
that the fingerprint is deterministic when a mode carries several diagnostic
mechanisms, and that the identity profile really is an identity.
"""

import pytest

from mascope_tools.composition import profiles as P


MAX_FORMULA_RANGE_SPECIES = 12  # PeakAssignmentConfig's ceiling, restated


class TestPresets:
    def test_every_profile_names_a_known_context(self):
        for profile in P.REAGENT_PROFILES.values():
            assert P.get_chemistry_context(profile.default_context)

    def test_every_preset_pair_fits_the_run_config_species_cap(self):
        # The grid is enumerated by a depth-first search whose depth is the
        # species count, which is why the run config caps it. A preset that
        # outgrew the cap has to fail here rather than at run time.
        for profile in P.REAGENT_PROFILES.values():
            for context in P.CHEMISTRY_CONTEXTS.values():
                ranges = P.resolve_element_ranges(profile, context)
                assert len(ranges.split()) <= MAX_FORMULA_RANGE_SPECIES

    def test_every_profile_grid_parses(self):
        for profile in P.REAGENT_PROFILES.values():
            assert P.parse_element_ranges(profile.element_ranges)

    def test_lookup_accepts_aliases_and_case(self):
        assert P.get_reagent_profile("bromide") is P.BR
        assert P.get_reagent_profile("Br") is P.BR
        assert P.get_chemistry_context("AMBIENT") is P.AMBIENT_AIR
        assert P.get_chemistry_context("urea-cims") is P.URONIUM

    def test_unknown_names_raise(self):
        with pytest.raises(KeyError):
            P.get_reagent_profile("krypton-cims")
        with pytest.raises(KeyError):
            P.get_chemistry_context("mars")


class TestFingerprint:
    def test_the_urea_adduct_identifies_the_uronium_profile(self):
        assert P.detect_reagent_profile(["+H+", "+(CH4N2O)H+"], "+") is P.UR

    def test_bromide_identifies_the_bromide_profile(self):
        assert P.detect_reagent_profile(["+Br-", "-H+"], "-") is P.BR

    def test_iodide_identifies_the_iodide_profile(self):
        assert P.detect_reagent_profile(["+I-", "-H+"], "-") is P.IODIDE

    def test_the_labelled_nitrate_wins_over_the_unlabelled_one(self):
        # A 15N deployment usually keeps both mechanisms on the mode, and the
        # labelled one is the more specific statement.
        assert P.detect_reagent_profile(["+NO3-", "+^NO3-"], "-") is P.NO3_15N
        assert P.detect_reagent_profile(["+^NO3-", "+NO3-"], "-") is P.NO3_15N

    def test_unlabelled_nitrate_alone_is_the_unlabelled_profile(self):
        assert P.detect_reagent_profile(["+NO3-"], "-") is P.NO3

    def test_resolution_does_not_depend_on_mechanism_order(self):
        forwards = P.detect_reagent_profile(["+Br-", "+(CH4N2O)H+"], "-")
        backwards = P.detect_reagent_profile(["+(CH4N2O)H+", "+Br-"], "-")
        assert forwards is backwards

    def test_nothing_diagnostic_falls_back_to_the_polarity(self):
        assert P.detect_reagent_profile(["+H+"], "+") is P.ESI_POS
        assert P.detect_reagent_profile(["-H+"], "-") is P.ESI_NEG

    def test_the_polarity_is_read_however_it_is_spelled(self):
        # A sample row carries "+"; other rows and callers spell it out. A
        # fallback that missed on the spelling would be indistinguishable from
        # a sample that said nothing.
        assert P.detect_reagent_profile([], "positive") is P.ESI_POS
        assert P.detect_reagent_profile([], "Negative") is P.ESI_NEG

    def test_nothing_at_all_is_the_identity_profile(self):
        # Not a guess: a sample the engine cannot read keeps the behaviour it
        # had before profiles existed.
        assert P.detect_reagent_profile([], None) is P.NO_PROFILE
        assert P.detect_reagent_profile([], "") is P.NO_PROFILE


class TestElementRanges:
    def test_the_context_narrows_the_grid(self):
        # Bromide's own grid allows two sulfurs; ambient air allows one.
        ranges = P.parse_element_ranges(P.resolve_element_ranges(P.BR, P.AMBIENT_AIR))
        assert ranges["S"] == (0, 1)
        assert ranges["Br"] == (0, 2)  # untouched: the cap equals the grid

    def test_the_context_never_widens_the_grid(self):
        for profile in P.REAGENT_PROFILES.values():
            grid = P.parse_element_ranges(profile.element_ranges)
            for context in P.CHEMISTRY_CONTEXTS.values():
                resolved = P.parse_element_ranges(
                    P.resolve_element_ranges(profile, context)
                )
                for symbol, (_, high) in resolved.items():
                    assert high <= grid[symbol][1], (
                        f"{context.name} widened {symbol} for {profile.name}"
                    )

    def test_an_element_capped_at_zero_leaves_the_grid(self):
        # Chlorine and bromine are not uronium chemistry; dropping them takes a
        # level off the enumeration rather than merely bounding it.
        ranges = P.resolve_element_ranges(P.BR, P.URONIUM)
        assert "Cl" not in ranges and "Br" not in ranges

    def test_an_organic_grid_floors_carbon_at_one(self):
        for name in ("BR", "UR", "NO3", "IODIDE", "ESI_POS"):
            profile = P.get_reagent_profile(name)
            ranges = P.parse_element_ranges(
                P.resolve_element_ranges(profile, P.NO_CONTEXT)
            )
            assert ranges["C"][0] == 1

    def test_the_carbon_floor_can_be_overridden(self):
        ranges = P.parse_element_ranges(
            P.resolve_element_ranges(P.BR, P.NO_CONTEXT, min_carbon=0)
        )
        assert ranges["C"][0] == 0

    def test_round_trip_through_the_range_grammar(self):
        for profile in P.REAGENT_PROFILES.values():
            parsed = P.parse_element_ranges(profile.element_ranges)
            assert P.format_element_ranges(parsed) == profile.element_ranges

    def test_an_invalid_token_raises(self):
        with pytest.raises(ValueError):
            P.parse_element_ranges("C0-40 nonsense")


class TestIdentityProfile:
    def test_it_reproduces_the_engines_historical_grid_and_window(self):
        # The regression guard of the plan: naming `none` must leave a run
        # indistinguishable from one computed before profiles existed.
        assert (
            P.resolve_element_ranges(P.NO_PROFILE, P.NO_CONTEXT)
            == "C0-100 H0-100 O0-100 N0-100"
        )
        assert P.resolve_mz_precision_ppm(P.NO_PROFILE, "orbi") == 10.0
        assert P.resolve_mz_precision_ppm(P.NO_PROFILE, "tof") == 10.0

    def test_the_identity_context_constrains_no_ratio(self):
        assert P.NO_CONTEXT.ratio_windows() == {}


class TestInstrumentWindow:
    def test_an_orbitrap_gets_the_narrow_window(self):
        assert P.resolve_mz_precision_ppm(P.BR, "orbi") == 3.0

    def test_a_tof_gets_the_wider_one(self):
        # Wider than an Orbitrap's, because a 3 ppm window would find nothing
        # on a TOF - but not wider than the engine's historical window, which
        # the gate measured as a regression on all three TOF sets. Widening it
        # waits for the instrument-scaled fit of step 2.1.
        assert P.resolve_mz_precision_ppm(P.BR, "tof") == 10.0
        assert P.resolve_mz_precision_ppm(P.BR, "tof") > P.resolve_mz_precision_ppm(
            P.BR, "orbi"
        )

    def test_an_unknown_instrument_class_falls_back(self):
        assert P.resolve_mz_precision_ppm(P.BR, None) == P.DEFAULT_MZ_PRECISION_PPM
        assert P.resolve_mz_precision_ppm(P.BR, "quadrupole") == (
            P.DEFAULT_MZ_PRECISION_PPM
        )


class TestRatioWindows:
    def test_a_context_publishes_only_the_windows_it_sets(self):
        windows = P.AMBIENT_AIR.ratio_windows()
        assert set(windows) == {"H/C", "O/C", "N/C", "DBE/C"}
        assert windows["O/C"] == (0.0, 1.5)

    def test_uronium_admits_more_nitrogen_than_ambient_air(self):
        # The source is N-rich by construction; ambient air is not.
        assert (
            P.URONIUM.ratio_windows()["N/C"][1]
            > (P.AMBIENT_AIR.ratio_windows()["N/C"][1])
        )
