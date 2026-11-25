"""Assignment profile presets: the data, the fingerprint, and the grid they imply.

These are the pure half of step 1.1 of ``docs/dev/assignment_quality_plan.md``.
The properties pinned here are the ones the engine leans on and that a later
edit to the presets could quietly break: that a context can only narrow a grid,
that the resolved grid never outgrows what the run config is allowed to carry,
that the fingerprint is deterministic when a mode carries several diagnostic
mechanisms, and that the identity profile really is an identity.
"""

import dataclasses

import pytest

from mascope_tools.composition import formula_shape
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
        assert P.detect_reagent_profile(["[M+H]+", "[M+CH4N2O+H]+"], "+") is P.UR

    def test_bromide_identifies_the_bromide_profile(self):
        assert P.detect_reagent_profile(["[M+Br]-", "[M-H]-"], "-") is P.BR

    def test_iodide_identifies_the_iodide_profile(self):
        assert P.detect_reagent_profile(["[M+I]-", "[M-H]-"], "-") is P.IODIDE

    def test_the_labelled_nitrate_wins_over_the_unlabelled_one(self):
        # A 15N deployment usually keeps both mechanisms on the mode, and the
        # labelled one is the more specific statement.
        assert P.detect_reagent_profile(["[M+NO3]-", "[M+^NO3]-"], "-") is P.NO3_15N
        assert P.detect_reagent_profile(["[M+^NO3]-", "[M+NO3]-"], "-") is P.NO3_15N

    def test_unlabelled_nitrate_alone_is_the_unlabelled_profile(self):
        assert P.detect_reagent_profile(["[M+NO3]-"], "-") is P.NO3

    def test_resolution_does_not_depend_on_mechanism_order(self):
        forwards = P.detect_reagent_profile(["[M+Br]-", "[M+CH4N2O+H]+"], "-")
        backwards = P.detect_reagent_profile(["[M+CH4N2O+H]+", "[M+Br]-"], "-")
        assert forwards is backwards

    def test_a_legacy_spelling_identifies_the_same_profile(self):
        # A row the mechanism table has not rewritten yet is the same
        # mechanism, and the fingerprint is read off the mechanism.
        assert P.detect_reagent_profile(["+H+", "+(CH4N2O)H+"], "+") is P.UR
        assert P.detect_reagent_profile(["+Br-", "-H+"], "-") is P.BR
        assert P.detect_reagent_profile(["+"], "+", "orbi") is P.EASYIC_POS

    def test_nothing_diagnostic_falls_back_to_the_polarity(self):
        # Protonation or deprotonation alone is how an electrospray or APCI
        # mode is written: no reagent, no electron transfer.
        assert P.detect_reagent_profile(["[M+H]+"], "+") is P.ESI_POS
        assert P.detect_reagent_profile(["[M-H]-"], "-") is P.ESI_NEG

    def test_electron_transfer_on_an_orbitrap_is_the_charge_transfer_source(self):
        # An Orbitrap's EASY-IC source is declared as electron transfer and
        # nothing else. Before the profile existed such a mode fell to the ESI
        # preset - a C60 grid with no matrix prior - and committed formulas no
        # atmosphere makes.
        assert P.detect_reagent_profile(["[M]+."], "+", "orbi") is P.EASYIC_POS
        assert P.detect_reagent_profile(["[M]-."], "-", "orbi") is P.EASYIC_NEG

    def test_electron_transfer_elsewhere_keeps_the_path_it_had(self):
        # An ambient-ion mode on an APi-TOF - the air's own ions, no reagent -
        # is declared with the same electron transfer and is not a
        # fluoranthene beam. The fleet holds such streams; they keep the ESI
        # preset, as does a sample whose instrument nobody could read.
        assert P.detect_reagent_profile(["[M]+."], "+", "tof") is P.ESI_POS
        assert P.detect_reagent_profile(["[M]-."], "-", "tof") is P.ESI_NEG
        assert P.detect_reagent_profile(["[M]-."], "-", None) is P.ESI_NEG
        assert P.detect_reagent_profile(["[M]-."], "-") is P.ESI_NEG

    def test_a_declared_secondary_channel_does_not_unmake_the_source(self):
        # A charge-transfer mode that also declares proton transfer or
        # deprotonation is still the charge-transfer source: electron transfer
        # is what says so, and the declared channel is searched as the mode's
        # own.
        assert (
            P.detect_reagent_profile(["[M]+.", "[M+H]+"], "+", "orbi") is P.EASYIC_POS
        )
        assert (
            P.detect_reagent_profile(["[M]-.", "[M-H]-"], "-", "orbi") is P.EASYIC_NEG
        )

    def test_a_reagent_wins_over_electron_transfer(self):
        # A reagent mode that also declares electron transfer is that reagent's
        # source; electron transfer only decides where no reagent does.
        assert (
            P.detect_reagent_profile(["[M+NO3]-", "[M]-.", "[M-H]-"], "-", "orbi")
            is P.NO3
        )
        assert P.detect_reagent_profile(["[M]+.", "[M+CH4N2O+H]+"], "+", "orbi") is P.UR
        assert P.detect_reagent_profile(["[M+Br]-", "[M]-."], "-", "orbi") is P.BR

    def test_a_profile_of_the_wrong_polarity_is_never_the_answer(self):
        # A row stored under the wrong polarity, or a caller that did not
        # filter the panel, must not turn a negative sample into a positive
        # source. The polarity is read however it is spelled.
        assert P.detect_reagent_profile(["[M]+.", "[M]-."], "-", "orbi") is P.EASYIC_NEG
        assert (
            P.detect_reagent_profile(["[M]-.", "[M]+."], "positive", "orbi")
            is P.EASYIC_POS
        )
        assert P.detect_reagent_profile(["[M+CH4N2O+H]+", "[M+Br]-"], "+") is P.UR
        assert P.detect_reagent_profile(["[M+CH4N2O+H]+"], "neg") is P.ESI_NEG

    def test_the_charge_transfer_profiles_take_the_ambient_prior(self):
        # Never the ESI profiles' absence of a context: the ambient windows are
        # what reject the nitrogen-rich, hydrogen-poor formulas the bare grid
        # committed.
        for profile in (P.EASYIC_POS, P.EASYIC_NEG):
            assert P.get_chemistry_context(profile.default_context) is P.AMBIENT_AIR
            ranges = P.parse_element_ranges(
                P.resolve_element_ranges(profile, P.AMBIENT_AIR)
            )
            assert ranges["N"] == (0, 3)
            assert ranges["S"] == (0, 1)
            assert ranges["C"][1] == 40

    def test_formate_is_a_channel_of_every_negative_reagent(self):
        # The chamber lesson: with no way to write a formate adduct, C10
        # products read as C11 acids at the top tier. Formate is an anion of
        # any air sample, so every negative reagent profile may see it; no
        # positive profile and no electrospray does.
        for profile in (P.NO3, P.NO3_15N, P.BR, P.IODIDE):
            assert "[M+HCOO]-" in profile.secondary_adducts, profile.name
        for profile in (P.UR, P.ESI_POS, P.ESI_NEG, P.EASYIC_POS, P.EASYIC_NEG):
            assert "[M+HCOO]-" not in profile.secondary_adducts, profile.name

    def test_the_charge_transfer_channels_are_secondary_not_declared(self):
        # Methyl loss beside hydride abstraction and proton transfer: what
        # charge transfer leaves of alpha-pinene at m/z 121 and of a cyclic
        # siloxane at its base peak.
        assert P.EASYIC_POS.secondary_adducts == ("[M-H]+", "[M+H]+", "[M-CH3]+")
        assert P.EASYIC_NEG.secondary_adducts == ("[M-H]-",)
        assert P.get_reagent_profile("charge-transfer") is P.EASYIC_POS
        assert P.get_reagent_profile("ct-") is P.EASYIC_NEG

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

    def test_a_grid_takes_the_tables_carbon_floor(self, monkeypatch):
        # Open since decision MAS-979: a carbon-free formula is searched, and
        # the carbon-free signature caps it.
        assert formula_shape.GRID_CARBON_FLOOR == 0
        for name in ("BR", "UR", "NO3", "IODIDE", "EASYIC_POS", "ESI_POS"):
            profile = P.get_reagent_profile(name)
            assert profile.min_carbon == formula_shape.GRID_CARBON_FLOOR
            ranges = P.parse_element_ranges(
                P.resolve_element_ranges(profile, P.NO_CONTEXT)
            )
            assert ranges["C"][0] == 0

    def test_a_profile_may_set_a_floor_of_its_own_grid(self):
        floored = dataclasses.replace(P.BR, min_carbon=1)
        ranges = P.parse_element_ranges(P.resolve_element_ranges(floored, P.NO_CONTEXT))
        assert ranges["C"][0] == 1
        ranges = P.parse_element_ranges(
            P.resolve_element_ranges(floored, P.NO_CONTEXT, min_carbon=0)
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


class TestContextPriors:
    """Step 3.6: the formula shapes an atmosphere holds only on corroboration."""

    @pytest.mark.parametrize("context", [P.AMBIENT_AIR, P.CHAMBER, P.INDOOR_AIR])
    def test_every_atmospheric_context_carries_both_priors(self, context):
        assert context.priors == (P.PRIOR_SULFUR, P.PRIOR_ODD_NITROGEN)

    def test_uronium_carries_the_sulfur_prior_alone(self):
        # Its analytes are the reduced-nitrogen bases the other prior doubts.
        assert P.URONIUM.priors == (P.PRIOR_SULFUR,)

    @pytest.mark.parametrize(
        "context", [P.OBJECT_HEADSPACE, P.COMBUSTION, P.WATER, P.FOOD, P.NO_CONTEXT]
    )
    def test_a_matrix_with_its_own_sulfur_and_nitrogen_carries_none(self, context):
        assert context.priors == ()
        assert context.priors_met("C10H16O4S") == ()

    def test_sulfur_meets_the_sulfur_prior(self):
        assert P.AMBIENT_AIR.priors_met("C10H16O4S") == (P.PRIOR_SULFUR,)

    @pytest.mark.parametrize("formula", ["C2H4N2", "C2H3N3", "C5H8N2O3", "C4H6N4O7"])
    def test_two_nitrogens_with_under_two_oxygens_each_meet_the_nitrogen_prior(
        self, formula
    ):
        assert P.AMBIENT_AIR.priors_met(formula) == (P.PRIOR_ODD_NITROGEN,)

    @pytest.mark.parametrize(
        "formula",
        [
            # A dinitrate and a nitroaromatic: two and three oxygens per
            # nitrogen are an organonitrate's and a nitro group's own counts.
            "C5H8N2O8",
            "C6H4N2O4",
            # One nitrogen is not the shape, however few its oxygens.
            "C5H5N",
            "C10H15NO",
        ],
    )
    def test_organonitrates_and_single_nitrogens_meet_no_prior(self, formula):
        assert P.AMBIENT_AIR.priors_met(formula) == ()

    def test_a_formula_meeting_both_names_both_in_order(self):
        assert P.AMBIENT_AIR.priors_met("C3H6N2S") == (
            P.PRIOR_SULFUR,
            P.PRIOR_ODD_NITROGEN,
        )

    def test_a_labelled_nitrogen_counts_as_nitrogen(self):
        assert P.AMBIENT_AIR.priors_met("C2H4N[15N]") == (P.PRIOR_ODD_NITROGEN,)

    def test_an_unreadable_formula_meets_none(self):
        assert P.AMBIENT_AIR.priors_met("not a formula") == ()
        assert P.AMBIENT_AIR.priors_met(None) == ()


class TestFittedWindow:
    """Step 3.6: a TOF's window bounded by the sample's own fitted width."""

    def test_only_a_tof_is_bounded(self):
        assert P.FITTED_WINDOW_INSTRUMENTS == frozenset({"tof"})

    def test_it_reaches_the_centre_and_then_the_widths(self):
        assert P.fitted_window_ppm(10.0, 1.0, -1.5, 6.0) == pytest.approx(7.5)
        assert P.fitted_window_ppm(10.0, 1.0, 1.5, 6.0) == pytest.approx(7.5)

    def test_it_is_never_wider_than_the_class_window(self):
        assert P.fitted_window_ppm(10.0, 3.0, 0.0, 6.0) == 10.0


# --- The class table (step 5.8b) ------------------------------------------------


CITED = P.Citation(
    "A. Author et al., A study of acids. J. Example 1 (2020) 1-10.",
    doi="10.1234/example.2020",
)
ACID = P.IonizationClass("[M-H]-", "an acidic hydrogen", "an acid", (CITED,))


def _with(*entries: P.IonizationClass) -> P.ReagentProfile:
    """The nitrate preset with a class table of fixture entries."""
    return dataclasses.replace(P.NO3, ionization_classes=entries)


class TestClassTable:
    def test_every_shipped_table_is_cited(self):
        # The entries are PKY-39's; whatever they are, each is held here.
        for profile in P.REAGENT_PROFILES.values():
            assert P.ionization_class_problems(profile) == [], profile.name

    def test_a_channel_finds_its_entry_by_any_spelling(self):
        profile = _with(ACID)

        assert P.ionization_class(profile, "[M-H]-") is ACID
        assert P.ionization_class(profile, "-H+") is ACID

    def test_a_channel_the_table_does_not_name_finds_none(self):
        assert P.ionization_class(_with(ACID), "[M+NO3]-") is None
        assert P.ionization_class(P.NO3, "[M-H]-") is None
        assert P.ionization_class(None, "[M-H]-") is None
        assert P.ionization_class(_with(ACID), None) is None

    def test_a_cited_entry_has_no_problems(self):
        isbn = P.Citation("A. Author, A book (2019).", isbn="978-3-95806-596-3")
        entry = P.IonizationClass("[M+H]+", "a basic site", "a base", (isbn,))
        # An ISBN-10 whose check digit is 10, written X.
        isbn_10 = P.Citation("B. Author, A book (1990).", isbn="0-8044-2957-X")
        older = P.IonizationClass("[M+Na]+", "an oxygen", "a polar", (isbn_10,))

        assert P.ionization_class_problems(_with(ACID, entry, older)) == []

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            (
                P.IonizationClass("acid", "an acidic hydrogen", "an acid", (CITED,)),
                "'acid' is not a mechanism",
            ),
            (P.IonizationClass("[M-H]-", " ", "an acid", (CITED,)), "no group"),
            (
                P.IonizationClass("[M-H]-", "an acidic hydrogen", "", (CITED,)),
                "no class",
            ),
            (
                P.IonizationClass("[M-H]-", "an acidic hydrogen", "an acid"),
                "no reference",
            ),
            (
                P.IonizationClass(
                    "[M-H]-", "an acidic hydrogen", "an acid", (P.Citation("A work"),)
                ),
                "neither a DOI nor an ISBN",
            ),
            (
                P.IonizationClass(
                    "[M-H]-",
                    "an acidic hydrogen",
                    "an acid",
                    (P.Citation("", doi="10.1234/x.1"),),
                ),
                "no citation",
            ),
            (
                P.IonizationClass(
                    "[M-H]-",
                    "an acidic hydrogen",
                    "an acid",
                    (P.Citation("A work", doi="doi:1234"),),
                ),
                "'doi:1234' is not a DOI",
            ),
            (
                P.IonizationClass(
                    "[M-H]-",
                    "an acidic hydrogen",
                    "an acid",
                    (P.Citation("A work", isbn="978-3-95806-596-4"),),
                ),
                "'978-3-95806-596-4' is not a valid ISBN",
            ),
            (
                # Its weighted sum leaves 1 over eleven, not 0.
                P.IonizationClass(
                    "[M-H]-",
                    "an acidic hydrogen",
                    "an acid",
                    (P.Citation("A work", isbn="0-8044-2952-X"),),
                ),
                "'0-8044-2952-X' is not a valid ISBN",
            ),
        ],
    )
    def test_an_entry_names_its_channel_its_class_and_its_work(self, entry, expected):
        [problem] = P.ionization_class_problems(_with(entry))

        assert problem.startswith("NO3 class 1")
        assert problem.endswith(expected)

    def test_a_channel_has_one_entry(self):
        again = P.IonizationClass("-H+", "an acidic hydrogen", "an acid", (CITED,))

        [problem] = P.ionization_class_problems(_with(ACID, again))

        assert problem.endswith("the channel has an entry already")

    def test_an_entry_is_recorded_with_its_works(self):
        assert ACID.record() == {
            "mechanism": "[M-H]-",
            "group": "an acidic hydrogen",
            "noun": "an acid",
            "references": [
                {
                    "citation": (
                        "A. Author et al., A study of acids. J. Example 1 (2020) 1-10."
                    ),
                    "doi": "10.1234/example.2020",
                }
            ],
        }
        assert CITED.text() == (
            "A. Author et al., A study of acids. J. Example 1 (2020) 1-10. "
            "(10.1234/example.2020)"
        )
