"""Resolving a run onto an assignment profile.

The presets and the grid arithmetic are covered in ``libraries/tools``; what
these pin is the seam: which preset a run gets, what the run config can override,
what the run records about it, and - the property the whole step is judged
against - that the identity profile leaves a run exactly as it was before
profiles existed.
"""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    PeakAssignmentPresets,
    named_chemistry_context,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    SOURCE_BATCH,
    SOURCE_CONFIG,
    SOURCE_DATASET,
    SOURCE_FITTED_WIDTH,
    SOURCE_PROFILE,
    NamedContext,
    SampleChemistry,
    preview_resolutions,
    resolve_profile,
    with_fitted_window,
    with_secondary_channels,
)
from mascope_backend.api.new.peak_assignments.schemas import ProfilePreviewQueryParams
from mascope_tools.composition import profiles as presets
from mascope_tools.composition.heuristic_filter import formula_plausibility
from mascope_tools.composition.mass_accuracy import MassTrend, unwiden_sigma_ppm
from mascope_tools.composition.measurement_model import MeasurementModel


UREA = ["[M+H]+", "[M+CH4N2O+H]+"]
BROMIDE = ["[M+Br]-", "[M-H]-"]


class TestResolution:
    def test_auto_reads_the_profile_off_the_mechanisms(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        )
        assert resolved.profile is presets.UR
        assert resolved.context is presets.URONIUM

    def test_auto_takes_the_profiles_default_context(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="orbi", polarity="-"
        )
        assert resolved.profile is presets.BR
        assert resolved.context is presets.AMBIENT_AIR

    def test_a_named_profile_overrides_the_fingerprint(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="NO3"),
            BROMIDE,
            instrument_type="orbi",
            polarity="-",
        )
        assert resolved.profile is presets.NO3

    def test_a_named_context_overrides_the_profiles_default(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(context="chamber"),
            BROMIDE,
            instrument_type="orbi",
            polarity="-",
        )
        assert resolved.profile is presets.BR
        assert resolved.context is presets.CHAMBER

    def test_an_alias_resolves(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="bromide", context="ambient"),
            [],
            instrument_type="orbi",
        )
        assert resolved.profile is presets.BR
        assert resolved.context is presets.AMBIENT_AIR

    def test_a_sample_with_nothing_diagnostic_falls_back_to_polarity(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), ["[M+H]+"], instrument_type="orbi", polarity="+"
        )
        assert resolved.profile is presets.ESI_POS

    def test_electron_transfer_resolves_to_the_charge_transfer_source(self):
        # The way an Orbitrap's EASY-IC source is declared: electron transfer
        # and nothing else. It takes the ambient prior, never the ESI preset's
        # absence of one, and its own channels are opened as secondary ones.
        positive = resolve_profile(
            PeakAssignmentConfig(), ["[M]+."], instrument_type="orbi", polarity="+"
        )
        assert positive.profile is presets.EASYIC_POS
        assert positive.context is presets.AMBIENT_AIR
        assert positive.heuristics_config().context_ratio_windows == (
            presets.AMBIENT_AIR.ratio_windows()
        )
        assert positive.mode_channels == ("[M]+.",)
        assert positive.minor_channels == frozenset()
        negative = resolve_profile(
            PeakAssignmentConfig(), ["[M]-."], instrument_type="orbi", polarity="-"
        )
        assert negative.profile is presets.EASYIC_NEG
        assert negative.context is presets.AMBIENT_AIR

    def test_a_declared_charge_transfer_channel_is_the_modes_own(self):
        # A mode that declares proton transfer beside electron transfer has said
        # the source runs it: the channel is searched as the mode's own, not
        # capped as an opportunistic one, and only the profile's other channel
        # is opportunistic. APCI and APPI modes declare both signs routinely.
        resolved = resolve_profile(
            PeakAssignmentConfig(),
            ["[M]+.", "[M+H]+"],
            instrument_type="orbi",
            polarity="+",
        )
        assert resolved.profile is presets.EASYIC_POS
        assert resolved.minor_channels == frozenset()
        assert resolved.added_channels == frozenset()
        negative = resolve_profile(
            PeakAssignmentConfig(),
            ["[M]-.", "[M-H]-"],
            instrument_type="orbi",
            polarity="-",
        )
        assert negative.profile is presets.EASYIC_NEG
        assert negative.minor_channels == frozenset()

    def test_a_declared_charge_transfer_channel_the_spectrum_also_shows_stays_own(
        self,
    ):
        # The fingerprint opening the channel does not demote a declared one.
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(),
                ["[M]+.", "[M+H]+"],
                instrument_type="orbi",
                polarity="+",
            ),
            [19.0178, 200.0],
            [1.0e4, 1.0e6],
            ["[M+H]+", "[M-H]+"],
        )
        assert [e.notation for e in resolved.channel_evidence if e.present] == [
            "[M-H]+",
            "[M+H]+",
            "[M-CH3]+",
        ]
        # Methyl loss is on by the window, as hydride abstraction is, and not
        # searched where the deployment holds no mechanism for it.
        assert resolved.unavailable_channels == ("[M-CH3]+",)
        assert resolved.minor_channels == frozenset({"[M-H]+"})
        assert resolved.added_channels == frozenset({"[M-H]+"})

    def test_formate_is_partner_gated_and_carbonate_is_not(self):
        # A nitrate acquisition starting at m/z 130: both channels stay on
        # unobservable, and only formate is held to a partner.
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(),
                ["[M+^NO3]-", "[M-H]-"],
                instrument_type="orbi",
                polarity="-",
            ),
            [130.0, 700.0],
            [1.0e6, 1.0e4],
            ["[M+CO3]-", "[M+HCOO]-"],
        )
        assert resolved.minor_channels == frozenset({"[M+CO3]-", "[M+HCOO]-"})
        assert resolved.partner_gated_channels == frozenset({"[M+HCOO]-"})

    def test_the_charge_transfer_channels_are_partner_gated(self):
        resolved = with_secondary_channels(
            resolve_profile(
                PeakAssignmentConfig(), ["[M]+."], instrument_type="orbi", polarity="+"
            ),
            [42.0, 160.0],
            [1.0e6, 1.0e4],
            ["[M+H]+", "[M-H]+"],
        )
        assert resolved.minor_channels == frozenset({"[M+H]+", "[M-H]+"})
        assert resolved.partner_gated_channels == frozenset({"[M+H]+", "[M-H]+"})
        assert resolved.snapshot()["partner_gated_channels"] == ["[M+H]+", "[M-H]+"]

    def test_electron_transfer_on_a_tof_keeps_the_esi_profile(self):
        # An ambient-ion mode on an APi-TOF is declared the same way and is
        # not a fluoranthene beam.
        resolved = resolve_profile(
            PeakAssignmentConfig(), ["[M]-."], instrument_type="tof", polarity="-"
        )
        assert resolved.profile is presets.ESI_NEG
        assert resolved.context is presets.NO_CONTEXT


class TestTheContextTheDataNames:
    """Step 3.6: a dataset or a batch names its context once."""

    def resolve(self, named, config=None, notations=BROMIDE, polarity="-"):
        return resolve_profile(
            config or PeakAssignmentConfig(),
            notations,
            instrument_type="orbi",
            polarity=polarity,
            named_context=named,
        )

    def test_auto_takes_the_batchs_before_the_datasets(self):
        resolved = self.resolve(NamedContext(batch="chamber", dataset="indoor-air"))
        assert resolved.context is presets.CHAMBER
        assert resolved.context_source == SOURCE_BATCH

    def test_auto_takes_the_datasets_where_the_batch_names_none(self):
        resolved = self.resolve(NamedContext(dataset="indoor-air"))
        assert resolved.context is presets.INDOOR_AIR
        assert resolved.context_source == SOURCE_DATASET

    def test_auto_takes_the_profiles_default_where_neither_names_one(self):
        resolved = self.resolve(NamedContext())
        assert resolved.context is presets.AMBIENT_AIR
        assert resolved.context_source == SOURCE_PROFILE

    def test_a_context_the_run_names_wins(self):
        resolved = self.resolve(
            NamedContext(batch="chamber"), PeakAssignmentConfig(context="water")
        )
        assert resolved.context is presets.WATER
        assert resolved.context_source == SOURCE_CONFIG

    def test_the_identity_profile_keeps_no_context(self):
        # Naming the identity profile is how a run switches the layer off.
        resolved = self.resolve(
            NamedContext(batch="chamber"), PeakAssignmentConfig(profile="none")
        )
        assert resolved.context is presets.NO_CONTEXT
        assert resolved.context_source == SOURCE_PROFILE

    def test_a_name_the_library_no_longer_knows_falls_to_the_default(self):
        resolved = self.resolve(NamedContext(batch="retired-context"))
        assert resolved.context is presets.AMBIENT_AIR
        assert resolved.context_source == SOURCE_PROFILE

    def test_the_named_context_narrows_the_grid_it_searches(self):
        # The chamber caps nitrogen at two; ambient air at three.
        resolved = self.resolve(NamedContext(batch="chamber"))
        assert "N0-2" in resolved.element_ranges
        assert resolved.heuristics_config().context_ratio_windows == (
            presets.CHAMBER.ratio_windows()
        )

    def test_the_snapshot_records_where_the_context_came_from(self):
        snapshot = self.resolve(NamedContext(dataset="chamber")).snapshot()
        assert snapshot["context"] == "chamber"
        assert snapshot["context_source"] == SOURCE_DATASET
        assert snapshot["named_context"] == {"batch": None, "dataset": "chamber"}

    def test_a_run_with_nothing_named_records_so(self):
        snapshot = self.resolve(None).snapshot()
        assert snapshot["context_source"] == SOURCE_PROFILE
        assert snapshot["named_context"] == {"batch": None, "dataset": None}

    def test_the_preview_resolves_it_as_a_run_would(self):
        [preview] = preview_resolutions(
            PeakAssignmentConfig(),
            [SampleChemistry(tuple(BROMIDE), "-", 3, "orbi")],
            named_context=NamedContext(batch="chamber"),
        )
        assert (preview["context"], preview["context_source"]) == (
            "chamber",
            SOURCE_BATCH,
        )

    @pytest.mark.parametrize(
        "sent, stored",
        [
            ("chamber", "chamber"),
            ("Ambient", "ambient-air"),
            (" smog-chamber ", "chamber"),
            ("none", "none"),
            ("auto", None),
            ("", None),
            (None, None),
        ],
    )
    def test_a_named_context_is_stored_by_its_own_name(self, sent, stored):
        assert named_chemistry_context(sent) == stored

    def test_an_unknown_named_context_is_refused(self):
        with pytest.raises(ValueError, match="unknown chemistry context"):
            named_chemistry_context("mars")


class TestTheFittedWindow:
    """Step 3.6: a TOF's untargeted window bounded by the sample's width."""

    def resolve(self, config=None, instrument="tof"):
        return resolve_profile(
            config or PeakAssignmentConfig(),
            BROMIDE,
            instrument_type=instrument,
            polarity="-",
        )

    def bound(self, resolved, sigma=1.0, mu=-1.0, instrument="tof", trend=None):
        """Bound at a model whose width is ``sigma`` (None: the class's stands
        in) and whose centre is ``mu``."""
        model = MeasurementModel(
            mu_ppm=mu,
            mu_source="fitted",
            sigma_ppm=None if sigma is None else unwiden_sigma_ppm(sigma),
            fallback_sigma_ppm=5.0,
            trend=trend,
        )
        return with_fitted_window(
            resolved, model=model, sigmas=6.0, instrument_type=instrument
        )

    def test_a_tofs_window_is_bounded_and_says_so(self):
        bounded = self.bound(self.resolve())
        assert bounded.mz_precision_ppm == pytest.approx(7.0)
        assert bounded.mz_precision_source == SOURCE_FITTED_WIDTH
        assert bounded.search_config(["[M+Br]-"]).mass_range_ppm == pytest.approx(7.0)
        assert bounded.snapshot()["fitted_window"] == {
            "class_mz_precision_ppm": 10.0,
            "sigma_ppm": 1.0,
            "mu_ppm": -1.0,
            "centre_reach_ppm": 1.0,
            "sigmas": 6.0,
            "model_version": 1,
        }

    def test_a_centre_that_follows_mz_is_reached_at_either_end(self):
        # A centre at -0.5 ppm at m/z 400 and -2.0 at m/z 100: the window
        # reaches the farther end before it reaches any width.
        trend = MassTrend(
            intercept_ppm=0.0,
            offset_mda=-0.2,
            sigma_ppm=0.5,
            points=40,
            mz_lo=100.0,
            mz_hi=400.0,
        )
        bounded = self.bound(self.resolve(), sigma=1.0, mu=-0.8, trend=trend)
        assert bounded.mz_precision_ppm == pytest.approx(2.0 + 6.0)
        assert bounded.snapshot()["fitted_window"]["centre_reach_ppm"] == pytest.approx(
            2.0
        )

    def test_nothing_fitted_leaves_the_class_window(self):
        resolved = self.resolve()
        assert self.bound(resolved, sigma=None) is resolved

    def test_a_width_wider_than_the_class_window_leaves_it(self):
        resolved = self.resolve()
        assert self.bound(resolved, sigma=2.0) is resolved

    def test_an_orbitrap_is_not_bounded(self):
        resolved = self.resolve(instrument="orbi")
        assert self.bound(resolved, sigma=0.1, mu=0.0, instrument="orbi") is resolved

    def test_a_window_the_run_names_stands(self):
        resolved = self.resolve(PeakAssignmentConfig(mz_precision_ppm=9.0))
        assert self.bound(resolved) is resolved

    def test_the_identity_profiles_pinned_window_stands(self):
        resolved = self.resolve(PeakAssignmentConfig(profile="none"))
        assert self.bound(resolved) is resolved
        assert resolved.mz_precision_ppm == 10.0

    def test_an_unbounded_run_records_no_bound(self):
        assert self.resolve().snapshot()["fitted_window"] is None


class TestTheSkeletonSwitch:
    """Step 3.6's rider: which runs read an organonitrate's skeleton."""

    NITRATE = ["[M+NO3]-", "[M-H]-"]

    def test_a_nitrate_run_on_an_orbitrap_reads_it_in_its_filter(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), self.NITRATE, instrument_type="orbi", polarity="-"
        )
        assert resolved.profile is presets.NO3
        assert resolved.nox_skeleton is True
        assert resolved.heuristics_config().context_nox_skeleton is True
        assert resolved.snapshot()["nox_skeleton"] is True

    @pytest.mark.parametrize(
        "notations, instrument, context",
        [
            (["[M+NO3]-", "[M-H]-"], "tof", "auto"),
            (BROMIDE, "orbi", "auto"),
            (["[M+NO3]-", "[M-H]-"], "orbi", "indoor-air"),
        ],
    )
    def test_no_other_run_does(self, notations, instrument, context):
        resolved = resolve_profile(
            PeakAssignmentConfig(context=context),
            notations,
            instrument_type=instrument,
            polarity="-",
        )
        assert resolved.nox_skeleton is False
        assert resolved.heuristics_config().context_nox_skeleton is False


class TestTheWindowsGrade:
    """Decision MAS-979 (#3188): the context's windows grade the run's formulas."""

    NITRATE = ["[M+NO3]-", "[M-H]-"]

    def test_the_filter_refuses_nothing_on_the_windows(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), self.NITRATE, instrument_type="orbi", polarity="-"
        )
        assert resolved.heuristics_config().context_windows_gate is False

    def test_a_context_may_ask_for_the_gate(self):
        gated = replace(presets.AMBIENT_AIR, windows_gate=True)
        resolved = replace(
            resolve_profile(
                PeakAssignmentConfig(),
                self.NITRATE,
                instrument_type="orbi",
                polarity="-",
            ),
            context=gated,
        )
        assert resolved.heuristics_config().context_windows_gate is True

    def test_inside_the_block_the_run_grades_in_its_windows(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), self.NITRATE, instrument_type="tof", polarity="-"
        )
        assert resolved.context is presets.AMBIENT_AIR
        stuffed = "C10H20N5O3"  # N/C 0.5 against ambient air's 0.4
        bare = formula_plausibility(stuffed)
        with resolved.graded():
            assert formula_plausibility(stuffed) < bare
            assert formula_plausibility("C10H16O7") == 1.0
        assert formula_plausibility(stuffed) == bare

    def test_a_skeleton_run_grades_on_the_skeleton(self):
        dinitrate = "C5H8N2O8"  # O/C 1.6 as written; a C5 diol skeleton
        orbitrap = resolve_profile(
            PeakAssignmentConfig(), self.NITRATE, instrument_type="orbi", polarity="-"
        )
        tof = resolve_profile(
            PeakAssignmentConfig(), self.NITRATE, instrument_type="tof", polarity="-"
        )
        with orbitrap.graded():
            on_the_skeleton = formula_plausibility(dinitrate)
        with tof.graded():
            as_written = formula_plausibility(dinitrate)
        assert on_the_skeleton > as_written


class TestOverrides:
    def test_the_grid_comes_from_the_profile_when_the_run_names_none(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        )
        assert resolved.element_ranges == presets.resolve_element_ranges(
            presets.UR, presets.URONIUM
        )
        assert resolved.element_ranges_source == SOURCE_PROFILE

    def test_a_run_can_still_pin_its_own_grid(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(formula_ranges="C0-20 H0-40"),
            UREA,
            instrument_type="orbi",
            polarity="+",
        )
        assert resolved.element_ranges == "C0-20 H0-40"
        assert resolved.element_ranges_source == SOURCE_CONFIG

    def test_the_window_follows_the_instrument_class(self):
        orbi = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="orbi", polarity="-"
        )
        tof = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="tof", polarity="-"
        )
        assert orbi.mz_precision_ppm == 3.0
        assert tof.mz_precision_ppm == 10.0
        assert orbi.mz_precision_source == SOURCE_PROFILE

    def test_a_run_can_still_pin_its_own_window(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(mz_precision_ppm=7.5),
            BROMIDE,
            instrument_type="tof",
            polarity="-",
        )
        assert resolved.mz_precision_ppm == 7.5
        assert resolved.mz_precision_source == SOURCE_CONFIG


class TestTheIdentityProfile:
    def test_it_reproduces_the_pre_profile_search(self):
        # The plan's regression guard, stated where a run can reach it: naming
        # `none` must leave the grid, the window and the filter untouched.
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="none"),
            BROMIDE,
            instrument_type="orbi",
            polarity="-",
        )
        search = resolved.search_config(["[M+Br]-"])
        assert search.element_count_ranges == "C0-100 H0-100 O0-100 N0-100"
        assert search.mass_range_ppm == 10.0
        assert resolved.heuristics_config().context_ratio_windows == {}

    def test_it_survives_a_diagnostic_mechanism_panel(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="none"),
            UREA,
            instrument_type="orbi",
            polarity="+",
        )
        assert resolved.profile is presets.NO_PROFILE
        assert resolved.context is presets.NO_CONTEXT


class TestSearchConfiguration:
    def test_the_context_windows_reach_the_filter(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="orbi", polarity="-"
        )
        heuristics = resolved.heuristics_config()
        assert heuristics.use_senior is True
        assert heuristics.context_ratio_windows == presets.AMBIENT_AIR.ratio_windows()

    def test_the_grid_reaches_the_finder_in_explicit_isotope_form(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(formula_ranges="C0-20 H0-40 ^N0-1"),
            [],
            instrument_type="orbi",
        )
        # What a person wrote stays on the run; what the finder parses is the
        # explicit form.
        assert resolved.element_ranges == "C0-20 H0-40 ^N0-1"
        assert resolved.search_config([]).element_count_ranges == (
            "C0-20 H0-40 [15N]0-1"
        )

    def test_the_notations_are_joined_for_the_finder(self):
        resolved = resolve_profile(PeakAssignmentConfig(), [], instrument_type="orbi")
        assert (
            resolved.search_config(["[M+H]+", "[M+Na]+"]).ionizations
            == "[M+H]+,[M+Na]+"
        )


class TestSnapshot:
    def test_it_records_the_answer_and_the_question(self):
        snapshot = resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        ).snapshot()
        assert snapshot["profile"] == "UR"
        assert snapshot["requested_profile"] == "auto"
        assert snapshot["context"] == "uronium"
        assert snapshot["requested_context"] == "auto"

    def test_it_records_what_was_actually_searched(self):
        snapshot = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="tof", polarity="-"
        ).snapshot()
        assert snapshot["element_ranges"] == presets.resolve_element_ranges(
            presets.BR, presets.AMBIENT_AIR
        )
        assert snapshot["mz_precision_ppm"] == 10.0
        assert snapshot["ratio_windows"]["O/C"] == [0.0, 1.5]

    def test_it_records_the_ceiling_stage_a_matched_the_reference_under(self):
        shipped = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="tof", polarity="-"
        ).snapshot()
        assert shipped["known_window"] == presets.KNOWN_WINDOW_CEILING.to_json()
        identity = resolve_profile(
            PeakAssignmentConfig(profile="none"), BROMIDE, instrument_type="tof"
        ).snapshot()
        assert identity["context"] == "none"
        assert identity["known_window"] is None

    def test_it_is_json_serializable(self):
        import json

        snapshot = resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        ).snapshot()
        assert json.loads(json.dumps(snapshot)) == snapshot


class TestConfigValidation:
    def test_an_unknown_profile_is_refused_by_the_model(self):
        # Refused on the request that carried it, not by a run that has already
        # been created and reported to the client.
        with pytest.raises(ValidationError):
            PeakAssignmentConfig(profile="krypton-cims")

    def test_an_unknown_context_is_refused_by_the_model(self):
        with pytest.raises(ValidationError):
            PeakAssignmentConfig(context="mars")

    def test_the_defaults_are_auto(self):
        config = PeakAssignmentConfig()
        assert config.profile == "auto"
        assert config.context == "auto"
        assert config.formula_ranges is None
        assert config.mz_precision_ppm is None

    def test_an_empty_name_reads_as_auto(self):
        assert PeakAssignmentConfig(profile="", context=" ").profile == "auto"

    def test_the_species_cap_still_applies_to_an_explicit_grid(self):
        with pytest.raises(ValidationError):
            PeakAssignmentConfig(
                formula_ranges=" ".join(f"C{i}-{i + 1}" for i in range(13))
            )


class TestPreview:
    """What a launcher is told before a run: one answer per distinct resolution."""

    def test_samples_that_resolve_alike_are_counted_together(self):
        previews = preview_resolutions(
            PeakAssignmentConfig(),
            [
                SampleChemistry(tuple(UREA), "+", samples=3),
                # Another mode carrying the same fingerprint is the same answer.
                SampleChemistry(("[M+CH4N2O+H]+",), "+", samples=2),
            ],
        )
        assert [(p["profile"], p["context"], p["samples"]) for p in previews] == [
            ("UR", "uronium", 5)
        ]

    def test_the_most_samples_come_first_and_ties_do_not_follow_the_input(self):
        chemistries = [
            SampleChemistry((), "-", samples=1),
            SampleChemistry(tuple(BROMIDE), "-", samples=4),
            SampleChemistry(tuple(UREA), "+", samples=1),
        ]
        forward = preview_resolutions(PeakAssignmentConfig(), chemistries)
        backward = preview_resolutions(PeakAssignmentConfig(), chemistries[::-1])
        assert [p["profile"] for p in forward] == ["BR", "ESI_NEG", "UR"]
        assert backward == forward

    def test_it_carries_the_names_a_run_would_record(self):
        (preview,) = preview_resolutions(
            PeakAssignmentConfig(),
            [SampleChemistry(tuple(BROMIDE), "-", instrument_type="orbi")],
        )
        snapshot = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="orbi", polarity="-"
        ).snapshot()
        for key in (
            "profile",
            "profile_label",
            "requested_profile",
            "context",
            "context_label",
            "requested_context",
            "element_ranges",
            "mz_precision_ppm",
        ):
            assert preview[key] == snapshot[key], key

    def test_the_instrument_class_sets_the_window_and_parts_the_answer(self):
        previews = preview_resolutions(
            PeakAssignmentConfig(),
            [
                SampleChemistry(tuple(UREA), "+", samples=2, instrument_type="orbi"),
                SampleChemistry(tuple(UREA), "+", samples=1, instrument_type="tof"),
            ],
        )
        assert [
            (p["profile"], p["mz_precision_ppm"], p["samples"]) for p in previews
        ] == [
            ("UR", presets.INSTRUMENT_MZ_PRECISION_PPM["orbi"], 2),
            ("UR", presets.INSTRUMENT_MZ_PRECISION_PPM["tof"], 1),
        ]

    def test_a_class_it_cannot_read_gets_the_unknown_class_window(self):
        (preview,) = preview_resolutions(
            PeakAssignmentConfig(), [SampleChemistry(tuple(UREA), "+")]
        )
        assert preview["mz_precision_ppm"] == presets.DEFAULT_MZ_PRECISION_PPM

    def test_a_window_the_config_names_is_every_class_window(self):
        (preview,) = preview_resolutions(
            PeakAssignmentConfig(mz_precision_ppm=5.0),
            [
                SampleChemistry(tuple(UREA), "+", samples=2, instrument_type="orbi"),
                SampleChemistry(tuple(UREA), "+", samples=1, instrument_type="tof"),
            ],
        )
        assert (preview["mz_precision_ppm"], preview["samples"]) == (5.0, 3)

    def test_a_named_profile_is_one_answer_per_polarity(self):
        # The polarity is kept apart, since a launcher warns where a profile's
        # own polarity is not its samples'.
        previews = preview_resolutions(
            PeakAssignmentConfig(profile="BR"),
            [
                SampleChemistry(tuple(UREA), "+", samples=2),
                SampleChemistry(tuple(BROMIDE), "-", samples=2),
            ],
        )
        assert sorted((p["polarity"], p["profile_polarity"]) for p in previews) == [
            ("+", "-"),
            ("-", "-"),
        ]
        assert {p["profile"] for p in previews} == {"BR"}

    def test_no_samples_is_no_answer(self):
        assert preview_resolutions(PeakAssignmentConfig(), []) == []


class TestPresets:
    """The names a launcher offers are the names the run config accepts."""

    def test_every_library_preset_is_served(self):
        served = PeakAssignmentPresets()
        assert {p.name for p in served.profiles} == set(presets.REAGENT_PROFILES)
        assert {c.name for c in served.contexts} == set(presets.CHEMISTRY_CONTEXTS)

    def test_every_served_name_is_one_the_config_accepts(self):
        served = PeakAssignmentPresets()
        for profile in served.profiles:
            assert PeakAssignmentConfig(profile=profile.name).profile == profile.name
        for context in served.contexts:
            assert PeakAssignmentConfig(context=context.name).context == context.name

    def test_a_profile_says_its_polarity_and_the_context_auto_takes(self):
        served = {p.name: p for p in PeakAssignmentPresets().profiles}
        assert served["BR"].polarity == "-"
        assert served["BR"].default_context == "ambient-air"
        assert served["UR"].default_context == "uronium"
        for profile in served.values():
            assert profile.default_context in presets.CHEMISTRY_CONTEXTS

    def test_the_identity_entries_come_last(self):
        served = PeakAssignmentPresets()
        assert served.profiles[-1].name == presets.IDENTITY_PROFILE_NAME
        assert served.contexts[-1].name == presets.NO_CONTEXT.name

    def test_a_context_carries_its_description(self):
        served = {c.name: c for c in PeakAssignmentPresets().contexts}
        assert served["ambient-air"].description == presets.AMBIENT_AIR.description
        assert served["ambient-air"].polarity is None


class TestPreviewQuery:
    def test_it_refuses_what_the_run_config_refuses(self):
        with pytest.raises(ValidationError):
            ProfilePreviewQueryParams(profile="krypton-cims")
        with pytest.raises(ValidationError):
            ProfilePreviewQueryParams(context="mars")

    def test_blank_and_auto_read_as_auto(self):
        query = ProfilePreviewQueryParams(profile=" ", context="AUTO")
        assert (query.profile, query.context) == ("auto", "auto")

    def test_an_alias_is_accepted(self):
        assert ProfilePreviewQueryParams(profile="bromide").profile == "bromide"
